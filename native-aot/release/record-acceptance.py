#!/usr/bin/env python3
"""Attach completed unsigned acceptance to metadata without changing runtime files."""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path

HERE=Path(__file__).resolve().parent
spec=importlib.util.spec_from_file_location('release_package',HERE/'package.py')
package=importlib.util.module_from_spec(spec);spec.loader.exec_module(package)

def sha(path):
    with path.open('rb') as stream:return hashlib.file_digest(stream,'sha256').hexdigest()

def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--package',type=Path,required=True);parser.add_argument('--receipt',type=Path,required=True);parser.add_argument('--audit',type=Path,required=True);args=parser.parse_args()
    root=args.package.resolve();manifest=package.verify(root)
    receipt=json.loads(args.receipt.read_text());audit=json.loads(args.audit.read_text())
    package.require(manifest['release_status']=='unsigned-candidate','Only unsigned metadata finalization supported')
    package.require(receipt['complete'] and audit['complete'] and audit['package_unchanged'] and audit['frozen_inputs_unchanged'],'Acceptance/audit must pass')
    package.require(receipt['before']==receipt['after'],'Package changed during acceptance')
    package.require(receipt['release_version']==manifest['version'],'Release version differs')
    package.require(receipt['gates']==audit['gates']=={'rpc':30,'commands':52,'workers':14,'pty':24} and audit['worker_exits']==20,'Both policies/layouts and all worker exits required')
    denial=receipt.get('source_read_denial') or {}
    package.require(denial.get('relocated_read_probe_exit')==0 and denial.get('source_read_probe_exit')!=0,'Source-denied relocation proof required')
    before=receipt['before']
    package.require({k:v for k,v in before.items() if k not in ('release.json','SHA256SUMS')}==manifest['files'],'Tested package contents differ')
    package.require(package.fact(root/'release.json')==before['release.json'],'Release metadata differs from tested state')
    package.require(package.fact(root/'SHA256SUMS')==before['SHA256SUMS'],'Checksum table differs from tested state')
    for mode in ('hybrid','tier10000'):
        proof=audit['native_selection'][mode]
        package.require(proof['version']=='1.0.0' and proof['native_sites']>0 and proof['mapped_image'] and proof['prelinked_graph']['version']==4,'Native mapped selection proof required')
    old_files=package.inventory(root)
    manifest['production_gates']['portable_acceptance']=True
    manifest['validation']={'unsigned_portable':{'receipt_sha256':sha(args.receipt),'audit_sha256':sha(args.audit),
        'tested_release_metadata_sha256':before['release.json']['sha256'],'gates':receipt['gates'],
        'worker_exits':20,'native_selection':audit['native_selection'],
        'scope':'Same runtime/assets/launchers; only release.json/SHA256SUMS updated to attach this completed single-M5 unsigned proof'}}
    package.seal(root,manifest);package.verify(root)
    package.require(package.inventory(root)==old_files,'Runtime/package files changed while recording metadata')
    print('PASS: completed unsigned portable acceptance attached; runtime/assets/launchers unchanged')

if __name__=='__main__':main()
