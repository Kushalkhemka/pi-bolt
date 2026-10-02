#!/usr/bin/env python3
"""Export matching source and relocatable relink inputs for the unsigned M5 pair.

Never changes original engine trees, patches, build objects or candidate files.
Archives exclude Git internals, user caches, credentials and Apple SDK files.
"""
import argparse
import difflib
import hashlib
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import tarfile
import tomllib
import io
import urllib.request

ROOT = Path(__file__).resolve().parents[2]
LAB = ROOT / 'native-aot'
BUILD = LAB / 'sources/bun/build/m5-aot-release'


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def save(path, data):
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + '\n')


def git(path, *args):
    return subprocess.check_output(['git', '-C', str(path), *args])


def copy(path, destination):
    destination.parent.mkdir(parents=True, exist_ok=True)
    if path.is_symlink():
        target = os.readlink(path)
        if os.path.isabs(target):
            raise ValueError('Absolute source link: ' + str(path))
        destination.symlink_to(target)
    else:
        shutil.copyfile(path, destination)
        destination.chmod(0o755 if path.stat().st_mode & 0o111 else 0o644)
        if digest(path) != digest(destination):
            raise ValueError('Source changed during copy: ' + str(path))


def copy_tree(path, destination):
    count = 0
    for base, dirs, files in os.walk(path, followlinks=False):
        dirs[:] = [name for name in dirs if name not in ('.git', '.cache', '__pycache__', '.bin', '.npm', '.cargo')]
        for name in files:
            if name in ('.DS_Store',) or name.endswith('.pyc'):
                continue
            item = Path(base) / name
            copy(item, destination / item.relative_to(path)); count += 1
    return count


def repository(name, destination, expected):
    source = LAB / 'sources' / name
    actual = git(source, 'rev-parse', 'HEAD').decode().strip()
    if actual != expected:
        raise ValueError('Source pin differs: ' + name)
    present, absent = 0, []
    for entry in git(source, 'ls-files', '-z').split(b'\0'):
        if not entry:
            continue
        relative = Path(os.fsdecode(entry)); path = source / relative
        if not path.exists() and not path.is_symlink():
            absent.append(relative.as_posix()); continue
        if path.is_dir() and not path.is_symlink():
            raise ValueError('Unexpected gitlink: ' + str(path))
        copy(path, destination / relative); present += 1
    # Include the complete native vendors selected by the actual official build,
    # including fetched content not tracked in the Bun main tree.
    if name == 'bun':
        copy_tree(source / 'vendor', destination / 'vendor')
    return {'commit': actual, 'present_tracked_files': present, 'sparse_absent_files': len(absent),
            'sparse_checkout': git(source, 'sparse-checkout', 'list').decode().splitlines() if name == 'WebKit' else [],
            'absent_scope': 'Unselected upstream tests/platform/product trees; complete selected JSC/WTF/bmalloc/build trees included' if absent else None}


def change_notices(source_root):
    notices, patches = [], []
    for name in ('bun', 'WebKit'):
        original = LAB / 'sources' / name
        changed = git(original, 'diff', '--name-only', 'HEAD', '-z').split(b'\0')
        for entry in changed:
            if not entry:
                continue
            relative = Path(os.fsdecode(entry)); source = original / relative
            exported = source_root / 'native-aot/sources' / name / relative
            if not source.is_file() or not exported.is_file():
                raise ValueError('Missing changed source: ' + str(source))
            before = exported.read_bytes()
            marker = 'Pi Bolt modification notice: changed 2026-10-02; see PI-BOLT-CHANGES.json. Original license terms are preserved.'
            if source.suffix in ('.cpp', '.h', '.rs', '.ts', '.js'):
                prefix = '// ' + marker + '\n'
            elif source.suffix == '.py':
                prefix = '# ' + marker + '\n'
            else:
                prefix = None
            if prefix:
                text = before.decode('utf-8')
                lines = text.splitlines(keepends=True)
                index = 1 if lines and lines[0].startswith('#!') else 0
                # Preserve Python encoding-cookie placement if present.
                if source.suffix == '.py' and index < len(lines) and 'coding' in lines[index][:80]:
                    index += 1
                lines.insert(index, prefix); exported.write_text(''.join(lines))
                patches.extend(difflib.unified_diff(text.splitlines(True), exported.read_text().splitlines(True),
                    fromfile='a/native-aot/sources/' + name + '/' + relative.as_posix(),
                    tofile='b/native-aot/sources/' + name + '/' + relative.as_posix()))
            notices.append({'repository': name, 'file': relative.as_posix(), 'changed': '2026-10-02',
                'built_source_sha256': hashlib.sha256(before).hexdigest(), 'exported_source_sha256': digest(exported),
                'notice': 'Dated comment-only banner' if prefix else 'Non-code test expectation; dated adjacent inventory notice'})
    save(source_root / 'PI-BOLT-CHANGES.json', notices)
    (source_root / 'exported-comment-notices.patch').write_text(''.join(patches))
    for name in ('bun', 'WebKit'):
        save(source_root / 'native-aot/sources' / name / 'PI-BOLT-CHANGES.json', [x for x in notices if x['repository'] == name])
    return notices


