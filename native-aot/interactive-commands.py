#!/usr/bin/env python3
"""Real PTY checks for Pi settings, extension dialogs/reload and slash commands; no timing report."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
from http.server import ThreadingHTTPServer

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'benchmarks'))
import interactive_workload as iw


def extension(version):
    source = iw.EXTENSION.rsplit('}', 1)[0]
    return source + '''
  pi.registerCommand("command-dialog", {handler: async (_, ctx) => {
    const selected = await ctx.ui.select("COMMAND_CHOICE", ["ALPHA", "BETA"]);
    const confirmed = selected ? await ctx.ui.confirm("COMMAND_CONFIRM", "Accept choice?") : false;
    emit({type:"command_dialog", selected:selected ?? null, confirmed, version:VERSION});
    ctx.ui.notify("COMMAND_FINISHED", "info");
  }});
}\n'''.replace('VERSION', json.dumps(version))


def check(name, config):
    server = ThreadingHTTPServer(('127.0.0.1', 0), iw.Handler)
    server.daemon_threads = True
    server.requests, server.request_times, server.read_results, server.errors = [], [], [], []
    server.pace_ms = 0
    server.release, server.stream_open = threading.Event(), threading.Event()
    server.first_gate, server.middle_gate = threading.Event(), threading.Event()
    service = threading.Thread(target=server.serve_forever, daemon=True); service.start()
    terminal = None
    try:
        with tempfile.TemporaryDirectory(prefix='pi-terminal-commands-') as directory:
            work = Path(directory); agent, sessions = work / 'agent', work / 'sessions'
            agent.mkdir(); sessions.mkdir()
            (work / 'fixture.txt').write_text(iw.FIXTURE)
            model = {'id': 'mock', 'name': 'mock', 'reasoning': False, 'input': ['text'],
                'contextWindow': 128000, 'maxTokens': 8192,
                'cost': dict.fromkeys(('input', 'output', 'cacheRead', 'cacheWrite'), 0)}
            (agent / 'models.json').write_text(json.dumps({'providers': {'benchmark': {
                'baseUrl': f'http://127.0.0.1:{server.server_port}/v1', 'api': 'openai-completions',
                'apiKey': 'local-dummy', 'models': [model]}}}))
            settings = agent / 'settings.json'
            settings.write_text(json.dumps({'enableInstallTelemetry': False, 'quietStartup': True,
                'compaction': {'enabled': False}, 'retry': {'enabled': False}}))
            entry = work / 'commands.ts'; entry.write_text(extension('before-reload'))
            env = {k: os.environ[k] for k in ('PATH', 'HOME', 'TMPDIR', 'LANG', 'LC_ALL', 'USER', 'LOGNAME') if k in os.environ}
            env.update(config.get('env', {}))
            env.update({'PI_CODING_AGENT_DIR': str(agent), 'PI_OFFLINE': '1', 'DO_NOT_TRACK': '1',
                'NODE_ENV': 'production', 'NO_COLOR': '1', 'TERM': 'xterm-256color', 'COLUMNS': '100', 'LINES': '30'})
            terminal = iw.Terminal(config['command'], env, work, entry, sessions,
                                   expected_tui_mode=config.get('expected_tui_mode'),
                                   keep_builtin_extensions=config.get('keep_builtin_extensions', False),
                                   use_default_tools=config.get('use_default_tools', False))
            def send(value):
                os.write(terminal.master, value.encode())
            def screen_contains(value):
                return value in '\n'.join(terminal.screen.completed)
            def idle():
                terminal.until(lambda: terminal.screen.idle_editor(work))
            def dialog(version, cancel=False):
                before = sum(e['type'] == 'command_dialog' for e in terminal.events)
                frames = terminal.frames
                send('/command-dialog\r')
                terminal.until(lambda: terminal.frames > frames and screen_contains('COMMAND_CHOICE'))
                if cancel:
                    send('\x1b')
                else:
                    send('\x1b[B\r')
                    terminal.until(lambda: screen_contains('COMMAND_CONFIRM'))
                    send('\r')
                terminal.until(lambda: sum(e['type'] == 'command_dialog' for e in terminal.events) > before)
                result = [e for e in terminal.events if e['type'] == 'command_dialog'][-1]
                assert result == {'type': 'command_dialog', 'selected': None if cancel else 'BETA',
                                  'confirmed': not cancel, 'version': version}, result
                idle()
                assert not screen_contains('COMMAND_CHOICE') and not screen_contains('COMMAND_CONFIRM')
            dialog('before-reload')
            dialog('before-reload', cancel=True)
            assert not server.requests, 'Extension dialogs unexpectedly called model'
            send('/settings\r')
            terminal.until(lambda: screen_contains('Auto-compact'))
            send('\r')
            terminal.until(lambda: json.loads(settings.read_text())['compaction']['enabled'] is True)
            send('\r')
            terminal.until(lambda: json.loads(settings.read_text())['compaction']['enabled'] is False)
            extra_gates = []
            if config.get('test_tui_mode_switch'):
                initial_mode = terminal.tui_mode
                send('TUI mode')
                terminal.until(lambda: any(line.startswith('→ TUI mode') for line in terminal.screen.completed))
                for target_mode in ('regular' if initial_mode == 'fullscreen' else 'fullscreen', initial_mode):
                    before_switch_frame = terminal.frames
                    send('\r')
                    terminal.until(lambda: terminal.frames > before_switch_frame
                                   and terminal.screen.alternate == (target_mode == 'fullscreen')
                                   and json.loads(settings.read_text()).get('tuiMode') == target_mode)
                    terminal.until(lambda: any(line.startswith('→ TUI mode') and line.endswith(target_mode)
                                               for line in terminal.screen.completed))
                extra_gates.append('settings_tui_mode_roundtrip')
            send('\x1b'); idle()
            assert not screen_contains('Auto-compact')
            entry.write_text(extension('after-reload'))
            ready_count = sum(e['type'] == 'ready' for e in terminal.events)
            send('/reload\r')
            terminal.until(lambda: sum(e['type'] == 'ready' for e in terminal.events) > ready_count)
            terminal.until(lambda: screen_contains('Reloaded keybindings'))
            idle(); dialog('after-reload')
            send('/model\r')
            terminal.until(lambda: screen_contains('Model Name: mock') and screen_contains('Escape/Ctrl+C to cancel'))
            send('\x1b'); idle()
            # Actual turn after all selector transitions proves editor focus and model recovery.
            terminal.turn('COMMAND_RECOVERY', server)
            history = terminal.session.read_text()
            for command, title in (('/tree', 'Session Tree'), ('/fork', 'Fork from Message')):
                frames = terminal.frames
                send(command + '\r')
                terminal.until(lambda: terminal.frames > frames and screen_contains(title))
                send('\x1b'); idle()
                assert terminal.session.read_text() == history
                assert not screen_contains(title)
            send('/name terminal-workflow\r')
            terminal.until(lambda: any(e.get('type') == 'session_info' and e.get('name') == 'terminal-workflow'
                                      for e in terminal.entries()))
            target = work / 'export.jsonl'
            send('/export ' + str(target) + '\r')
            terminal.until(lambda: target.exists())
            entries = [json.loads(line) for line in target.read_text().splitlines()]
            assert any(e.get('type') == 'message' and e['message'].get('role') == 'assistant'
                       and e['message'].get('stopReason') == 'stop' for e in entries)
            original_file, original_history = terminal.session, terminal.session.read_text()
            ready_count = sum(e['type'] == 'ready' for e in terminal.events)
            before_new_frame = terminal.frames
            send('/new\r')
            terminal.until(lambda: sum(e['type'] == 'ready' for e in terminal.events) > ready_count)
            terminal.session = Path([e['session'] for e in terminal.events if e['type'] == 'ready'][-1])
            assert terminal.session != original_file
            terminal.until(lambda: terminal.frames > before_new_frame and terminal.screen.idle_editor(work)
                           and not screen_contains('VISIBLE_COMMAND_RECOVERY_FINAL'))
            terminal.turn('NEW_SESSION_RECOVERY', server)
            assert original_file.read_text() == original_history
            assert 'PROBE_COMMAND_RECOVERY' not in json.dumps(server.requests[-2]['messages'])
            assert len(server.requests) == 4 and len(server.read_results) == 2 and not server.errors
            tui_mode, alternate_entries = terminal.tui_mode, terminal.screen.alternate_entries
            startup = next(e for e in terminal.events if e['type'] == 'ready')
            terminal.close(); terminal = None
            return {'name': name, 'passed': True, 'gates': ['extension_select_confirm', 'extension_select_cancel',
                'settings_toggle_persistence', 'settings_cancel_editor_focus', 'extension_reload_new_code',
                'model_selector_cancel', 'post_commands_streamed_tool_turn', 'tree_selector_cancel', 'fork_selector_cancel',
                'session_name', 'jsonl_export', 'new_session_reset_and_recovery'] + extra_gates,
                'provider_requests': 4, 'real_read_tools': 2, 'terminal_screen_endpoint': 'visible-idle-editor-v3-alternate-screen',
                'tui_mode': tui_mode, 'alternate_screen_entries': alternate_entries,
                'keep_builtin_extensions': config.get('keep_builtin_extensions', False),
                'use_default_tools': config.get('use_default_tools', False),
                'observed_startup_tools': {'available': startup['availableTools'], 'active': startup['activeTools']},
                'command': config['command'], 'env': config.get('env', {})}
    finally:
        if terminal:
            terminal.close()
        server.release.set(); server.first_gate.set(); server.middle_gate.set()
        server.shutdown(); server.server_close(); service.join(2)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--variants-file', required=True)
    parser.add_argument('--variants', nargs='+', required=True)
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    configs = json.loads(Path(args.variants_file).read_text())
    results = []
    output = Path(args.output).resolve(); output.parent.mkdir(parents=True, exist_ok=True)
    report = {'scope': 'Bounded PTY command functionality, no performance samples', 'complete': False,
        'harness_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        'shared_terminal_harness_sha256': hashlib.sha256(Path(iw.__file__).read_bytes()).hexdigest(),
        'terminal_screen_sha256': hashlib.sha256(Path(iw.__file__).with_name('terminal_screen.py').read_bytes()).hexdigest(),
        'checks': results}
    output.write_text(json.dumps(report, indent=2) + '\n')
    try:
        for name in args.variants:
            result = check(name, configs[name]); results.append(result)
            output.write_text(json.dumps(report, indent=2) + '\n')
            print(f'{name}: {len(result["gates"])} PTY command gates passed', flush=True)
    except Exception as error:
        report['failure'] = {'variant': name, 'error': str(error), 'type': type(error).__name__}
        output.write_text(json.dumps(report, indent=2) + '\n')
        raise
    report['complete'] = True
    output.write_text(json.dumps(report, indent=2) + '\n')


if __name__ == '__main__':
    main()
