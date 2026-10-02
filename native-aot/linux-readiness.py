#!/usr/bin/env python3
"""Inspect Linux build readiness; optional correctness runs require a matching Linux host."""
import argparse
import datetime
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parent
NATIVE_BSS_ADDRESS = 0x200500000000
NATIVE_BSS_BYTES = 4 << 30


def native_address_contract():
    """Keep the opt-in probe tied to the serialized fixed-address ABI."""
    header = ROOT / 'sources/WebKit/Source/bmalloc/bmalloc/StaticRegion.h'
    contract = {'address': hex(NATIVE_BSS_ADDRESS), 'bytes': NATIVE_BSS_BYTES,
                'minimum_address_bits': 46, 'source_available': header.is_file()}
    if not header.is_file():
        contract['source_matches'] = None
        contract['note'] = 'Expected pinned ABI; repeat after bootstrap to check actual source'
        return contract
    text = header.read_text()
    try:
        base = int(re.search(r'constexpr uintptr_t base = (0x[0-9a-fA-F]+)', text)[1], 16)
        size = re.search(r'arenaReservation = (\d+)ULL << (\d+)', text)
        size = int(size[1]) << int(size[2])
        enum = re.search(r'enum class Arena[^\{]*\{(.*?)\};', text, re.S)[1]
        enum = re.sub(r'//[^\n]*|/\*.*?\*/', '', enum, flags=re.S)
        arenas = [item.strip() for item in enum.split(',') if item.strip()]
        offset = int(re.search(r'offsetOfEmptyStringInBss = (\d+)', text)[1])
        address = base + arenas.index('Bss') * size + offset
        contract.update(source_sha256=sha(header), source_address=hex(address), source_bytes=size,
                        source_matches=address == NATIVE_BSS_ADDRESS and size == NATIVE_BSS_BYTES)
    except (AttributeError, IndexError, TypeError, ValueError) as error:
        contract.update(source_matches=False, error=f'Cannot establish source ABI: {error}')
    return contract


def probe_native_address_space(host_platform=None, libc=None):
    """A short-lived Linux child checks availability without replacing mappings."""
    import ctypes
    host_platform = sys.platform if host_platform is None else host_platform
    result = {'status': 'refused_non_linux', 'probe_executed': False, 'available': False,
              'requested_address': hex(NATIVE_BSS_ADDRESS), 'bytes': NATIVE_BSS_BYTES,
              'protection': 'PROT_NONE', 'flags': ['MAP_PRIVATE', 'MAP_ANONYMOUS', 'MAP_NORESERVE'],
              'limitation': 'The child releases the mapping; availability now does not guarantee the runtime reservation later'}
    if host_platform != 'linux':
        return result
    if ctypes.sizeof(ctypes.c_void_p) != 8:
        result['status'] = 'refused_non_64_bit'
        return result
    libc = ctypes.CDLL(None, use_errno=True) if libc is None else libc
    libc.mmap.argtypes = [ctypes.c_void_p, ctypes.c_size_t, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_long]
    libc.mmap.restype = ctypes.c_void_p
    libc.munmap.argtypes = [ctypes.c_void_p, ctypes.c_size_t]
    libc.munmap.restype = ctypes.c_int
    ctypes.set_errno(0)
    # Linux constants; never MAP_FIXED, RW protection, or a memory touch.
    pointer = libc.mmap(ctypes.c_void_p(NATIVE_BSS_ADDRESS), NATIVE_BSS_BYTES, 0,
                        0x02 | 0x20 | 0x4000, -1, 0)
    result['probe_executed'] = True
    if pointer == ctypes.c_void_p(-1).value:
        error = ctypes.get_errno()
        result.update(status='mapping_failed', errno=error, error=os.strerror(error))
        return result
    address = pointer or 0
    result['returned_address'] = hex(address)
    ctypes.set_errno(0)
    unmap = libc.munmap(ctypes.c_void_p(address), NATIVE_BSS_BYTES)
    result['mapping_released'] = unmap == 0
    if unmap:
        error = ctypes.get_errno()
        result.update(status='unmap_failed', errno=error, error=os.strerror(error))
    elif address != NATIVE_BSS_ADDRESS:
        result['status'] = 'different_address'
    else:
        result.update(status='available', available=True)
    return result