def relink_kit(kit, provenance):
    text = (BUILD / 'build.ninja').read_text().replace('$\n', '')
    lines = text.splitlines()
    index = next(i for i, line in enumerate(lines) if line.startswith('build bun-profile |') and ': link ' in line)
    inputs = shlex.split(lines[index].split(': link ', 1)[1].split(' | ', 1)[0])
    flags_line = next(line for line in lines[index + 1:index + 5] if line.startswith('  ldflags = '))
    flags = shlex.split(flags_line.split(' = ', 1)[1])
    webkit = [name for name in inputs if name.startswith('deps/WebKit/lib/')]
    if set(Path(x).name for x in webkit) != {'libJavaScriptCore.a', 'libWTF.a', 'libbmalloc.a'}:
        raise ValueError('Unexpected replaceable WebKit archive set')
    files = {}
    for name in inputs:
        path = BUILD / name
        copy(path, kit / 'inputs' / name)
        files[name] = {'sha256': digest(path), 'bytes': path.stat().st_size}
    support = {'symbols.txt': LAB / 'sources/bun/src/symbols.txt', 'linker.order': BUILD / 'linker.order'}
    for name, path in support.items():
        copy(path, kit / 'support' / name)
    for i, value in enumerate(flags):
        if value.startswith('-Wl,-map,'):
            flags[i] = '-Wl,-map,@OUTPUT@/bun-profile.linker-map'
        elif value.startswith('-Wl,-object_path_lto,'):
            flags[i] = '-Wl,-object_path_lto,@OUTPUT@/bun-profile.lto.o'
        elif value.startswith('-Wl,-order_file,'):
            flags[i] = '-Wl,-order_file,@SUPPORT@/linker.order'
        elif i and flags[i - 1] == '-isysroot':
            flags[i] = '@SDK@'
        elif i and flags[i - 1] == '-exported_symbols_list':
            flags[i] = '@SUPPORT@/symbols.txt'
    if any('/Users/' in value or '/Library/' in value for value in flags):
        raise ValueError('Nonrelocatable link flag remained')
    copy(Path(__file__).with_name('relink-runtime.py'), kit / 'relink-runtime.py')
    save(kit / 'RELINK.json', {'schema': 1, 'inputs': inputs, 'files': files, 'webkit_inputs': webkit,
        'flags': flags, 'support': {name:digest(path) for name,path in support.items()},
        'runtime_sha256': provenance['compiler_sha256'], 'original_build_ninja_sha256': digest(BUILD / 'build.ninja'),
        'toolchain': (BUILD / 'toolchain-identity/cxx.txt').read_text().strip(), 'minimum_macos': 27})
    copy(LAB / 'sources/bun/LICENSE.md', kit / 'BUN-LICENSE.md')
    copy(LAB / 'sources/WebKit/Source/JavaScriptCore/COPYING.LIB', kit / 'WEBKIT-COPYING.LIB')
    (kit / 'README.md').write_text('''# M5 runtime relink kit

This executable uses statically linked JavaScriptCore/WebKit. Original file-level BSD and Library GPL terms remain in the accompanying source archive and license documents. Modification for your own use and reverse engineering for debugging such modifications are permitted by the applicable licenses; this kit adds no restrictions.

The ordered application objects, Rust rlibs, native dependency objects and three replaceable WebKit archives are included. LLVM clang23.1.2, macOS27 SDK/developer tools and an ARM64 Mac are recipient prerequisites; no Apple SDK is redistributed.

```sh
python3 relink-runtime.py --clang /absolute/llvm23/bin/clang++ --output /absolute/fresh/relinked
# After rebuilding modified JSC/WTF/bmalloc with the supplied source/profile:
python3 relink-runtime.py --clang /absolute/llvm23/bin/clang++ --webkit-libs /absolute/modified-WebKit/lib --output /absolute/fresh/modified
```

ABI-changing header/library changes may require rebuilding Bun bindings and other application objects from the source archive. The kit does not promise arbitrary archive compatibility. ThinLTO objects retain bitcode for relinking; full source is supplied as the preferred modification route.

Do not pair a modified runtime with the original Pi native image. Use the included Pi sources, npm input snapshot, build-pi.js and pi.py to regenerate BOTH the executable and native image with the new runtime. The source archive README provides that route. No signing, notarization, source rebuild or performance certification follows from an ordinary successful relink.
''')
    return len(inputs), sum(f['bytes'] for f in files.values())


