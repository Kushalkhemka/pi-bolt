#!/usr/bin/env python3
"""Build and validate the checked subset across native/JIT/interpreter and tiering policies."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import lab
import pi

ROOT = Path(__file__).resolve().parent

def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--runtime', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    runtime, out = args.runtime.resolve(), args.output.resolve()
    out.mkdir(parents=True, exist_ok=False)
    (out / 'results.json').write_text(json.dumps({'complete': False}) + '\n')
    source, executable, image = out / 'primitive.mjs', out / 'primitive', out / 'primitive.aot'
    subprocess.run(['node', str(ROOT / 'prepare-fixture.mjs'), str(source), str(out / 'frontend-receipt.json')], check=True, capture_output=True, text=True)
    compiler_hash = sha(runtime)
    env = {**lab.jsc_environment(), **pi.COMMON, 'PI_NATIVE_AOT_LINKED': '1',
           'PI_NATIVE_AOT_OUT': str(image), 'BUN_JSC_numberOfAOTCompilerThreads': '2',
           'BUN_JSC_useSoundTypes': 'true', 'BUN_JSC_reportSoundTypeViolations': 'false'}
    result = subprocess.run([str(runtime), 'build', '--compile', '--bytecode', '--format=esm',
        '--bytecode-order=' + str(lab.ROOT / 'research/pi-workload.order'),
        '--no-compile-autoload-dotenv', '--no-compile-autoload-bunfig', str(source), '--outfile', str(executable)],
        env=env, text=True, capture_output=True)
    (out / 'build.log').write_text(result.stdout + result.stderr)
    if result.returncode or not image.is_file() or not image.stat().st_size:
        raise RuntimeError(f'Checked backend fixture build failed; see {out / "build.log"}')
    results = {}
    for mode in ('jit', 'interpreter', 'native', 'hybrid', 'hybrid-tier32', 'hybrid-tier1000', 'hybrid-tier10000'):
        tier = mode.startswith('hybrid-tier')
        native = mode in ('native', 'hybrid') or tier
        env = {**lab.jsc_environment(), **pi.COMMON,
               'BUN_JSC_useSoundTypes': 'true', 'BUN_JSC_reportSoundTypeViolations': 'false',
               'BUN_JSC_useAOT': str(native).lower(), 'BUN_JSC_useJIT': str(mode != 'native' and mode != 'interpreter').lower(),
               'BUN_JSC_useAOTNativeTiering': str(tier).lower(),
               'BUN_JSC_thresholdForAOTNativeTiering': mode.removeprefix('hybrid-tier') if tier else '32', 'BUN_JSC_useConcurrentJIT': 'false',
               'BUN_JSC_verboseAOTCompilation': 'true', 'BUN_JSC_verboseDiskCache': 'true',
               'EXPECT_TYPED_NATIVE_TIERING': '1' if tier else '0'}
        if native:
            env.update(BUN_JSC_aotImagePath=str(image), BUN_JSC_useAOTMappedImages='true')
        run = subprocess.run([str(executable)], env=env, text=True, capture_output=True, timeout=90)
        (out / (mode + '.log')).write_text(run.stdout + run.stderr)
        if run.returncode:
            raise RuntimeError(f'{mode}: checked fixture failed; inspect {out / (mode + ".log")}')
        outcome = json.loads(run.stdout)
        expected = {'primitive_contract_validation': True, 'scalar_result': 21, 'captured': 6, 'failures': 4, 'checksum': 3910008}
        if any(outcome.get(k) != v for k, v in expected.items()):
            raise RuntimeError(f'{mode}: unexpected checked contract result: {outcome}')
        # There is deliberately no JS implementation/binding of $$t in this fixture.
        # A wrong-type TypeError and correct return values therefore require check_type.
        selected = sorted(set(re.findall(r'^AOT: (scalarAdd|compoundScalar).*? is at ', run.stderr, re.M)))
        if native and not {'scalarAdd', 'compoundScalar'} <= set(selected):
            raise RuntimeError(f'{mode}: native typed functions were not selected: {selected}')
        promotions = list(map(int, re.findall(r'^AOT: tiered function (\d+) to ordinary bytecode profiling$', run.stderr, re.M)))
        if tier and (not promotions or outcome.get('dfg_compiles', 0) <= 0):
            raise RuntimeError('Typed check preservation through native promotion/DFG was not demonstrated')
        if not tier and promotions:
            raise RuntimeError(f'{mode}: unexpected native tiering')
        results[mode] = {'outcome': outcome, 'selected_native_functions': selected, 'promotion_indices': promotions}
    if sha(runtime) != compiler_hash:
        raise RuntimeError('Compiler changed during typed fixture validation')
    report = {'complete': True, 'runtime_sha256': compiler_hash, 'executable_sha256': sha(executable), 'image_sha256': sha(image),
        'source_sha256': sha(source), 'frontend_sha256': sha(ROOT / 'frontend.mjs'), 'modes': results,
        'native_checks_validated': True, 'promotion_and_dfg_guards_validated': True,
        'limits': 'Supported primitive subset only; this fixture does not prove full Pi coverage or whole-TypeScript soundness.'}
    (out / 'results.json').write_text(json.dumps(report, indent=2) + '\n')
    print('PASS: checked primitive contracts in JIT/interpreter/native/hybrid; native-first promotion to DFG preserves guards')


if __name__ == '__main__':
    main()
