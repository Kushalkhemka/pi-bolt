#!/usr/bin/env python3
"""Verify existing artifacts and write direct-exec comparison configs; never run Pi."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SCREENING = ('full_native_gc2', 'full_hybrid_gc2', 'full_jit_gc2',
             'stack_native_gc2', 'stack_hybrid_gc2', 'jsc_native_gc2',
             'jsc_hybrid_gc2', 'stable_gc2')
ALIGNMENT = ('pi_version', 'target', 'unicode_fast_path', 'unicode_optimizer_sha256',
             'bytecode_order_sha256', 'aot_pipeline', 'static_heap', 'inline_loop_fast_paths')


def fingerprint(path):
    path = Path(path)
    with path.open('rb') as stream:
        sha = hashlib.file_digest(stream, 'sha256').hexdigest()
    return {'sha256': sha, 'bytes': path.stat().st_size}


def artifact(directory):
    directory = directory.resolve()
    manifest_path = directory / 'manifest.json'
    data = json.loads(manifest_path.read_text())
    hashes = {str(manifest_path): fingerprint(manifest_path)}
    for key in ('executable', 'image'):
        path = Path(data[key]).resolve()
        if path.parent != directory:
            raise ValueError(f'{manifest_path}: {key} belongs to a different directory')
        hashes[str(path)] = actual = fingerprint(path)
        if actual['sha256'] != data[key + '_sha256']:
            raise ValueError(f'{path}: digest differs from the artifact manifest')
    for flag in ('native_version_verified', 'pi_execution_verified'):
        if data.get(flag) is not True:
            raise ValueError(f'{manifest_path}: {flag} is not true')
    if data.get('static_heap') is not False or data.get('aot_pipeline') != 'linked':
        raise ValueError(f'{manifest_path}: expected a heap-independent linked sidecar')
    return data, hashes


def runtime_config(data, mode):
    env = dict(data['common_runtime_env'])
    env.update(PI_OFFLINE='1', BUN_JSC_numberOfGCMarkers='2',
               BUN_JSC_useJIT=str(mode != 'native').lower(),
               BUN_JSC_useAOT=str(mode != 'jit').lower())
    if mode != 'jit':
        env.update(BUN_JSC_useAOTMappedImages='true', BUN_JSC_aotImagePath=data['image'])
    return {'command': [data['executable']], 'env': env}


def prepare(candidate, stack, jsc, stable, smol):
    manifests, provenance, configs = {}, {}, {}
    for label, directory in (('full', candidate), ('stack', stack), ('jsc', jsc)):
        data, hashes = artifact(directory)
        manifests[label], provenance[label] = data, hashes
        for mode in ('native', 'hybrid', 'jit'):
            configs[f'{label}_{mode}_gc2'] = runtime_config(data, mode)
    reference = manifests['full']
    for label, data in manifests.items():
        for key in ALIGNMENT:
            if data.get(key) != reference.get(key):
                raise ValueError(f'{label}: {key} differs from the final candidate')
        for source in ('bun', 'webkit'):
            if data['sources'][source]['commit'] != reference['sources'][source]['commit']:
                raise ValueError(f'{label}: upstream {source} source commit differs')
        expected = {k: v for k, v in reference['common_runtime_env'].items() if k != 'PI_PACKAGE_DIR'}
        actual = {k: v for k, v in data['common_runtime_env'].items() if k != 'PI_PACKAGE_DIR'}
        if actual != expected:
            raise ValueError(f'{label}: common engine settings differ')
    for label, directory in (('stable', stable), ('smol', smol)):
        directory = directory.resolve()
        executable = directory / 'pi-native'
        package = directory / 'package.json'
        if json.loads(package.read_text())['version'] != reference['pi_version']:
            raise ValueError(f'{directory}: stable Pi package version differs')
        provenance[label] = {str(executable): fingerprint(executable), str(package): fingerprint(package)}
        log = directory / 'build.log'
        if log.exists():
            provenance[label][str(log)] = fingerprint(log)
        env = {'PI_PACKAGE_DIR': str(directory), 'PI_OFFLINE': '1'}
        if label == 'stable':
            configs['stable_default'] = {'command': [str(executable)], 'env': env}
            configs['stable_gc2'] = {'command': [str(executable)],
                                     'env': {**env, 'BUN_JSC_numberOfGCMarkers': '2'}}
        else:
            configs['stable_smol_baked'] = {'command': [str(executable)], 'env': env}
    metadata = {
        'date': datetime.now(timezone.utc).isoformat(),
        'generator_sha256': fingerprint(__file__)['sha256'],
        'screening_rows': list(SCREENING), 'configs': configs, 'artifacts': provenance,
        'native_alignment': {key: reference.get(key) for key in ALIGNMENT},
        'native_source_commits': {key: reference['sources'][key]['commit'] for key in ('bun', 'webkit')},
        'native_build_identity': {label: {key: data.get(key) for key in
            ('runtime_sha256', 'backend_patch_sha256', 'bun_patch_sha256', 'build_driver_sha256', 'aot_compilation')}
            for label, data in manifests.items()},
        'interpretation': [
            'All rows execute their existing binary directly; the shell/Python launcher is excluded.',
            'GC2 rows have the same marker limit, Pi version, Unicode transform, trained order and offline behavior.',
            'full native/hybrid/jit share one executable: they isolate runtime mode within the final compiler build.',
            'Cross-build rows include compiler/runtime revisions as well as builtin coverage; do not attribute all changes to one feature.',
            'Stable ordered/smol artifacts have no native manifest: their exact bytes and available build log are recorded, not reconstructed.',
            'Hashes are of standalone executables and their matching sidecars; overwritten compiler binaries are not executed.',
            'Use one randomized paired-round cohort per workload; do not pool independent old/new benchmark batches.'
        ]}
    return configs, metadata


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--candidate', type=Path, default=ROOT / 'artifacts/darwin-arm64-m5-builtin-coverage')
    parser.add_argument('--stack', type=Path, default=ROOT / 'artifacts/darwin-arm64-m5-stack-fix')
    parser.add_argument('--jsc', type=Path, default=ROOT / 'artifacts/darwin-arm64-m5-jsc-builtin-candidate')
    parser.add_argument('--stable', type=Path, default=ROOT / 'artifacts/darwin-arm64-stable-ordered')
    parser.add_argument('--smol', type=Path, default=ROOT / 'artifacts/darwin-arm64-stable-smol-baked')
    parser.add_argument('--output', type=Path, default=ROOT / 'research/builtin-comparison-variants.json')
    args = parser.parse_args()
    configs, metadata = prepare(args.candidate, args.stack, args.jsc, args.stable, args.smol)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(configs, indent=2) + '\n')
    metadata_path = args.output.with_name(args.output.stem + '-provenance.json')
    metadata['variants_sha256'] = fingerprint(args.output)['sha256']
    metadata_path.write_text(json.dumps(metadata, indent=2) + '\n')
    print(f'PASS: verified {len(configs)} direct-exec rows; wrote {args.output} and {metadata_path}')
    print('Screening rows: ' + ' '.join(SCREENING))


if __name__ == '__main__':
    main()
