#!/usr/bin/env python3
"""Real Pi PTY conversations, paced streaming, active aborts and session resume.

All traffic uses an isolated loopback model. A small external TS extension sends
completion/tool lifecycle observations on a private inherited pipe, never stdout.
Terminal rendering, actual tool results, persisted messages and resumed history
are independently checked. Functional gates are excluded from timed cohorts.
"""
import argparse
import datetime
import fcntl
import hashlib
import json
import os
from pathlib import Path
import pty
import random
import re
import select
import signal
import socket
import struct
import subprocess
import tempfile
import termios
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import benchmark as b
from terminal_screen import Screen

SYNC_END = b'\x1b[?2026l'
FIXTURE = 'LOCAL_INTERACTIVE_FIXTURE\n' + 'deterministic tool contents\n' * 12
EXTENSION = '''import {writeSync} from "node:fs";
export default function(pi) {
  const emit = value => writeSync(Number(process.env.PI_TEST_EVENT_FD), JSON.stringify(value) + "\\n");
  pi.on("session_start", (_, ctx) => emit({type:"ready", session:ctx.sessionManager.getSessionFile(),
    availableTools:pi.getAllTools().map(tool=>tool.name), activeTools:pi.getActiveTools()}));
  pi.on("agent_end", e => emit({type:"end", reasons:e.messages.filter(m=>m.role==="assistant").map(m=>m.stopReason)}));
  pi.on("tool_execution_start", e => emit({type:"tool_start", name:e.toolName}));
  pi.on("tool_execution_end", e => emit({type:"tool_end", name:e.toolName, error:e.isError}));
  process.stdout.on("resize", () => emit({type:"resize", columns:process.stdout.columns, rows:process.stdout.rows}));
}
'''


def chunks(label):
    # Markdown, code, Unicode and width calculations are actual terminal work.
    values = [f'VISIBLE_{label}_FIRST\n\n', '## Local streamed reply\n\n']
    values += [f'- Item {i:02d}: **bold** `code` café Ελληνικά 日本語 👩🏽‍💻\n' for i in range(12)]
    values += [f'\nVISIBLE_{label}_MIDDLE\n\n```typescript\n', 'const answer: number = 42;\n', 'console.log(answer);\n', '```\n\n']
    values += [f'Paragraph {i:02d}: ' + 'wrapped text and numbers 0123456789 ' * 5 + '\n\n' for i in range(13)]
    values += [f'VISIBLE_{label}_FINAL\n']
    assert len(values) == 32
    return values


