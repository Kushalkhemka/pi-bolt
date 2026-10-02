#!/usr/bin/env python3
"""Bounded offline Pi RPC CLI workflows; this does not test terminal menus or performance."""
import argparse
import base64
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import queue
import re
import subprocess
import tempfile
import threading
import time

import pi


EXTENSION = '''import {appendFileSync} from "node:fs";
export default function(pi) {
  const audit = data => appendFileSync(process.env.PI_WORKFLOW_AUDIT, JSON.stringify(data) + "\\n");
  let cancelNew = false;
  pi.on("session_start", (_, ctx) => audit({event:"start", file:ctx.sessionManager.getSessionFile(),
    availableTools:pi.getAllTools().map(tool=>tool.name), activeTools:pi.getActiveTools()}));
  pi.on("session_before_switch", e => {
    audit({event:"before_switch", reason:e.reason});
    if (e.reason === "new" && cancelNew) {cancelNew=false; return {cancel:true};}
  });
  pi.on("session_before_fork", e => audit({event:"before_fork", id:e.entryId}));
  pi.on("session_compact", e => audit({event:"compact", reason:e.reason, summary:e.compactionEntry.summary}));
  pi.registerCommand("workflow-dialog", {description:"Offline dialog probe", handler:async (_, ctx) => {
    const selected = await ctx.ui.select("Workflow choice", ["alpha", "beta"]);
    const confirmed = await ctx.ui.confirm("Workflow confirm", "Confirm chosen value");
    pi.appendEntry("workflow-dialog", {selected, confirmed});
    audit({event:"dialog", selected, confirmed});
  }});
  pi.registerCommand("workflow-cancel-new", {handler:async () => {cancelNew=true;}});
}
'''


class Provider(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'

    def log_message(self, *_):
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        self.server.requests.append(body)
        user = next(m for m in reversed(body['messages']) if m['role'] == 'user')['content']
        text = user if isinstance(user, str) else ''.join(part.get('text', '') for part in user)
        retry_error = text == 'RETRY_ONCE' and sum(
            any(m['role'] == 'user' and 'RETRY_ONCE' in str(m['content']) for m in request['messages'])
            for request in self.server.requests) == 1
        if text == 'PROVIDER_ERROR' or retry_error:
            message = 'EXPECTED_WORKFLOW_RETRY_ERROR' if retry_error else 'EXPECTED_WORKFLOW_PROVIDER_ERROR'
            payload = json.dumps({'error': {'message': message, 'type': 'rate_limit_error' if retry_error else 'server_error'}}).encode()
            self.send_response(429 if retry_error else 500)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(payload)))
            self.end_headers(); self.wfile.write(payload); self.wfile.flush()
            return
        self.send_response(200)
        self.send_header('Content-Type', 'text/event-stream')
        self.send_header('Connection', 'close')
        self.end_headers()
        def emit(delta, finish=None):
            chunk = {'id': 'workflow', 'object': 'chat.completion.chunk', 'created': 1,
                     'model': body['model'], 'choices': [{'index': 0, 'delta': delta, 'finish_reason': finish}]}
            self.wfile.write(('data: ' + json.dumps(chunk) + '\n\n').encode())
        emit({'role': 'assistant', 'content': ''})
        # Pi's compaction and turn-prefix summary requests deliberately have no tools.
        if not body.get('tools'):
            emit({'content': '## Goal\nWORKFLOW_COMPACTED\n\n## Next Steps\nContinue offline validation.'})
            emit({}, 'stop')
        elif 'TOOL_ERROR' in text and body['messages'][-1]['role'] != 'tool':
            emit({'tool_calls': [{'index': 0, 'id': 'missing-file', 'type': 'function',
                 'function': {'name': 'read', 'arguments': json.dumps({'path': 'definitely-missing-workflow.txt'})}}]})
            emit({}, 'tool_calls')
        else:
            emit({'content': 'WORKFLOW_TOOL_RECOVERED' if 'TOOL_ERROR' in text else 'WORKFLOW_OK:' + text})
            emit({}, 'stop')
        self.wfile.write(b'data: [DONE]\n\n'); self.wfile.flush(); self.close_connection = True


