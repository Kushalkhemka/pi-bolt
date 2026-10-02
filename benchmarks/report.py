#!/usr/bin/env python3
"""Generate a reviewable report from saved measurements; never remeasure."""
import json
from pathlib import Path

root = Path(__file__).resolve().parent
rpc = json.loads((root / 'results.json').read_text())
tui = json.loads((root / 'tui-results.json').read_text())
screen = json.loads((root / 'screening.json').read_text())
labels = {'node': 'Node default', 'node_compile_cache': 'Node + compile cache',
    'bun_same_entry': 'Bun on identical npm entry', 'bun_bytecode': 'Bun compiled + bytecode',
    'bun_bytecode_smol': 'Bun bytecode + smol', 'bun_bytecode_nojit': 'Bun bytecode, JIT disabled',
    'bun_official_entry': 'Bun on unbundled official entry', 'bun_compiled': 'Bun standard compiled',
    'bun_same_entry_smol': 'Bun on npm entry + smol'}
order = ['node', 'node_compile_cache', 'bun_same_entry', 'bun_bytecode', 'bun_bytecode_smol', 'bun_bytecode_nojit']
lines = ['**Pi Node.js versus Bun — local benchmark, 1 October 2026**', '',
    f'Apple M5, 16 GB RAM, macOS {rpc["macos"]}; installed Pi {rpc["pi_version"]}; '
    f'Node {rpc["node_version"]}; Bun {rpc["bun_version"]}.', '',
    '**Best tested practical configuration: Bun standalone with minification and bytecode, using the default JIT.** '
    'The smol variant is essentially tied on speed and does not reduce memory in this short workload. '
    'Disabling JIT is the lowest-memory option, with slower turn execution. This is bytecode precompilation; '
    'the experimental native AOT shown in the earlier screenshot was not available or tested.', '',
    'The numbers below are medians of 20 fresh processes per configuration, following three discarded warmups. '
    'Configurations were shuffled within each round using a fixed seed. Filesystem caches were warm; '
    'Node’s compile cache was populated. These are not cold-boot measurements.', '',
    'A freshly rebuilt bytecode executable took about 1.7 seconds to reach RPC readiness on its first launch '
    '(recorded in final-build-smoke.json). This first-launch cost is excluded from the warmed medians below; '
    'its cause was not isolated.', '',
    '| Configuration | RPC ready | Startup + 5 turns | CPU through 5 turns | Memory at ready | Memory after 5 turns |',
    '|---|---:|---:|---:|---:|---:|']
for name in order:
    m = rpc['summary'][name]['median']
    lines.append(f'| {labels[name]} | {m["ready_ms"]:.1f} ms | {m["startup_plus_5_turns_ms"]:.1f} ms | '
        f'{m["cpu_through_5_turns_ms"]:.1f} ms | {m["ready_footprint_mb"]:.1f} MB | '
        f'{m["after_5_turns_footprint_mb"]:.1f} MB |')
lines += ['', 'CPU is the sum of user and system time across Pi’s threads, measured using macOS '
    'proc_pid_rusage and converted from Mach ticks with mach_timebase_info. It can exceed wall time. '
    'The JSON also includes wait4 CPU through verification/shutdown, as a cross-check. '
    'Memory is macOS physical footprint in decimal MB, not RSS or JavaScript heap. '
    'The OS lifetime peak field is recorded in JSON; its reported value did not exceed the final footprint '
    'for most runs, so brief memory peaks are not separately characterized.', '',
    '| Configuration | RPC ready p10–p90 | Total wall time p10–p90 |', '|---|---:|---:|']
for name in order:
    s = rpc['summary'][name]
    lines.append(f'| {labels[name]} | {s["p10"]["ready_ms"]:.1f}–{s["p90"]["ready_ms"]:.1f} ms | '
        f'{s["p10"]["startup_plus_5_turns_ms"]:.1f}–{s["p90"]["startup_plus_5_turns_ms"]:.1f} ms |')
lines += ['', '**Terminal verification**', '',
    'A separate 20-run benchmark used a 100×30 pseudo-terminal. It measured the first fully rendered screen, '
    'then typed a unique marker and verified that Pi’s editor rendered it. Version/package checks were disabled '
    'with PI_OFFLINE=1. No model requests were made during this test.', '',
    '| Configuration | First screen | First accepted input | Memory at first screen |', '|---|---:|---:|---:|']
for name in ['node', 'node_compile_cache', 'bun_same_entry', 'bun_bytecode', 'bun_bytecode_nojit']:
    m = tui['summary'][name]['median']
    lines.append(f'| {labels[name]} | {m["first_screen_ms"]:.1f} ms | {m["interactive_input_ms"]:.1f} ms | '
        f'{m["ready_footprint_mb"]:.1f} MB |')