class Handler(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'

    def setup(self):
        super().setup()
        self.connection.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)

    def log_message(self, *_):
        pass

    def do_POST(self):
        try:
            body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            assert self.path == '/v1/chat/completions' and body['model'] == 'mock'
            self.server.requests.append(body)
            self.server.request_times.append(time.perf_counter())
            user = next(m for m in reversed(body['messages']) if m['role'] == 'user')
            label = re.search(r'PROBE_([A-Z0-9_]+)', str(user['content']))[1]
            self.send_response(200)
            self.send_header('Content-Type', 'text/event-stream')
            self.send_header('Cache-Control', 'no-cache')
            self.send_header('Connection', 'close')
            self.end_headers()

            def emit(delta, finish=None):
                chunk = {'id': f'probe-{label}', 'object': 'chat.completion.chunk', 'created': 1,
                         'model': 'mock', 'choices': [{'index': 0, 'delta': delta, 'finish_reason': finish}]}
                self.wfile.write(('data: ' + json.dumps(chunk) + '\n\n').encode())
                self.wfile.flush()

            emit({'role': 'assistant', 'content': ''})
            if label == 'CANCEL_STREAM':
                emit({'content': 'STREAM_ABORT_VISIBLE\n'})
                self.server.stream_open.set()
                # A held response cannot complete before Esc is processed.
                self.server.release.wait(15)
                return
            if body['messages'][-1]['role'] != 'tool':
                name = 'bash' if label == 'CANCEL_TOOL' else 'read'
                arguments = {'command': "printf '%s%s' TOOL_ABORT_ RUNNING; echo $$ > tool-root.pid; sleep 30 & echo $! > tool-child.pid; wait"} if name == 'bash' else {'path': 'fixture.txt'}
                emit({'tool_calls': [{'index': 0, 'id': f'call-{label}', 'type': 'function',
                                     'function': {'name': name, 'arguments': json.dumps(arguments)}}]})
                emit({}, 'tool_calls')
            else:
                assert label != 'CANCEL_TOOL', 'Cancelled tool unexpectedly triggered another model request'
                content = body['messages'][-1]['content']
                assert content == FIXTURE, f'Wrong actual read result: {content!r}'
                self.server.read_results.append(label)
                for i, value in enumerate(chunks(label)):
                    emit({'content': value})
                    if label == 'STREAM_INPUT' and i == 0:
                        assert self.server.first_gate.wait(15), 'First visible stream gate timed out'
                    if label == 'STREAM_INPUT' and i == 14:
                        assert self.server.middle_gate.wait(15), 'Middle visible stream gate timed out'
                    if self.server.pace_ms:
                        self.server.release.wait(self.server.pace_ms / 1000)
                emit({}, 'stop')
            self.wfile.write(b'data: [DONE]\n\n')
            self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            pass  # Expected when the CLI cancels an open SSE response.
        except Exception as error:
            self.server.errors.append(repr(error))
        finally:
            self.close_connection = True


