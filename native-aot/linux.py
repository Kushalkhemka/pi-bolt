#!/usr/bin/env python3
"""Prepare portable Linux x64 or ARM64 Bun bytecode baselines."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import lab
import pi
from stable import receipt

ROOT = Path(__file__).resolve().parent


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--bun', default=str(ROOT.parent / 'benchmarks/runtime/node_modules/.bin/bun'))
    parser.add_argument('--pi-package', default='/opt/homebrew/lib/node_modules/@earendil-works/pi-coding-agent')
    parser.add_argument('--arch', choices=['x64', 'arm64'], default='x64')
    parser.add_argument('--output')
    parser.add_argument('--priority-final-render', action='store_true',
                        help='Use the same completed-turn render change as the Mac comparison')
    args = parser.parse_args()
    bun = shutil.which(args.bun)
    if not bun:
        parser.error(f'Bun not found: {args.bun}')
    env = lab.jsc_environment()
    version = subprocess.check_output([bun, '--version'], env=env, text=True).strip()
    if version != '1.4.2':
        parser.error(f'Expected pinned stable Bun 1.4.2, found {version}')
    package = Path(args.pi_package).resolve()
    output = Path(args.output or ROOT / ('artifacts/linux-' + args.arch + '-baseline')).resolve()
    if output.exists():
        parser.error(f'Output already exists: {output}; use a fresh output directory')
    output.mkdir(parents=True)
    executable = output / 'pi-bun-bytecode'
    source_before = pi.source_fingerprints(package)
    runtime_sha = pi.digest(Path(bun).resolve())
    driver_sha = pi.digest(ROOT / 'build-pi.js')
    build_inputs = {str(path): pi.digest(path) for path in (
        ROOT / 'build-pi.js', ROOT / 'optimizations/standalone-workers.js',
        ROOT / 'optimizations/sanitize-unicode.js', ROOT / 'optimizations/priority-final-render.js')}
    command = [bun, '--no-env-file', str(ROOT / 'build-pi.js'), str(package), str(executable),
               'true', 'bun-linux-' + args.arch, '', '', str(args.priority_final_render).lower()]
    result = subprocess.run(command, env=env, capture_output=True, text=True)
    (output / 'build.log').write_text(result.stdout + result.stderr)
    if result.returncode:
        raise RuntimeError(f'Linux build failed; inspect {output / "build.log"}')
    if (source_before != pi.source_fingerprints(package)
            or runtime_sha != pi.digest(Path(bun).resolve())
            or any(before != pi.digest(Path(path)) for path, before in build_inputs.items())):
        raise RuntimeError('Relevant Pi/compiler/driver inputs changed during build')
    build_receipt = receipt(result.stdout)
    package_version = json.loads((package / 'package.json').read_text())['version']
    expected = {'unicode_fast_path': True, 'substitutions': 1,
                'priority_final_render': args.priority_final_render,
                'priority_final_render_substitutions': int(args.priority_final_render),
                'worker_specifier_substitutions': 2 if package_version == '1.0.0' else 0}
    for key, value in expected.items():
        if build_receipt.get(key) != value:
            raise RuntimeError(f'Linux build receipt mismatch: {key}')
    # Check the ELF class, endian encoding, and target machine ID without executing it.
    header = executable.read_bytes()[:20]
    if header[:6] != b'\x7fELF\x02\x01' or int.from_bytes(header[18:20], 'little') != {'x64': 62, 'arm64': 183}[args.arch]:
        raise RuntimeError(f'Build did not produce a Linux {args.arch} ELF executable')
    assets = pi.application_assets(package)
    for name, source in assets.items():
        if source.is_dir():
            shutil.copytree(source, output / name)
        elif source.is_file():
            shutil.copy2(source, output / name)
    launcher = output / 'pi.sh'
    launcher.write_text('''#!/bin/sh
set -eu
PI_PACKAGE_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
export PI_PACKAGE_DIR
exec "$PI_PACKAGE_DIR/pi-bun-bytecode" "$@"
''')
    launcher.chmod(0o755)
    copied_asset_hashes = {}
    for name, source in assets.items():
        sources = sorted(source.rglob('*')) if source.is_dir() else [source]
        for original in sources:
            if not original.is_file():
                continue
            relative = Path(name) / original.relative_to(source) if source.is_dir() else Path(name)
            digest = pi.digest(original)
            if pi.digest(output / relative) != digest:
                raise RuntimeError(f'Copied asset differs: {relative}')
            copied_asset_hashes[str(relative)] = digest
    data = {'target': 'linux-' + args.arch, 'mode': 'stable Bun standalone with bytecode and JIT', 'native_aot': False, 'unicode_fast_path': True,
            'unicode_optimizer_sha256': hashlib.sha256((ROOT / 'optimizations/sanitize-unicode.js').read_bytes()).hexdigest(),
            'build_driver_sha256': hashlib.sha256((ROOT / 'build-pi.js').read_bytes()).hexdigest(),
            'priority_final_render': args.priority_final_render,
            'priority_final_render_optimizer_sha256': pi.digest(ROOT / 'optimizations/priority-final-render.js') if args.priority_final_render else None,
            'runtime_sha256': runtime_sha, 'input_sources': source_before,
            'build_inputs': build_inputs, 'build_receipt': build_receipt,
            'standalone_workers_optimizer_sha256': build_inputs[str(ROOT / 'optimizations/standalone-workers.js')],
            'worker_specifier_substitutions': build_receipt['worker_specifier_substitutions'],
            'codemode_worker_embedded': package_version == '1.0.0',
            'input_sources_validation': 'Selected original entry/render/Unicode inputs and compiler/driver match before and after build; copied assets and the full dependency tree are not immutable snapshots',
            'pi_version': package_version, 'bun_version': version,
            'command': command, 'executable_bytes': executable.stat().st_size,
            'executable_sha256': hashlib.sha256(executable.read_bytes()).hexdigest(),
            'assets': list(assets), 'assets_are_copies': True, 'elf_architecture_verified': True,
            'copied_asset_sha256': copied_asset_hashes,
            'native_host_execution_verified': False,
            'native_aot_blocker': 'Pinned public JSC AOT branch has placeholder x64 runtime stubs' if args.arch == 'x64' else 'Native Linux ARM64 validation pending'}
    (output / 'manifest.json').write_text(json.dumps(data, indent=2) + '\n')
    print(f'Prepared Linux {args.arch} bytecode baseline: {output}; native execution awaits the Linux host')


if __name__ == '__main__':
    main()
