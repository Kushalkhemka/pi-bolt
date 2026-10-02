#!/usr/bin/env python3
"""Snapshot a matched M5 runtime into a relocatable, unsigned candidate.

This never publishes, signs, or calls an unsigned candidate production-ready.
Python >=3.11 is needed by maintainers only; runtime/install use macOS tools.
"""
import argparse
import gzip
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import struct
import subprocess
import tarfile

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
PROJECT_LICENSE = ROOT/'LICENSES/Apache-2.0.txt' if (ROOT/'LICENSES/Apache-2.0.txt').is_file() else ROOT/'LICENSE'
ASSETS = ('package.json', 'theme', 'assets', 'export-html', 'docs', 'examples',
          'README.md', 'CHANGELOG.md', 'photon_rs_bg.wasm', 'native')


def require(ok, message):
    if not ok:
        raise ValueError(message)


def fact(path):
    with path.open('rb') as f:
        digest = hashlib.file_digest(f, 'sha256').hexdigest()
    return {'sha256': digest, 'bytes': path.stat().st_size,
            'mode': stat.S_IMODE(path.stat().st_mode)}


def inventory(root):
    result = {}
    for p in sorted(root.rglob('*')):
        require(not p.is_symlink(), 'Package symlink forbidden: ' + str(p))
        name = p.relative_to(root).as_posix()
        require(not any(ord(c) < 32 or ord(c) == 127 for c in name), 'Control character in package path')
        if p.is_file() and name not in ('release.json', 'SHA256SUMS'):
            result[name] = fact(p)
        elif not p.is_dir() and not p.is_file():
            raise ValueError('Special file forbidden: ' + str(p))
    return result


def seal(root, manifest):
    manifest['files'] = inventory(root)
    (root / 'release.json').write_text(json.dumps(manifest, indent=2, sort_keys=True) + '\n')
    rows = {**manifest['files'], 'release.json': fact(root / 'release.json')}
    (root / 'SHA256SUMS').write_text(''.join(f"{v['sha256']}  {k}\n" for k, v in sorted(rows.items())))


def verify(root):
    manifest = json.loads((root / 'release.json').read_text())
    require(manifest['schema'] == 1 and manifest['target'] == 'darwin-arm64', 'Unknown release format')
    require(inventory(root) == manifest['files'], 'Package file hashes/modes/inventory differ')
    expected = ''.join(f"{v['sha256']}  {k}\n" for k,v in sorted(
        {**manifest['files'], 'release.json': fact(root / 'release.json')}.items()))
    require((root / 'SHA256SUMS').read_text() == expected, 'Checksum table differs')
    for field in ('executable', 'image', 'package_root'):
        p = (root / manifest[field]).resolve()
        require(p.is_relative_to(root.resolve()), 'Manifest path leaves package')
    for name in manifest['launchers'].values():
        require(not Path(name).is_absolute() and (root / name).resolve().is_relative_to(root.resolve()), 'Launcher escapes package')
    return manifest


def archive(root, output):
    require(not output.exists(), 'Archive already exists')
    # Canonical ownership/time; no xattrs, symlinks, absolute paths or host usernames.
    with output.open('xb') as raw, gzip.GzipFile(filename='', mode='wb', fileobj=raw, mtime=0) as gz:
        with tarfile.open(fileobj=gz, mode='w', format=tarfile.PAX_FORMAT) as tar:
            for p in [root] + sorted(root.rglob('*')):
                info = tar.gettarinfo(str(p), (Path(root.name) / p.relative_to(root)).as_posix())
                require(info.isfile() or info.isdir(), 'Only regular files/directories allowed')
                info.uid = info.gid = 0; info.uname = info.gname = ''; info.mtime = 0
                info.mode = 0o755 if p.is_dir() or os.access(p, os.X_OK) else 0o644
                info.pax_headers = {}
                with p.open('rb') if p.is_file() else open(os.devnull, 'rb') as f:
                    tar.addfile(info, f if p.is_file() else None)


