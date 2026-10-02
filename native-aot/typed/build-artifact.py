#!/usr/bin/env python3
"""Build a checked-source, erased-source native, or erased-source stable Pi control."""
import argparse
from datetime import datetime, timezone
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
PI_VERSIONS = json.loads((ROOT / 'versions.json').read_text())


def sha(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def build(args):
    runtime, output = args.runtime.resolve(), args.output.resolve()
    package, order = args.pi_package.resolve(), args.bytecode_order.resolve()
    if lab.target() != 'darwin-arm64':
        raise ValueError('This typed comparison first targets native macOS ARM64')
    if args.stable and not args.erased_source_control:
        raise ValueError('Stable Bun lacks this check_type frontend; use --erased-source-control')
    if output.exists():
        raise ValueError(f'Choose a fresh artifact directory: {output}')
    env = lab.jsc_environment()
    # These three research engine settings do not exist in stable Bun 1.4.2.
    # The stable reference must keep its supported default compiler settings.
    if not args.stable:
        env.update(pi.COMMON)
    version = subprocess.check_output([str(runtime), '--version'], env=env, text=True).strip()
    if args.stable and version != '1.4.2':
        raise ValueError(f'Expected stable Bun 1.4.2, got {version}')
    package_data = json.loads((package / 'package.json').read_text())
    pin = PI_VERSIONS.get(package_data['version'])
    if package_data['name'] != '@earendil-works/pi-coding-agent' or pin is None:
        raise ValueError(f'Unsupported checked-source Pi package: {package_data["name"]}@{package_data["version"]}')
    source_before = pi.source_fingerprints(package)
    inputs = [runtime, order, Path(__file__).resolve(), ROOT / 'build-pi-typed.mjs',
              ROOT / 'frontend.mjs', ROOT / 'plugin.mjs', ROOT / 'compatibility.mjs', ROOT / 'versions.mjs', ROOT / 'versions.json',
              lab.ROOT / 'optimizations/sanitize-unicode.js',
              lab.ROOT / 'optimizations/priority-final-render.js', lab.ROOT / 'optimizations/standalone-workers.js']
    if not args.stable:
        inputs += [lab.PATCH, lab.BUN_PATCH]
    before = {str(path): sha(path) for path in inputs}
    output.mkdir(parents=True)
    executable, image, receipt_path = output / 'pi-native', output / 'pi-native.aot', output / 'typed-receipt.json'
    if not args.stable:
        env.update({'PI_NATIVE_AOT_OUT': str(image), 'PI_NATIVE_AOT_MODULE_URL': '/$bunfs/root/pi-native',
                    'PI_NATIVE_AOT_LINKED': '1', 'BUN_JSC_numberOfAOTCompilerThreads': '2',
                    'BUN_JSC_verboseDiskCache': 'true', 'BUN_JSC_aotMapFilePath': str(output / 'image.map'),
                    'BUN_JSC_useSoundTypes': 'true', 'BUN_JSC_reportSoundTypeViolations': 'false'})
    command = [str(runtime), '--no-env-file', str(ROOT / 'build-pi-typed.mjs'),
               '--package', str(package), '--source-root', str(args.source_root.resolve()),
               '--outfile', str(executable), '--bytecode-order', str(order), '--receipt', str(receipt_path),
               '--unicode-fast-path', 'true', '--priority-final-render', 'true',
               '--erased-source-control', str(args.erased_source_control).lower()]
    result = subprocess.run(command, env=env, text=True, capture_output=True)
    (output / 'build.log').write_text(result.stdout + result.stderr)
    if result.returncode or not executable.is_file() or not receipt_path.is_file():
        raise ValueError(f'Typed Pi build failed ({result.returncode}); see {output / "build.log"}')
    if source_before != pi.source_fingerprints(package) or any(sha(Path(path)) != value for path, value in before.items()):
        raise ValueError('Relevant source/compiler/frontend inputs changed during the build')
    receipt = json.loads(receipt_path.read_text())
    checks = receipt['inserted_check_sites']
    if (receipt['original_source_commit'] != pin['source_commit'] or receipt['pi_version'] != package_data['version']
            or receipt['erased_source_control'] != args.erased_source_control):
        raise ValueError('Original-source/control receipt mismatch')
    if receipt['build_driver_sha256'] != before[str(ROOT / 'build-pi-typed.mjs')]:
        raise ValueError('Build-driver receipt mismatch')
    if (args.erased_source_control and checks != 0) or (not args.erased_source_control and checks <= 0):
        raise ValueError('Checked subset was not inserted as requested')
    if not receipt['unicode_fast_path'] or not receipt['priority_final_render']:
        raise ValueError('Missing matching Unicode/final-render transforms')
    expected_workers = 2 if package_data['version'] == '1.0.0' else 0
    if (receipt['worker_specifier_substitutions'] != expected_workers or
            receipt['standalone_workers_optimizer_sha256'] != before[str(lab.ROOT / 'optimizations/standalone-workers.js')]):
        raise ValueError('Shared standalone worker transform receipt mismatch')
    coverage = re.search(r'AOT: sidecar compiled (\d+) of (\d+) functions', result.stderr)
    if not args.stable and (not image.is_file() or not image.stat().st_size or not coverage):
        raise ValueError('Linked native sidecar export was not confirmed')
    assets = pi.application_assets(package)
    for name, target in assets.items():
        if target.exists():
            (output / name).symlink_to(target, target_is_directory=target.is_dir())
    runtime_env = {**({} if args.stable else pi.COMMON), 'PI_PACKAGE_DIR': str(output)}
    if not args.stable:
        runtime_env.update(BUN_JSC_useSoundTypes='true', BUN_JSC_reportSoundTypeViolations='false')
    mode = ('stable Bun bytecode + JIT with original-source erasure' if args.stable else
            'native JSC sidecar with original-source erasure' if args.erased_source_control else
            'native JSC sidecar with explicit primitive original-source checked subset')
    limits = ('Partial primitive contracts only; no complete TypeScript soundness, reference/structural contracts, '
              'or declaration-file assumptions. Matching original-source erasure controls are required. '
              'Relevant frontend/compiler/Pi inputs match before/after; transitive npm modules and symlinked assets are not immutable snapshots. '
              'CLI/provider/worker validation is required before performance claims.')
    data = {'date': datetime.now(timezone.utc).isoformat(), 'target': lab.target(), 'sources': lab.LOCK,
            'mode': mode, 'native_aot': not args.stable, 'pi_version': package_data['version'], 'pi_package': str(package),
            'bun_version': version, 'runtime': str(runtime), 'runtime_sha256': before[str(runtime)],
            'executable': str(executable), 'executable_sha256': sha(executable), 'executable_bytes': executable.stat().st_size,
            'command': command, 'build_env': {key: value for key, value in env.items() if key.startswith(('BUN_JSC_', 'PI_NATIVE_AOT_'))},
            'build_driver_sha256': before[str(ROOT / 'build-pi-typed.mjs')], 'typed_builder_sha256': before[str(Path(__file__).resolve())],
            'input_sources': source_before, 'build_inputs': before,
            'input_sources_validation': 'Relevant original Pi inputs and frontend/compiler match before and after compilation; selected original TS/npm JS pairs independently rehashed by the driver',
            'typed_receipt': str(receipt_path), 'typed_receipt_sha256': sha(receipt_path),
            'typed_frontend_sha256': before[str(ROOT / 'frontend.mjs')], 'typed_plugin_sha256': before[str(ROOT / 'plugin.mjs')],
            'typed_compatibility_sha256': before[str(ROOT / 'compatibility.mjs')],
            'typescript_compiler_path': receipt['typescript_compiler_path'], 'typescript_compiler_sha256': receipt['typescript_compiler_sha256'],
            'original_source_commit': pin['source_commit'], 'erased_source_control': args.erased_source_control,
            'typed_version_registry_sha256': before[str(ROOT / 'versions.json')], 'entrypoints': receipt['entrypoints'],
            'entry_naming': receipt['entry_naming'], 'worker_specifier_substitutions': receipt['worker_specifier_substitutions'],
            'standalone_workers_optimizer_sha256': receipt['standalone_workers_optimizer_sha256'],
            'typescript_type_annotations_preserved': False, 'typescript_checked_subset': not args.erased_source_control,
            'typed_subset_schema': receipt['schema'], 'inserted_check_sites': checks, 'typed_modules': len(receipt['modules']),
            'generic_modules': len(receipt['generic_modules']), 'unicode_fast_path': True,
            'unicode_optimizer_sha256': before[str(lab.ROOT / 'optimizations/sanitize-unicode.js')],
            'priority_final_render': True, 'priority_final_render_optimizer_sha256': before[str(lab.ROOT / 'optimizations/priority-final-render.js')],
            'bytecode_order': str(order), 'bytecode_order_sha256': before[str(order)], 'exec_argv': [], 'smol_baked': False,
            'compiler_option_comparison_limit': 'Stable Bun 1.4.2 does not expose the native research engine COMMON options; source selection/erasure/driver/transforms match, compiler engine options differ' if args.stable else None,
            'common_runtime_env': runtime_env, 'assets_are_symlinks_to_pi_package': True,
            'assets': {name: str(path) for name, path in assets.items() if path.exists()},
            'interactive_execution_verified': False, 'pi_execution_verified': False, 'provenance_limit': limits}
    if args.stable:
        runtime_env['PI_OFFLINE'] = '1'
        probe = subprocess.check_output([str(executable), '--version'], env={**lab.jsc_environment(), **runtime_env}, text=True).strip()
        if probe != package_data['version']:
            raise ValueError('Stable Pi version mismatch')
        data['native_host_version_verified'] = True
        variants = {'stable_erased_default': {'command': [str(executable)], 'env': runtime_env},
                    'stable_erased_gc2': {'command': [str(executable)], 'env': {**runtime_env, 'BUN_JSC_numberOfGCMarkers': '2'}}}
        (output / 'variants.json').write_text(json.dumps(variants, indent=2) + '\n')
    else:
        data.update({'image': str(image), 'image_sha256': sha(image), 'image_bytes': image.stat().st_size,
                     'backend_patch_sha256': before[str(lab.PATCH)], 'bun_patch_sha256': before[str(lab.BUN_PATCH)],
                     'aot_pipeline': 'linked', 'aot_compilation': {'compiled': int(coverage[1]), 'considered': int(coverage[2])},
                     'static_heap': False, 'prelinked_graph_version': 4, 'native_call_linking': 'dynamic',
                     'internal_modules_aot': True, 'inline_loop_fast_paths': False, 'native_hot_layout': False,
                     'skip_trained_hot': False, 'native_skipped_hot_jobs': 0, 'native_version_verified': False})
    (output / 'manifest.json').write_text(json.dumps(data, indent=2) + '\n')
    print(f'Built {mode}: {output}; typed modules {data["typed_modules"]}, inserted check sites {checks}; functional validation pending')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--runtime', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--pi-package', type=Path, default=Path('/opt/homebrew/lib/node_modules/@earendil-works/pi-coding-agent'))
    parser.add_argument('--source-root', type=Path, default=lab.SOURCES / 'pi-v0.85.1')
    parser.add_argument('--bytecode-order', type=Path, default=lab.ROOT / 'research/pi-workload.order')
    parser.add_argument('--erased-source-control', action='store_true')
    parser.add_argument('--stable', action='store_true')
    args = parser.parse_args()
    try:
        build(args)
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        parser.exit(1, f'error: {error}\n')


if __name__ == '__main__':
    main()
