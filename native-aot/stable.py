#!/usr/bin/env python3
"""Build reproducible macOS ARM64 Pi bytecode controls with pinned stable Bun."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import struct
import subprocess
import pi

ROOT = Path(__file__).resolve().parent
PINNED_BUN = '1.4.2'


def fingerprint(path):
    with Path(path).open('rb') as stream:
        sha = hashlib.file_digest(stream, 'sha256').hexdigest()
    return {'sha256': sha, 'bytes': Path(path).stat().st_size}


def environment():
    # Build and version probing use a clean ordinary host environment. Inherited
    # runtime/bytecode tracing flags must not change either baseline's behavior.
    return {key: value for key, value in os.environ.items()
            if key in ('PATH', 'HOME', 'TMPDIR', 'LANG', 'LC_ALL', 'USER', 'LOGNAME')}


def build_command(args, bun, package, output):
    return [str(bun), '--no-env-file', str(ROOT / 'build-pi.js'), str(package), str(output / 'pi-native'),
            str(not args.no_unicode_fast_path).lower(), '', str(args.bytecode_order.resolve()),
            json.dumps(['--smol'] if args.smol else []), str(args.priority_final_render).lower()]


def receipt(stdout):
    matches = []
    for line in stdout.splitlines():
        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict) and 'unicode_fast_path' in data and 'priority_final_render' in data:
            matches.append(data)
    if len(matches) != 1:
        raise ValueError('Build driver did not produce exactly one optimization receipt')
    return matches[0]


def build(args):
    if platform.system() != 'Darwin' or platform.machine() != 'arm64':
        raise ValueError('This builder prepares native Mac ARM64 controls; use linux.py for Linux artifacts')
    runtime = shutil.which(args.bun)
    if runtime is None:
        raise ValueError(f'Bun executable not found: {args.bun}')
    bun = Path(runtime).resolve()
    env = environment()
    version = subprocess.check_output([str(bun), '--version'], env=env, text=True).strip()
    if version != PINNED_BUN:
        raise ValueError(f'Expected pinned stable Bun {PINNED_BUN}, found {version}')
    package, output = args.pi_package.resolve(), args.output.resolve()
    if output.exists():
        raise ValueError(f'Output already exists: {output}; choose a fresh directory')
    if not args.bytecode_order.is_file():
        raise ValueError(f'Trained bytecode order file missing: {args.bytecode_order}')
    package_data = json.loads((package / 'package.json').read_text())
    source_paths = [Path(path) for path in pi.source_fingerprints(package)]
    source_hashes = {str(path): fingerprint(path) for path in source_paths}
    inputs = [ROOT / 'build-pi.js', ROOT / 'optimizations/sanitize-unicode.js', ROOT / 'optimizations/standalone-workers.js',
              ROOT / 'optimizations/priority-final-render.js', args.bytecode_order.resolve(), bun]
    input_hashes = {str(path): fingerprint(path) for path in inputs}
    output.mkdir(parents=True)
    command = build_command(args, bun, package, output)
    result = subprocess.run(command, env=env, text=True, capture_output=True)
    (output / 'build.log').write_text(result.stdout + result.stderr)
    executable = output / 'pi-native'
    if result.returncode or not executable.is_file():
        raise ValueError(f'Stable build failed ({result.returncode}); inspect {output / "build.log"}')
    build_receipt = receipt(result.stdout)
    expected = {'unicode_fast_path': not args.no_unicode_fast_path,
                'substitutions': 0 if args.no_unicode_fast_path else 1,
                'priority_final_render': args.priority_final_render,
                'priority_final_render_substitutions': 1 if args.priority_final_render else 0}
    for key, value in expected.items():
        if build_receipt.get(key) != value:
            raise ValueError(f'Optimization receipt mismatch for {key}: {build_receipt}')
    for path, before in {**source_hashes, **input_hashes}.items():
        if fingerprint(path) != before:
            raise ValueError(f'Build input changed while compiling: {path}')
    with executable.open('rb') as stream:
        header = stream.read(12)
    if len(header) < 12 or struct.unpack_from('<I', header)[0] != 0xfeedfacf or struct.unpack_from('<I', header, 4)[0] != 0x0100000c:
        raise ValueError('Build did not produce a native ARM64 Mach-O executable')
    assets = pi.application_assets(package)
    for name, target in assets.items():
        if target.exists():
            (output / name).symlink_to(target, target_is_directory=target.is_dir())
    runtime_env = {'PI_PACKAGE_DIR': str(output), 'PI_OFFLINE': '1'}
    variants = {'stable_default': {'command': [str(executable)], 'env': runtime_env},
                'stable_gc2': {'command': [str(executable)], 'env': {**runtime_env, 'BUN_JSC_numberOfGCMarkers': '2'}}}
    (output / 'variants.json').write_text(json.dumps(variants, indent=2) + '\n')
    pi_version = subprocess.check_output([str(executable), '--version'], env={**env, **runtime_env}, text=True).strip()
    if pi_version != package_data['version']:
        raise ValueError(f'Stable executable reports Pi {pi_version}; expected {package_data["version"]}')
    data = {'date': datetime.now(timezone.utc).isoformat(), 'target': 'darwin-arm64',
            'mode': 'stable Bun standalone bytecode + JIT', 'native_aot': False,
            'pi_version': pi_version, 'pi_package': str(package), 'bun_version': version,
            'runtime': str(bun), 'runtime_sha256': input_hashes[str(bun)]['sha256'],
            'executable': str(executable), 'executable_sha256': fingerprint(executable)['sha256'],
            'executable_bytes': executable.stat().st_size, 'command': command,
            'build_driver_sha256': input_hashes[str(ROOT / 'build-pi.js')]['sha256'],
            'standalone_workers_optimizer_sha256': input_hashes[str(ROOT / 'optimizations/standalone-workers.js')]['sha256'],
            'worker_specifier_substitutions': build_receipt.get('worker_specifier_substitutions', 0),
            'codemode_worker_embedded': package_data['version'] == '1.0.0',
            'unicode_fast_path': not args.no_unicode_fast_path,
            'unicode_optimizer_sha256': input_hashes[str(ROOT / 'optimizations/sanitize-unicode.js')]['sha256'],
            'bytecode_order': str(args.bytecode_order.resolve()),
            'bytecode_order_sha256': input_hashes[str(args.bytecode_order.resolve())]['sha256'],
            'priority_final_render': args.priority_final_render,
            'priority_final_render_optimizer_sha256': input_hashes[str(ROOT / 'optimizations/priority-final-render.js')]['sha256'] if args.priority_final_render else None,
            'exec_argv': ['--smol'] if args.smol else [], 'smol_baked': args.smol,
            'input_sources': source_hashes, 'build_inputs': input_hashes, 'build_receipt': build_receipt,
            'stable_builder_sha256': fingerprint(__file__)['sha256'], 'common_runtime_env': runtime_env,
            'assets_are_symlinks_to_pi_package': True, 'assets': {name: str(path) for name, path in assets.items() if path.exists()},
            'native_host_version_verified': True, 'interactive_execution_verified': False,
            'provenance_limit': 'Relevant entry/render/Unicode sources are hashed; symlinked assets and the complete transitive npm dependency tree are not immutable snapshots. Functional CLI/provider/worker checks are required before performance claims.'}
    (output / 'manifest.json').write_text(json.dumps(data, indent=2) + '\n')
    print(f'Built stable Bun {version} Pi {pi_version}: {output}; CLI functional validation still required')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--bun', default=str(ROOT.parent / 'benchmarks/runtime/node_modules/.bin/bun'))
    parser.add_argument('--pi-package', type=Path, default=Path('/opt/homebrew/lib/node_modules/@earendil-works/pi-coding-agent'))
    parser.add_argument('--bytecode-order', type=Path, default=ROOT / 'research/pi-workload.order')
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--priority-final-render', action='store_true')
    parser.add_argument('--smol', action='store_true', help='Bake --smol into the standalone runtime execArgv')
    parser.add_argument('--no-unicode-fast-path', action='store_true')
    args = parser.parse_args()
    try:
        build(args)
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        parser.exit(1, f'error: {error}\n')


if __name__ == '__main__':
    main()