def build(args):
    artifact = args.artifact.resolve()
    m = json.loads((artifact / 'manifest.json').read_text())
    require(m['pi_version'] == '1.0.0' and m['target'] == 'darwin-arm64', 'Only the pinned Pi1.0 M5 artifact is supported')
    require(m['runtime_sha256'] == '8f212e04300c762ef712a515dfa9a5f478a5600cef5333038991cce2c4a073d5', 'Unknown M5 compiler; revalidate before packaging')
    require(m.get('standalone_photon_wasm_relocated') is True, 'Rebuild with the relocatable Photon Wasm loader')
    require(m.get('image_worker_termination_awaited') is True, 'Rebuild with awaited image worker termination')
    require(m.get('native_version_verified') and m.get('hybrid_version_verified') and m.get('file_mapping_verified'),
            'Run pi.py verify before preparing a candidate')
    require(m['build_driver_sha256'] == fact(ROOT/'native-aot/build-pi.js')['sha256'], 'Build driver differs from artifact')
    require(m['standalone_workers_optimizer_sha256'] == fact(ROOT/'native-aot/optimizations/standalone-workers.js')['sha256'], 'Worker/Photon transform differs')
    require(re.fullmatch(r'[0-9]+\.[0-9]+\.[0-9]+-m5\.[0-9]+', args.version), 'Version must look like 1.0.0-m5.1')
    output = args.output.resolve(); require(not output.exists(), 'Use a fresh package directory')
    original = {name: fact(artifact / name) for name in ('pi-native', 'pi-native.aot')}
    require(original['pi-native']['sha256'] == m['executable_sha256'] and original['pi-native.aot']['sha256'] == m['image_sha256'], 'Artifact differs from validation manifest')
    require(struct.unpack_from('<II', (artifact/'pi-native').read_bytes()[:8]) == (0xfeedfacf,0x0100000c), 'Expected ARM64 Mach-O')
    output.mkdir(parents=True)
    for name in ('pi-native', 'pi-native.aot') + ASSETS:
        src = artifact / name; require(src.exists(), 'Required asset missing: ' + name)
        if src.is_dir(): shutil.copytree(src, output/name, symlinks=False, copy_function=shutil.copyfile)
        else: shutil.copyfile(src, output/name)
    # Keep executable native modules executable only if their source was executable.
    for p in output.rglob('*'):
        if p.is_file(): p.chmod(0o644)
    (output/'pi-native').chmod(0o755)
    (output/'bin').mkdir()
    template = (HERE/'launcher.sh').read_text()
    for mode, name, tier in [('hybrid','pi',''), ('tier10000','pi-tier10000',
                            'export BUN_JSC_useAOTNativeTiering=true\nexport BUN_JSC_thresholdForAOTNativeTiering=10000')]:
        text=template.replace('@EXECUTABLE_BYTES@',str(original['pi-native']['bytes'])).replace('@IMAGE_BYTES@',str(original['pi-native.aot']['bytes'])).replace('@TIERING@',tier)
        (output/'bin'/name).write_text(text);(output/'bin'/name).chmod(0o755)
    shutil.copyfile(HERE/'doctor.sh',output/'bin/pi-doctor');(output/'bin/pi-doctor').chmod(0o755)
    shutil.copyfile(PROJECT_LICENSE, output/'PI-BOLT-LICENSE')
    shutil.copyfile(ROOT/'NOTICE', output/'PI-BOLT-NOTICE')
    package = Path(m['pi_package']).resolve()
    require(package.name == 'pi-coding-agent' and package.parent.name == '@earendil-works'
            and package.parent.parent.name == 'node_modules', 'Unknown Pi npm installation layout')
    subprocess.run(['python3',str(HERE/'collect-licenses.py'),'--output',str(output/'licenses'),
                    '--artifact',str(artifact),'--pi-runtime',str(package.parent.parent.parent),
                    '--bun',str(args.bun_source.resolve()),'--webkit',str(args.webkit_source.resolve()),
                    '--pi-source',str(args.pi_source.resolve())],check=True)
    source_provenance = {'bun':m['sources']['bun'],'webkit':m['sources']['webkit'],
        'compiler_sha256':m['runtime_sha256'], 'executable_sha256':m['executable_sha256'], 'image_sha256':m['image_sha256'],
        'backend_patch_sha256':m['backend_patch_sha256'],'bun_patch_sha256':m['bun_patch_sha256'],
        'build_driver_sha256':m['build_driver_sha256'],'bytecode_order_sha256':m['bytecode_order_sha256'],
        'aot_compilation':m['aot_compilation'],
        'packaging_inputs':{p.relative_to(ROOT).as_posix():fact(p)['sha256'] for p in
            (Path(__file__),HERE/'launcher.sh',HERE/'doctor.sh',HERE/'collect-licenses.py',PROJECT_LICENSE,ROOT/'NOTICE')}}
    manifest={'schema':1,'version':args.version,'pi_version':'1.0.0','target':'darwin-arm64',
        'minimum_macos':'27.0','cpu_family':'Apple M5','tested_cpu':'Apple M5 (base)',
        'launchers':{'hybrid':'bin/pi','tier10000':'bin/pi-tier10000'},'package_root':'.','executable':'pi-native','image':'pi-native.aot',
        'release_status':'unsigned-candidate','signing':{'developer_id':False,'notarized':False},
        'source_provenance':source_provenance,'production_gates':{
            'portable_acceptance':False,'signed_hardened_acceptance':False,'notarization':False,
            'quarantined_install':False,'second_m5':False,'source_relink_delivery':False,'provider_soak':False},
        'limitations':['macOS27+, M5 family only; base M5 tested','Unsigned candidate; not approved for public binary release',
            'Installed checksums detect corruption, not independent publisher authentication','No universal performance or complete provider/extension certification']}
    seal(output,manifest);verify(output)
    require({name:fact(artifact/name) for name in original}==original,'Source artifact changed')
    print(output)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    commands=parser.add_subparsers(dest='command',required=True)
    b=commands.add_parser('build');b.add_argument('--artifact',type=Path,required=True);b.add_argument('--output',type=Path,required=True);b.add_argument('--version',default='1.0.0-m5.1')
    b.add_argument('--bun-source',type=Path,default=ROOT/'native-aot/sources/bun')
    b.add_argument('--webkit-source',type=Path,default=ROOT/'native-aot/sources/WebKit')
    b.add_argument('--pi-source',type=Path,default=ROOT/'native-aot/sources/pi-v1.0.0')
    v=commands.add_parser('verify');v.add_argument('package',type=Path)
    a=commands.add_parser('archive');a.add_argument('package',type=Path);a.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    try:
        if args.command=='build':build(args)
        elif args.command=='verify':verify(args.package.resolve());print('PASS: complete package inventory and matched checksums')
        else:
            package=args.package.resolve();verify(package);archive(package,args.output.resolve())
            data=fact(args.output.resolve());print(data['sha256']+'  '+args.output.name)
    except (OSError,ValueError,KeyError,subprocess.SubprocessError) as e:parser.exit(1,'error: '+str(e)+'\n')

if __name__=='__main__':main()