def native_address_preflight(matches):
    contract = native_address_contract()
    result = {'status': 'refused_host_mismatch', 'probe_executed': False,
              'available': False, 'source_contract': contract}
    if not matches:
        return result
    if contract['source_matches'] is False:
        result['status'] = 'source_contract_changed'
        return result
    try:
        child = subprocess.run([sys.executable, str(Path(__file__).resolve()), '--native-address-preflight-child'],
                               capture_output=True, text=True, timeout=15)
        if child.returncode:
            result.update(status='probe_child_failed', returncode=child.returncode, stderr=child.stderr[-2000:])
        else:
            result.update(json.loads(child.stdout))
    except (subprocess.TimeoutExpired, ValueError, OSError) as error:
        result.update(status='probe_child_failed', error=str(error))
    return result


def native_address_self_test():
    """Mock-only checks: no OS mapping, Bun invocation, or Linux host required."""
    import ctypes
    from types import SimpleNamespace
    from unittest.mock import Mock, patch
    passed = []

    def check(name, condition):
        if not condition:
            raise RuntimeError('Native address preflight self-test failed: ' + name)
        passed.append(name)

    def fake(pointer, unmap=0):
        return SimpleNamespace(mmap=Mock(return_value=pointer), munmap=Mock(return_value=unmap))

    libc = fake(NATIVE_BSS_ADDRESS)
    result = probe_native_address_space('linux', libc)
    check('exact_address_released', result['available'] and result['mapping_released'])
    arguments = libc.mmap.call_args.args
    check('no_replace_no_commit_flags', arguments[0].value == NATIVE_BSS_ADDRESS
          and arguments[1:] == (NATIVE_BSS_BYTES, 0, 0x4022, -1, 0))
    check('exact_unmap', libc.munmap.call_args.args[0].value == NATIVE_BSS_ADDRESS
          and libc.munmap.call_args.args[1] == NATIVE_BSS_BYTES)
    libc = fake(0x100000)
    result = probe_native_address_space('linux', libc)
    check('different_address_released', result['status'] == 'different_address'
          and not result['available'] and libc.munmap.call_args.args[0].value == 0x100000)
    libc = fake(ctypes.c_void_p(-1).value)
    libc.mmap.side_effect = lambda *args: (ctypes.set_errno(12), ctypes.c_void_p(-1).value)[1]
    result = probe_native_address_space('linux', libc)
    check('mapping_failure_no_unmap', result['status'] == 'mapping_failed'
          and result['errno'] == 12 and not libc.munmap.called)
    libc = fake(NATIVE_BSS_ADDRESS, unmap=-1)
    result = probe_native_address_space('linux', libc)
    check('unmap_failure_blocks', result['status'] == 'unmap_failed' and not result['available'])
    libc = fake(NATIVE_BSS_ADDRESS)
    result = probe_native_address_space('darwin', libc)
    check('non_linux_no_mapping', result['status'] == 'refused_non_linux' and not libc.mmap.called)
    with patch.object(subprocess, 'run') as child:
        result = native_address_preflight(False)
        check('host_mismatch_no_child', result['status'] == 'refused_host_mismatch' and not child.called)
    with patch.dict(globals(), native_address_contract=lambda: {'source_matches': False}), \
            patch.object(subprocess, 'run') as child:
        result = native_address_preflight(True)
        check('abi_change_no_child', result['status'] == 'source_contract_changed' and not child.called)
    with patch.dict(globals(), native_address_contract=lambda: {'source_matches': True}), \
            patch.object(subprocess, 'run', return_value=SimpleNamespace(returncode=0, stdout=json.dumps(
                {'status': 'available', 'available': True, 'probe_executed': True}), stderr='')) as child:
        result = native_address_preflight(True)
        check('separate_child_command', result['available'] and child.call_args.args[0]
              == [sys.executable, str(Path(__file__).resolve()), '--native-address-preflight-child'])
    print(json.dumps({'complete': True, 'mock_only': True, 'passed': passed}))