class Terminal:
    def __init__(self, command, env, work, extension, sessions, session=None, observe=True, expected_tui_mode=None,
                 keep_builtin_extensions=False, use_default_tools=False):
        self.master, slave = pty.openpty()
        fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack('HHHH', 30, 100, 0, 0))
        self.events_fd, event_writer = os.pipe()
        args = ['--provider', 'benchmark', '--model', 'mock', '--thinking', 'off',
                '--session-dir', str(sessions),
                '--no-skills', '--no-prompt-templates', '--no-themes']
        if not use_default_tools:
            args += ['--tools', 'read,bash']
        if not keep_builtin_extensions:
            args += ['--no-extensions']
        if observe:
            args += ['--extension', str(extension)]
        if session:
            args += ['--session', str(session)]
        self.started = time.perf_counter()
        self.work = work
        self.observe, self.sessions = observe, sessions
        try:
            self.proc = subprocess.Popen(command + args, cwd=work,
                env={**env, 'PI_TEST_EVENT_FD': str(event_writer)}, stdin=slave, stdout=slave, stderr=slave,
                pass_fds=(event_writer,), start_new_session=True)
        except Exception:
            for fd in (slave, self.master, self.events_fd, event_writer):
                os.close(fd)
            raise
        os.close(slave)
        os.close(event_writer)
        self.output, self.event_buffer = b'', b''
        self.screen = Screen()
        self.terminal_replies = {}
        self.events = []
        self.frames = 0
        try:
            self.until(lambda: self.frames > 0 and (any(e['type'] == 'ready' for e in self.events)
                       if observe else b'mock' in self.output and b'\x1b]0;' in self.output))
            self.tui_mode = 'fullscreen' if self.screen.alternate else 'regular'
            if expected_tui_mode is not None:
                assert expected_tui_mode in ('fullscreen', 'regular'), expected_tui_mode
                assert self.tui_mode == expected_tui_mode, (self.tui_mode, expected_tui_mode)
            if observe and keep_builtin_extensions and use_default_tools:
                startup = next(e for e in self.events if e['type'] == 'ready')
                assert 'codemode' in startup['availableTools'] and 'codemode' not in startup['activeTools'], startup
        except Exception:
            self.proc.kill(); self.proc.wait()
            os.close(self.master); os.close(self.events_fd)
            raise
        self.ready = time.perf_counter()
        self.session = Path(next(e['session'] for e in self.events if e['type'] == 'ready')) if observe else None

    def pump(self, timeout=.02):
        readable = select.select([self.master, *([self.events_fd] if self.observe else [])], [], [], timeout)[0]
        for fd in readable:
            try:
                value = os.read(fd, 65536)
            except OSError as error:
                raise RuntimeError(f'PTY closed: {self.output[-6000:]!r}') from error
            if not value:
                raise RuntimeError(f'CLI exited: {self.output[-6000:]!r}')
            if fd == self.master:
                self.output += value
                self.screen.feed(value)
                self.frames = self.screen.frames
                # A PTY alone is not a terminal emulator: answer the startup
                # queries that an actual dark terminal answers, avoiding timeouts.
                for query, reply in ((b'\x1b[c', b'\x1b[?1;2c'),
                                     (b'\x1b]11;?\x07', b'\x1b]11;rgb:0000/0000/0000\x07'),
                                     (b'\x1b[?996n', b'\x1b[?997;1n')):
                    count = self.output.count(query)
                    previous = self.terminal_replies.get(query, 0)
                    for _ in range(count - previous):
                        os.write(self.master, reply)
                    self.terminal_replies[query] = count
            else:
                self.event_buffer += value
                while b'\n' in self.event_buffer:
                    line, self.event_buffer = self.event_buffer.split(b'\n', 1)
                    self.events.append(json.loads(line))

    def until(self, predicate, timeout=20):
        deadline = time.monotonic() + timeout
        while not predicate():
            if time.monotonic() > deadline:
                raise TimeoutError(f'Interactive condition timed out; events={self.events[-8:]}; tty={self.output[-6000:]!r}')
            self.pump(min(.02, max(0, deadline - time.monotonic())))

    def visible(self, marker, offset=0):
        # Ignore ANSI codes between cells. Require a complete synchronized frame
        # after the marker, rather than matching model traffic on the event pipe.
        data = self.output[offset:]
        text = re.sub(rb'\x1b\[[0-?]*[ -/]*[@-~]', b'', data)
        if marker.encode() in data:
            return SYNC_END in data[data.rfind(marker.encode()):]
        return marker.encode() in text and data.endswith(SYNC_END)

    def submit(self, label):
        os.write(self.master, f'PROBE_{label} read fixture.txt and summarize.\r'.encode())

    def completed(self, label):
        if self.session is None:
            files = list(self.sessions.glob('*.jsonl'))
            assert len(files) <= 1, files
            if not files:
                return False
            self.session = files[0]
        if not self.session.exists():
            return False
        messages = [e.get('message', {}) for e in self.entries() if e.get('type') == 'message']
        return any(m.get('role') == 'assistant' and m.get('stopReason') == 'stop'
                   and ''.join(c.get('text', '') for c in m.get('content', [])) == ''.join(chunks(label)) for m in messages)

    def entries(self):
        return [json.loads(line) for line in self.session.read_text().splitlines()]

    def idle_completion(self, label, after_frame):
        # A final text marker can appear while Working is still displayed. Require
        # the actual synchronized screen's source-backed idle editor, independently
        # of the observer's agent_end event (which precedes the UI listener).
        return (self.frames > after_frame and self.screen.idle_editor(self.work)
                and f'VISIBLE_{label}_FINAL' in '\n'.join(self.screen.completed)
                and self.completed(label))

    def turn(self, label, server):
        offset, ends, request_index = len(self.output), sum(e['type'] == 'end' for e in self.events), len(server.requests)
        before_frame = self.frames
        started = time.perf_counter()
        self.submit(label)
        self.until(lambda: len(server.requests) > request_index)
        first_request = server.request_times[request_index]
        # Fullscreen follows the bottom of the transcript. A burst can coalesce
        # into one frame that already excludes FIRST from the viewport; measure
        # the first reply marker actually emitted in a synchronized frame.
        visible_markers = [f'VISIBLE_{label}_{part}' for part in ('FIRST', 'MIDDLE', 'FINAL')]
        self.until(lambda: any(self.visible(marker, offset) for marker in visible_markers))
        first_visible_marker = next(marker for marker in visible_markers if self.visible(marker, offset))
        first_visible = time.perf_counter()
        self.until(lambda: (not self.observe or sum(e['type'] == 'end' for e in self.events) > ends)
                   and self.idle_completion(label, before_frame))
        return {'prompt_to_request_ms': (first_request - started) * 1000,
                'first_visible_ms': (first_visible - started) * 1000,
                'first_visible_marker': first_visible_marker,
                'complete_ms': (time.perf_counter() - started) * 1000,
                'idle_editor_frame': self.frames, 'working_indicator_cleared': True}

    def resize(self, rows, columns):
        self.screen.resize(rows, columns)
        fcntl.ioctl(self.master, termios.TIOCSWINSZ, struct.pack('HHHH', rows, columns, 0, 0))
        os.kill(self.proc.pid, signal.SIGWINCH)

    def close(self):
        try:
            if self.proc.poll() is not None:
                return
            os.write(self.master, b'\x03/quit\r')
            deadline = time.monotonic() + 5
            while self.proc.poll() is None and time.monotonic() < deadline:
                # Drain terminal shutdown output; do not block the child's writes.
                if select.select([self.master], [], [], .02)[0]:
                    try:
                        os.read(self.master, 65536)
                    except OSError:
                        break
            self.proc.wait(timeout=5)
            assert self.proc.returncode == 0, self.proc.returncode
        finally:
            if self.proc.poll() is None:
                self.proc.kill()
                self.proc.wait()
            # The real bash tool uses a detached group. Clean failures as well.
            pid_file = self.work / 'tool-root.pid'
            if pid_file.exists():
                try:
                    os.killpg(int(pid_file.read_text()), signal.SIGKILL)
                except ProcessLookupError:
                    pass
            os.close(self.master)
            os.close(self.events_fd)