def archive(directory, output):
    def source_filter(info):
        parts = Path(info.name).parts
        if any(part in ('.git', '.cache', '__pycache__', '.npm') for part in parts) or info.name.endswith('.pyc'):
            return None
        return info
    with tarfile.open(output, 'w:gz', compresslevel=3) as tar:
        tar.add(directory, arcname=directory.name, recursive=True, filter=source_filter)
    if output.stat().st_size >= 2_000_000_000:
        raise ValueError('Release asset exceeds 2 GB: ' + str(output))
    return {'file': output.name, 'bytes': output.stat().st_size, 'sha256': digest(output)}


def sanitize_build_evidence(source):
    """Redact optional reference evidence, never preferred source/object bytes."""
    facts = []
    substitutions = ((str(ROOT), '@PI_BOLT_WORKSPACE@'), (str(Path.home()), '@BUILD_USER_HOME@'))
    for path in sorted((source / 'build-evidence').rglob('*')):
        if not path.is_file() or path.is_symlink():
            continue
        before = path.read_bytes()
        try:
            text = before.decode('utf-8')
        except UnicodeDecodeError:
            continue
        for old, replacement in substitutions:
            text = text.replace(old, replacement)
        after = text.encode('utf-8')
        if after == before:
            continue
        path.write_bytes(after)
        facts.append({'file': path.relative_to(source).as_posix(),
            'original_reference_sha256': hashlib.sha256(before).hexdigest(),
            'exported_reference_sha256': hashlib.sha256(after).hexdigest(),
            'transformation': 'Build-user/workspace paths replaced by portable placeholders; reference only, not compiled/preferred-source inputs'})
    save(source / 'REFERENCE-EVIDENCE-TRANSFORMS.json', facts)
    return facts