def sha(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def tool(command):
    executable = shutil.which(command[0])
    if not executable:
        return {'available': False}
    result = subprocess.run([executable, *command[1:]], capture_output=True, text=True, timeout=15)
    return {'available': result.returncode == 0, 'path': executable,
            'version': (result.stdout or result.stderr).strip().splitlines()[:3]}


def elf(path):
    with path.open('rb') as stream:
        header = stream.read(64)
    if len(header) < 20 or header[:6] != b'\x7fELF\x02\x01':
        raise ValueError(f'{path}: expected ELF64 little-endian executable')
    machine = int.from_bytes(header[18:20], 'little')
    return {'class': 64, 'endian': 'little', 'machine': machine,
            'architecture': {62: 'x64', 183: 'arm64'}.get(machine, 'unsupported'),
            'sha256': sha(path), 'bytes': path.stat().st_size}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--arch', required=True, choices=['x64', 'arm64'])
    parser.add_argument('--pi-package', required=True)
    parser.add_argument('--bun', default='bun')
    parser.add_argument('--artifact', help='Existing Linux stable-bytecode or native artifact directory')
    parser.add_argument('--require-build', action='store_true', help='Fail unless native ARM64 build prerequisites are present')
    parser.add_argument('--native-address-preflight', action='store_true',
                        help='Opt in to a separate Linux child checking the native fixed BSS reservation before invoking Bun')
    parser.add_argument('--validate', action='store_true', help='Run bounded correctness only on a matching Linux host')
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    output = Path(args.output).resolve(); output.parent.mkdir(parents=True, exist_ok=True)
    host_arch = {'aarch64': 'arm64', 'arm64': 'arm64', 'x86_64': 'x64', 'amd64': 'x64'}.get(platform.machine().lower(), platform.machine())
    matches = sys.platform == 'linux' and host_arch == args.arch
    report = {'date': datetime.datetime.now(datetime.timezone.utc).isoformat(),
        'requested_target': 'linux-' + args.arch, 'host_platform': sys.platform, 'host_architecture': host_arch,
        'kernel': platform.release(), 'libc': list(platform.libc_ver()), 'page_bytes': os.sysconf('SC_PAGE_SIZE'),
        'matching_linux_host': matches, 'inspection_complete': False, 'native_host_execution_verified': False, 'performance_validated': False,
        'memory_metric_on_linux': 'RSS/VmHWM; not comparable to macOS phys_footprint',
        'native_aot_port_status': 'ARM64 backend/build recipe prepared; host execution pending' if args.arch == 'arm64'
            else 'Native x64 AOT blocked: public backend runtime stubs/register constraints require a port',
        'tools': {}, 'blockers': []}
    output.write_text(json.dumps(report, indent=2) + '\n')
    if args.native_address_preflight:
        report['native_address_preflight'] = native_address_preflight(matches)
        if not report['native_address_preflight']['available']:
            report['blockers'].append('Native fixed-address preflight: ' + report['native_address_preflight']['status'])
            output.write_text(json.dumps(report, indent=2) + '\n')
            parser.exit(2, 'Native address-space readiness blocked; inspect ' + str(output) + '\n')
    report['tools'] = {name: tool(command) for name, command in {
            'bun': [args.bun, '--version'], 'python': ['python3', '--version'], 'git': ['git', '--version'],
            'cmake': ['cmake', '--version'], 'ninja': ['ninja', '--version'], 'clang': ['clang', '--version'],
            'rust': ['rustc', '--version']}.items()}
    output.write_text(json.dumps(report, indent=2) + '\n')
    package = Path(args.pi_package).resolve()
    manifest = package / 'package.json'
    if not manifest.is_file():
        report['blockers'].append('Pi package missing')
    else:
        report['pi_version'] = json.loads(manifest.read_text())['version']
        if report['pi_version'] not in ('0.85.1', '1.0.0'):
            report['blockers'].append('Pi package must be pinned to 0.85.1 or 1.0.0 for current source guards')
        native_modules = list(package.rglob('*.node'))
        report['native_dependency_objects'] = []
        for path in native_modules:
            # Pi ships optional prebuilds for several OS/architecture pairs.
            # Inventory them, but only block candidates for the requested host.
            identity = str(path.relative_to(package)).lower()
            platform_named = any(tag in identity for tag in ('darwin', 'win32', 'linux', 'freebsd'))
            candidate = (('linux-' + args.arch) in identity and 'musl' not in identity) or not platform_named
            try:
                item = elf(path)
                if candidate and item['architecture'] != args.arch:
                    report['blockers'].append(f'Wrong-architecture native dependency: {path}')
            except ValueError:
                item = {'format': 'not ELF64 little-endian'}
                if candidate:
                    report['blockers'].append(f'Non-Linux native dependency: {path}')
            report['native_dependency_objects'].append({'path': str(path), 'target_candidate': candidate, **item})
        report['native_dependency_inventory_limit'] = 'Path-tag candidates only; actual resolver/clipboard and arbitrary extension native modules still require host testing'
    if args.require_build:
        if not matches:
            report['blockers'].append('Native source build requires a matching Linux host')
        if args.arch != 'arm64':
            report['blockers'].append('Native x64 AOT compiler port is not implemented')
        if platform.libc_ver()[0] != 'glibc':
            report['blockers'].append('This native recipe targets glibc; musl has not been prepared/validated')
        for name, metadata in report['tools'].items():
            if not metadata['available']:
                report['blockers'].append(f'Missing build tool: {name}')
        report['required_toolchain'] = {'llvm': '23.1', 'rust': 'nightly-2026-09-15',
            'note': 'Bun build driver enforces/downloads pinned dependencies; presence checks do not prove a successful build'}
    configs = None
    if args.artifact:
        artifact = Path(args.artifact).resolve()
        data = json.loads((artifact / 'manifest.json').read_text())
        native = bool(data.get('image'))
        executable = artifact / ('pi-native' if native else 'pi-bun-bytecode')
        report['artifact'] = {'directory': str(artifact), 'manifest_sha256': sha(artifact / 'manifest.json'),
                              'native_aot': native, 'executable': elf(executable)}
        if report['artifact']['executable']['architecture'] != args.arch:
            report['blockers'].append('Artifact architecture differs from requested target')
        if sha(executable) != data['executable_sha256']:
            report['blockers'].append('Executable hash differs from recorded build')
        if native:
            image = artifact / 'pi-native.aot'
            report['artifact']['image_sha256'] = sha(image)
            if sha(image) != data['image_sha256']:
                report['blockers'].append('Native image hash differs from recorded build')
            configs = json.loads((artifact / 'variants.json').read_text())
            configs = {key: configs[key] for key in ('pi_native', 'pi_hybrid', 'pi_jit', 'pi_interpreter')}
            # Never silently rewrite an engine/image pair relocated from another host.
            for config in configs.values():
                if Path(config['command'][0]).resolve() != executable:
                    report['blockers'].append('Native artifact paths must be generated on this Linux host')
                if Path(config['env'].get('PI_PACKAGE_DIR', '')).resolve() != artifact:
                    report['blockers'].append('Native asset directory differs from artifact directory')
                if config['env'].get('BUN_JSC_useAOT') == 'true' and Path(config['env'].get('BUN_JSC_aotImagePath', '')).resolve() != image:
                    report['blockers'].append('Native image path differs from inspected engine/image pair')
        else:
            configs = {'stable_bytecode': {'command': [str(executable)], 'env': {'PI_PACKAGE_DIR': str(artifact)}}}
        for path in artifact.iterdir():
            if path.is_symlink() and not path.exists():
                report['blockers'].append(f'Broken asset symlink: {path}')
    if args.validate and not matches:
        report['blockers'].append('Correctness execution refused on a different OS/architecture; emulation is not native validation')
    if args.validate and configs is None:
        report['blockers'].append('--validate requires --artifact')
    report['inspection_complete'] = True
    output.write_text(json.dumps(report, indent=2) + '\n')
    if (args.validate or args.require_build) and report['blockers']:
        parser.exit(2, 'Linux readiness blocked; inspect ' + str(output) + '\n')
    if args.validate:
        variants = output.with_name(output.stem + '-variants.json')
        variants.write_text(json.dumps(configs, indent=2) + '\n')
        report['correctness_results'] = []
        for script in ('cli-workflows.py', 'interactive-commands.py'):
            result_path = output.with_name(output.stem + '-' + script.removesuffix('.py') + '.json')
            subprocess.run([sys.executable, str(ROOT / script), '--variants-file', str(variants),
                '--variants', *configs, '--output', str(result_path)], check=True, timeout=600)
            report['correctness_results'].append({'path': str(result_path), 'sha256': sha(result_path)})
            output.write_text(json.dumps(report, indent=2) + '\n')
        report['native_host_execution_verified'] = True
        report['scope'] = 'Matching Linux kernel/architecture correctness only; no universal functionality/performance guarantee'
        output.write_text(json.dumps(report, indent=2) + '\n')
    print(f'Linux {args.arch}: matching_host={matches}, native_execution={report["native_host_execution_verified"]}; report {output}')


if __name__ == '__main__':
    if sys.argv[1:] == ['--self-test-native-address-preflight']:
        native_address_self_test()
    elif sys.argv[1:] == ['--native-address-preflight-child']:
        try:
            print(json.dumps(probe_native_address_space()))
        except (OSError, AttributeError) as error:
            print(json.dumps({'status': 'probe_setup_failed', 'probe_executed': False,
                              'available': False, 'error': str(error)}))
    else:
        main()
