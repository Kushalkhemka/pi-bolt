#!/usr/bin/env python3
"""Preserve available original notices. This inventory is not a clearance decision."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess

NOTICE = re.compile(r'(?:license|licence|copying|copyright|notice|authors)', re.I)
SKIP = {'.git', '__pycache__', '.cache', 'LayoutTests', 'JSTests', 'PerformanceTests', 'WebDriverTests'}
CODE = {'.h', '.hpp', '.c', '.cpp', '.mm', '.m', '.rs', '.zig', '.js', '.ts'}

def digest(data):
    return hashlib.sha256(data).hexdigest()

def walk(root, excluded=()):
    for directory, dirs, files in os.walk(root, followlinks=False):
        dirs[:] = sorted(d for d in dirs if d not in SKIP and d not in excluded)
        for name in sorted(files):
            file = Path(directory) / name
            if file.is_file():
                yield file

def git_commit(root):
    result = subprocess.run(['git', '-C', str(root), 'rev-parse', 'HEAD'],
        capture_output=True, text=True, check=False)
    return result.stdout.strip() if result.returncode == 0 else None

def copyright_header(file):
    # Preserve the exact leading comment only; never manufacture a license text.
    with file.open('rb') as stream:
        prefix = stream.read(65536)
    stripped = prefix.lstrip()
    if stripped.startswith(b'/*'):
        end = stripped.find(b'*/')
        if end < 0:
            return None
        header = stripped[:end + 2]
    elif stripped.startswith(b'//'):
        lines = []
        for line in stripped.splitlines(keepends=True):
            if not line.startswith(b'//'):
                break
            lines.append(line)
        header = b''.join(lines)
    else:
        return None
    return header if re.search(rb'copyright|\(c\)|\blicen[cs]e\b|SPDX-License-Identifier', header, re.I) else None

def main():
    root = Path(__file__).resolve().parents[2]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True, help='fresh notice directory')
    parser.add_argument('--artifact', type=Path, default=root/'native-aot/artifacts/darwin-arm64-m5-pi-v1-v2-priority')
    parser.add_argument('--pi-runtime', type=Path, default=root/'native-aot/pi-1.0-runtime')
    parser.add_argument('--pi-source', type=Path, default=root/'native-aot/sources/pi-v1.0.0')
    parser.add_argument('--bun', type=Path, default=root/'native-aot/sources/bun')
    parser.add_argument('--webkit', type=Path, default=root/'native-aot/sources/WebKit')
    parser.add_argument('--additional', action='append', default=[], metavar='LABEL=PATH',
        help='additional pinned source tree or original notice; never downloaded automatically')
    parser.add_argument('--dry-run', action='store_true', help='inspect without writing any output')
    args = parser.parse_args()
    roots = {'npm': args.pi_runtime.resolve(), 'pi': args.pi_source.resolve(),
        'bun': args.bun.resolve(), 'webkit': args.webkit.resolve()}
    # Bun injects these npm compatibility modules into the runtime. Do not lose
    # their notices when pruning its separate development node_modules trees.
    polyfills = roots['bun']/'src/node-fallbacks'
    if polyfills.exists():
        roots['bun-polyfills'] = polyfills
    export_vendor = roots['npm']/'node_modules/@earendil-works/pi-coding-agent/dist/core/export-html/vendor'
    if export_vendor.exists():
        roots['pi-export-vendor'] = export_vendor
    supplementary = Path(__file__).resolve().parent/'notices'
    if supplementary.exists():
        roots['release-notices'] = supplementary
    for extra in args.additional:
        label, separator, value = extra.partition('=')
        if not separator or not re.fullmatch(r'[A-Za-z0-9_-]+', label) or label in roots:
            parser.error('additional roots need a unique safe LABEL=PATH')
        roots[label] = Path(value).resolve()
    output = args.output.resolve()
    if output.exists():
        parser.error('output already exists; collection requires a fresh directory')
    for source in [*roots.values(), args.artifact.resolve()]:
        if source == output or source in output.parents:
            parser.error('output must be outside the input trees/artifact')
        if not source.exists():
            parser.error(f'missing input {source}')
    report = {'collection_complete': False, 'legal_clearance': False,
        'scope': 'Conservative available-source notice inventory, not an exact linked-component SBOM or a source/relink delivery bundle',
        'roots': {label: {'path': str(path), 'git_commit': git_commit(path) if path.is_dir() else None}
            for label, path in roots.items()},
        'notices': [], 'copyright_headers': [], 'packages': [], 'external_assets': [],
        'unresolved': [], 'collector_sha256': digest(Path(__file__).read_bytes())}
    provenance = supplementary/'provenance.json'
    if provenance.exists():
        proof = json.loads(provenance.read_text())
        for item in proof['files']:
            supplied = supplementary/item['relative_path']
            if digest(supplied.read_bytes()) != item['sha256']:
                raise RuntimeError(f'Supplemental upstream notice hash changed: {supplied}')
        report['supplementary_upstream_provenance'] = proof
    payloads = {}
    def preserve(file, label, kind='notice', content=None):
        raw = file.read_bytes() if content is None else content
        sha = digest(raw)
        relative = str(file.relative_to(roots[label])) if roots[label].is_dir() else file.name
        destination = f'originals/{label}/{relative}' if kind == 'notice' else f'headers/{sha}.txt'
        existing = payloads.get(destination)
        if existing is not None and existing != raw:
            raise RuntimeError(f'Notice destination collision {destination}')
        payloads[destination] = raw
        item = {'source_root': label, 'source_relative_path': relative, 'sha256': sha,
            'bytes': len(raw), 'destination': destination}
        report['notices' if kind == 'notice' else 'copyright_headers'].append(item)
        return item
    for label, source in roots.items():
        files = [source] if source.is_file() else walk(source, ('node_modules',) if label in ('bun','pi') else ())
        for file in files:
            if NOTICE.search(file.name) and file.suffix.lower() not in {'.png','.jpg','.wasm','.node','.map','.html','.js','.ts','.cpp','.h','.rs'}:
                preserve(file, label)
            if file.name in {'Cargo.lock','Cargo.toml','npm-shrinkwrap.json','package-lock.json'}:
                # Pin dependency evidence without treating manifests as permission texts.
                payloads[f'metadata/{label}/{file.relative_to(source)}'] = file.read_bytes()
            if label == 'release-notices' and file.name in {'provenance.json','.gitmodules','Makefile'}:
                payloads[f'metadata/{label}/{file.relative_to(source)}'] = file.read_bytes()
            if label == 'npm' and file.name == 'package.json':
                try:
                    package = json.loads(file.read_text())
                except (ValueError, UnicodeError):
                    continue
                if not package.get('name') or not package.get('version'):
                    continue
                own = [p for p in file.parent.iterdir() if p.is_file() and NOTICE.search(p.name)]
                row = {'name': package['name'], 'version': package['version'],
                    'license_declared': package.get('license', package.get('licenses')),
                    'repository': package.get('repository'), 'package_json_relative_path': str(file.relative_to(source)),
                    'package_json_sha256': digest(file.read_bytes()),
                    'top_level_original_notices': [str(p.relative_to(source)) for p in own]}
                report['packages'].append(row)
                payloads[f'metadata/npm/{file.relative_to(source)}'] = file.read_bytes()
                if not own:
                    report['unresolved'].append({'category':'package_has_no_top_level_notice',
                        'package': package['name'], 'version': package['version'],
                        'evidence': row['package_json_relative_path'], 'declared_license_is_not_a_notice': True})
    # JSC/WTF licenses commonly live in file headers, not a single project LICENSE.
    for relative in ['Source/JavaScriptCore','Source/WTF','Source/bmalloc']:
        for file in walk(roots['webkit']/relative):
            if file.suffix in CODE:
                header = copyright_header(file)
                if header:
                    preserve(file,'webkit','header',header)
    for file in walk(roots['bun']/'src', ('node_modules',)):
        if file.suffix in CODE:
            header = copyright_header(file)
            if header:
                preserve(file,'bun','header',header)
    if 'pi-export-vendor' in roots:
        for file in walk(roots['pi-export-vendor']):
            if file.suffix == '.js':
                header = copyright_header(file)
                if header:
                    preserve(file,'pi-export-vendor','header',header)
    for file in sorted(args.artifact.iterdir()):
        if file.is_symlink():
            target = file.resolve(strict=True)
            inventory = []
            for asset in ([target] if target.is_file() else walk(target)):
                inventory.append({'relative_path': asset.name if target.is_file() else str(asset.relative_to(target)),
                    'sha256': digest(asset.read_bytes()), 'bytes': asset.stat().st_size})
            report['external_assets'].append({'artifact_path':file.name,'symlink_target':str(target),
                'requires_dereference_for_portability':True,'files':inventory})
    report['unresolved'].extend([
        {'category':'static_linked_lgpl_source_and_relink_delivery',
            'evidence':'bun/LICENSE.md and webkit/Source/JavaScriptCore/COPYING.LIB',
            'needed':'Review exact LGPL versions/headers; provide corresponding modified library source and an applicable relinking/object or qualifying source distribution path with build instructions. Notice collection does not establish this.'},
        {'category':'quickjs_ng_and_wasm_transitive_notices',
            'needed':'Pinned release-tag QuickJS-NG MIT notice is supplied in release-notices when available. Exact installed quickjs.wasm matches npm3.6.2 tarball; published npm metadata has no gitHead. Validate tag-to-binary build provenance and any remaining Wasm toolchain/dependency notices; notice collection does not prove source reproducibility.'},
        {'category':'rust_native_linked_dependency_completeness',
            'needed':'Reconcile Bun and Photon Rust dependency licenses/notices against exact Cargo.lock/build/link inputs; source notice scan is not proof every compiled crate is covered.'},
        {'category':'native_apple_frameworks_and_assets',
            'needed':'Keep Pi native helper source notice, audit supplied artwork and helper third-party code; Apple SDK/framework license rights are not established by open-source notices. Do not ship Apple SDK files.'},
        {'category':'html_export_vendored_versions',
            'evidence':'Export vendor headers identify marked18.0.5 and highlight.js11.9.0; installed npm package versions differ. Original headers preserved separately.',
            'needed':'Exact tagged full notices are supplied in release-notices when available. Retain these in addition to vendored headers; do not treat differing npm package notices as exact version provenance.'}])
    report['collection_complete'] = True
    report['unique_notice_payloads'] = len(payloads)
    summary = {'collection_complete':True,'legal_clearance':False,'notices':len(report['notices']),
        'header_origins':len(report['copyright_headers']),'packages':len(report['packages']),
        'asset_links':len(report['external_assets']),'unresolved':len(report['unresolved']),
        'output':str(output),'dry_run':args.dry_run}
    if not args.dry_run:
        output.mkdir(parents=True,exist_ok=False)
        for relative, content in sorted(payloads.items()):
            destination = output/relative
            destination.parent.mkdir(parents=True,exist_ok=True)
            destination.write_bytes(content)
            if digest(destination.read_bytes()) != digest(content):
                raise RuntimeError(f'Copied notice mismatch {relative}')
        (output/'inventory.json').write_text(json.dumps(report,indent=2,sort_keys=True)+'\n')
        (output/'README.txt').write_text('Original notices are copied byte-for-byte. Header extracts are identified separately. This conservative inventory may include source components not linked into the release. Collection complete does not mean legal clearance, complete source availability, or successful relinking. Review inventory.json unresolved items before redistribution.\n')
    print(json.dumps(summary,sort_keys=True))

if __name__ == '__main__':
    main()
