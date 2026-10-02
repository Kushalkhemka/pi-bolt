#!/usr/bin/env python3
"""Offline Pi 1.0 CLI worker checks; temporary files, local API, no performance claims."""
import argparse
import base64
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import importlib.util
import json
import os
from pathlib import Path
import re
import struct
import subprocess
import tempfile
import threading
import time
import zlib

ROOT = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('worker_rpc_helpers', ROOT / 'cli-workflows.py')
helpers = importlib.util.module_from_spec(spec)
spec.loader.exec_module(helpers)

SCRIPTS = {
    'WORKER_IO': '''const source = await tools.read({path:"input.txt"});
if (!source.includes("WORKER_INPUT_OK")) throw new Error("Unexpected input");
await tools.write({path:"output.txt",content:"WORKER_OUTPUT_OK\\n"});
const copied = await tools.read({path:"output.txt"});
if (!copied.includes("WORKER_OUTPUT_OK")) throw new Error("Unexpected output");
store("workerProbe",7); text(source); return copied;''',
    'WORKER_STORE': 'if(load("workerProbe")!==7) throw new Error("Store did not persist"); return "WORKER_STORE_OK";',
    'WORKER_BAD_NAME': 'return await tools.worker_probe_missing({});',
    'WORKER_BAD_TYPE': 'return null.workerProbe;',
    'WORKER_RECOVERY': 'return "WORKER_RECOVERY_OK";',
    'WORKER_CANCEL': 'return await tools.bash({command:"printf WORKER_CANCEL_RUNNING; sleep 2"});',
}

# Test-only observation of real Worker messages. Successful resizing alone is
# insufficient: Pi silently falls back to in-process Photon if a worker fails.
EXTENSION = '''import { appendFileSync } from "node:fs";
import { Worker } from "node:worker_threads";
import { getNativeClipboard } from "@earendil-works/pi-tui";
export default function(pi) {
  const audit = value => appendFileSync(process.env.PI_WORKER_AUDIT, JSON.stringify(value)+"\\n");
  if(process.platform==="darwin") {
    const helper = getNativeClipboard();
    // The Darwin addon returns false for unknown names before consulting OS
    // modifier state. Never read or write any user clipboard data.
    if(!helper || typeof helper.getText!=="function" || typeof helper.getImage!=="function" ||
       typeof helper.isModifierPressed!=="function" || helper.isModifierPressed("__probe__")!==false)
      throw new Error("Native helper could not load or return its benign unknown-name result");
    audit({event:"native_helper",loaded:true,unknownModifier:false,clipboardAccessed:false});
  }
  const watched = new WeakSet();
  const originalOn = Worker.prototype.on;
  const originalPost = Worker.prototype.postMessage;
  let nextID = 0;
  const ids = new WeakMap();
  const watch = worker => {
    if(watched.has(worker)) return;
    watched.add(worker); ids.set(worker,++nextID);
    const id = ids.get(worker);
    audit({event:"worker_watch",id});
    originalOn.call(worker,"message",message => {
      if(message?.type==="done") audit({event:"codemode_done",id,ok:message.ok});
      if(message?.result?.wasResized!==undefined) audit({event:"image_result",id,
        width:message.result.width,height:message.result.height,wasResized:message.result.wasResized});
    });
    originalOn.call(worker,"exit",code => audit({event:"worker_exit",id,code}));
  };
  Worker.prototype.on = function(event,callback) {
    watch(this); return originalOn.call(this,event,callback);
  };
  Worker.prototype.postMessage = function(message,...args) {
    watch(this);
    if(message?.inputBytes instanceof Uint8Array) audit({event:"image_post",id:ids.get(this),bytes:message.inputBytes.byteLength});
    return originalPost.call(this,message,...args);
  };
  pi.on("tool_call",event => audit({event:"tool_call",name:event.toolName,args:event.input}));
}
'''


