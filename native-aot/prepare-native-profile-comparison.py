#!/usr/bin/env python3
"""Verify fresh artifacts and emit fair direct-exec configs; never execute Pi."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
from input_provenance import fingerprint, verify_unchanged

ROOT = Path(__file__).resolve().parent
PI_ALIGNMENT = ('pi_version', 'target', 'pi_package', 'build_driver_sha256',
                'unicode_fast_path', 'unicode_optimizer_sha256', 'bytecode_order_sha256', 'input_sources')
ENGINE_ALIGNMENT = ('runtime_sha256', 'backend_patch_sha256', 'bun_patch_sha256',
                    'aot_pipeline', 'static_heap', 'inline_loop_fast_paths',
                    'prelinked_graph_version', 'native_call_linking', 'internal_modules_aot', 'sources')
FEATURES = ('native_hot_layout', 'skip_trained_hot', 'priority_final_render')


def load_artifact(directory, native):
    directory = directory.resolve()
    manifest = directory / 'manifest.json'
    data = json.loads(manifest.read_text())
    hashes = {str(manifest): fingerprint(manifest)}
    for key in (('executable', 'image') if native else ('executable',)):
        path = Path(data[key]).resolve()
        if path.parent != directory:
            raise ValueError(f'{manifest}: {key} is outside artifact directory')
        actual = hashes[str(path)] = fingerprint(path)
        if actual['sha256'] != data[key + '_sha256']:
            raise ValueError(f'{path}: recorded digest differs')
    if not data.get('input_sources'):
        raise ValueError(f'{manifest}: missing original Pi source snapshot')
    verify_unchanged(data['input_sources'])
    for key, path in (('build_driver_sha256', ROOT / 'build-pi.js'),
                      ('unicode_optimizer_sha256', ROOT / 'optimizations/sanitize-unicode.js'),
                      ('bytecode_order_sha256', ROOT / 'research/pi-workload.order')):
        if data.get(key) != fingerprint(path)['sha256']:
            raise ValueError(f'{manifest}: current {key} differs from build record')
    if native:
        for key in ('native_version_verified', 'pi_execution_verified', 'hybrid_version_verified',
                    'prelinked_graph_verified', 'file_mapping_verified'):
            if data.get(key) is not True:
                raise ValueError(f'{manifest}: {key} is not true')
        if data.get('aot_pipeline') != 'linked' or data.get('static_heap') is not False:
            raise ValueError(f'{manifest}: expected heap-independent linked native image')
        rpc_path = Path(data['rpc_validation']).resolve()
        if rpc_path.parent != directory:
            raise ValueError(f'{manifest}: RPC validation belongs to another artifact')
        rpc = json.loads(rpc_path.read_text())
        samples = rpc.get('samples', [])
        required = {'pi_native', 'pi_hybrid', 'pi_jit'}
        if not samples or not required <= {s.get('variant') for s in samples}:
            raise ValueError(f'{rpc_path}: missing native/hybrid/JIT RPC checks')
        for sample in samples:
            if sample.get('passed') is False or sample.get('requests') != sample.get('turns', 0) * 2 or sample.get('read_tools') != sample.get('turns') or not sample.get('reply_sha256'):
                raise ValueError(f'{rpc_path}: unsuccessful RPC sample')
            old_hashes = rpc.get('artifacts', {}).get(sample['variant'], {})
            for key in (('executable', 'image') if rpc['configs'][sample['variant']]['env'].get('BUN_JSC_useAOT') == 'true' else ('executable',)):
                path = data[key]
                if old_hashes.get(path, {}).get('sha256') != data[key + '_sha256']:
                    raise ValueError(f'{rpc_path}: checked {key} differs from current artifact')
        hashes[str(rpc_path)] = fingerprint(rpc_path)
    else:
        if data.get('native_host_version_verified') is not True or data.get('bun_version') != '1.4.2' or data.get('native_aot') is not False:
            raise ValueError(f'{manifest}: expected verified stable Bun 1.4.2 bytecode artifact')
    if data.get('priority_final_render'):
        if data.get('priority_final_render_optimizer_sha256') != fingerprint(ROOT / 'optimizations/priority-final-render.js')['sha256']:
            raise ValueError(f'{manifest}: priority transform hash differs')
    return data, hashes


def equal_fields(label, data, reference, fields):
    for key in fields:
        if data.get(key) != reference.get(key):
            raise ValueError(f'{label}: {key} differs from control')


def native_config(data, mode, gc_markers=2):
    if mode not in ('native', 'hybrid', 'jit', 'interpreter') or gc_markers not in (1, 2):
        raise ValueError('Unsupported direct runtime configuration')
    env = {**data['common_runtime_env'], 'PI_OFFLINE': '1', 'BUN_JSC_numberOfGCMarkers': str(gc_markers),
           'BUN_JSC_useAOT': str(mode in ('native', 'hybrid')).lower(), 'BUN_JSC_useJIT': str(mode in ('hybrid', 'jit')).lower()}
    if mode in ('native', 'hybrid'):
        env.update(BUN_JSC_useAOTMappedImages='true', BUN_JSC_aotImagePath=data['image'])
    return {'command': [data['executable']], 'env': env}


def prepare(directories, include_selective=False, omit_hot=False, include_selective_priority=False):
    manifests, provenance, configs, omitted = {}, {}, {}, {}
    native_labels = ('control', 'priority') + (() if omit_hot else ('hot',)) + (('selective',) if include_selective else ()) + (('selective_priority',) if include_selective_priority else ())
    for label in native_labels + ('stable', 'stable_priority', 'stable_priority_smol'):
        data, hashes = load_artifact(directories[label], label in native_labels)
        manifests[label], provenance[label] = data, hashes
    if omit_hot:
        omitted['hot'] = 'Explicitly omitted; no pure physical image-order claim is made.'
    if not include_selective_priority:
        omitted['selective_priority'] = 'Combined selective/priority hybrid excluded unless explicitly requested and verified.'
    if not include_selective:
        omitted['selective'] = 'Excluded by default: selective hybrid export failed runtime validation; --include-selective requires verified native/hybrid/JIT RPC artifacts.'
    control = manifests['control']
    for label, data in manifests.items():
        equal_fields(label, data, control, PI_ALIGNMENT)
        expected_priority = label in ('priority', 'selective_priority', 'stable_priority', 'stable_priority_smol')
        if data.get('priority_final_render') is not expected_priority:
            raise ValueError(f'{label}: unexpected priority transform setting')
        if label in native_labels:
            equal_fields(label, data, control, ENGINE_ALIGNMENT)
            env = {k: v for k, v in data['common_runtime_env'].items() if k != 'PI_PACKAGE_DIR'}
            reference = {k: v for k, v in control['common_runtime_env'].items() if k != 'PI_PACKAGE_DIR'}
            if env != reference:
                raise ValueError(f'{label}: common runtime options differ')
            if data.get('native_hot_layout') is not (label == 'hot') or data.get('skip_trained_hot') is not (label in ('selective', 'selective_priority')):
                raise ValueError(f'{label}: unexpected native feature settings')
            for mode in (('hybrid',) if label in ('selective', 'selective_priority') else ('native', 'hybrid') if label == 'hot' else ('native', 'hybrid', 'jit')):
                configs[f'{label}_{mode}'] = native_config(data, mode)
            if label == 'priority':
                configs['priority_native_gc1'] = native_config(data, 'native', 1)
                configs['priority_interpreter'] = native_config(data, 'interpreter')
        else:
            equal_fields(label, data, manifests['stable'], ('runtime_sha256', 'bun_version', 'stable_builder_sha256'))
            expected_smol = label == 'stable_priority_smol'
            if data.get('smol_baked') is not expected_smol or data.get('exec_argv') != (['--smol'] if expected_smol else []):
                raise ValueError(f'{label}: unexpected baked runtime arguments')
            env = {**data['common_runtime_env'], 'PI_OFFLINE': '1'}
            if expected_smol:
                configs[label] = {'command': [data['executable']], 'env': env}
            else:
                configs[label + '_default'] = {'command': [data['executable']], 'env': env}
                configs[label + '_gc2'] = {'command': [data['executable']], 'env': {**env, 'BUN_JSC_numberOfGCMarkers': '2'}}
    metadata = {'date': datetime.now(timezone.utc).isoformat(), 'generator_sha256': fingerprint(__file__)['sha256'],
        'configs': configs, 'artifacts': provenance, 'artifact_manifests': manifests, 'omitted': omitted,
        'alignment': {key: control.get(key) for key in PI_ALIGNMENT + ENGINE_ALIGNMENT},
        'interpretation': [
            'Direct executable startup excludes shell/Python launchers. Native rows use GC2 except explicit priority_native_gc1.',
            'Compare control native/hybrid against stable GC2, and priority native/hybrid against stable priority GC2 for matching Pi transforms.',
            'Control versus priority within one runtime measures the Pi final-render change; that change is not an AOT gain.',
            'Priority native GC1 shares the priority executable and changes only the GC marker limit. Priority interpreter uses that executable with AOT/JIT both disabled and GC2.',
            'Native/hybrid/JIT within one artifact share their executable and Pi transform, isolating runtime mode.',
            'Hot versus control uses one compiler and identical Pi build settings but distinct images; require the stricter image layout guard before an image-order-only claim.',
            'Stable default and baked --smol are additional runtime controls, not equal GC configurations.',
            'Native and stable Bun use distinct compiler binaries and native common engine flags; cross-runtime differences are combined configuration effects.',
            'Interactive correctness gates must pass before timing; this preparation script requires native loader and RPC verification, not a CLI functional flag.',
            'Use one randomized paired cohort per workload and the corrected visible-idle-editor-v2 endpoint; never pool historical final-text wall samples.'
        ], 'limits': [
            'Source snapshot phase depends on the supplied artifacts, not their row labels. Native input_sources_validation records before/after versus post-build-only checks; stable builder metadata records its checked source/build inputs.',
            'A post-build-only snapshot does not prove unchanged inputs during export. Before/after checks cover the recorded relevant sources, not the complete transitive npm tree or mutable symlinked assets.',
            'Stable 1.4.2 is a pinned market control; this matrix cannot establish superiority over every Bun release/configuration or another application benchmark.'
        ]}
    return configs, metadata


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    defaults = {'control': 'builtin-profile-control', 'hot': 'native-hot-layout', 'selective': 'selective-hybrid',
                'priority': 'priority-final-render', 'selective_priority': 'selective-priority', 'stable': 'stable-control', 'stable_priority': 'stable-priority',
                'stable_priority_smol': 'stable-priority-smol'}
    for label, suffix in defaults.items():
        parser.add_argument('--' + label.replace('_', '-'), type=Path, default=ROOT / ('artifacts/darwin-arm64-m5-' + suffix))
    parser.add_argument('--omit-hot', action='store_true', help='Omit hot layout rows when no same-compiler validated artifact is available')
    parser.add_argument('--include-selective-priority', action='store_true', help='Require verified combined selective/priority artifact and add hybrid row')
    parser.add_argument('--include-selective', action='store_true', help='Require verified selective artifact; otherwise omit it')
    parser.add_argument('--output', type=Path, default=ROOT / 'research/native-profile-comparison-variants.json')
    args = parser.parse_args()
    configs, metadata = prepare({label: getattr(args, label) for label in defaults}, args.include_selective, args.omit_hot, args.include_selective_priority)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(configs, indent=2) + '\n')
    metadata['variants_sha256'] = fingerprint(args.output)['sha256']
    meta = args.output.with_name(args.output.stem + '-provenance.json')
    meta.write_text(json.dumps(metadata, indent=2) + '\n')
    print(f'PASS: verified {len(configs)} direct-exec rows; wrote {args.output} and {meta}')
    print('Rows: ' + ' '.join(configs))


if __name__ == '__main__':
    main()
