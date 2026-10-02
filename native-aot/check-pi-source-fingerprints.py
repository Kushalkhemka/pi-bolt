#!/usr/bin/env python3
"""Bounded provenance checks; temporary copies only, no engine execution."""
import argparse
import json
from pathlib import Path
import shutil
import tempfile

import pi


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('package')
    args = parser.parse_args()
    package = Path(args.package).resolve()
    version = json.loads((package / 'package.json').read_text())['version']
    original = pi.source_fingerprints(package)
    if version == '0.85.1':
        if len(original) != 7:
            raise RuntimeError('Historical Pi source fingerprint scope changed')
        print(json.dumps({'pi_version': version, 'source_inputs': len(original), 'passed': True}))
        return
    if version != '1.0.0':
        raise RuntimeError('This compatibility test targets exactly Pi 0.85.1/1.0.0')
    worker = 'dist/extensions/codemode/worker.js'
    wasm = 'node_modules/quickjs-wasi/quickjs.wasm'
    loader = 'node_modules/@earendil-works/pi-tui/dist/native-module-path.js'
    image_paths = ('dist/utils/image-resize.js', 'dist/utils/image-resize-core.js',
                   'dist/utils/image-process.js', 'dist/utils/photon.js')
    for relative in (worker, wasm, loader, *image_paths):
        if str(package / relative) not in original:
            raise RuntimeError('Missing provenance input: ' + relative)
    with tempfile.TemporaryDirectory(prefix='pi-1.0-inputs-check-') as temporary:
        copied = Path(temporary)
        for name in original:
            relative = Path(name).relative_to(package)
            destination = copied / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(name, destination)
        before = pi.source_fingerprints(copied)
        (copied / worker).write_bytes((copied / worker).read_bytes() + b'\n// Provenance test\n')
        after = pi.source_fingerprints(copied)
        changed = {name for name in before if before[name] != after[name]}
        if changed != {str(copied / worker)}:
            raise RuntimeError('Codemode worker mutation was not isolated/detected')
        for relative in image_paths:
            path = copied / relative
            previous = pi.source_fingerprints(copied)
            path.write_bytes(path.read_bytes() + b'\n// Image provenance test\n')
            current = pi.source_fingerprints(copied)
            if {name for name in previous if previous[name] != current[name]} != {str(path)}:
                raise RuntimeError('Image source mutation was not isolated/detected: ' + relative)
        after = pi.source_fingerprints(copied)
        added = copied / 'node_modules/@earendil-works/pi-tui/native/linux/prebuilds/linux-x64/test-only.node'
        added.parent.mkdir(parents=True, exist_ok=True)
        added.write_bytes(b'Provenance marker; never loaded')
        with_added = pi.source_fingerprints(copied)
        if set(with_added) - set(after) != {str(added)}:
            raise RuntimeError('Added native helper was not detected')
        added.unlink()
        if pi.source_fingerprints(copied) != after:
            raise RuntimeError('Removed native helper was not detected')
    if pi.source_fingerprints(package) != original:
        raise RuntimeError('Installed package changed during the read-only check')
    print(json.dumps({'pi_version': version, 'source_inputs': len(original), 'passed': True,
                      'cases': ['codemode_worker_change', 'four_image_source_changes',
                                'native_helper_add_remove', 'original_inputs_unchanged']}))


if __name__ == '__main__':
    main()
