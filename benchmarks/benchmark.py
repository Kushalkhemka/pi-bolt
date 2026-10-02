#!/usr/bin/env python3
"""Local Pi runtime benchmark. Standard library only; no real model/API keys.

Use --runs 20 --warmups 3 --output results.json for a final measurement.
The mock model issues one real read tool call and 32 streamed text chunks per
turn. Memory is macOS phys_footprint or Linux RSS. Child CPU comes from wait4.
"""
import argparse
import datetime
import hashlib
import json
import os
from pathlib import Path
import queue
import random
import shutil
import statistics
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from platform_metrics import memory, hardware_metadata, peak_rss_mb

ROOT = Path(__file__).resolve().parent
PI = Path(os.environ.get('PI_BENCH_PACKAGE_DIR', '/opt/homebrew/lib/node_modules/@earendil-works/pi-coding-agent'))
BUN = Path(os.environ.get('PI_BENCH_BUN', str(ROOT / 'runtime/node_modules/.bin/bun')))
NODE = shutil.which('node')
TEXT_CHUNKS = [(f'Local benchmark response chunk {i:02d}. ' + 'abcdef0123456789 ' * 6 + '\n') for i in range(32)]
TEXT = ''.join(TEXT_CHUNKS)


class MockHandler(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'

    def log_message(self, *_):
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        if self.path != '/v1/chat/completions' or body['model'] != 'mock':
            self.send_error(400)
            return
        self.server.request_count += 1
        last = body['messages'][-1]
        tool_request = last['role'] != 'tool'
        self.send_response(200)
        self.send_header('Content-Type', 'text/event-stream')
        self.send_header('Cache-Control', 'no-cache')
        self.send_header('Connection', 'close')
        self.end_headers()
        def emit(delta, finish=None, usage=None):
            chunk = {'id': 'chatcmpl-local-benchmark', 'object': 'chat.completion.chunk',
                     'created': 1, 'model': 'mock',
                     'choices': [{'index': 0, 'delta': delta, 'finish_reason': finish}]}
            if usage:
                chunk['usage'] = usage
            self.wfile.write(('data: ' + json.dumps(chunk) + '\n\n').encode())
        emit({'role': 'assistant', 'content': ''})
        if tool_request:
            emit({'tool_calls': [{'index': 0, 'id': 'call_read_fixture', 'type': 'function',
                                 'function': {'name': 'read', 'arguments': json.dumps({'path': 'fixture.txt'})}}]})
            emit({}, 'tool_calls', {'prompt_tokens': 100, 'completion_tokens': 20, 'total_tokens': 120})
        else:
            if 'LOCAL_FIXTURE' not in str(last['content']):
                raise AssertionError('read tool did not return fixture')
            for chunk in TEXT_CHUNKS:
                emit({'content': chunk})
            emit({}, 'stop', {'prompt_tokens': 200, 'completion_tokens': 1000, 'total_tokens': 1200})
        self.wfile.write(b'data: [DONE]\n\n')
        self.wfile.flush()
        self.close_connection = True


def prepare(port):
    work = ROOT / 'state/work'
    agent = ROOT / 'state/agent'
    work.mkdir(parents=True, exist_ok=True)
    agent.mkdir(parents=True, exist_ok=True)
    (work / 'fixture.txt').write_text('LOCAL_FIXTURE\n' + 'deterministic local file contents\n' * 100)
    (agent / 'models.json').write_text(json.dumps({'providers': {'benchmark': {
        'baseUrl': f'http://127.0.0.1:{port}/v1', 'api': 'openai-completions', 'apiKey': 'local-dummy',
        'models': [{'id': 'mock', 'name': 'mock', 'reasoning': False, 'input': ['text'],
                    'contextWindow': 128000, 'maxTokens': 8192,
                    'cost': {'input': 0, 'output': 0, 'cacheRead': 0, 'cacheWrite': 0}}]}}}))
    (agent / 'settings.json').write_text(json.dumps({'enableInstallTelemetry': False,
        'compaction': {'enabled': False}, 'retry': {'enabled': False}, 'quietStartup': True}))
    # Use an allowlist so inherited credentials/runtime tunables cannot affect runs.
    env = {k: os.environ[k] for k in ('PATH', 'HOME', 'TMPDIR', 'LANG', 'LC_ALL', 'USER', 'LOGNAME') if k in os.environ}
    env.update({'PI_CODING_AGENT_DIR': str(agent), 'PI_TELEMETRY': '0', 'DO_NOT_TRACK': '1',
                'NODE_ENV': 'production', 'NO_COLOR': '1'})
    return work, env


def variants():
    bundled = str(PI / 'dist/bundle/cli.js')
    official = str(PI / 'dist/bun/cli.js')
    base = [str(BUN), '--no-env-file']
    compiled_env = {'PI_PACKAGE_DIR': str(ROOT / 'bin')}
    return {
        'node': {'command': [NODE, bundled], 'env': {}},
        'node_compile_cache': {'command': [NODE, bundled], 'env': {'NODE_COMPILE_CACHE': str(ROOT / 'state/node-cache')}},
        'bun_same_entry': {'command': base + [bundled], 'env': {}},
        'bun_same_entry_smol': {'command': base + ['--smol', bundled], 'env': {}},
        'bun_official_entry': {'command': base + [official], 'env': {}},
        'bun_compiled': {'command': [str(ROOT / 'bin/pi-bun-compiled')], 'env': compiled_env},
        'bun_bytecode': {'command': [str(ROOT / 'bin/pi-bun-bytecode')], 'env': compiled_env},
        'bun_bytecode_smol': {'command': [str(ROOT / 'bin/pi-bun-bytecode')], 'env': {**compiled_env, 'BUN_OPTIONS': '--smol'}},
        'bun_bytecode_nojit': {'command': [str(ROOT / 'bin/pi-bun-bytecode')], 'env': {**compiled_env, 'BUN_JSC_useJIT': 'false'}},
    }


def one_run(name, variant, work, env, server, turns=5):
    args = ['--mode', 'rpc', '--provider', 'benchmark', '--model', 'mock', '--thinking', 'off',
            '--no-session', '--no-extensions', '--no-skills', '--no-prompt-templates', '--no-themes', '--tools', 'read']
    events = queue.Queue()
    stderr = []
    start_requests = server.request_count
    started = time.perf_counter()
    proc = subprocess.Popen(variant['command'] + args, cwd=work, env={**env, **variant['env']},
                            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    def reader():
        for line in proc.stdout:
            try:
                events.put(json.loads(line))
            except Exception:
                events.put({'type': 'invalid', 'line': line.decode(errors='replace')})
        events.put({'type': 'eof'})
    def err_reader():
        stderr.append(proc.stderr.read().decode(errors='replace'))
    thread = threading.Thread(target=reader, daemon=True)
    err_thread = threading.Thread(target=err_reader, daemon=True)
    thread.start()
    err_thread.start()
    all_events = []
    def send(value):
        proc.stdin.write((json.dumps(value) + '\n').encode())
        proc.stdin.flush()
    def until(predicate):
        deadline = time.monotonic() + 30
        while True:
            event = events.get(timeout=max(0.01, deadline - time.monotonic()))
            all_events.append(event)
            if event.get('type') in ('eof', 'invalid'):
                err_thread.join(1)
                raise RuntimeError(f'{name}: unexpected {event}; stderr={stderr}')
            if event.get('type') == 'response' and not event.get('success'):
                raise RuntimeError(f'{name}: RPC failure {event}')
            if event.get('type') == 'message_end' and event.get('message', {}).get('stopReason') == 'error':
                raise RuntimeError(f'{name}: model failure {event}')
            if predicate(event):
                return event
    try:
        send({'type': 'get_state', 'id': 'ready'})
        ready = until(lambda e: e.get('id') == 'ready')
        ready_time = time.perf_counter()
        assert ready['data']['model']['id'] == 'mock'
        at_ready = memory(proc.pid)
        turn_times = []
        for i in range(turns):
            turn_start = time.perf_counter()
            send({'type': 'prompt', 'id': f'turn-{i}', 'message': f'Turn {i}: read fixture.txt and summarize.'})
            until(lambda e: e.get('type') == 'agent_end')
            turn_times.append((time.perf_counter() - turn_start) * 1000)
        completed = time.perf_counter()
        at_end = memory(proc.pid)
        send({'type': 'get_messages', 'id': 'verify'})
        messages = until(lambda e: e.get('id') == 'verify')['data']['messages']
        finals = [m for m in messages if m['role'] == 'assistant' and m.get('stopReason') == 'stop']
        assert len(finals) == turns, f'{name}: {len(finals)} completed replies'
        assert all(''.join(c.get('text', '') for c in m['content']) == TEXT for m in finals)
        tool_events = [e for e in all_events if e.get('type') == 'tool_execution_end']
        assert len(tool_events) == turns and all(not e.get('isError') for e in tool_events)
        assert server.request_count - start_requests == turns * 2
        # EOF requests a normal Pi shutdown; collect per-child CPU, not harness/server CPU.
        proc.stdin.close()
        _, status, usage = os.wait4(proc.pid, 0)
        proc.returncode = os.waitstatus_to_exitcode(status)
        thread.join(1)
        err_thread.join(1)
        assert proc.returncode == 0, f'{name}: exit {proc.returncode}: {stderr}'
        return {'variant': name, 'ready_ms': (ready_time - started) * 1000,
                'startup_plus_5_turns_ms': (completed - started) * 1000,
                'five_turns_ms': (completed - ready_time) * 1000,
                'cpu_ms': (usage.ru_utime + usage.ru_stime) * 1000,
                'cpu_through_5_turns_ms': at_end['cpu_ms'],
                'ready_footprint_mb': at_ready['footprint_mb'],
                'after_5_turns_footprint_mb': at_end['footprint_mb'],
                'peak_footprint_mb': at_end['peak_footprint_mb'],
                'peak_rss_mb': peak_rss_mb(usage),
                'turn_ms': turn_times, 'requests': turns * 2, 'turns': turns, 'read_tools': len(tool_events),
                'reply_sha256': hashlib.sha256(TEXT.encode()).hexdigest(), 'stderr': ''.join(stderr)}
    except Exception:
        proc.kill()
        proc.wait()
        raise


def summary(samples):
    output = {}
    for name in dict.fromkeys(x['variant'] for x in samples):
        subset = [x for x in samples if x['variant'] == name]
        keys = [k for k, v in subset[0].items() if isinstance(v, (int, float))]
        output[name] = {'runs': len(subset), 'median': {}, 'p10': {}, 'p90': {}}
        for key in keys:
            vals = sorted(x[key] for x in subset)
            output[name]['median'][key] = statistics.median(vals)
            output[name]['p10'][key] = vals[int((len(vals) - 1) * .1)]
            output[name]['p90'][key] = vals[int((len(vals) - 1) * .9)]
    return output


def artifact_provenance(configs):
    output = {}
    for name, config in configs.items():
        candidates = [Path(arg) for arg in config['command'] if Path(arg).is_file()]
        if config['env'].get('BUN_JSC_aotImagePath'):
            candidates.append(Path(config['env']['BUN_JSC_aotImagePath']))
        manifest = Path(config['command'][0]).parent / 'manifest.json'
        if manifest.is_file():
            candidates.append(manifest)
        output[name] = {str(path): {'sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
                                   'bytes': path.stat().st_size} for path in candidates}
    return output


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--runs', type=int, default=4)
    parser.add_argument('--turns', type=int, default=5)
    parser.add_argument('--warmups', type=int, default=1)
    parser.add_argument('--variants', nargs='*')
    parser.add_argument('--variants-file', help='JSON file with additional named command/env variants')
    parser.add_argument('--output', default='screening.json')
    args = parser.parse_args()
    if args.runs < 1 or args.warmups < 0 or args.turns < 1:
        parser.error('--runs/--turns must be positive and --warmups must be nonnegative')
    server = ThreadingHTTPServer(('127.0.0.1', 0), MockHandler)
    server.request_count = 0
    threading.Thread(target=server.serve_forever, daemon=True).start()
    work, env = prepare(server.server_port)
    configs = variants()
    if args.variants_file:
        added = json.loads(Path(args.variants_file).read_text())
        for name, config in added.items():
            if name in configs:
                parser.error(f'Variant already exists: {name}')
            if not config.get('command') or not all(isinstance(x, str) for x in config['command']):
                parser.error(f'{name}: command must be a nonempty list of strings')
            if not isinstance(config.get('env'), dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in config['env'].items()):
                parser.error(f'{name}: env must map strings to strings')
        configs.update(added)
    selected = args.variants or list(configs)
    for name in selected:
        if name not in configs:
            parser.error(f'Unknown variant: {name}')
    rng = random.Random(20261001)
    selected_configs = {k: configs[k] for k in selected}
    provenance = artifact_provenance(selected_configs)
    samples, warmups = [], []
    for index in range(args.warmups + args.runs):
        order = selected.copy()
        rng.shuffle(order)
        for name in order:
            sample = one_run(name, configs[name], work, env, server, args.turns)
            sample['round'] = index - args.warmups
            (warmups if index < args.warmups else samples).append(sample)
            print(f'{name:25s} round={sample["round"]:2d} ready={sample["ready_ms"]:7.1f}ms '
                  f'wall={sample["startup_plus_5_turns_ms"]:7.1f}ms cpu={sample["cpu_ms"]:7.1f}ms '
                  f'footprint={sample["after_5_turns_footprint_mb"]:6.1f}MB', flush=True)
        result = {'date': datetime.datetime.now(datetime.timezone.utc).isoformat(), 'pi_version': json.loads((PI / 'package.json').read_text())['version'],
                  'node_version': subprocess.check_output([NODE, '--version'], text=True).strip(),
                  'bun_version': subprocess.check_output([str(BUN), '--version'], text=True).strip(),
                  **hardware_metadata(),
                  'configs': selected_configs, 'artifacts': provenance,
                  'harness_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                  'common_env': {k: v for k, v in env.items() if k not in ('PATH', 'HOME', 'USER', 'LOGNAME', 'TMPDIR')},
                  'workload': f'RPC ready + {args.turns} turns; 1 actual read tool per turn; 32 text chunks per turn; {args.turns * 2} loopback HTTP requests',
                  'metric_names_note': 'Historical *_5_turns_* keys refer to the requested number of turns',
                  'reply_bytes_per_turn': len(TEXT.encode()), 'warmups': warmups,
                  'samples': samples, 'summary': summary(samples)}
        (ROOT / args.output).write_text(json.dumps(result, indent=2))
    server.shutdown()
    for name, item in result['summary'].items():
        print(name, json.dumps(item['median'], sort_keys=True), flush=True)


if __name__ == '__main__':
    main()
