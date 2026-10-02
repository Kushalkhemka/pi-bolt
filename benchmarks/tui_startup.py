#!/usr/bin/env python3
"""Measure Pi terminal startup and prove its editor handles a keystroke."""
import argparse
import datetime
import hashlib
import fcntl
import json
import os
from pathlib import Path
import pty
import random
import select
import struct
import subprocess
import termios
import time
import benchmark as b

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--variants-file')
    parser.add_argument('--variants', nargs='+')
    parser.add_argument('--runs', type=int, default=20)
    parser.add_argument('--warmups', type=int, default=3)
    parser.add_argument('--output', default='tui-results.json')
    args = parser.parse_args()
    if args.runs < 1 or args.warmups < 0:
        parser.error('--runs must be positive and --warmups must be nonnegative')
    work, env = b.prepare(9)
    env.update({'TERM': 'xterm-256color', 'COLUMNS': '100', 'LINES': '30', 'PI_OFFLINE': '1'})
    configs = b.variants()
    if args.variants_file:
        configs.update(json.loads(open(args.variants_file).read()))
    names = args.variants or ['node', 'node_compile_cache', 'bun_same_entry', 'bun_bytecode', 'bun_bytecode_nojit']
    selected_configs = {name: configs[name] for name in names}
    metadata = {'date': datetime.datetime.now(datetime.timezone.utc).isoformat(), **b.hardware_metadata(),
                'configs': selected_configs, 'artifacts': b.artifact_provenance(selected_configs),
                'harness_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}
    samples = []
    rng = random.Random(20261002)
    for round in range(-args.warmups, args.runs):
        order = names.copy()
        rng.shuffle(order)
        for name in order:
            master, slave = pty.openpty()
            fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack('HHHH', 30, 100, 0, 0))
            command = configs[name]['command'] + ['--provider', 'benchmark', '--model', 'mock',
                '--thinking', 'off', '--no-session', '--no-extensions', '--no-skills', '--no-prompt-templates', '--no-themes']
            started = time.perf_counter()
            proc = subprocess.Popen(command, cwd=work, env={**env, **configs[name]['env']},
                                    stdin=slave, stdout=slave, stderr=slave)
            os.close(slave)
            output = b''
            try:
                # End of first complete synchronized screen update, after footer/editor.
                deadline = time.monotonic() + 15
                while b'mock' not in output or b'\x1b[?2026l' not in output:
                    if time.monotonic() > deadline:
                        raise TimeoutError(f'{name}: no initial screen: {output!r}')
                    if select.select([master], [], [], .1)[0]:
                        output += os.read(master, 65536)
                rendered = time.perf_counter()
                at_ready = b.memory(proc.pid)
                # A unique string typed into raw terminal must be rendered by the editor.
                marker = b'LOCALINPUTPROBE'
                output = b''
                os.write(master, marker)
                while marker not in output:
                    if time.monotonic() > deadline:
                        raise TimeoutError(f'{name}: editor did not accept input: {output!r}')
                    if select.select([master], [], [], .1)[0]:
                        output += os.read(master, 65536)
                accepted = time.perf_counter()
                sample = {'variant': name, 'round': round,
                    'first_screen_ms': (rendered - started) * 1000,
                    'interactive_input_ms': (accepted - started) * 1000,
                    'ready_footprint_mb': at_ready['footprint_mb']}
                if round >= 0:
                    samples.append(sample)
                print(f'{name:24s} round={round:2d} screen={sample["first_screen_ms"]:.1f}ms '
                      f'input={sample["interactive_input_ms"]:.1f}ms', flush=True)
            finally:
                # No model request or persisted session: discard the isolated editor.
                proc.kill()
                proc.wait(timeout=5)
                os.close(master)
        (b.ROOT / args.output).write_text(json.dumps({**metadata, 'samples': samples, 'summary': b.summary(samples),
            'method': '100x30 PTY; first complete screen then actual raw-mode editor keystroke; PI_OFFLINE=1; no API request'}, indent=2))


if __name__ == "__main__":
    main()
