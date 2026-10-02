#!/usr/bin/env python3
"""Verify native selection, ordinary JIT promotion and benign call/GC semantics."""
import argparse
import json
from pathlib import Path
import re
import subprocess
import lab
import pi
p = argparse.ArgumentParser(description=__doc__)
p.add_argument('--runtime', required=True, type=Path)
p.add_argument('--output', required=True, type=Path)
a = p.parse_args()
out = a.output.resolve()
out.mkdir(parents=True, exist_ok=False)
(out / 'results.json').write_text(json.dumps({'complete': False}) + '\n')
exe, image = out / 'runtime', out / 'runtime.aot'
env = {**lab.jsc_environment(), **pi.COMMON, 'PI_NATIVE_AOT_LINKED': '1',
       'PI_NATIVE_AOT_OUT': str(image), 'BUN_JSC_numberOfAOTCompilerThreads': '2'}
build = subprocess.run([str(a.runtime.resolve()), 'build', '--compile', '--bytecode', '--format=esm',
                        '--no-compile-autoload-dotenv', '--no-compile-autoload-bunfig',
                        '--bytecode-order=' + str(lab.ROOT / 'research/pi-workload.order'),
                        str(lab.ROOT / 'fixtures/native-tiering.mjs'), '--outfile', str(exe)],
                       env=env, text=True, capture_output=True)
(out / 'build.log').write_text(build.stdout + build.stderr)
if build.returncode or not image.is_file():
    raise RuntimeError(f'Native tier fixture build failed: {out / "build.log"}')
configs = [('native', False, True, False, 32), ('hybrid', True, True, False, 32),
           ('hybrid-tier32', True, True, True, 32), ('hybrid-tier1000', True, True, True, 1000),
           ('hybrid-tier10000', True, True, True, 10000),
           ('hybrid-tier-disabled', True, True, True, 0), ('jit', True, False, False, 32),
           ('interpreter', False, False, False, 32), ('jit-off-tier-option', False, True, True, 32)]
results = {}
for name, jit, native, tier, threshold in configs:
    env = {**lab.jsc_environment(), **pi.COMMON, 'BUN_JSC_useJIT': str(jit).lower(),
           'BUN_JSC_useAOT': str(native).lower(), 'BUN_JSC_useAOTNativeTiering': str(tier).lower(),
           'BUN_JSC_thresholdForAOTNativeTiering': str(threshold), 'BUN_JSC_aotImagePath': str(image),
           'BUN_JSC_useConcurrentJIT': 'false', 'BUN_JSC_verboseAOTCompilation': 'true'}
    expect = jit and native and tier and threshold > 0
    env['EXPECT_NATIVE_TIERING'] = '1' if expect else '0'
    r = subprocess.run([str(exe)], env=env, text=True, capture_output=True, timeout=90)
    (out / (name + '.log')).write_text(r.stdout + r.stderr)
    if r.returncode:
        raise RuntimeError(f'{name} failed: {out / (name + ".log")}')
    result = json.loads(r.stdout)
    selected = sorted(set(re.findall(r'^AOT: (hot\w+).*? is at ', r.stderr, re.M)))
    promotions = re.findall(r'^AOT: tiered function (\d+) to ordinary bytecode profiling$', r.stderr, re.M)
    if native and not {'hotValue', 'hotRecursive', 'hotConstruct'} <= set(selected):
        raise RuntimeError(f'{name}: native-first selection was not demonstrated: {selected}')
    if expect != bool(promotions):
        raise RuntimeError(f'{name}: promotion gate differs from expected')
    results[name] = {'result': result, 'selected_native_functions': selected,
                     'promotion_indices': list(map(int, promotions))}
if len({r['result']['checksum'] for r in results.values()}) != 1:
    raise RuntimeError('Cross-mode checksum differs')
(out / 'results.json').write_text(json.dumps({'complete': True, 'results': results,
    'runtime_sha256': pi.digest(a.runtime.resolve()),
    'fixture_sha256': pi.digest(lab.ROOT / 'fixtures/native-tiering.mjs'),
    'executable_sha256': pi.digest(exe), 'image_sha256': pi.digest(image)}, indent=2) + '\n')
print(f'PASS: {len(results)} native/JIT/interpreter/threshold cases; native-first and DFG promotion confirmed')
