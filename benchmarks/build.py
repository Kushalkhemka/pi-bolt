#!/usr/bin/env python3
"""Rebuild benchmark binaries from the installed Pi package, without modifying it."""
import json
from pathlib import Path
import subprocess
import benchmark as b

out = b.ROOT / 'bin'
out.mkdir(exist_ok=True)
entry = b.PI / 'dist/bun/cli.js'
worker = b.PI / 'dist/utils/image-resize-worker.js'
common = [str(b.BUN), 'build', '--compile', '--no-compile-autoload-bunfig', '--no-compile-autoload-dotenv']
builds = {'pi-bun-compiled': [], 'pi-bun-bytecode': ['--bytecode', '--format=esm', '--minify', '--sourcemap']}
for name, flags in builds.items():
    command = common + flags + [str(entry), str(worker), '--outfile', str(out / name)]
    subprocess.run(command, check=True)
assets = {'package.json': b.PI / 'package.json', 'theme': b.PI / 'dist/modes/interactive/theme',
    'assets': b.PI / 'dist/modes/interactive/assets', 'export-html': b.PI / 'dist/core/export-html',
    'docs': b.PI / 'docs', 'examples': b.PI / 'examples',
    'photon_rs_bg.wasm': b.PI / 'node_modules/@silvia-odwyer/photon-node/photon_rs_bg.wasm'}
for name, target in assets.items():
    link = out / name
    if target.exists() and not link.exists():
        link.symlink_to(target, target_is_directory=target.is_dir())
(b.ROOT / 'build-info.json').write_text(json.dumps({
    'pi_version': json.loads((b.PI / 'package.json').read_text())['version'],
    'bun_version': subprocess.check_output([str(b.BUN), '--version'], text=True).strip(),
    'builds': {name: {'flags': flags, 'size_bytes': (out / name).stat().st_size}
               for name, flags in builds.items()},
    'common_flags': common[1:], 'entry': str(entry), 'worker': str(worker),
    'assets_are_symlinks_to_installed_package': True}, indent=2))