class Provider(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'

    def log_message(self, *_):
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        self.server.requests.append(body)
        labels = set(SCRIPTS) | {'WORKER_IMAGE'}
        prompts = []
        for index, message in enumerate(body['messages']):
            if message['role'] != 'user':
                continue
            content = message['content']
            text = content if isinstance(content, str) else ''.join(part.get('text', '') for part in content)
            if text in labels:
                prompts.append((index, text))
        prompt_index, label = prompts[-1]
        self.send_response(200)
        self.send_header('Content-Type', 'text/event-stream')
        self.send_header('Connection', 'close')
        self.end_headers()

        def emit(delta, finish=None):
            chunk = {'id': 'pi-worker-check', 'object': 'chat.completion.chunk', 'created': 1,
                     'model': body['model'], 'choices': [{'index': 0, 'delta': delta, 'finish_reason': finish}]}
            self.wfile.write(('data: ' + json.dumps(chunk) + '\n\n').encode())

        emit({'role': 'assistant', 'content': ''})
        # Image results add a synthetic user attachment after the tool response.
        # Look only after the current real prompt, including repeated recovery.
        if any(message['role'] == 'tool' for message in body['messages'][prompt_index + 1:]):
            emit({'content': 'WORKER_DONE:' + label}); emit({}, 'stop')
        elif label in SCRIPTS:
            emit({'tool_calls': [{'index': 0, 'id': label, 'type': 'function', 'function': {
                'name': 'codemode', 'arguments': json.dumps({'code': '// @options: {"timeout_ms":8000}\n' + SCRIPTS[label]})}}]})
            emit({}, 'tool_calls')
        elif label == 'WORKER_IMAGE':
            emit({'tool_calls': [{'index': 0, 'id': label, 'type': 'function',
                'function': {'name': 'read', 'arguments': json.dumps({'path': 'image.png'})}}]})
            emit({}, 'tool_calls')
        else:
            emit({'content': 'WORKER_DONE:' + label}); emit({}, 'stop')
        self.wfile.write(b'data: [DONE]\n\n'); self.wfile.flush(); self.close_connection = True


def png_fixture(width=2200, height=2):
    def chunk(name, contents):
        return struct.pack('>I', len(contents)) + name + contents + struct.pack('>I', zlib.crc32(name + contents) & 0xffffffff)
    rows = (b'\0' + bytes((40, 120, 200, 255)) * width) * height
    return b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', width, height, 8, 6, 0, 0, 0)) \
        + chunk(b'IDAT', zlib.compress(rows)) + chunk(b'IEND', b'')


def completed_turn(client, label, expected_error=False):
    offset = len(client.events)
    client.prompt(label)
    events = client.events[offset:]
    ends = [event for event in events if event['type'] == 'tool_execution_end'
            and event.get('toolName') == ('read' if label == 'WORKER_IMAGE' else 'codemode')]
    if len(ends) != 1 or ends[0]['isError'] is not expected_error:
        raise RuntimeError({'label': label, 'tool_ends': ends})
    if client.request('get_last_assistant_text')['text'] != 'WORKER_DONE:' + label:
        raise RuntimeError('Unexpected provider completion: ' + label)
    return ends[0], events


def check(config, name, diagnostics):
    server = ThreadingHTTPServer(('127.0.0.1', 0), Provider)
    server.daemon_threads = True; server.requests = []
    service = threading.Thread(target=server.serve_forever, daemon=True); service.start()
    client = None
    audit = []; evidence = {'name': name, 'passed': False, 'gates': []}
    try:
        with tempfile.TemporaryDirectory(prefix='pi-v1-workers-') as temporary:
            root = Path(temporary); agent = root / 'agent'; agent.mkdir()
            sessions = root / 'sessions'; sessions.mkdir()
            (root / 'input.txt').write_text('WORKER_INPUT_OK\n')
            (root / 'image.png').write_bytes(png_fixture())
            (agent / 'models.json').write_text(json.dumps({'providers': {'worker-check': {
                'baseUrl': f'http://127.0.0.1:{server.server_port}/v1', 'api': 'openai-completions', 'apiKey': 'local-dummy',
                'models': [{'id': 'mock', 'name': 'mock', 'reasoning': False, 'input': ['text', 'image'],
                    'contextWindow': 128000, 'maxTokens': 8192, 'cost': dict.fromkeys(('input', 'output', 'cacheRead', 'cacheWrite'), 0)}]}}}))
            (agent / 'settings.json').write_text(json.dumps({'enableInstallTelemetry': False,
                'retry': {'enabled': False}, 'compaction': {'enabled': False}, 'quietStartup': True,
                'defaultTools': ['read', 'write', 'bash', 'codemode'], 'codemode': {'mode': 'on'}}))
            extension = root / 'worker-observer.mjs'; extension.write_text(EXTENSION)
            audit_path = diagnostics / (name + '-worker-observer.jsonl')
            audit_path.write_text('')
            env = {key: os.environ[key] for key in ('PATH', 'HOME', 'TMPDIR', 'LANG', 'USER', 'LOGNAME') if key in os.environ}
            env.update(config.get('env', {})); env.update({'PI_CODING_AGENT_DIR': str(agent),
                'PI_WORKER_AUDIT': str(audit_path), 'PI_OFFLINE': '1', 'DO_NOT_TRACK': '1', 'NO_COLOR': '1', 'NODE_ENV': 'production'})
            command = config['command'] + ['--mode', 'rpc', '--provider', 'worker-check', '--model', 'mock',
                '--thinking', 'off', '--session-dir', str(sessions), '--extension', str(extension),
                '--no-skills', '--no-prompt-templates', '--no-themes']
            evidence['command'] = command
            client = helpers.Rpc(command, root, env)
            client.request('get_state')
            io, events = completed_turn(client, 'WORKER_IO')
            if (root / 'output.txt').read_text() != 'WORKER_OUTPUT_OK\n' or 'WORKER_INPUT_OK' not in json.dumps(io):
                raise RuntimeError('Worker read/write result mismatch')
            nested = [event.get('toolName') for event in events if event['type'] == 'tool_execution_end'
                      and event.get('toolName') != 'codemode']
            if nested != ['read', 'write', 'read']:
                raise RuntimeError({'nested_tools': nested})
            evidence['gates'].append('codemode_read_write_read')
            store, _ = completed_turn(client, 'WORKER_STORE')
            if 'WORKER_STORE_OK' not in json.dumps(store):
                raise RuntimeError('Codemode store failed')
            evidence['gates'].append('codemode_store_next_worker')
            for label in ('WORKER_BAD_NAME', 'WORKER_BAD_TYPE'):
                error, _ = completed_turn(client, label, True)
                if 'TypeError' not in json.dumps(error):
                    raise RuntimeError('Expected ordinary script error missing')
                recovered, _ = completed_turn(client, 'WORKER_RECOVERY')
                if 'WORKER_RECOVERY_OK' not in json.dumps(recovered):
                    raise RuntimeError('Worker error recovery failed')
                evidence['gates'].append(label.lower() + '_recovery')
            offset = len(client.events)
            client.request('prompt', message='WORKER_CANCEL')
            running = lambda event: event['type'] == 'tool_execution_update' and event.get('toolName') == 'bash' \
                and 'WORKER_CANCEL_RUNNING' in json.dumps(event)
            if not any(running(event) for event in client.events[offset:]):
                client.until(running)
            client.request('abort')
            if not any(event['type'] == 'agent_settled' for event in client.events[offset:]):
                client.until(lambda event: event['type'] == 'agent_settled')
            aborted = [event for event in client.events[offset:] if event['type'] == 'tool_execution_end'
                       and event.get('toolName') == 'codemode']
            if len(aborted) != 1 or not aborted[0]['isError']:
                raise RuntimeError({'cancelled_codemode': aborted})
            recovered, _ = completed_turn(client, 'WORKER_RECOVERY')
            if 'WORKER_RECOVERY_OK' not in json.dumps(recovered):
                raise RuntimeError('Worker cancellation recovery failed')
            evidence['gates'].append('finite_async_nested_tool_abort_recovery')
            image, _ = completed_turn(client, 'WORKER_IMAGE')
            content = image.get('result', {}).get('content', [])
            pictures = [item for item in content if item.get('type') == 'image']
            if len(pictures) != 1 or 'original 2200x2, displayed at 2000x2' not in json.dumps(content):
                raise RuntimeError({'image_read': image})
            data = base64.b64decode(pictures[0]['data'])
            if data[:8] != b'\x89PNG\r\n\x1a\n' or struct.unpack('>II', data[16:24]) != (2000, 2):
                raise RuntimeError('Resized PNG dimensions mismatch')
            evidence['gates'].append('read_image_photon_resize')
            client.close()
            audit = [json.loads(line) for line in audit_path.read_text().splitlines()]
            image_jobs = [item for item in audit if item['event'] == 'image_result' and item['wasResized']
                          and (item['width'], item['height']) == (2000, 2)]
            if len(image_jobs) != 1:
                raise RuntimeError('Image worker completion not proven; in-process fallback is insufficient')
            exited = {item['id'] for item in audit if item['event'] == 'worker_exit'}
            watched = {item['id'] for item in audit if item['event'] == 'worker_watch'}
            if watched != exited:
                raise RuntimeError({'workers_not_exited': sorted(watched - exited)})
            done = [item for item in audit if item['event'] == 'codemode_done']
            if len(done) != 7 or sum(item['ok'] for item in done) != 5:
                raise RuntimeError({'codemode_worker_done': done})
            native_helper = [item for item in audit if item['event'] == 'native_helper']
            if os.uname().sysname == 'Darwin' and len(native_helper) != 1:
                raise RuntimeError('Native Darwin helper loading was not proven')
            if native_helper:
                evidence['gates'].append('native_helper_load_no_clipboard_access')
            evidence.update(passed=True, worker_count=len(watched), codemode_completed_workers=len(done),
                image_completed_workers=len(image_jobs), observed_worker_exits=len(exited),
                worker_observation_scope='Test-only Worker API observation proves successful responses and exits; no timing',
                provider_requests=len(server.requests), env=config.get('env', {}))
            return evidence
    except Exception as error:
        evidence['error'] = str(error)
        raise
    finally:
        if client and client.proc.poll() is None:
            client.proc.kill(); client.proc.wait(timeout=10)
        if client:
            for reader in client.readers:
                reader.join(2)
        # Preserve incomplete evidence as well as passes before temporary files vanish.
        if client:
            if audit_path.is_file():
                audit = [json.loads(line) for line in audit_path.read_text().splitlines()]
            (diagnostics / (name + '-events.json')).write_text(json.dumps(client.events, indent=2) + '\n')
            (diagnostics / (name + '-stderr.log')).write_text(''.join(client.errors))
            stderr = ''.join(client.errors)
            evidence['native_selection_log_events'] = len(re.findall(r'\(module \d+ start \d+ kind \d+\) is at ', stderr))
            evidence['native_tier_promotion_events'] = len(re.findall(r'AOT: tiered function \d+ to ordinary bytecode profiling', stderr))
        (diagnostics / (name + '-workers.json')).write_text(json.dumps(audit, indent=2) + '\n')
        (diagnostics / (name + '-provider-requests.json')).write_text(json.dumps(server.requests, indent=2) + '\n')
        (diagnostics / (name + '-result.json')).write_text(json.dumps(evidence, indent=2) + '\n')
        server.shutdown(); server.server_close(); service.join(2)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--variants-file')
    parser.add_argument('--variants', nargs='+')
    parser.add_argument('--npm-package', help='Add Node execution of this exact Pi1.0 npm package')
    parser.add_argument('--node', default='node')
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    output = Path(args.output).resolve(); output.parent.mkdir(parents=True, exist_ok=True)
    diagnostics = output.with_name(output.stem + '-diagnostics'); diagnostics.mkdir(exist_ok=True)
    configurations = {}
    if args.variants_file:
        all_variants = json.loads(Path(args.variants_file).read_text())
        configurations.update({name: all_variants[name] for name in args.variants or all_variants})
    if args.npm_package:
        package = Path(args.npm_package).resolve()
        if json.loads((package / 'package.json').read_text())['version'] != '1.0.0':
            parser.error('--npm-package must be exactly Pi1.0.0')
        configurations['npm_node'] = {'command': [args.node, str(package / 'dist/bundle/cli.js')],
                                      'env': {'PI_PACKAGE_DIR': str(package)}}
    if not configurations:
        parser.error('Provide --npm-package or --variants-file')
    report = {'complete': False, 'scope': 'Offline Pi1.0 CLI codemode and image workers; no performance measurement',
              'harness_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
              'observer_sha256': hashlib.sha256(EXTENSION.encode()).hexdigest(), 'checks': []}
    output.write_text(json.dumps(report, indent=2) + '\n')
    try:
        for name, config in configurations.items():
            result = check(config, name, diagnostics)
            report['checks'].append(result); output.write_text(json.dumps(report, indent=2) + '\n')
            print(name + ': codemode/image worker checks passed', flush=True)
    except Exception as error:
        report['error'] = str(error); output.write_text(json.dumps(report, indent=2) + '\n')
        raise
    report['complete'] = True; output.write_text(json.dumps(report, indent=2) + '\n')


if __name__ == '__main__':
    main()