lines += ['', '**Workload and validation**', '',
    f'Each RPC run completed five sequential turns against an OpenAI-compatible HTTP server on loopback. '
    f'Each turn invoked Pi’s real read tool on a fixed 3.2 KB local fixture, then returned '
    f'{rpc["reply_bytes_per_turn"]:,} bytes of text in 32 SSE chunks. There were exactly 10 model requests, '
    'five successful reads, and five matching final replies per run. Replies were compared byte-for-byte; '
    'their SHA-256 is stored in the raw results. Every measured process exited successfully.', '',
    'The Python mock server and measurement harness run outside Pi; their CPU/memory are excluded. '
    'The HTTP responses have no artificial delay or token-generation time. Startup readiness is a successful '
    'get_state RPC response. Total wall time ends at the fifth agent_end event, before result verification/shutdown.', '',
    'All variants used NODE_ENV=production, isolated agent settings, telemetry disabled, thinking off, '
    'retry/compaction disabled, and no session persistence. Extensions, skills, extra templates and extra themes '
    'were disabled equally. Inherited credentials and unrelated runtime tunables were omitted. '
    'This benchmarks Pi’s base runtime; it does not estimate gains for your full extension setup, long sessions, '
    'image processing, OAuth flows, or remote model latency. Nothing changed the globally installed Pi.', '',
    '**Earlier configuration screening**', '',
    'Six measured runs per variant, two discarded warmups. This screening selected the variants for the main comparison.', '',
    '| Configuration | RPC ready | Startup + 5 turns | Memory after 5 turns |', '|---|---:|---:|---:|']
for name in ['bun_official_entry', 'bun_compiled', 'bun_same_entry_smol']:
    m = screen['summary'][name]['median']
    lines.append(f'| {labels[name]} | {m["ready_ms"]:.1f} ms | {m["startup_plus_5_turns_ms"]:.1f} ms | '
        f'{m["after_5_turns_footprint_mb"]:.1f} MB |')
lines += ['', '**Build and environment configuration**', '',
    'The Bun binaries use Pi’s existing dist/bun/cli.js, which includes its Bun runtime bootstrap. '
    'The direct runtime comparison uses the identical dist/bundle/cli.js for Node and Bun. '
    'Minification/bytecode results combine a runtime change with a build optimization.', '',
    'The bytecode build uses these flags:', '',
    '```text', 'bun build --compile --bytecode --format=esm --minify --sourcemap',
    '  --no-compile-autoload-bunfig --no-compile-autoload-dotenv',
    '  <installed-pi>/dist/bun/cli.js <installed-pi>/dist/utils/image-resize-worker.js',
    '  --outfile benchmarks/bin/pi-bun-bytecode', '```', '',
    'Recommended performance profile: NODE_ENV=production, default JIT, no heap/JSC tuning. '
    'The direct Bun runs use --no-env-file for consistent configuration; this should only be retained '
    'in normal use if you supply needed environment variables yourself. '
    'The low-memory experimental profile adds BUN_JSC_useJIT=false. It was tested only on the workload above '
    'and may affect features or extensions that depend on JIT. It is not native AOT.', '',
    'Node’s tuned baseline sets NODE_COMPILE_CACHE to benchmarks/state/node-cache. '
    'The Bun smol binary sets BUN_OPTIONS=--smol. Exact commands and environment overrides are saved in results.json.', '',
    'The bytecode executable is ' + f'{(root / "bin/pi-bun-bytecode").stat().st_size / 1e6:.1f} MB; '
    'the ordinary compiled executable is ' + f'{(root / "bin/pi-bun-compiled").stat().st_size / 1e6:.1f} MB. '
    'Those sizes include Bun’s runtime, but exclude the shared asset symlinks. The binaries are benchmark artifacts '
    'and their assets refer to the installed Pi package.', '',
    '**Reproduce from the Pi-Bolt directory**', '',
    '```sh', 'npm ci --prefix benchmarks/runtime --no-audit --no-fund', 'python3 benchmarks/build.py',
    'python3 benchmarks/benchmark.py --runs 20 --warmups 3 \\',
    '  --variants node node_compile_cache bun_same_entry bun_bytecode bun_bytecode_smol bun_bytecode_nojit \\',
    '  --output results.json', 'python3 benchmarks/tui_startup.py', 'python3 benchmarks/report.py', '```', '',
    'This harness is specific to macOS and the installed package path recorded above. '
    'Bun is pinned to 1.4.2 in the local runtime lockfile. Updating the installed Pi would require rebuilding '
    'and rerunning to obtain comparable results.', '',
    'Configuration references: [Bun executable and bytecode documentation](https://bun.com/docs/bundler/executables), '
    '[Bun smol documentation](https://bun.com/docs/runtime), '
    '[Node compile cache documentation](https://nodejs.org/api/module.html#module-compile-cache).', '']
(root / 'REPORT.md').write_text('\n'.join(lines))
print(root / 'REPORT.md')
