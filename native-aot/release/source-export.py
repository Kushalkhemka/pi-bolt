#!/usr/bin/env python3
"""Create a small reviewable GitHub source export; never creates/pushes a repo."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil

ROOT=Path(__file__).resolve().parents[2]

def digest(path):
    with path.open('rb') as stream:return hashlib.file_digest(stream,'sha256').hexdigest()

def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--output',type=Path,required=True);args=parser.parse_args()
    output=args.output.resolve()
    if output.exists():parser.error('Choose a fresh export directory')
    selected=set()
    if (ROOT/'LICENSES').is_dir():
        selected.update(p for p in (ROOT/'LICENSES').rglob('*') if p.is_file())
    if (ROOT/'assets/pi-bolt.svg').is_file():
        selected.add(ROOT/'assets/pi-bolt.svg')
    for name in ('README.md','LICENSE','NOTICE','.gitignore','.gitattributes','native-aot/sources.lock.json',
                 'native-aot/pi-1.0-runtime/package.json','native-aot/pi-1.0-runtime/package-lock.json',
                 'native-aot/research/pi-v1-v2-workload.order'):
        selected.add(ROOT/name)
    for pattern in ('native-aot/*.py','native-aot/*.js','native-aot/*.sh','benchmarks/*.py'):
        selected.update(ROOT.glob(pattern))
    for directory in ('docs','native-aot/patches','native-aot/fixtures','native-aot/optimizations','native-aot/typed','native-aot/release','.github/workflows'):
        selected.update(p for p in (ROOT/directory).rglob('*') if p.is_file() and '__pycache__' not in p.parts and p.suffix!='.pyc')
    # No arbitrary workspace directory enumeration, credentials, .git or npm caches.
    before={p.relative_to(ROOT).as_posix():digest(p) for p in sorted(selected)}
    output.mkdir(parents=True)
    for p in sorted(selected):
        if p.is_symlink():raise ValueError('Source symlink not allowed: '+str(p))
        if p.stat().st_size>5_000_000:raise ValueError('Unexpected large source input: '+str(p))
        relative=p.relative_to(ROOT);destination=output/relative;destination.parent.mkdir(parents=True,exist_ok=True)
        shutil.copyfile(p,destination);destination.chmod(0o755 if p.suffix=='.sh' else 0o644)
        if digest(destination)!=before[relative.as_posix()]:raise ValueError('Copy hash differs')
    if before!={p.relative_to(ROOT).as_posix():digest(p) for p in sorted(selected)}:raise ValueError('Source inputs changed during export')
    receipt={'schema':1,'scope':'Selected source export; upstream engine trees are fetched using exact pins, not included',
             'files':before,'files_count':len(before),'git_initialized':False,'published':False,
             'project_license':'Apache-2.0','binary_distribution':'unsigned candidate; remaining gates documented'}
    (output/'SOURCE-EXPORT.json').write_text(json.dumps(receipt,indent=2,sort_keys=True)+'\n')
    print(output);print('PASS:',len(before),'source files copied with matching hashes')

if __name__=='__main__':main()
