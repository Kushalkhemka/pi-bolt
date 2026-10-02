"""Small original Pi input snapshots shared by native/stable build tooling."""
import hashlib
from pathlib import Path

PI_INPUTS = ('package.json', 'dist/bun/cli.js', 'dist/utils/image-resize-worker.js',
             'dist/modes/interactive/interactive-mode.js',
             'node_modules/@earendil-works/pi-ai/dist/utils/sanitize-unicode.js',
             'node_modules/@earendil-works/pi-tui/dist/tui.js',
             'node_modules/@earendil-works/pi-tui/dist/components/editor.js')


def fingerprint(path):
    path = Path(path)
    with path.open('rb') as stream:
        sha = hashlib.file_digest(stream, 'sha256').hexdigest()
    return {'sha256': sha, 'bytes': path.stat().st_size}


def pi_input_sources(package):
    package = Path(package).resolve()
    return {str(package / name): fingerprint(package / name) for name in PI_INPUTS}


def verify_unchanged(before):
    for path, expected in before.items():
        if fingerprint(path) != expected:
            raise RuntimeError(f'Original Pi input changed while building: {path}')
