#!/usr/bin/env python3
"""Build and inspect a Pi executable with an experimental native JSC sidecar."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import sys

import lab

ARTIFACTS = Path(os.environ.get('PI_NATIVE_ARTIFACT_DIR', lab.ROOT / 'artifacts' / lab.target())).resolve()
COMMON = {f'BUN_JSC_{name}': 'true' for name in (
    'resolveAllScopeSlotsStatically', 'evaluateObjectLiteralValuesFirst', 'definePlainInstanceFieldsInConstructor')}
MODES = ('native', 'native-low-cpu', 'native-balanced', 'hybrid', 'jit', 'jit-balanced', 'interpreter')


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def source_fingerprints(package):
    paths = ('package.json', 'dist/bun/cli.js', 'dist/utils/image-resize-worker.js',
             'dist/modes/interactive/interactive-mode.js',
             'node_modules/@earendil-works/pi-ai/dist/utils/sanitize-unicode.js',
             'node_modules/@earendil-works/pi-tui/dist/tui.js',
             'node_modules/@earendil-works/pi-tui/dist/components/editor.js')
    # Pi 1.0 adds a codemode worker/WASM and TUI-owned native helpers. Keep the
    # original 0.85.1 fingerprint set stable; inventory these additional inputs
    # when present, including creation/removal across the before/after check.
    if json.loads((package / 'package.json').read_text())['version'] == '1.0.0':
        optional = ('dist/config.js', 'dist/bun/runtime-setup.js', 'dist/bun/sandbox-env-setup.js',
                    'dist/bun/restore-sandbox-env.js', 'dist/extensions/codemode/worker.js',
                    'dist/extensions/codemode/execute.js', 'dist/utils/image-resize.js',
                    'dist/utils/image-resize-core.js', 'dist/utils/image-process.js', 'dist/utils/photon.js',
                    'node_modules/@silvia-odwyer/photon-node/photon_rs.js',
                    'node_modules/@earendil-works/pi-codemode/package.json',
                    'node_modules/@earendil-works/pi-codemode/dist/wasm.js',
                    'node_modules/@earendil-works/pi-codemode/dist/runtime/host.js',
                    'node_modules/@earendil-works/pi-codemode/dist/runtime/worker.js',
                    'node_modules/@earendil-works/pi-codemode/dist/runtime/protocol.js',
                    'node_modules/quickjs-wasi/package.json', 'node_modules/quickjs-wasi/quickjs.wasm',
                    'node_modules/@earendil-works/pi-tui/package.json',
                    'node_modules/@earendil-works/pi-tui/dist/native-platform.js',
                    'node_modules/@earendil-works/pi-tui/dist/native-module-path.js')
        paths += tuple(path for path in optional if (package / path).is_file())
        native = package / 'node_modules/@earendil-works/pi-tui/native'
        paths += tuple(str(path.relative_to(package)) for path in sorted(native.rglob('*.node')) if path.is_file())
    return {str(package / path): {'sha256': digest(package / path),
                                  'bytes': (package / path).stat().st_size} for path in paths}


def application_assets(package):
    assets = {'package.json': package / 'package.json', 'theme': package / 'dist/modes/interactive/theme',
              'assets': package / 'dist/modes/interactive/assets', 'export-html': package / 'dist/core/export-html',
              'docs': package / 'docs', 'examples': package / 'examples', 'README.md': package / 'README.md',
              'CHANGELOG.md': package / 'CHANGELOG.md',
              'photon_rs_bg.wasm': package / 'node_modules/@silvia-odwyer/photon-node/photon_rs_bg.wasm'}
    if json.loads((package / 'package.json').read_text())['version'] == '1.0.0':
        assets['native'] = package / 'node_modules/@earendil-works/pi-tui/native'
        if not assets['native'].is_dir():
            raise ValueError('Missing Pi 1.0 native terminal helpers')
    return assets


def build(args):
    if lab.target() not in lab.LOCK['experimental_native_targets']:
        raise RuntimeError('Native AOT requires the ARM64 prototype; Linux x64 needs a runtime-stub port. Use linux.py for the Linux baseline.')
    runtime = Path(args.runtime).resolve() if args.runtime else lab.SOURCES / 'bun/build/release-local/bun'
    if args.pipeline == 'source' and args.bytecode_order:
        raise RuntimeError('--bytecode-order requires --pipeline linked; source export cannot identify linked payloads.')
    if args.pipeline == 'linked' and not args.bytecode_order:
        raise RuntimeError('Linked AOT requires --bytecode-order with a recorded Pi profile.')
    if args.native_hot_layout and args.pipeline != 'linked':
        raise RuntimeError('--native-hot-layout requires the linked sidecar pipeline.')
    if args.skip_trained_hot and args.pipeline != 'linked':
        raise RuntimeError('--skip-trained-hot requires the linked sidecar pipeline.')
    package = Path(args.pi_package).resolve()
    manifest = json.loads((package / 'package.json').read_text())
    input_sources = source_fingerprints(package)
    build_inputs = {str(path): digest(path) for path in [runtime, lab.ROOT / 'build-pi.js',
                    lab.ROOT / 'optimizations/standalone-workers.js',
                    lab.ROOT / 'optimizations/sanitize-unicode.js',
                    lab.ROOT / 'optimizations/priority-final-render.js', lab.PATCH, lab.BUN_PATCH]}
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    executable, image = ARTIFACTS / 'pi-native', ARTIFACTS / 'pi-native.aot'
    if executable.exists() or image.exists():
        raise RuntimeError(f'Artifacts already exist in {ARTIFACTS}; choose a fresh build directory before rebuilding.')
    env = lab.jsc_environment()
    env.update(COMMON)
    if args.inline_loop_fast_paths:
        env['BUN_JSC_useAOTInlineFastPathsInLoops'] = 'true'
    if args.native_hot_layout:
        env['BUN_JSC_useAOTTrainedLayout'] = 'true'
    if args.skip_trained_hot:
        env['BUN_JSC_skipAOTTrainedHotFunctions'] = 'true'
    env.update({'PI_NATIVE_AOT_OUT': str(image), 'PI_NATIVE_AOT_MODULE_URL': '/$bunfs/root/pi-native',
                'BUN_JSC_verboseDiskCache': 'true', 'BUN_JSC_aotMapFilePath': str(ARTIFACTS / 'image.map')})
    if args.pipeline == 'linked':
        env['PI_NATIVE_AOT_LINKED'] = '1'
        env['BUN_JSC_numberOfAOTCompilerThreads'] = '2'
    command = [str(runtime), '--no-env-file', str(lab.ROOT / 'build-pi.js'),
               str(package), str(executable), str(not args.no_unicode_fast_path).lower(), '',
               str(Path(args.bytecode_order).resolve()) if args.bytecode_order else '', '',
               str(args.priority_final_render).lower()]
    result = subprocess.run(command, env=env, text=True, capture_output=True)
    log = ARTIFACTS / 'build.log'
    log.write_text(result.stdout + result.stderr)
    if result.returncode or not image.is_file() or image.stat().st_size == 0:
        raise RuntimeError(f'Pi native image build failed ({result.returncode}); inspect {log}')
    if source_fingerprints(package) != input_sources:
        raise RuntimeError('Relevant Pi source inputs changed during compilation; rebuild in a fresh directory.')
    if any(digest(Path(name)) != expected for name, expected in build_inputs.items()):
        raise RuntimeError('Compiler/driver/patch/optimizer inputs changed during compilation')
    assets = application_assets(package)
    for name, target in assets.items():
        if target.exists():
            (ARTIFACTS / name).symlink_to(target, target_is_directory=target.is_dir())
    coverage = re.search(r'AOT: sidecar compiled (\d+) of (\d+) functions', result.stderr)
    if args.pipeline == 'linked' and not coverage:
        raise RuntimeError('Compiler did not confirm heap-independent sidecar linking; use the current patched runtime.')
    layout = re.search(r'AOT: trained native layout jobs (\d+) hot (\d+)', result.stderr)
    skipped = re.search(r'AOT: selective hybrid skipped (\d+) trained hot function jobs', result.stderr)
    if args.native_hot_layout and (not layout or int(layout[2]) == 0):
        raise RuntimeError('The compiler did not place trained hot functions; inspect the build log and profile.')
    if args.skip_trained_hot and (not skipped or int(skipped[1]) == 0):
        raise RuntimeError('The compiler did not omit trained hot functions; inspect the build log and profile.')
    data = {'pi_version': manifest['version'], 'pi_package': str(package), 'target': lab.target(),
            'sources': lab.LOCK, 'mode': 'generic JavaScript native AOT with JSC sidecar',
            'typescript_type_annotations_preserved': False, 'unicode_fast_path': not args.no_unicode_fast_path,
            'unicode_optimizer_sha256': digest(lab.ROOT / 'optimizations/sanitize-unicode.js'), 'executable': str(executable), 'image': str(image),
            'build_driver_sha256': digest(lab.ROOT / 'build-pi.js'),
            'input_sources': input_sources,
            'build_inputs': build_inputs,
            'standalone_workers_optimizer_sha256': digest(lab.ROOT / 'optimizations/standalone-workers.js'),
            'worker_specifier_substitutions': 2 if manifest['version'] == '1.0.0' else 0,
            'codemode_worker_embedded': manifest['version'] == '1.0.0',
            'standalone_photon_wasm_relocated': manifest['version'] == '1.0.0',
            'image_worker_termination_awaited': manifest['version'] == '1.0.0',
            'input_sources_validation': 'Relevant original entry/render/Unicode sources match before and after build; complete dependency tree and symlinked assets are not immutable snapshots',
            'aot_pipeline': args.pipeline,
            'inline_loop_fast_paths': args.inline_loop_fast_paths,
            'native_hot_layout': args.native_hot_layout,
            'native_layout_jobs': int(layout[1]) if layout else None,
            'native_layout_hot_jobs': int(layout[2]) if layout else None,
            'skip_trained_hot': args.skip_trained_hot,
            'native_skipped_hot_jobs': int(skipped[1]) if skipped else 0,
            'priority_final_render': args.priority_final_render,
            'priority_final_render_optimizer_sha256': digest(lab.ROOT / 'optimizations/priority-final-render.js') if args.priority_final_render else None,
            'aot_compilation': {'compiled': int(coverage[1]), 'considered': int(coverage[2])} if coverage else None,
            'static_heap': False,
            'prelinked_graph_version': 4,
            'native_call_linking': 'dynamic',
            'internal_modules_aot': args.pipeline == 'linked',
            'runtime': str(runtime),
            'bytecode_order_sha256': digest(Path(args.bytecode_order)) if args.bytecode_order else None,
            'runtime_sha256': digest(runtime),
            'executable_sha256': digest(executable), 'image_sha256': digest(image),
            'executable_bytes': executable.stat().st_size, 'image_bytes': image.stat().st_size,
            'backend_patch_sha256': digest(lab.PATCH), 'bun_patch_sha256': digest(lab.BUN_PATCH),
            'common_runtime_env': {**COMMON, 'PI_PACKAGE_DIR': str(ARTIFACTS)}, 'command': command,
            'native_version_verified': False, 'pi_execution_verified': False,
            'assets_are_symlinks_to_pi_package': True}
    (ARTIFACTS / 'manifest.json').write_text(json.dumps(data, indent=2) + '\n')
    print(f'Built Pi {manifest["version"]}: {executable}; image {image.stat().st_size / 1e6:.1f} MB')


def configuration(data, mode):
    if mode not in MODES:
        raise ValueError(f'Unknown runtime mode: {mode}')
    env = data['common_runtime_env'].copy()
    env['BUN_JSC_useJIT'] = 'true' if mode in ('jit', 'jit-balanced', 'hybrid') else 'false'
    native = mode in ('native', 'native-low-cpu', 'native-balanced', 'hybrid')
    env['BUN_JSC_useAOT'] = str(native).lower()
    if mode == 'native-low-cpu':
        env['BUN_JSC_numberOfGCMarkers'] = '1'
    elif mode in ('native-balanced', 'jit-balanced', 'hybrid'):
        env['BUN_JSC_numberOfGCMarkers'] = '2'
    if native:
        env['BUN_JSC_useAOTMappedImages'] = 'true'
        env['BUN_JSC_aotImagePath'] = data['image']
    return {'command': [data['executable']], 'env': env}


def load():
    data = json.loads((ARTIFACTS / 'manifest.json').read_text())
    for name in ['executable', 'image']:
        if digest(Path(data[name])) != data[name + '_sha256']:
            raise RuntimeError(f'{name} changed after build; rebuild and verify before running.')
    return data


def verify():
    data = load()
    env = lab.jsc_environment()
    env.update(configuration(data, 'native')['env'])
    env['BUN_JSC_verboseAOTCompilation'] = 'true'
    env['BUN_JSC_verboseDiskCache'] = 'true'
    result = subprocess.run([data['executable'], '--version'], env=env, text=True, capture_output=True, timeout=60)
    (ARTIFACTS / 'native-proof.log').write_text(result.stderr)
    sites = [line for line in result.stderr.splitlines() if re.search(r'^AOT: .* is at .* size \d+', line)]
    if result.returncode or result.stdout.strip() != data['pi_version'] or not sites:
        raise RuntimeError(f'Pi native execution not verified: exit={result.returncode}, native sites={len(sites)}, stdout={result.stdout!r}; inspect {ARTIFACTS / "native-proof.log"}')
    if 'AOT: mapped read-only image ' not in result.stderr:
        raise RuntimeError('Version probe did not confirm the file mapping loader.')
    graph = re.search(r'BytecodeCache: accepted prelinked module graph v(\d+) modules (\d+)', result.stderr)
    if not graph or int(graph[1]) != data.get('prelinked_graph_version', 4) or int(graph[2]) < 1:
        raise RuntimeError('Prelinked module graph was not accepted; startup may be reparsing the bundle.')
    if 'BytecodeCache: rejected prelinked module graph' in result.stderr:
        raise RuntimeError('A prelinked module graph was rejected; inspect the runtime/serializer compatibility.')
    data['prelinked_graph_verified'] = True
    data['prelinked_graph_modules'] = int(graph[2])
    data['file_mapping_verified'] = True
    data['native_version_verified'] = True
    data['native_sites_in_version_probe'] = len(sites)
    data['native_sites_example'] = sites[:5]
    builtin_sites = [line for line in sites if f'(module {0xeb17ffff} ' in line]
    data['native_jsc_builtin_sites_in_version_probe'] = len(builtin_sites)
    data['native_jsc_builtin_examples'] = builtin_sites[:10]
    bun_builtin_sites = [line for line in sites if f'(module {0xb017ffff} ' in line]
    data['native_bun_builtin_sites_in_version_probe'] = len(bun_builtin_sites)
    data['native_bun_builtin_examples'] = bun_builtin_sites[:10]
    hybrid_env = lab.jsc_environment()
    hybrid_env.update(configuration(data, 'hybrid')['env'])
    hybrid_env['BUN_JSC_verboseAOTCompilation'] = 'true'
    hybrid_probe = subprocess.run([data['executable'], '--version'], env=hybrid_env, text=True, capture_output=True, timeout=60)
    (ARTIFACTS / 'hybrid-proof.log').write_text(hybrid_probe.stderr)
    hybrid_sites = re.findall(r'^AOT: .* is at .* size \d+', hybrid_probe.stderr, re.MULTILINE)
    if hybrid_probe.returncode or hybrid_probe.stdout.strip() != data['pi_version'] or not hybrid_sites:
        raise RuntimeError('Hybrid mode did not demonstrate native code selection.')
    data['hybrid_version_verified'] = True
    data['hybrid_native_sites_in_version_probe'] = len(hybrid_sites)
    (ARTIFACTS / 'manifest.json').write_text(json.dumps(data, indent=2) + '\n')
    copied = configuration(data, 'native')
    copied['env']['BUN_JSC_useAOTMappedImages'] = 'false'
    configs = {'pi_native_copy_aot': copied, **{
        'pi_' + mode.replace('-', '_'): configuration(data, mode) for mode in MODES}}
    configs.update({'pi_native_aot': configs['pi_native'], 'pi_native_low_cpu_aot': configs['pi_native_low_cpu'],
                    'pi_native_jit': configs['pi_jit'], 'pi_native_interpreter': configs['pi_interpreter']})
    (ARTIFACTS / 'variants.json').write_text(json.dumps(configs, indent=2) + '\n')
    print(f'Pi {data["pi_version"]} native version probe verified: {len(sites)} native code sites')


def validate():
    data = load()
    if not data.get('native_version_verified'):
        raise RuntimeError('Verify native code selection before RPC validation.')
    variants = ARTIFACTS / 'variants.json'
    output = ARTIFACTS / 'rpc-validation.json'
    command = [sys.executable, str(lab.ROOT.parent / 'benchmarks/benchmark.py'),
               '--variants-file', str(variants), '--variants', *['pi_' + mode.replace('-', '_') for mode in MODES],
               '--runs', '1', '--warmups', '0', '--output', str(output)]
    env = os.environ.copy()
    env['PI_BENCH_PACKAGE_DIR'] = data['pi_package']
    subprocess.run(command, env=env, check=True)
    report = json.loads(output.read_text())
    if len(report['samples']) != len(MODES) or any(x['requests'] != 10 or x['read_tools'] != 5 for x in report['samples']):
        raise RuntimeError('Incomplete RPC validation.')
    data['pi_execution_verified'] = True
    data['rpc_validation'] = str(output)
    (ARTIFACTS / 'manifest.json').write_text(json.dumps(data, indent=2) + '\n')
    write_launchers(data)
    print(f'RPC behavior validated; direct launchers: {ARTIFACTS}')


def write_launchers(data):
    # These launchers exec Pi directly, without hashing the large image on every launch.
    # Bash's prefix expansion enumerates names without an env/grep subprocess or
    # parsing values, which can contain spaces, shell syntax, and newlines.
    for mode in MODES:
        config = configuration(data, mode)
        script = ('#!/bin/bash\nset -eu\n'
                  'for runtime_key in "${!JSC_@}" "${!BUN_JSC_@}" "${!PI_NATIVE_AOT_@}"; do\n'
                  '    unset "$runtime_key"\n'
                  'done\n')
        for key, value in config['env'].items():
            script += 'export ' + key + '=' + shlex.quote(value) + '\n'
        script += 'exec ' + shlex.quote(data['executable']) + ' "$@"\n'
        launcher = ARTIFACTS / ('pi-' + mode + '.sh')
        launcher.write_text(script)
        launcher.chmod(0o755)


def run(args):
    data = load()
    if not data['pi_execution_verified']:
        raise RuntimeError('Run verify and validate before launching the experiment.')
    config = configuration(data, args.mode)
    env = lab.jsc_environment()
    env.update(config['env'])
    os.execve(data['executable'], [data['executable'], *args.pi_args], env)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    build_parser = commands.add_parser('build')
    build_parser.add_argument('--no-unicode-fast-path', action='store_true', help='Build the unmodified Pi Unicode control')
    build_parser.add_argument('--inline-loop-fast-paths', action='store_true', help='Emit native fast paths inside loops; benchmark code-size/CPU tradeoff')
    build_parser.add_argument('--native-hot-layout', action='store_true', help='Place native sidecar code using the recorded hot-function order')
    build_parser.add_argument('--skip-trained-hot', action='store_true', help='Hybrid experiment: omit trained hot native functions so they can use the JIT')
    build_parser.add_argument('--priority-final-render', action='store_true', help='Render the completed agent turn promptly using Pi’s existing immediate scheduler')
    build_parser.add_argument('--runtime', help='Bun executable built with the pinned native patches')
    build_parser.add_argument('--pipeline', choices=['source', 'linked'], default='source')
    build_parser.add_argument('--bytecode-order', help='Pi bytecode order profile for the whole-program linker')
    build_parser.add_argument('--pi-package', default='/opt/homebrew/lib/node_modules/@earendil-works/pi-coding-agent')
    commands.add_parser('verify')
    commands.add_parser('validate', help='Validate five real read-tool turns in every runtime mode')
    run_parser = commands.add_parser('run')
    run_parser.add_argument('--mode', choices=MODES, default='native')
    run_parser.add_argument('pi_args', nargs=argparse.REMAINDER)
    args = parser.parse_args()
    if args.command == 'run' and args.pi_args[:1] == ['--']:
        args.pi_args = args.pi_args[1:]
    try:
        {'build': lambda: build(args), 'verify': verify, 'validate': validate, 'run': lambda: run(args)}[args.command]()
    except (RuntimeError, OSError, ValueError, subprocess.SubprocessError) as error:
        print(f'error: {error}', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