def one_run(name, config, turns, pace_ms, functional=False, observe=False, tool_cache=None):
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    server.daemon_threads = True
    server.requests, server.request_times, server.read_results, server.errors = [], [], [], []
    server.pace_ms = pace_ms
    server.release, server.stream_open = threading.Event(), threading.Event()
    server.first_gate, server.middle_gate = threading.Event(), threading.Event()
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        with tempfile.TemporaryDirectory(prefix='pi-interactive-') as directory:
            root = Path(directory)
            agent, sessions = root / 'agent', root / 'sessions'
            agent.mkdir(); sessions.mkdir()
            if tool_cache:
                (agent / 'bin').mkdir()
                for tool_name in ('fd', 'rg'):
                    source = Path(tool_cache) / tool_name
                    if source.is_file() and os.access(source, os.X_OK):
                        (agent / 'bin' / tool_name).symlink_to(source.resolve())
            (root / 'fixture.txt').write_text(FIXTURE)
            (agent / 'models.json').write_text(json.dumps({'providers': {'benchmark': {
                'baseUrl': f'http://127.0.0.1:{server.server_port}/v1', 'api': 'openai-completions', 'apiKey': 'local-dummy',
                'models': [{'id': 'mock', 'name': 'mock', 'reasoning': False, 'input': ['text'],
                            'contextWindow': 128000, 'maxTokens': 8192,
                            'cost': {'input': 0, 'output': 0, 'cacheRead': 0, 'cacheWrite': 0}}]}}}))
            (agent / 'settings.json').write_text(json.dumps({'enableInstallTelemetry': False,
                'compaction': {'enabled': False}, 'retry': {'enabled': False}, 'quietStartup': True}))
            extension = root / 'observe.ts'
            extension.write_text(EXTENSION)
            env = {k: os.environ[k] for k in ('PATH', 'HOME', 'TMPDIR', 'LANG', 'LC_ALL', 'USER', 'LOGNAME') if k in os.environ}
            env.update(config['env'])
            env.update({'PI_CODING_AGENT_DIR': str(agent), 'PI_OFFLINE': '1', 'DO_NOT_TRACK': '1',
                        'NODE_ENV': 'production', 'NO_COLOR': '1', 'TERM': 'xterm-256color', 'COLUMNS': '100', 'LINES': '30'})
            terminal = Terminal(config['command'], env, root, extension, sessions, observe=observe or functional,
                                expected_tui_mode=config.get('expected_tui_mode'),
                                keep_builtin_extensions=config.get('keep_builtin_extensions', False),
                                use_default_tools=config.get('use_default_tools', False))
            try:
                at_ready = b.memory(terminal.proc.pid)
                turn_times = [terminal.turn(f'TURN_{i}', server) for i in range(turns)]
                completed = time.perf_counter()
                at_end = b.memory(terminal.proc.pid)
                tool_ends = [e for e in terminal.events if e['type'] == 'tool_end']
                if terminal.observe:
                    assert len(tool_ends) == turns and all(e['name'] == 'read' and not e['error'] for e in tool_ends), tool_ends
                persisted_tools = [e['message'] for e in terminal.entries() if e.get('type') == 'message'
                                   and e.get('message', {}).get('role') == 'toolResult']
                assert len(persisted_tools) == turns and all(m['toolName'] == 'read' and not m['isError'] for m in persisted_tools)
                assert len(server.requests) == turns * 2 and len(server.read_results) == turns
                result = {'variant': name, 'turns': turns, 'pace_ms': pace_ms,
                    'tui_mode': terminal.tui_mode, 'alternate_screen_entries': terminal.screen.alternate_entries,
                    'keep_builtin_extensions': config.get('keep_builtin_extensions', False),
                    'use_default_tools': config.get('use_default_tools', False),
                    'observed_startup_tools': next(({'available': e['availableTools'], 'active': e['activeTools']}
                        for e in terminal.events if e['type'] == 'ready'), None),
                    'ready_ms': (terminal.ready - terminal.started) * 1000,
                    'startup_plus_turns_ms': (completed - terminal.started) * 1000,
                    'turns_ms': (completed - terminal.ready) * 1000,
                    'cpu_through_turns_ms': at_end['cpu_ms'],
                    'ready_footprint_mb': at_ready['footprint_mb'],
                    'after_turns_footprint_mb': at_end['footprint_mb'],
                    'peak_footprint_mb': at_end['peak_footprint_mb'],
                    'turn_times': turn_times, 'frames': terminal.frames, 'requests': len(server.requests),
                    'read_tools': len(persisted_tools), 'session_persisted': True,
                    'replies_sha256': hashlib.sha256(''.join(''.join(chunks(f'TURN_{i}')) for i in range(turns)).encode()).hexdigest()}
                if functional:
                    functional_check(terminal, server)
                    result['functional'] = {'streamed_partial_render': True, 'input_during_stream': True,
                        'resize_during_stream': True, 'active_stream_abort_and_recovery': True,
                        'active_bash_abort_and_recovery': True}
            except Exception:
                failure = {'variant': name, 'terminal': terminal.output.decode(errors='replace'),
                           'events': terminal.events, 'requests': server.requests, 'server_errors': server.errors,
                           'session': terminal.session.read_text() if terminal.session and terminal.session.exists() else None}
                (b.ROOT / f'interactive-failure-{name}.json').write_text(json.dumps(failure, indent=2) + '\n')
                raise
            finally:
                terminal.close()
            if functional:
                prior = [e for e in terminal.entries() if e.get('type') == 'message']
                old_file = terminal.session
                count = len(server.requests)
                resumed = Terminal(config['command'], env, root, extension, sessions, old_file,
                                   expected_tui_mode=config.get('expected_tui_mode'),
                                   keep_builtin_extensions=config.get('keep_builtin_extensions', False),
                                   use_default_tools=config.get('use_default_tools', False))
                try:
                    assert resumed.session == old_file
                    resumed.turn('RESUMED', server)
                    restored_request = server.requests[count]
                    rendered_history = json.dumps(restored_request['messages'], ensure_ascii=False)
                    labels = [f'TURN_{i}' for i in range(turns)] + ['STREAM_INPUT', 'RECOVER_CANCEL_STREAM', 'RECOVER_CANCEL_TOOL']
                    for label in labels:
                        assert f'PROBE_{label}' in rendered_history
                        assert f'VISIBLE_{label}_FINAL' in rendered_history
                    assert FIXTURE in ''.join(str(m['content']) for m in restored_request['messages'] if m['role'] == 'tool')
                    after = [e for e in resumed.entries() if e.get('type') == 'message']
                    assert after[:len(prior)] == prior and len(after) > len(prior)
                    result['functional']['interactive_session_resume_with_history'] = True
                finally:
                    resumed.close()
            assert not server.errors, server.errors
            result['passed'] = True
            return result
    finally:
        server.release.set()
        server.first_gate.set(); server.middle_gate.set()
        server.shutdown(); server.server_close()


