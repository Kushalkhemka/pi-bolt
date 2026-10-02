#!/usr/bin/env python3
"""Compare linked-sidecar behavior and native code selection in four runtime modes."""
import argparse
import json
import re
from pathlib import Path
import subprocess
import lab
import pi

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--runtime', required=True)
parser.add_argument('--output', required=True)
parser.add_argument('--native-hot-layout', action='store_true')
parser.add_argument('--skip-trained-hot', action='store_true')
args = parser.parse_args()
out = Path(args.output).resolve()
out.mkdir(parents=True, exist_ok=False)
runtime = Path(args.runtime).resolve()
exe, image = out / 'linked-runtime', out / 'linked-runtime.aot'
env = lab.jsc_environment()
env.update(pi.COMMON)
env.update(PI_NATIVE_AOT_LINKED='1', PI_NATIVE_AOT_OUT=str(image),
           BUN_JSC_numberOfAOTCompilerThreads='2')
if args.native_hot_layout:
    env['BUN_JSC_useAOTTrainedLayout'] = 'true'
if args.skip_trained_hot:
    env['BUN_JSC_skipAOTTrainedHotFunctions'] = 'true'
command = [str(runtime), 'build', '--compile', '--bytecode', '--format=esm',
           '--no-compile-autoload-dotenv', '--no-compile-autoload-bunfig',
           '--bytecode-order=' + str(lab.ROOT / 'research/pi-workload.order'),
           str(lab.ROOT / 'fixtures/linked-runtime.mjs'), '--outfile', str(exe)]
build = subprocess.run(command, env=env, text=True, capture_output=True)
(out / 'build.log').write_text(build.stdout + build.stderr)
if build.returncode or not image.is_file():
    raise RuntimeError(f'Fixture compilation failed; see {out / "build.log"}')
omitted_keys = {tuple(map(int, fields)) for fields in re.findall(
    r'^AOT: selective hybrid omitted trained hot @(\d+):(\d+):(\d+)$', build.stderr, re.MULTILINE)}
if args.skip_trained_hot:
    omitted_count = re.search(r'AOT: selective hybrid skipped (\d+) trained hot function jobs', build.stderr)
    if not omitted_keys or not omitted_count or int(omitted_count[1]) != len(omitted_keys):
        raise RuntimeError('Selective compilation did not export its exact omitted key set/count')
elif omitted_keys:
    raise RuntimeError('Nonselective compilation unexpectedly omitted trained hot keys')
layout = re.search(r'AOT: trained native layout jobs (\d+) hot (\d+)', build.stderr)
if args.native_hot_layout:
    if not layout or int(layout[1]) <= 0 or int(layout[2]) <= 0:
        raise RuntimeError('Trained native layout was not positively demonstrated')
elif layout:
    raise RuntimeError('Compilation unexpectedly used trained native layout')
