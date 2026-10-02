#!/usr/bin/env python3
"""Build and verify the public native AOT backend. The Pi integration is in pi.py."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parent
LOCK = json.loads((ROOT / 'sources.lock.json').read_text())
SOURCES = ROOT / 'sources'
SPARSE = ['Source/JavaScriptCore', 'Source/WTF', 'Source/bmalloc', 'Source/cmake',
          'Source/ThirdParty', 'Tools/Scripts', 'Tools/jsc', 'Tools/TestWebKitAPI',
          '.github/scripts']
EXPECTED = [10, 7, 6, 'native-smoke', 55, 9]
PATCH = ROOT / 'patches/jsc-image-export.patch'
BUN_PATCH = ROOT / 'patches/bun-aot-engine-init.patch'
COMPILE_FLAGS = ['--resolveAllScopeSlotsStatically=true', '--evaluateObjectLiteralValuesFirst=true',
                 '--definePlainInstanceFieldsInConstructor=true']


def target():
    machine = platform.machine().lower()
    arch = {'aarch64': 'arm64', 'arm64': 'arm64', 'amd64': 'x64', 'x86_64': 'x64'}.get(machine, machine)
    return f'{sys.platform}-{arch}'


def checked(command, cwd=None, env=None):
    return subprocess.run(command, cwd=cwd, env=env, check=True,
                          text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE).stdout.strip()


def verify_pin(name, path):
    expected = LOCK[name]['commit']
    actual = checked(['git', 'rev-parse', 'HEAD'], cwd=path)
    if actual != expected:
        raise RuntimeError(f'{path}: expected {expected}, found {actual}. Use a separate checkout for other revisions.')


def apply_source_patch(name, dirname, patch):
    path = SOURCES / dirname
    verify_pin(name, path)
    reverse = subprocess.run(['git', 'apply', '--reverse', '--check', str(patch)], cwd=path, capture_output=True)
    if reverse.returncode:
        checked(['git', 'apply', '--check', str(patch)], cwd=path)
        checked(['git', 'apply', str(patch)], cwd=path)


def apply_backend_patch():
    apply_source_patch('webkit', 'WebKit', PATCH)
    apply_source_patch('bun', 'bun', BUN_PATCH)


def bootstrap():
    SOURCES.mkdir(exist_ok=True)
    for name, dirname in [('bun', 'bun'), ('webkit', 'WebKit')]:
        path = SOURCES / dirname
        source = LOCK[name]
        if not path.exists():
            subprocess.run(['git', 'clone', '--depth=1', '--filter=blob:none', '--no-checkout',
                            '--single-branch', '--branch', source['ref'], source['repository'], str(path)], check=True)
            subprocess.run(['git', 'fetch', '--depth=1', 'origin', source['commit']], cwd=path, check=True)
            if name == 'webkit':
                subprocess.run(['git', 'sparse-checkout', 'set', *SPARSE], cwd=path, check=True)
            subprocess.run(['git', 'checkout', '--detach', source['commit']], cwd=path, check=True)
        verify_pin(name, path)
        if name == 'webkit':
            subprocess.run(['git', 'sparse-checkout', 'add', *SPARSE], cwd=path, check=True)
        print(f'{dirname}: {source["commit"]}', flush=True)
    apply_backend_patch()


def build_jsc(args):
    host = target()
    if host not in LOCK['experimental_native_targets']:
        raise RuntimeError(f'Native AOT build targets are {LOCK["experimental_native_targets"]}; {host} needs an x64 stub port.')
    for name, dirname in [('bun', 'bun'), ('webkit', 'WebKit')]:
        verify_pin(name, SOURCES / dirname)
    apply_backend_patch()
    bun = shutil.which(args.bun)
    if not bun:
        raise RuntimeError(f'Bun executable not found: {args.bun}')
    env = os.environ.copy()
    # npm's bun entry is a symlink; PATH needs the directory containing its name.
    env['PATH'] = str(Path(bun).absolute().parent) + os.pathsep + env.get('PATH', '')
    env['BUN_WEBKIT_PATH'] = str(SOURCES / 'WebKit')
    # Bun's outer -j does not limit the nested CMake/Ninja build.
    env['CMAKE_BUILD_PARALLEL_LEVEL'] = str(args.jobs)
    runtime_build = args.command == 'build-bun'
    if args.profile == 'm5-aot-release' and (not runtime_build or host != 'darwin-arm64'):
        raise RuntimeError('The M5 profile requires build-bun on macOS ARM64.')
    command = ([bun, 'scripts/build.ts', '--profile=m5-aot-release', '--build-dir=build/m5-aot-release']
               if args.profile == 'm5-aot-release' else
               [bun, 'run', 'build:release:local' if runtime_build else 'jsc:build']) + [
               '--webkit-version=' + LOCK['webkit']['commit'], f'-j{args.jobs}']
    log = ROOT / 'research' / ('bun-build.log' if runtime_build else 'jsc-build.log')
    log.parent.mkdir(exist_ok=True)
    print(f'Building {host}; log: {log}', flush=True)
    with log.open('w') as output:
        process = subprocess.run(command, cwd=SOURCES / 'bun', env=env, stdout=output, stderr=subprocess.STDOUT)
    metadata = {'host': host, 'command': command, 'sources': LOCK, 'exit_code': process.returncode,
                'bun_version': checked([bun, '--version']),
                'backend_patch_sha256': hashlib.sha256(PATCH.read_bytes()).hexdigest(),
                'bun_patch_sha256': hashlib.sha256(BUN_PATCH.read_bytes()).hexdigest(),
                'nested_compile_jobs': args.jobs, 'profile': args.profile,
                'runtime': str(SOURCES / 'bun/build' / args.profile / 'bun'),
                'jsc': str(SOURCES / 'bun/build' / args.profile / 'deps/WebKit/bin/jsc'), 'log': str(log)}
    (ROOT / 'research' / ('bun-build-result.json' if runtime_build else 'build-result.json')).write_text(json.dumps(metadata, indent=2) + '\n')
    if process.returncode:
        raise RuntimeError(f'{"Bun" if runtime_build else "JSC"} build exited {process.returncode}; inspect {log}')
    print(f'Built {SOURCES / 'bun/build' / args.profile / 'bun' if runtime_build else default_jsc()}', flush=True)


def default_jsc():
    return SOURCES / 'bun/build/release-local/deps/WebKit/bin/jsc'


def jsc_environment():
    return {key: value for key, value in os.environ.items() if not key.startswith(('JSC_', 'BUN_JSC_', 'PI_NATIVE_AOT_'))}


def export_image(jsc, source, image, is_module, timeout=None):
    # JSON string quoting keeps source/output paths from becoming driver code.
    driver = 'const bytes = writeAOTImage(' + json.dumps(str(image)) + ', readFile(' + json.dumps(str(source)) + '), ' + str(is_module).lower() + '); print(JSON.stringify({image_bytes: bytes}));'
    with tempfile.TemporaryDirectory(prefix='pi-aot-export-') as temp:
        path = Path(temp) / 'export.js'
        path.write_text(driver)
        command = [str(jsc), '--useAOT=false', *COMPILE_FLAGS, str(path)]
        result = subprocess.run(command, env=jsc_environment(), text=True, capture_output=True, timeout=timeout)
    if result.returncode:
        raise RuntimeError(f'Native image export failed ({result.returncode}): {result.stderr}\n{result.stdout}')
    value = json.loads(result.stdout.strip())
    if value['image_bytes'] <= 0 or image.stat().st_size != value['image_bytes']:
        raise RuntimeError(f'Incomplete native image: {value}')
    return {'command': command[:-1] + ['<temporary export driver>'], **value,
            'image_sha256': hashlib.sha256(image.read_bytes()).hexdigest()}


def compile_image(args):
    jsc = Path(args.jsc).resolve() if args.jsc else default_jsc()
    source, output = Path(args.source).resolve(), Path(args.output).resolve()
    if source == output:
        raise RuntimeError('Source and native-image output must be different files.')
    if output.exists():
        raise RuntimeError(f'Output already exists: {output}. Choose a new image path.')
    report = export_image(jsc, source, output, args.module)
    print(json.dumps({'source': str(source), 'image': str(output), 'is_module': args.module, **report}, indent=2))


def verify_jsc(args):
    jsc = Path(args.jsc).resolve() if args.jsc else default_jsc()
    fixture = ROOT / 'fixtures/backend-smoke.js'
    reports = {}
    with tempfile.TemporaryDirectory(prefix='pi-aot-verify-') as temp:
        image = Path(temp) / 'smoke.aot'
        export = export_image(jsc, fixture, image, False, timeout=60)
        altered = Path(temp) / 'altered.js'
        altered.write_text('// Different source must miss the native image.\n' + fixture.read_text())
        configurations = {
            'interpreter': (['--useJIT=false', '--useAOT=false', *COMPILE_FLAGS], fixture),
            'native_backend': (['--useJIT=false', '--useAOT=true', '--aotImagePath=' + str(image)], fixture),
            'native_copy_backend': (['--useJIT=false', '--useAOT=true', '--useAOTMappedImages=false', '--aotImagePath=' + str(image)], fixture),
            'different_source': (['--useJIT=false', '--useAOT=true', '--aotImagePath=' + str(image)], altered),
        }
        for name, (flags, source) in configurations.items():
            command = [str(jsc), *flags, str(source)]
            result = subprocess.run(command, env=jsc_environment(), text=True, capture_output=True, timeout=60)
            if result.returncode:
                raise RuntimeError(f'{name} failed ({result.returncode}): {result.stderr}\n{result.stdout}')
            value = json.loads(result.stdout.strip())
            if value['result'] != EXPECTED or value['native'] != [name in ('native_backend', 'native_copy_backend')] * 5:
                raise RuntimeError(f'{name}: unexpected result {value}')
            reports[name] = {'command': command, 'output': value, 'stderr': result.stderr}
        for label, contents in [('truncated', b'BUNAOT01'), ('wrong-stamp', image.read_bytes()[:8] + b'\x00' * 8 + image.read_bytes()[16:])]:
            invalid = Path(temp) / (label + '.aot')
            invalid.write_bytes(contents)
            command = [str(jsc), '--useJIT=false', '--useAOT=true', '--aotImagePath=' + str(invalid), str(fixture)]
            result = subprocess.run(command, env=jsc_environment(), text=True, capture_output=True, timeout=60)
            if result.returncode or json.loads(result.stdout)['native'] != [False] * 5:
                raise RuntimeError(f'{label}: invalid image was not safely rejected: {result.stderr}')
            reports[label] = {'rejected': True, 'stderr': result.stderr}
        # An exporter must parse/compile a program without evaluating it.
        throwing_source = Path(temp) / 'must-not-execute.js'
        throwing_source.write_text('throw new Error("source executed during compilation"); function probe() { return 42; }')
        export_image(jsc, throwing_source, Path(temp) / 'no-execution.aot', False, timeout=60)
    report = {'host': target(), 'jsc': str(jsc), 'sources': LOCK, 'native_backend_verified': True,
              'jsc_sha256': hashlib.sha256(jsc.read_bytes()).hexdigest(),
              'backend_patch_sha256': hashlib.sha256(PATCH.read_bytes()).hexdigest(),
              'pi_native_aot_available': bool((ROOT / 'artifacts' / target() / 'manifest.json').exists()), 'compilation_phase': 'separate build process',
              'source_not_executed_during_build': True, 'export': export, 'checks': reports}
    output = ROOT / 'research' / 'backend-verification.json'
    output.parent.mkdir(exist_ok=True)
    output.write_text(json.dumps(report, indent=2) + '\n')
    print(f'Build-time native backend verified with JIT disabled; report: {output}')


def status():
    paths = {
        'frontend_type_checks': ('src/js_parser/typescript.rs', ('useSoundTypes', '$$t')),
        'native_image_emission': ('src/jsc/bindings/ZigSourceProvider.cpp', ('PI_NATIVE_AOT_OUT', 'aotCompileImage')),
        'engine_compiler_initialization': ('src/jsc/bindings/ZigGlobalObject.cpp', ('installCompilers',)),
    }
    findings = {}
    for name, (relative, terms) in paths.items():
        path = SOURCES / 'bun' / relative
        findings[name] = {'file': relative, 'markers_found': [term for term in terms if path.exists() and term in path.read_text()]}
    manifest_path = ROOT / 'artifacts' / target() / 'manifest.json'
    manifest = json.loads(manifest_path.read_text()) if manifest_path.exists() else {}
    output = {'host': target(), 'targets': LOCK['targets'], 'sources': LOCK,
              'jsc_exists': default_jsc().is_file(), 'bun_source_observations': findings,
              'pi_native_execution_verified': manifest.get('pi_execution_verified', False),
              'native_version_verified': manifest.get('native_version_verified', False),
              'file_mapping_verified': manifest.get('file_mapping_verified', False),
              'unicode_fast_path': manifest.get('unicode_fast_path', False),
              'experimental_native_targets': LOCK['experimental_native_targets'],
              'validated_native_targets': LOCK['validated_native_targets'],
              'linux_native_aot_status': LOCK['linux_native_aot_status'],
              'linux_arm64_status': LOCK['linux_arm64_status'],
              'typescript_type_annotations_preserved': False,
              'mode': 'experimental generic JavaScript AOT with a native image sidecar'}
    print(json.dumps(output, indent=2))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    commands.add_parser('status')
    commands.add_parser('bootstrap')
    build = commands.add_parser('build-jsc')
    build.add_argument('--bun', default='bun', help='Existing Bun executable used to drive the source build')
    build.add_argument('--jobs', type=int, default=2)
    build.set_defaults(profile='release-local')
    runtime = commands.add_parser('build-bun', help='Build an experimental Bun runtime against the pinned AOT engine')
    runtime.add_argument('--bun', default='bun')
    runtime.add_argument('--jobs', type=int, default=2)
    runtime.add_argument('--profile', choices=['release-local', 'm5-aot-release'], default='release-local')
    verify = commands.add_parser('verify-jsc')
    verify.add_argument('--jsc', help='JSC shell from the pinned AOT branch; default is the local build')
    image = commands.add_parser('compile-image', help='Compile one JavaScript source file into a native JSC image')
    image.add_argument('--jsc')
    image.add_argument('--source', required=True)
    image.add_argument('--output', required=True)
    image.add_argument('--module', action='store_true', help='Parse the source as ESM instead of a classic script')
    args = parser.parse_args()
    if getattr(args, 'jobs', 1) < 1:
        parser.error('--jobs must be at least 1')
    try:
        {'status': status, 'bootstrap': bootstrap, 'build-jsc': lambda: build_jsc(args),
         'build-bun': lambda: build_jsc(args),
         'verify-jsc': lambda: verify_jsc(args), 'compile-image': lambda: compile_image(args)}[args.command]()
    except (RuntimeError, OSError, ValueError, subprocess.SubprocessError) as error:
        print(f'error: {error}', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