def functional_check(terminal, server):
    offset, ends = len(terminal.output), sum(e['type'] == 'end' for e in terminal.events)
    before_stream_frame = terminal.frames
    terminal.submit('STREAM_INPUT')
    terminal.until(lambda: terminal.visible('VISIBLE_STREAM_INPUT_FIRST', offset))
    assert sum(e['type'] == 'end' for e in terminal.events) == ends
    # Keep model response open while typing and resizing: test input, not echo.
    os.write(terminal.master, b'INPUT_WHILE_STREAMING')
    terminal.until(lambda: terminal.visible('INPUT_WHILE_STREAMING', offset))
    before = terminal.frames
    terminal.resize(24, 80)
    terminal.until(lambda: terminal.frames > before and any(
        e['type'] == 'resize' and e['columns'] == 80 and e['rows'] == 24 for e in terminal.events))
    os.write(terminal.master, b'\x03')  # Clear draft; Esc is the abort key.
    server.first_gate.set()
    terminal.until(lambda: terminal.visible('VISIBLE_STREAM_INPUT_MIDDLE', offset))
    assert sum(e['type'] == 'end' for e in terminal.events) == ends
    server.middle_gate.set()
    terminal.until(lambda: sum(e['type'] == 'end' for e in terminal.events) > ends
                   and terminal.idle_completion('STREAM_INPUT', before_stream_frame))
    server.release.clear()
    for label, expected in [('CANCEL_STREAM', 'aborted'), ('CANCEL_TOOL', None)]:
        offset, ends = len(terminal.output), sum(e['type'] == 'end' for e in terminal.events)
        before_cancel_frame = terminal.frames
        tool_count = sum(e['type'] == 'tool_start' for e in terminal.events)
        terminal.submit(label)
        if label == 'CANCEL_STREAM':
            terminal.until(lambda: server.stream_open.is_set() and terminal.visible('STREAM_ABORT_VISIBLE', offset))
        else:
            terminal.until(lambda: sum(e['type'] == 'tool_start' for e in terminal.events) > tool_count
                           and terminal.visible('TOOL_ABORT_RUNNING', offset)
                           and (terminal.work / 'tool-child.pid').exists())
        assert sum(e['type'] == 'end' for e in terminal.events) == ends
        os.write(terminal.master, b'\x1b')
        terminal.until(lambda: sum(e['type'] == 'end' for e in terminal.events) > ends
                       and terminal.frames > before_cancel_frame and terminal.screen.idle_editor(terminal.work))
        if expected:
            assert expected in [e for e in terminal.events if e['type'] == 'end'][-1]['reasons']
            terminal.until(lambda: terminal.visible('Operation aborted', offset))
            assert any(e.get('message', {}).get('stopReason') == 'aborted' for e in terminal.entries())
            server.release.set()
            server.release.clear()
        else:
            terminal.until(lambda: any(e['type'] == 'tool_end' and e['name'] == 'bash' and e['error'] for e in terminal.events))
            terminal.until(lambda: any(m.get('role') == 'toolResult' and m.get('toolName') == 'bash'
                and m.get('isError') and 'Command aborted' in json.dumps(m)
                for m in (e.get('message', {}) for e in terminal.entries())))
            pids = [int((terminal.work / p).read_text()) for p in ('tool-root.pid', 'tool-child.pid')]
            def processes_gone():
                for pid in pids:
                    try:
                        os.kill(pid, 0)
                    except ProcessLookupError:
                        continue
                    return False
                return True
            terminal.until(processes_gone)
            for name in ('tool-root.pid', 'tool-child.pid'):
                (terminal.work / name).unlink()
        terminal.turn('RECOVER_' + label, server)
    assert not any('INPUT_WHILE_STREAMING' in str(r['messages'][-1]) for r in server.requests)