results = {}
selection_evidence = {}
call_semantics_evidence = {}
for mode in ('jit', 'interpreter', 'native', 'hybrid'):
    env = lab.jsc_environment()
    env.update(pi.COMMON)
    env['BUN_JSC_verboseDiskCache'] = 'true'
    selects_native = mode in ('native', 'hybrid')
    env.update(BUN_JSC_useJIT=str(mode in ('jit', 'hybrid')).lower(), BUN_JSC_useAOT=str(selects_native).lower())
    env['BUN_JSC_verboseAOTCompilation'] = 'true'
    if selects_native:
        env.update(BUN_JSC_aotImagePath=str(image), BUN_JSC_verboseAOTCompilation='true')
    result = subprocess.run([str(exe)], env=env, capture_output=True, text=True, timeout=60)
    (out / (mode + '.log')).write_text(result.stderr)
    if result.returncode:
        raise RuntimeError(f'{mode} fixture failed ({result.returncode}); see {out / (mode + ".log")}')
    if 'BytecodeCache: accepted prelinked module graph v4 modules ' not in result.stderr:
        raise RuntimeError(f'{mode}: prelinked module graph was not accepted')
    results[mode] = json.loads(result.stdout)
    if not all(results[mode].get(fact) for fact in ('builtin_validation', 'bun_builtin_validation', 'worker_builtin_validation', 'call_semantics_validation')):
        raise RuntimeError(f'{mode}: builtin semantic assertions were not completed')
    if selects_native:
        selected = [(name, tuple(map(int, fields))) for name, *fields in re.findall(
            r'^AOT: (.*?) \(module (\d+) start (\d+) kind (\d+)\) is at ', result.stderr, re.MULTILINE)]
        selected_keys = {key for _, key in selected}
        call_semantics_evidence[mode] = {}
        for name in ('argumentShape', 'fixedArguments', 'ConstructorChoice', 'ordinaryResult', 'tailSum'):
            native_keys = {key for selected_name, key in selected if selected_name == name}
            fallback_keys = {tuple(map(int, fields)) for fields in re.findall(
                rf'^AOT: not in the image: {name} of .*? \(module (\d+) start (\d+) kind (\d+)\)$',
                result.stderr, re.MULTILINE)}
            if not native_keys and not (args.skip_trained_hot and fallback_keys and fallback_keys <= omitted_keys):
                raise RuntimeError(f'{mode}: ordinary function {name} native selection/explicit selective omission was not demonstrated')
            call_semantics_evidence[mode][name] = {'selected_native_keys': sorted(native_keys),
                                                  'explicitly_omitted_keys': sorted(fallback_keys & omitted_keys)}
        if selected_keys & omitted_keys:
            raise RuntimeError(f'{mode}: omitted trained keys unexpectedly selected native code')
        selected_http = {key for name, key in selected if name == 'node:http'}
        missing_http = {tuple(map(int, fields)) for fields in re.findall(
            r'^AOT: not in the image: node:http of node:http \(module (\d+) start (\d+) kind (\d+)\)$',
            result.stderr, re.MULTILINE)}
        if args.skip_trained_hot:
            http_keys = selected_http | missing_http
            if len(http_keys) != 1:
                raise RuntimeError(f'{mode}: node:http exact code-selection identity was not demonstrated')
            http_key = next(iter(http_keys))
            if (http_key in omitted_keys) != (http_key in missing_http) or (http_key in omitted_keys) == (http_key in selected_http):
                raise RuntimeError(f'{mode}: node:http fallback does not match its exact exported omission key')
            # Resolve the helper's internal-module identity from the named
            # module-root diagnostic, rather than accepting an ambiguous Server.
            server_modules = {int(module) for module in re.findall(
                r'^AOT: (?:not in the image: node:_http_server of node:_http_server|node:_http_server) \(module (\d+) start \d+ kind \d+\)(?: is at .*|)$',
                result.stderr, re.MULTILINE)}
            server_keys = {key for name, key in selected if name == 'Server' and key[0] in server_modules}
            if not server_keys:
                raise RuntimeError(f'{mode}: included node:_http_server Server helper did not select native code')
            selection_evidence[mode] = {'node_http_key': http_key, 'node_http_omitted': http_key in omitted_keys,
                                      'included_internal_server_keys': sorted(server_keys),
                                      'omitted_keys_selected': []}
        elif not selected_http:
            raise RuntimeError('Native internal-module coverage was not demonstrated')
    if selects_native and 'AOT: nativeErrorFrame ' not in result.stderr:
        raise RuntimeError('Error-finalization regression did not execute a native frame')
    if selects_native and 'AOT: nativeErrorParentFrame ' not in result.stderr:
        raise RuntimeError('Error-finalization regression did not execute a native async parent')
    # These diagnostics prove selected sidecar code, not entry counts or that
    # each semantic edge case below independently executed a native builtin.
    if selects_native:
        # findLast is only called by the data-URL worker application in this
        # fixture. Its selection strengthens worker-path evidence without
        # treating address logs as worker-specific execution counters.
        for builtin in ('map', 'filter', 'reduce', 'every', 'find', 'findLast', 'next'):
            if not any(line.startswith(f'AOT: {builtin} (module {0xeb17ffff} ') and ' is at ' in line
                       for line in result.stderr.splitlines()):
                raise RuntimeError(f'{mode}: builtin {builtin} did not select sidecar code')
        # Bun's generated functions use a separate combined source provider.
        # These helpers are reached by the fixture's explicit nextTick workload.
        for builtin in ('nextTick', 'processTicksAndRejections'):
            if not any(line.startswith(f'AOT: {builtin} (module {0xb017ffff} ') and ' is at ' in line
                       for line in result.stderr.splitlines()):
                raise RuntimeError(f'{mode}: Bun builtin {builtin} did not select sidecar code')
    elif any(line.startswith('AOT: ') and ' is at ' in line for line in result.stderr.splitlines()):
        raise RuntimeError(f'{mode}: unexpectedly selected native sidecar code')
if any(result != results['jit'] for result in results.values()):
    raise RuntimeError('Modes produced different results')
report = {'results': results, 'command': command, 'runtime_sha256': pi.digest(runtime),
          'native_hot_layout': args.native_hot_layout,
          'native_layout_jobs': int(layout[1]) if layout else None,
          'native_layout_hot_jobs': int(layout[2]) if layout else None,
          'skip_trained_hot': args.skip_trained_hot,
          'omitted_trained_hot_keys': sorted(omitted_keys),
          'selective_selection_evidence': selection_evidence,
          'ordinary_call_selection_evidence': call_semantics_evidence,
          'fixture_sha256': pi.digest(lab.ROOT / 'fixtures/linked-runtime.mjs'),
          'executable_sha256': pi.digest(exe), 'image_sha256': pi.digest(image),
          'native_evidence': 'Selected sidecar code addresses for named JSC (0xeb17ffff) and Bun (0xb017ffff) builtins; not execution counts or per-edge native coverage'}
(out / 'results.json').write_text(json.dumps(report, indent=2) + '\n')
print(json.dumps(results))