def locked_crate(item, destination):
    name = item['name'] + '-' + item['version']
    url = 'https://static.crates.io/crates/' + item['name'] + '/' + name + '.crate'
    with urllib.request.urlopen(url, timeout=60) as response:
        data = response.read()
    if hashlib.sha256(data).hexdigest() != item['checksum']:
        raise ValueError('Registry package checksum differs: ' + name)
    destination.mkdir(parents=True)
    with tarfile.open(fileobj=io.BytesIO(data), mode='r:gz') as archive:
        for member in archive.getmembers():
            pieces = Path(member.name).parts
            if not pieces or pieces[0] != name or '..' in pieces or member.name.startswith('/'):
                raise ValueError('Invalid registry package path')
            relative = Path(*pieces[1:]); output = destination / relative
            if member.isdir():
                output.mkdir(parents=True, exist_ok=True)
            elif member.isfile():
                output.parent.mkdir(parents=True, exist_ok=True)
                output.write_bytes(archive.extractfile(member).read())
                output.chmod(0o755 if member.mode & 0o111 else 0o644)
            else:
                raise ValueError('Registry package link not supported')
    facts = {p.relative_to(destination).as_posix():digest(p) for p in destination.rglob('*') if p.is_file()}
    save(destination / '.cargo-checksum.json', {'files': facts, 'package': item['checksum']})
    return {'package': name, 'url': url, 'sha256': item['checksum'], 'bytes': len(data)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--package', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args(); output = args.output.resolve(); package = args.package.resolve()
    if output.exists():
        parser.error('Fresh output directory required')
    release = json.loads((package / 'release.json').read_text()); provenance = release['source_provenance']
    if digest(BUILD / 'bun') != provenance['compiler_sha256']:
        raise ValueError('Preserved build runtime does not match candidate compiler')
    for field, path in (('backend_patch_sha256', LAB / 'patches/jsc-image-export.patch'),
                        ('bun_patch_sha256', LAB / 'patches/bun-aot-engine-init.patch'),
                        ('build_driver_sha256', LAB / 'build-pi.js'),
                        ('bytecode_order_sha256', LAB / 'research/pi-v1-v2-workload.order')):
        if digest(path) != provenance[field]:
            raise ValueError('Candidate source input differs: ' + field)
    for name, patch in (('bun', 'bun-aot-engine-init.patch'), ('WebKit', 'jsc-image-export.patch')):
        if git(LAB / 'sources' / name, 'diff', '--binary', 'HEAD') != (LAB / 'patches' / patch).read_bytes():
            raise ValueError('Working source differs from exact compiled patch: ' + name)
    pair = {name:digest(package / name) for name in ('pi-native', 'pi-native.aot')}
    output.mkdir(parents=True); receipt = {'complete': False, 'package_pair': pair, 'source_provenance': provenance,
        'legal_clearance': False, 'published': False, 'scope': 'Matching source snapshot and relink object delivery preparation'}
    save(output / 'companion.json', receipt)
    source = output / ('pi-bolt-m5-source-' + release['version']); source.mkdir()
    repos = {}
    for name, pin in (('bun', provenance['bun']['commit']), ('WebKit', provenance['webkit']['commit']),
                      ('pi-v1.0.0', 'a13d35a742c6ef8462812a28fbe1d8c8b7431c32')):
        print('Copying repository', name, flush=True)
        repos[name] = repository(name, source / 'native-aot/sources' / name, pin)
    helper_count = 0
    for pattern in ('native-aot/*.py', 'native-aot/*.js', 'native-aot/*.sh', 'benchmarks/*.py'):
        for path in ROOT.glob(pattern):
            copy(path, source / path.relative_to(ROOT)); helper_count += 1
    for name in ('patches', 'optimizations', 'typed', 'fixtures', 'release'):
        helper_count += copy_tree(LAB / name, source / 'native-aot' / name)
    for name in ('sources.lock.json', 'research/pi-v1-v2-workload.order', 'research/pi-workload.order', 'pi-1.0-runtime/package.json',
                 'pi-1.0-runtime/package-lock.json'):
        copy(LAB / name, source / 'native-aot' / name)
    for name in ('LICENSE', 'NOTICE'):
        copy(ROOT / name, source / name)
    if (ROOT / 'LICENSES').is_dir():
        copy_tree(ROOT / 'LICENSES', source / 'LICENSES')
    print('Copying installed build inputs (not npm caches)', flush=True)
    npm_count = copy_tree(LAB / 'pi-1.0-runtime/node_modules', source / 'native-aot/pi-1.0-runtime/node_modules')
    bun_npm_count = copy_tree(LAB / 'sources/bun/node_modules', source / 'native-aot/sources/bun/node_modules')
    generated = copy_tree(BUILD / 'codegen', source / 'build-evidence/generated-code')
    for name in ('build.ninja', 'configure.json', 'toolchain-identity/cxx.txt', 'toolchain-identity/cc.txt',
                 'deps/WebKit/CMakeCache.txt'):
        copy(BUILD / name, source / 'build-evidence' / name)
    cargo = tomllib.loads((LAB / 'sources/bun/Cargo.lock').read_text())
    registry = Path.home() / '.cargo/registry/src/index.crates.io-1949cf8c6b5b557f'
    crates, fetched = [], []
    for item in cargo['package']:
        if 'source' not in item:
            continue
        name = item['name'] + '-' + item['version']; path = registry / name
        if not path.is_dir():
            print('Fetching locked crate source', name, flush=True)
            fetched.append(locked_crate(item, source / 'build-inputs/cargo-registry' / name))
        else:
            copy_tree(path, source / 'build-inputs/cargo-registry' / name)
        crates.append(name)
    modifications = change_notices(source)
    reference_transforms = sanitize_build_evidence(source)
    metadata = {'repositories': repos, 'source_provenance': provenance, 'npm_installed_files': npm_count,
        'bun_build_js_input_files': bun_npm_count, 'helper_files': helper_count, 'generated_evidence_files': generated,
        'included_locked_registry_crates': crates, 'fetched_checksum_verified_crates': fetched, 'unavailable_locked_crates': [],
        'linked_validation_profile_sha256': digest(LAB / 'research/pi-workload.order'),
        'optional_reference_evidence_transformations': reference_transforms,
        'excluded': ['Apple SDK', 'toolchain executables', 'Git internals', 'npm/Cargo caches', 'credentials', 'build outputs except generated source evidence'],
        'notice_overlay': 'Dated comment-only changes; built and exported hashes recorded in PI-BOLT-CHANGES.json',
        'legal_clearance': False}
    save(source / 'SOURCE.json', metadata)
    (source / 'README.md').write_text('''# Matching M5 source companion

This distribution includes the actual modified Bun, selected WebKit JSC/WTF/bmalloc/build trees, native vendor sources, Pi1.0 source and the installed npm inputs used for the candidate, exact lockfiles/patches/profile, generated source evidence, and available locked Cargo crate sources. Original licenses and copyright notices remain attached. Pi Bolt project additions are Apache2.0; this does not replace upstream component terms. WebKit uses mixed file-level BSD and Library GPL v2-or-later terms; see its original COPYING.LIB and file headers.

PI-BOLT-CHANGES.json records exact original build-input hashes and the exported comment-only dated notice overlay. The original two patches represent the compiled sources BEFORE the notice overlay. No original build input was altered when preparing this companion. SOURCE.json lists upstream pins, sparse-source coverage and all180 locked registry crate sources. Newly fetched crate packages were verified against Cargo.lock SHA256 checksums. This snapshot is not a complete offline Rust toolchain distribution.

The adjacent relink archive supplies the ordered application objects and replaceable static WebKit archives. Source rebuilding permits deeper ABI-changing modifications. Install LLVM clang23.1.2, an appropriate Rust nightly/toolchain selected by rust-toolchain.toml, Python3.12+, Bun1.4.2 bootstrap, Node/npm and macOS27 developer tools. Apple SDK/toolchain programs are recipient prerequisites and are not redistributed.

These exported source trees have no Git internals. Use the official driver directly with the recorded provenance rather than lab.py's Git-checkout bootstrap verification:

```sh
cd native-aot/sources/bun
GIT_SHA=37da174d500f2201793c8352f791afdd9102eab9 BUN_WEBKIT_PATH="$(cd ../WebKit && pwd)" CMAKE_BUILD_PARALLEL_LEVEL=2 /absolute/bootstrap/bun scripts/build.ts --profile=m5-aot-release --build-dir=build/m5-aot-release --webkit-version=d28f16e5cc234c19054d7edec2a2de5519500a1f -j2
```

The installed npm inputs are included; npm ci from the supplied lock is an alternative requiring registry access. The official build driver downloads pinned tools/dependencies when needed. For recipients wishing to use lab.py's exact Git guards, fetch the recorded commits into a separate checkout and apply the included two original patches, then apply exported-comment-notices.patch to that checkout layout if desired. No full clean source rebuild of this exported snapshot has been claimed.

Regenerate BOTH Pi executable and sidecar with the rebuilt/relinked runtime, from this archive root:

```sh
PI_NATIVE_ARTIFACT_DIR=/absolute/fresh/pi-build python3 native-aot/pi.py build --runtime /absolute/new/bun-profile --pipeline linked --pi-package "$PWD/native-aot/pi-1.0-runtime/node_modules/@earendil-works/pi-coding-agent" --bytecode-order "$PWD/native-aot/research/pi-v1-v2-workload.order" --priority-final-render
```

Run the included backend, linked-image, typed/tiering, worker, CLI and release-gate checks on a regenerated pair. A modified runtime must not silently retain the old sidecar. The original unsigned candidate is not notarized or certified production-ready. Relink proof, source inventory and notices are technical evidence, not legal clearance or a bit-reproducible/source-rebuild guarantee.
''')
    kit = output / ('pi-bolt-m5-relink-' + release['version']); kit.mkdir()
    print('Copying ordered link inputs', flush=True)
    count, size = relink_kit(kit, provenance)
    receipt.update(source=metadata, modified_files=len(modifications), relink_inputs=count, relink_input_bytes=size)
    save(output / 'companion.json', receipt)
    print('Compressing source and relink archives', flush=True)
    receipt['archives'] = [archive(source, output / (source.name + '.tar.gz')), archive(kit, output / (kit.name + '.tar.gz'))]
    if pair != {name:digest(package / name) for name in pair}:
        raise ValueError('Original candidate pair changed')
    for name, patch in (('bun', 'bun-aot-engine-init.patch'), ('WebKit', 'jsc-image-export.patch')):
        if git(LAB / 'sources' / name, 'diff', '--binary', 'HEAD') != (LAB / 'patches' / patch).read_bytes():
            raise ValueError('Original working source changed during export')
    receipt.update(complete=True, original_pair_unchanged=True)
    save(output / 'companion.json', receipt)
    print(json.dumps(receipt['archives'], indent=2), flush=True)


if __name__ == '__main__':
    main()
