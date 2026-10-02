#!/usr/bin/env python3
"""Exercise native Pi tools, an external TypeScript extension, and session persistence."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import queue
import subprocess
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pi

TOOLS = [('write', {'path': 'probe.txt', 'content': 'alpha\n'}),
         ('read', {'path': 'probe.txt'}),
         ('edit', {'path': 'probe.txt', 'oldText': 'alpha', 'newText': 'beta'}),
         ('bash', {'command': 'printf native-exec'}),
         ('native_probe', {'name': 'Pi'})]


class Handler(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'

    def log_message(self, *_):
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        self.server.requests.append(body)
        turn = (len(self.server.requests) - 1) // 2
        self.send_response(200)
        self.send_header('Content-Type', 'text/event-stream')
        self.send_header('Connection', 'close')
        self.end_headers()
        def emit(delta, finish=None):
            data = {'id': 'functional', 'object': 'chat.completion.chunk', 'created': 1,
                    'model': 'mock', 'choices': [{'index': 0, 'delta': delta, 'finish_reason': finish}]}
            self.wfile.write(('data: ' + json.dumps(data) + '\n\n').encode())
        emit({'role': 'assistant', 'content': ''})
        if body['messages'][-1]['role'] != 'tool':
            name, arguments = TOOLS[turn]
            emit({'tool_calls': [{'index': 0, 'id': f'call_{turn}', 'type': 'function',
                                 'function': {'name': name, 'arguments': json.dumps(arguments)}}]})
            emit({}, 'tool_calls')
        else:
            emit({'content': f'turn-{turn}-complete'})
            emit({}, 'stop')
        self.wfile.write(b'data: [DONE]\n\n')
        self.wfile.flush()
        self.close_connection = True


def check(data, mode):
    config = pi.configuration(data, mode)
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    server.requests = []
    threading.Thread(target=server.serve_forever, daemon=True).start()
    with tempfile.TemporaryDirectory(prefix='pi-native-functional-') as directory:
        root = Path(directory)
        agent, sessions = root / 'agent', root / 'sessions'
        agent.mkdir(); sessions.mkdir()
        (agent / 'models.json').write_text(json.dumps({'providers': {'benchmark': {
            'baseUrl': f'http://127.0.0.1:{server.server_port}/v1', 'api': 'openai-completions', 'apiKey': 'local-dummy',
            'models': [{'id': 'mock', 'name': 'mock', 'reasoning': False, 'input': ['text'],
                        'contextWindow': 128000, 'maxTokens': 8192,
                        'cost': {'input': 0, 'output': 0, 'cacheRead': 0, 'cacheWrite': 0}}]}}}))
        (agent / 'settings.json').write_text(json.dumps({'enableInstallTelemetry': False,
            'compaction': {'enabled': False}, 'retry': {'enabled': False}, 'quietStartup': True}))
        extension = root / 'extension.ts'
        extension.write_text('''import { Type } from "@earendil-works/pi-ai";
import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";
export default function (pi: ExtensionAPI) {
    pi.registerTool({name: "native_probe", label: "Native probe", description: "Offline compatibility probe",
        parameters: Type.Object({name: Type.String()}),
        async execute(_id: string, params: {name: string}) {
            return {content: [{type: "text", text: "native-" + params.name}], details: {ok: true}};
        }});
}
''')
        env = {k: os.environ[k] for k in ('PATH', 'HOME', 'TMPDIR', 'LANG', 'USER', 'LOGNAME') if k in os.environ}
        env.update(config['env'])
        env.update({'PI_CODING_AGENT_DIR': str(agent), 'PI_OFFLINE': '1', 'DO_NOT_TRACK': '1',
                    'NODE_ENV': 'production', 'NO_COLOR': '1'})
        command = config['command'] + ['--mode', 'rpc', '--provider', 'benchmark', '--model', 'mock',
            '--thinking', 'off', '--session-dir', str(sessions), '--no-extensions', '--extension', str(extension),
            '--no-skills', '--no-prompt-templates', '--no-themes']
        proc = subprocess.Popen(command, cwd=root, env=env, stdin=subprocess.PIPE,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        events, received, errors = queue.Queue(), [], []
        def read():
            try:
                for line in proc.stdout:
                    events.put(json.loads(line))
            finally:
                events.put({'type': 'eof'})
        def err():
            for line in proc.stderr:
                errors.append(line)
        reader = threading.Thread(target=read, daemon=True); reader.start()
        err_reader = threading.Thread(target=err, daemon=True); err_reader.start()
        def send(value):
            proc.stdin.write(json.dumps(value) + '\n'); proc.stdin.flush()
        def until(predicate):
            deadline = time.monotonic() + 30
            while True:
                event = events.get(timeout=max(.01, deadline - time.monotonic()))
                received.append(event)
                if event.get('type') == 'eof':
                    err_reader.join(1)
                    raise RuntimeError(f'{mode}: exited unexpectedly: {errors}')
                if event.get('type') == 'response' and not event.get('success'):
                    raise RuntimeError(f'{mode}: {event}')
                if event.get('type') == 'message_end' and event.get('message', {}).get('stopReason') == 'error':
                    raise RuntimeError(f'{mode}: {event}')
                if predicate(event):
                    return event
        try:
            send({'type': 'get_state', 'id': 'ready'})
            ready = until(lambda e: e.get('id') == 'ready')
            for i, (tool, _) in enumerate(TOOLS):
                send({'type': 'prompt', 'id': str(i), 'message': f'Use {tool} for offline probe {i}.'})
                until(lambda e: e.get('type') == 'agent_end')
            send({'type': 'get_messages', 'id': 'messages'})
            messages = until(lambda e: e.get('id') == 'messages')['data']['messages']
            ended = [e for e in received if e.get('type') == 'tool_execution_end']
            assert [e['toolName'] for e in ended] == [t[0] for t in TOOLS], ended
            assert all(not e.get('isError') for e in ended), {'tools': ended, 'stderr': ''.join(errors)}
            assert (root / 'probe.txt').read_text() == 'beta\n'
            assert any('native-exec' in json.dumps(e) for e in ended)
            assert any('native-Pi' in json.dumps(e) for e in ended)
            assert len(server.requests) == 10
            send({'type': 'abort', 'id': 'abort-idle'})
            until(lambda e: e.get('id') == 'abort-idle')
            proc.stdin.close(); proc.wait(timeout=10); err_reader.join(1)
            assert proc.returncode == 0, errors
            files = list(sessions.rglob('*.jsonl'))
            assert len(files) == 1, files
            entries = [json.loads(line) for line in files[0].read_text().splitlines()]
            assert sum(e.get('type') == 'message' for e in entries) >= 15, entries
            # Reopen the persisted session in a fresh process with the same extension.
            resume = command + ['--session', str(files[0])]
            second = subprocess.run(resume, cwd=root, env=env,
                input=json.dumps({'type': 'get_messages', 'id': 'resume'}) + '\n',
                capture_output=True, text=True, timeout=30)
            restored = [json.loads(line) for line in second.stdout.splitlines()]
            answer = next(e for e in restored if e.get('id') == 'resume')
            assert answer['success'] and answer['data']['messages'] == messages
            assert second.returncode == 0, second.stderr
            return {'mode': mode, 'tools': [t[0] for t in TOOLS], 'requests': 10,
                    'typescript_extension': True, 'session_saved_and_restored': True,
                    'idle_abort': True, 'reply_sha256': hashlib.sha256(json.dumps(messages, sort_keys=True).encode()).hexdigest(),
                    'stderr': ''.join(errors), 'passed': True}
        finally:
            if proc.poll() is None:
                proc.kill(); proc.wait()
            server.shutdown(); server.server_close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--modes', nargs='+', choices=pi.MODES, default=['native'])
    args = parser.parse_args()
    data = pi.load()
    results = []
    for mode in args.modes:
        results.append(check(data, mode))
        print(f'{mode}: tools, TypeScript extension, session resume and idle abort passed', flush=True)
    output = pi.ARTIFACTS / 'functional-validation.json'
    output.write_text(json.dumps({'checks': results}, indent=2) + '\n')


if __name__ == '__main__':
    main()