def provenance(config):
    paths = [Path(config['command'][0])]
    if config['env'].get('BUN_JSC_aotImagePath'):
        paths.append(Path(config['env']['BUN_JSC_aotImagePath']))
    manifest = paths[0].parent / 'manifest.json'
    if manifest.exists():
        paths.append(manifest)
    return {str(p): {'sha256': hashlib.sha256(p.read_bytes()).hexdigest(), 'bytes': p.stat().st_size} for p in paths}



def manifest_snapshots(configs, artifacts):
    snapshots = {}
    for name, config in configs.items():
        manifest = Path(config['command'][0]).resolve().parent / 'manifest.json'
        if not manifest.is_file():
            continue
        raw = manifest.read_bytes()
        expected = artifacts[name].get(str(manifest), {})
        if hashlib.sha256(raw).hexdigest() != expected.get('sha256'):
            raise RuntimeError(f'{manifest}: manifest changed while taking benchmark provenance')
        snapshots[name] = json.loads(raw)
    return snapshots


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--variants-file', required=True)
    parser.add_argument('--variants', nargs='+', required=True)
    parser.add_argument('--runs', type=int, default=6)
    parser.add_argument('--warmups', type=int, default=2)
    parser.add_argument('--turns', type=int, default=5)
    parser.add_argument('--pace-ms', type=float, default=2)
    parser.add_argument('--functional', action='store_true')
    parser.add_argument('--observe', action='store_true', help='Include external TS lifecycle observer in timed workloads')
    parser.add_argument('--tool-cache', default=str(Path.home() / '.pi/agent/bin'), help='Reuse only fd/rg binaries in isolated agent/bin; never reuse user settings or credentials')
    parser.add_argument('--cold-tools', action='store_true', help='Leave the isolated managed-tool directory empty')
    parser.add_argument('--output', default='interactive-results.json')
    args = parser.parse_args()
    if args.runs < 1 or args.warmups < 0 or args.turns < 1 or args.pace_ms < 0:
        parser.error('Invalid runs/warmups/turns/pace')
    configs = json.loads(Path(args.variants_file).read_text())
    selected = {name: configs[name] for name in args.variants}
    rng = random.Random(20261002)
    samples, warmups = [], []
    artifacts = {name: provenance(config) for name, config in selected.items()}
    report = {'date': datetime.datetime.now(datetime.timezone.utc).isoformat(), **b.hardware_metadata(),
        'pi_version': json.loads((b.PI / 'package.json').read_text())['version'],
        'harness_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        'terminal_screen_sha256': hashlib.sha256(Path(__file__).with_name('terminal_screen.py').read_bytes()).hexdigest(),
        'observation_extension_sha256': hashlib.sha256(EXTENSION.encode()).hexdigest(),
        'configs': selected, 'artifacts': artifacts,
        'artifact_manifests': manifest_snapshots(selected, artifacts),
        'workload': '100x30 PTY; submitted prompts; real read tool; 32 flushed Markdown/code/Unicode SSE deltas per turn; persisted session',
        'external_observation_extension': args.observe or args.functional,
        'extension_policy': 'keep_builtin_extensions:true omits --no-extensions, retaining built-in registration and discovery in fresh isolated cwd/agent settings. False/default supplies --no-extensions; explicit observer still loads. PI_OFFLINE=1 disables automatic startup networking; all model requests use loopback mock.',
        'tool_policy': 'use_default_tools:true omits --tools and uses Pi defaults; with built-in extensions retained, codemode is registered but inactive. False/default supplies --tools read,bash, a Pi1.0 allowlist that also excludes codemode from configured tools.',
        'mock_tcp_nodelay': True,
        'terminal_protocol': 'Dark terminal: primary DA response, OSC11 RGB background, dark color-scheme response; legacy input keys',
        'managed_tool_cache': None if args.cold_tools else args.tool_cache,
        'managed_tools': {} if args.cold_tools else {str(path): {'sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
            'bytes': path.stat().st_size} for path in (Path(args.tool_cache) / n for n in ('fd', 'rg')) if path.is_file()},
        'pace_ms_per_delta': args.pace_ms, 'functional_gates_excluded_from_measurements': True,
        'completion': 'exact persisted stop reply plus final reply marker on a completed synchronized visible screen with the Working indicator cleared and source-backed idle editor borders/input/footer; also agent_end when observation is enabled; does not assert later agent_settled processing is finished',
        'endpoint_version': 'visible-idle-editor-v3-alternate-screen',
        'first_visible_endpoint': 'First FIRST/MIDDLE/FINAL marker for this reply emitted in a completed synchronized frame; fullscreen burst rendering may coalesce directly to MIDDLE or FINAL. Held functional partial gates separately require FIRST and MIDDLE.',
        'ready_note': 'Extension session_start plus complete frame for observed checks; initial terminal title and complete model-footer frame, in either order, for unobserved runs. Pi sets its title during session rebinding after enabling submission. First submitted prompt reaching API verifies actual submission, with its latency included in wall time.',
        'memory_note': b.hardware_metadata()['memory_metric'] + '; process peak includes startup; CPU includes all threads in Pi process through final completion, excluding mock, harness and subprocess CPU',
        'limits': 'Loopback mock excludes real model/network latency. Fresh processes, warm filesystem caches. Optional observation extension and VT screen controller are common to all variants; controller CPU is outside Pi process CPU and its frame processing can affect visible wall time. Paced wall time includes artificial streaming intervals. Historical final-text endpoint cohorts must not be pooled with visible-idle-editor-v2.',
        'samples': samples, 'warmups': warmups}
    for round in range(-args.warmups, args.runs):
        order = list(selected); rng.shuffle(order)
        for name in order:
            try:
                sample = one_run(name, selected[name], args.turns, args.pace_ms, args.functional, args.observe,
                                 None if args.cold_tools else args.tool_cache)
            except Exception as error:
                if not args.functional:
                    raise
                sample = {'variant': name, 'passed': False, 'error': repr(error)}
            sample['round'] = round
            (warmups if round < 0 else samples).append(sample)
            if sample['passed']:
                print(f'{name:24s} round={round:2d} wall={sample["startup_plus_turns_ms"]:.1f}ms '
                      f'cpu={sample["cpu_through_turns_ms"]:.1f}ms footprint={sample["after_turns_footprint_mb"]:.1f}MB', flush=True)
            else:
                print(f'{name}: FAILED: {sample["error"][:300]}', flush=True)
        report['summary'] = b.summary([s for s in samples if s['passed']])
        (b.ROOT / args.output).write_text(json.dumps(report, indent=2) + '\n')
    if any(not s['passed'] for s in samples):
        raise SystemExit(1)


if __name__ == '__main__':
    main()