class Rpc:
    def __init__(self, command, cwd, env):
        self.proc = subprocess.Popen(command, cwd=cwd, env=env, stdin=subprocess.PIPE,
                                     stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        self.queue, self.events, self.errors = queue.Queue(), [], []
        self.dialogs = []
        self.counter = 0
        def read():
            try:
                for line in self.proc.stdout:
                    try:
                        self.queue.put(json.loads(line))
                    except Exception as error:
                        self.queue.put({'type': 'decode_error', 'error': str(error), 'line': line})
            finally:
                self.queue.put({'type': 'eof'})
        def stderr():
            self.errors.extend(self.proc.stderr)
        self.readers = [threading.Thread(target=f, daemon=True) for f in (read, stderr)]
        for thread in self.readers:
            thread.start()

    def send(self, value):
        self.proc.stdin.write(json.dumps(value) + '\n'); self.proc.stdin.flush()

    def until(self, predicate):
        deadline = time.monotonic() + 30
        while True:
            event = self.queue.get(timeout=max(.01, deadline - time.monotonic()))
            self.events.append(event)
            if event['type'] in ('eof', 'decode_error', 'extension_error'):
                raise RuntimeError({'event': event, 'stderr': ''.join(self.errors)})
            if event['type'] == 'extension_ui_request' and event['method'] in ('select', 'confirm'):
                self.dialogs.append(event)
                assert event['title'] in ('Workflow choice', 'Workflow confirm'), event
                reply = {'type': 'extension_ui_response', 'id': event['id']}
                reply.update({'value': 'beta'} if event['method'] == 'select' else {'confirmed': True})
                self.send(reply)
            if predicate(event):
                return event
            if time.monotonic() >= deadline:
                raise TimeoutError('RPC workflow deadline')

    def request(self, kind, expected=True, **fields):
        self.counter += 1
        identity = f'workflow-{self.counter}'
        self.send({'type': kind, 'id': identity, **fields})
        event = self.until(lambda e: e.get('type') == 'response' and e.get('id') == identity)
        assert event['success'] is expected, event
        return event.get('data', event)

    def prompt(self, message, expected_stop='stop'):
        offset = len(self.events)
        self.request('prompt', message=message)
        self.until(lambda e: e['type'] == 'agent_end' and not e.get('willRetry'))
        self.until(lambda e: e['type'] == 'agent_settled')
        replies = [e['message'] for e in self.events[offset:]
                   if e['type'] == 'message_end' and e['message']['role'] == 'assistant']
        assert replies and replies[-1]['stopReason'] == expected_stop, replies
        return replies[-1]

    def close(self):
        if self.proc.poll() is None:
            self.proc.stdin.close()
            try:
                self.proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.proc.kill(); self.proc.wait()
        for thread in self.readers:
            thread.join(2)
        assert self.proc.returncode == 0, ''.join(self.errors)
        for stream in (self.proc.stdout, self.proc.stderr):
            stream.close()


def check(config, name, diagnostics=None):
    server = ThreadingHTTPServer(('127.0.0.1', 0), Provider)
    server.daemon_threads = True
    server.requests = []
    service = threading.Thread(target=server.serve_forever, daemon=True); service.start()
    client = None
    try:
        with tempfile.TemporaryDirectory(prefix='pi-cli-workflows-') as directory:
            root = Path(directory)
            agent, sessions = root / 'agent', root / 'sessions'
            agent.mkdir(); sessions.mkdir()
            model = {'name': 'workflow', 'reasoning': False, 'input': ['text'], 'contextWindow': 128000,
                     'maxTokens': 8192, 'cost': dict.fromkeys(('input', 'output', 'cacheRead', 'cacheWrite'), 0)}
            (agent / 'models.json').write_text(json.dumps({'providers': {'workflow': {
                'baseUrl': f'http://127.0.0.1:{server.server_port}/v1', 'api': 'openai-completions',
                'apiKey': 'local-dummy', 'models': [{**model, 'id': key} for key in ('mock', 'mock-two')]}}}))
            (agent / 'settings.json').write_text(json.dumps({'enableInstallTelemetry': False,
                'retry': {'enabled': False, 'baseDelayMs': 1, 'maxRetries': 1, 'provider': {'maxRetries': 0}},
                'compaction': {'enabled': False, 'keepRecentTokens': 16}, 'quietStartup': True}))
            extension = root / 'workflow.ts'; extension.write_text(EXTENSION)
            env = {k: os.environ[k] for k in ('PATH', 'HOME', 'TMPDIR', 'LANG', 'USER', 'LOGNAME') if k in os.environ}
            env.update(config.get('env', {}))
            env.update({'PI_CODING_AGENT_DIR': str(agent), 'PI_WORKFLOW_AUDIT': str(root / 'audit.jsonl'),
                        'PI_OFFLINE': '1', 'DO_NOT_TRACK': '1', 'NO_COLOR': '1', 'NODE_ENV': 'production'})
            command = config['command'] + ['--mode', 'rpc', '--provider', 'workflow', '--model', 'mock',
                '--thinking', 'off', '--session-dir', str(sessions), '--extension', str(extension),
                '--no-skills', '--no-prompt-templates', '--no-themes']
            if not config.get('keep_builtin_extensions', False):
                command += ['--no-extensions']
            client = Rpc(command, root, env)
            state = client.request('get_state')
            client.request('unknown_workflow_command', expected=False)
            client.request('set_model', expected=False, provider='workflow', modelId='missing')
            client.request('get_entries', expected=False, since='missing-entry')
            client.request('set_session_name', expected=False, name='  ')
            assert client.request('get_state')['sessionId'] == state['sessionId']
            commands = client.request('get_commands')['commands']
            assert any(c['name'] == 'workflow-dialog' and c['source'] == 'extension' for c in commands)
            requests_before = len(server.requests)
            client.request('prompt', message='/workflow-dialog')
            assert [d['method'] for d in client.dialogs] == ['select', 'confirm']
            assert len(server.requests) == requests_before, 'Extension command unexpectedly called model'
            entries = client.request('get_entries')['entries']
            assert any(e.get('customType') == 'workflow-dialog' and e['data'] == {'selected': 'beta', 'confirmed': True} for e in entries)
            error = client.prompt('PROVIDER_ERROR', 'error')
            assert 'EXPECTED_WORKFLOW_PROVIDER_ERROR' in error['errorMessage'], error
            client.prompt('RECOVER_AFTER_PROVIDER_ERROR')
            retry_offset, request_offset = len(client.events), len(server.requests)
            client.request('set_auto_retry', enabled=True)
            client.prompt('RETRY_ONCE')
            retry_events = client.events[retry_offset:]
            starts = [e for e in retry_events if e['type'] == 'auto_retry_start']
            ends = [e for e in retry_events if e['type'] == 'auto_retry_end']
            assert len(starts) == 1 and starts[0]['attempt'] == 1 and starts[0]['delayMs'] == 1, starts
            assert len(ends) == 1 and ends[0]['success'] is True and ends[0]['attempt'] == 1, ends
            assert len(server.requests) - request_offset == 2
            assert client.request('get_last_assistant_text')['text'] == 'WORKFLOW_OK:RETRY_ONCE'
            client.request('set_auto_retry', enabled=False)
            client.prompt('TOOL_ERROR')
            tool_errors = [e for e in client.events if e['type'] == 'tool_execution_end' and e.get('toolName') == 'read']
            assert len(tool_errors) == 1 and tool_errors[0]['isError'] is True
            assert 'definitely-missing-workflow.txt' in json.dumps(tool_errors[0])
            assert client.request('get_last_assistant_text')['text'] == 'WORKFLOW_TOOL_RECOVERED'
            client.request('set_model', provider='workflow', modelId='mock-two')
            client.prompt('MODEL_TWO')
            assert server.requests[-1]['model'] == 'mock-two'
            client.request('set_session_name', name='workflow-session')
            original = client.request('get_state')
            assert original['sessionName'] == 'workflow-session' and not original['isStreaming']
            original_messages = client.request('get_messages')['messages']
            client.request('prompt', message='/workflow-cancel-new')
            assert client.request('new_session')['cancelled'] is True
            assert client.request('get_messages')['messages'] == original_messages
            assert client.request('new_session')['cancelled'] is False
            fresh = client.request('get_state')
            assert fresh['sessionId'] != original['sessionId']
            assert client.request('get_messages')['messages'] == []
            client.prompt('NEW_SESSION')
            assert client.request('switch_session', sessionPath=original['sessionFile'])['cancelled'] is False
            restored_messages = client.request('get_messages')['messages']
            persisted_entries = list(map(json.loads, Path(original['sessionFile']).read_text().splitlines()))
            persisted_messages = [e['message'] for e in persisted_entries if e.get('type') == 'message']
            retry_entries = [e for e in persisted_entries if e.get('type') == 'message'
                             and 'EXPECTED_WORKFLOW_RETRY_ERROR' in e['message'].get('errorMessage', '')]
            assert len(retry_entries) == 1
            context_edits = [e for e in persisted_entries if e.get('type') == 'context_edit']
            if context_edits:
                # Pi 1.0 durably omits the failed retry from its finalized model
                # projection; raw history still retains the failed response.
                assert len(context_edits) == 1 and context_edits[0]['replacement'] is None
                assert context_edits[0]['targetId'] == retry_entries[0]['id']
                assert restored_messages == [m for m in persisted_messages if m != retry_entries[0]['message']]
                assert restored_messages == original_messages
                retry_resume_projection = 'durable_context_edit'
            else:
                # Pi 0.85 restores the raw failed retry when reopening a session.
                assert restored_messages == persisted_messages
                assert [m for m in restored_messages if m != retry_entries[0]['message']] == original_messages
                retry_resume_projection = 'raw_history'
            candidates = client.request('get_fork_messages')['messages']
            chosen = next(m for m in candidates if m['text'] == 'MODEL_TWO')
            fork = client.request('fork', entryId=chosen['entryId'])
            assert not fork['cancelled'] and fork['text'] == 'MODEL_TWO'
            fork_state = client.request('get_state')
            assert fork_state['sessionId'] != original['sessionId']
            fork_messages = client.request('get_messages')['messages']
            assert len(fork_messages) < len(original_messages)
            assert not any('MODEL_TWO' in json.dumps(m) for m in fork_messages)
            client.prompt('FORK_RECOVERY')
            tree = client.request('get_tree')
            assert tree['tree'] and tree['leafId']
            export = root / 'session.html'
            assert client.request('export_html', outputPath=str(export))['path'] == str(export)
            encoded = re.search(r'<script id="session-data" type="application/json">([^<]+)</script>', export.read_text())
            assert encoded, 'Export omitted embedded session data'
            exported = json.loads(base64.b64decode(encoded.group(1)))
            assert exported['leafId'] == tree['leafId']
            assert any(e.get('type') == 'message' and e['message'].get('role') == 'user'
                       and any(p.get('text') == 'FORK_RECOVERY' for p in e['message']['content']) for e in exported['entries'])
            compact_offset = len(client.events)
            compact = client.request('compact', customInstructions='Preserve WORKFLOW_COMPACTED for validation')
            assert 'WORKFLOW_COMPACTED' in compact['summary'] and compact['firstKeptEntryId'], compact
            compact_events = client.events[compact_offset:]
            assert any(e['type'] == 'compaction_start' and e['reason'] == 'manual' for e in compact_events)
            assert any(e['type'] == 'compaction_end' and not e['aborted'] and not e['willRetry'] for e in compact_events)
            after_compact = client.request('get_messages')['messages']
            assert any(m.get('role') == 'compactionSummary' and 'WORKFLOW_COMPACTED' in m['summary'] for m in after_compact)
            assert not client.request('get_state')['isCompacting']
            client.prompt('COMPACTION_RECOVERY')
            assert 'WORKFLOW_COMPACTED' in json.dumps(server.requests[-1]['messages'])
            client.close()
            stderr = ''.join(client.errors)
            diagnostic_facts = {}
            if diagnostics:
                log = diagnostics / (re.sub(r'[^A-Za-z0-9_.-]', '_', name) + '.stderr.log')
                log.write_text(stderr)
                promotions = [int(value) for value in re.findall(r'AOT: tiered function (\d+) to ordinary bytecode profiling', stderr)]
                selections = re.findall(r'AOT: .*\(module \d+ start \d+ kind \d+\) is at ', stderr)
                diagnostic_facts = {'stderr_path': str(log), 'stderr_sha256': hashlib.sha256(stderr.encode()).hexdigest(),
                    'native_tier_promotion_events': len(promotions), 'native_tier_promoted_function_indices': sorted(set(promotions)),
                    'native_code_selection_log_events': len(selections),
                    'evidence_scope': 'Promotion log events identify actual transitions to bytecode profiling; selection logs identify selected code, not entry counts. Correctness only, no timing.'}
            client = None
            audit = [json.loads(line) for line in (root / 'audit.jsonl').read_text().splitlines()]
            assert sum(e['event'] == 'start' for e in audit) >= 4, audit
            if config.get('keep_builtin_extensions', False):
                starts = [e for e in audit if e['event'] == 'start']
                assert all('codemode' in e['availableTools'] and 'codemode' not in e['activeTools'] for e in starts), starts
                assert all(not any(tool['function']['name'] == 'codemode' for tool in request.get('tools', []))
                           for request in server.requests)
            assert any(e['event'] == 'before_fork' and e['id'] == chosen['entryId'] for e in audit)
            assert any(e['event'] == 'compact' and e['reason'] == 'manual' and 'WORKFLOW_COMPACTED' in e['summary'] for e in audit)
            persisted = [json.loads(line) for line in Path(original['sessionFile']).read_text().splitlines()]
            assert any(e.get('customType') == 'workflow-dialog' for e in persisted)
            return {'name': name, 'passed': True, 'gates': ['invalid_command_recovery', 'extension_command_dialogs',
                'provider_error_recovery', 'auto_retry_429_then_success', 'tool_error_recovery', 'model_switch', 'session_name',
                'cancelled_new_session', 'new_session', 'switch_session_history', 'fork_and_recovery',
                'session_tree', 'html_export', 'manual_compaction_and_recovery', 'extension_lifecycle_and_persistence'],
                'provider_requests': len(server.requests), 'extension_start_events': sum(e['event'] == 'start' for e in audit),
                'retry_resume_projection': retry_resume_projection,
                'keep_builtin_extensions': config.get('keep_builtin_extensions', False),
                'observed_startup_tools': [{'available': e['availableTools'], 'active': e['activeTools']}
                                           for e in audit if e['event'] == 'start'],
                'network_scope': 'PI_OFFLINE=1 disables automatic startup networking; isolated provider baseUrl is loopback. Model request counts are checked; this is not OS packet tracing.',
                'requests_sha256': hashlib.sha256(json.dumps(server.requests, sort_keys=True).encode()).hexdigest(),
                'command': command, 'env': config.get('env', {}), **diagnostic_facts}
    finally:
        if client:
            if client.proc.poll() is None:
                client.proc.kill(); client.proc.wait()
            for thread in client.readers:
                thread.join(2)
        server.shutdown(); server.server_close(); service.join(2)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--variants-file', required=True)
    parser.add_argument('--variants', nargs='+', required=True)
    parser.add_argument('--output', required=True)
    parser.add_argument('--diagnostics-dir', help='Retain drained stderr and count explicit AOT promotion diagnostics; does not enable verbose flags')
    args = parser.parse_args()
    configurations = json.loads(Path(args.variants_file).read_text())
    results = []
    output = Path(args.output).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    diagnostics = Path(args.diagnostics_dir).resolve() if args.diagnostics_dir else None
    if diagnostics:
        diagnostics.mkdir(parents=True, exist_ok=True)
    report = {'scope': 'offline RPC CLI workflows, no timing or terminal-menu coverage', 'complete': False,
        'harness_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        'extension_sha256': hashlib.sha256(EXTENSION.encode()).hexdigest(), 'checks': results}
    output.write_text(json.dumps(report, indent=2) + '\n')
    try:
        for name in args.variants:
            result = check(configurations[name], name, diagnostics)
            results.append(result)
            output.write_text(json.dumps(report, indent=2) + '\n')
            print(f'{name}: {len(result["gates"])} workflow gates passed', flush=True)
    except Exception as error:
        report['failure'] = {'variant': name, 'error': str(error), 'type': type(error).__name__}
        output.write_text(json.dumps(report, indent=2) + '\n')
        raise
    report['complete'] = True
    output.write_text(json.dumps(report, indent=2) + '\n')


if __name__ == '__main__':
    main()
