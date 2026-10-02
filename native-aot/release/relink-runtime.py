#!/usr/bin/env python3
"""Relink the supplied Bun application objects with replaceable WebKit archives.

Uses the recipient's LLVM23 and Apple SDK. Does not overwrite a supplied binary,
sign, notarize, or reuse a native Pi sidecar with a modified runtime.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import shlex
import subprocess


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True, help='Fresh external directory')
    parser.add_argument('--webkit-libs', type=Path, help='Replacement libWTF.a/libJavaScriptCore.a/libbmalloc.a directory')
    parser.add_argument('--clang', default='clang++', help='LLVM23 clang++ executable')
    parser.add_argument('--sdk', type=Path, help='Recipient Apple macOS SDK; defaults to xcrun --show-sdk-path')
    args = parser.parse_args()
    kit = Path(__file__).resolve().parent
    output = args.output.resolve()
    if output.exists() or output.is_relative_to(kit):
        parser.error('Choose a fresh directory outside the relink kit')
    if platform.system() != 'Darwin' or platform.machine() != 'arm64':
        parser.error('These objects require macOS ARM64')
    manifest = json.loads((kit / 'RELINK.json').read_text())
    version = subprocess.check_output([args.clang, '--version'], text=True)
    if 'clang version 23.1.2' not in version:
        parser.error('The preserved ThinLTO objects require LLVM clang23.1.2')
    sdk = args.sdk.resolve() if args.sdk else Path(subprocess.check_output(['xcrun', '--show-sdk-path'], text=True).strip())
    if not sdk.is_dir():
        parser.error('A recipient-installed Apple SDK is required')
    inputs, facts = [], {}
    for name in manifest['inputs']:
        original = kit / 'inputs' / name
        if not original.is_file() or digest(original) != manifest['files'][name]['sha256']:
            raise ValueError('Preserved input changed: ' + name)
        selected = args.webkit_libs.resolve() / original.name if args.webkit_libs and name in manifest['webkit_inputs'] else original
        if not selected.is_file():
            raise ValueError('Missing replacement archive: ' + str(selected))
        inputs.append(str(selected)); facts[name] = {'sha256': digest(selected), 'bytes': selected.stat().st_size}
    for name, expected in manifest['support'].items():
        if digest(kit / 'support' / name) != expected:
            raise ValueError('Support file changed: ' + name)
    output.mkdir(parents=True)
    receipt = {'complete': False, 'scope': 'Relink only; not source rebuild, byte-identical reproduction, signing or release certification',
               'clang_version': version, 'sdk': str(sdk), 'selected_inputs': facts,
               'replacement_webkit': args.webkit_libs is not None, 'source_runtime_sha256': manifest['runtime_sha256']}
    path = output / 'receipt.json'
    path.write_text(json.dumps(receipt, indent=2) + '\n')
    rsp = output / 'objects.rsp'
    rsp.write_text('\n'.join(shlex.quote(name) for name in inputs) + '\n')
    flags = []
    for value in manifest['flags']:
        flags.append(value.replace('@SDK@', str(sdk)).replace('@SUPPORT@', str(kit / 'support')).replace('@OUTPUT@', str(output)))
    command = [args.clang, '@' + str(rsp), *flags, '-o', str(output / 'bun-profile')]
    receipt['command'] = command
    env = {k:v for k,v in os.environ.items() if not k.startswith(('BUN_JSC_', 'JSC_', 'PI_NATIVE_AOT_'))}
    try:
        with (output / 'link.log').open('x') as log:
            result = subprocess.run(command, env=env, stdout=log, stderr=subprocess.STDOUT)
        receipt['link_exit_code'] = result.returncode
        if result.returncode:
            raise RuntimeError('Link failed; inspect link.log')
        # The fresh result is built by this relink command, then executed normally.
        probe = subprocess.run([str(output / 'bun-profile'), '--version'], env=env, text=True, capture_output=True, timeout=30)
        (output / 'version.stdout').write_text(probe.stdout); (output / 'version.stderr').write_text(probe.stderr)
        receipt.update(version_exit_code=probe.returncode, version=probe.stdout.strip(),
                       relinked_sha256=digest(output / 'bun-profile'), relinked_bytes=(output / 'bun-profile').stat().st_size)
        if probe.returncode or not probe.stdout.strip():
            raise RuntimeError('Relinked version check failed')
        receipt['complete'] = True
    except BaseException as error:
        receipt['error'] = repr(error)
        raise
    finally:
        path.write_text(json.dumps(receipt, indent=2) + '\n')
    print('PASS: preserved inputs relinked; version', receipt['version'])


if __name__ == '__main__':
    main()
