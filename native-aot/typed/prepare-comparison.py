#!/usr/bin/env python3
"""Verify checked/erased source controls and emit ten direct comparison configurations."""
import argparse
import copy
import json
from pathlib import Path
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import lab
import pi

BASE = lab.ROOT / 'artifacts'


def load(directory):
    data = json.loads((directory / 'manifest.json').read_text())
    for key in ('executable', 'image', 'typed_receipt'):
        if key in data and pi.digest(Path(data[key])) != data[key + '_sha256']:
            raise ValueError(f'{directory}: changed {key}')
    receipt = json.loads(Path(data['typed_receipt']).read_text())
    return data, receipt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--checked', type=Path, default=BASE / 'darwin-arm64-m5-checked-types-priority')
    parser.add_argument('--erased', type=Path, default=BASE / 'darwin-arm64-m5-erased-types-priority')
    parser.add_argument('--stable', type=Path, default=BASE / 'darwin-arm64-m5-stable-erased-types-priority')
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--verify-versions', action='store_true')
    parser.add_argument('--tiering-threshold', type=int, default=1000)
    parser.add_argument('--compact', action='store_true', help='Six rows: checked native/hybrid/tiered, erased hybrid/tiered, stable erased GC2')
    parser.add_argument('--full-cli-layouts', action='store_true', help='Preserve builtin extensions/default tools and add regular TUI duplicates')
    args = parser.parse_args()
    if args.tiering_threshold <= 0:
        parser.error('--tiering-threshold must be positive')
    rows = [load(directory.resolve()) for directory in (args.checked, args.erased, args.stable)]
    fields = ('pi_version', 'pi_package', 'target', 'original_source_commit', 'build_driver_sha256',
              'typed_frontend_sha256', 'typed_plugin_sha256', 'typed_compatibility_sha256', 'typed_version_registry_sha256',
              'entrypoints', 'entry_naming', 'worker_specifier_substitutions', 'standalone_workers_optimizer_sha256',
              'typescript_compiler_sha256',
              'input_sources', 'unicode_optimizer_sha256', 'priority_final_render_optimizer_sha256', 'bytecode_order_sha256')
    for field in fields:
        if any(data.get(field) != rows[0][0].get(field) for data, _ in rows[1:]):
            raise ValueError(f'Checked/erased/stable control mismatch: {field}')
    def modules(receipt):
        return sorted(receipt['modules'], key=lambda row: row['installed_path'])
    if any(modules(receipt) != modules(rows[0][1]) for _, receipt in rows[1:]):
        raise ValueError('Original-source matched module inputs/checked contracts differ across controls')
    if any(receipt['generic_modules'] != rows[0][1]['generic_modules'] for _, receipt in rows[1:]):
        # Build traversal order is not semantically meaningful.
        canonical = lambda r: sorted(r['generic_modules'], key=lambda row: row['installed_path'])
        if any(canonical(receipt) != canonical(rows[0][1]) for _, receipt in rows[1:]):
            raise ValueError('Generic fallback selection differs across controls')
    for index, (data, receipt) in enumerate(rows):
        if data['typescript_checked_subset'] != (index == 0) or receipt['erased_source_control'] != (index != 0):
            raise ValueError('Checked/erased mode receipt mismatch')
        if not data['unicode_fast_path'] or not data['priority_final_render']:
            raise ValueError('Missing matching render/Unicode transform')
        if index < 2 and (not data.get('native_version_verified') or not data.get('hybrid_version_verified')):
            raise ValueError('Verify native/hybrid execution before preparing comparison')
    if rows[0][0]['runtime_sha256'] != rows[1][0]['runtime_sha256']:
        raise ValueError('Checked/erased native compilers differ')
    variants = {}
    tier_suffix = f'tier{args.tiering_threshold}'
    for name, (data, _) in zip(('checked', 'erased'), rows[:2]):
        for suffix, mode in (('native', 'native-balanced'), ('hybrid', 'hybrid'), (tier_suffix, 'hybrid'), ('jit_gc2', 'jit-balanced')):
            if args.compact and ((name == 'erased' and suffix == 'native') or suffix == 'jit_gc2'):
                continue
            config = pi.configuration(data, mode)
            config['env'].update(BUN_JSC_useSoundTypes='true', BUN_JSC_reportSoundTypeViolations='false',
                                 BUN_JSC_useAOTNativeTiering=str(suffix == tier_suffix).lower())
            if suffix == tier_suffix:
                config['env']['BUN_JSC_thresholdForAOTNativeTiering'] = str(args.tiering_threshold)
            variants[name + '_' + suffix] = config
    stable = rows[2][0]
    if any(key.startswith(('BUN_JSC_useSoundTypes', 'BUN_JSC_reportSoundTypeViolations', 'PI_NATIVE_AOT_')) for key in stable['common_runtime_env']):
        raise ValueError('Stable control contains unsupported native/type flags')
    if not args.compact:
        variants['stable_erased_default'] = {'command': [stable['executable']], 'env': stable['common_runtime_env'].copy()}
    variants['stable_erased_gc2'] = {'command': [stable['executable']], 'env': {**stable['common_runtime_env'], 'BUN_JSC_numberOfGCMarkers': '2'}}
    def cli_layouts(configurations):
        if not args.full_cli_layouts:
            return configurations
        enriched = {}
        for name, configuration in configurations.items():
            fullscreen = copy.deepcopy(configuration)
            fullscreen['env']['PI_OFFLINE'] = '1'
            fullscreen.update(expected_tui_mode='fullscreen', test_tui_mode_switch=True,
                              keep_builtin_extensions=True, use_default_tools=True)
            enriched[name] = fullscreen
            regular = copy.deepcopy(fullscreen)
            regular['command'].extend(['--tui-mode', 'regular'])
            regular['expected_tui_mode'] = 'regular'
            enriched[name + '_regular'] = regular
        return enriched
    variants = cli_layouts(variants)
    versions = {}
    if args.verify_versions:
        for name, config in variants.items():
            result = subprocess.run([*config['command'], '--version'], env={**lab.jsc_environment(), **config['env']},
                                    text=True, capture_output=True, timeout=60)
            if result.returncode or result.stdout.strip() != stable['pi_version']:
                raise ValueError(f'{name}: version probe failed: {result.stderr[-2000:]}')
            versions[name] = result.stdout.strip()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    if any(args.output.with_suffix(suffix).exists() for suffix in ('.json', '.provenance.json', '.functional.json')):
        raise ValueError('Choose fresh comparison/provenance paths')
    args.output.write_text(json.dumps(variants, indent=2) + '\n')
    interpreter = pi.configuration(rows[0][0], 'interpreter')
    interpreter['env'].update(BUN_JSC_useSoundTypes='true', BUN_JSC_reportSoundTypeViolations='false',
                              BUN_JSC_useAOTNativeTiering='false')
    args.output.with_suffix('.functional.json').write_text(json.dumps({**variants, **cli_layouts({'checked_interpreter': interpreter})}, indent=2) + '\n')
    provenance = {'artifact_manifests': {name: str(path.resolve() / 'manifest.json') for name, path in
                    zip(('checked', 'erased', 'stable'), (args.checked, args.erased, args.stable))},
                  'versions': versions, 'source_controls_match': True, 'rows': list(variants),
                  'native_tiering_threshold': args.tiering_threshold,
                  'full_cli_layouts': args.full_cli_layouts,
                  'limitations': ['Primitive original-source subset only; unsupported contracts remain generic.',
                      'Checked/erased custom engine settings match; stable1.4.2 lacks the three COMMON research compiler options and sound-type options.',
                      'This is a separate typed-driver cohort and cannot be pooled with the standard npm build-driver cohort.',
                      'Native/hybrid versions verified; real CLI/provider/extension/worker/interactive gates are required before performance claims.',
                      'Profile reflects the generic Pi workload, not freshly trained for inserted guard bytecode.']}
    args.output.with_suffix('.provenance.json').write_text(json.dumps(provenance, indent=2) + '\n')
    print(f'Prepared {len(variants)} matching source-control variants; {len(versions)} versions verified')


if __name__ == '__main__':
    main()
