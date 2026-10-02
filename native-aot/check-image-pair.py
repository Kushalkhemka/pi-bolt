#!/usr/bin/env python3
"""Check matched image selection and stale-engine rejection in fresh processes."""
import argparse
import json
from pathlib import Path
import re
import subprocess

import lab
import pi


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--artifacts', type=Path, required=True)
    parser.add_argument('--stale-image', type=Path, required=True)
    args = parser.parse_args()
    artifacts = args.artifacts.resolve()
    stale = args.stale_image.resolve()
    data = json.loads((artifacts / 'manifest.json').read_text())
    for kind in ('executable', 'image'):
        if pi.digest(Path(data[kind])) != data[kind + '_sha256']:
            raise RuntimeError(f'{kind} differs from its verified manifest')
    results = []
    for mapped in (True, False):
        for compatible in (True, False):
            for mode in ('native', 'hybrid'):
                image = Path(data['image']) if compatible else stale
                env = lab.jsc_environment()
                env.update(pi.configuration(data, mode)['env'])
                env.update(BUN_JSC_aotImagePath=str(image),
                           BUN_JSC_useAOTMappedImages=str(mapped).lower(),
                           BUN_JSC_verboseAOTCompilation='true')
                result = subprocess.run([data['executable'], '--version'], env=env,
                                        capture_output=True, text=True, timeout=30)
                label = f'{mode}-{"mapped" if mapped else "copied"}-{"matched" if compatible else "stale"}'
                log = artifacts / ('pair-' + label + '.log')
                log.write_text(result.stderr)
                sites = re.findall(r'^AOT: .* is at .* size \d+', result.stderr, re.MULTILINE)
                if result.returncode or result.stdout.strip() != data['pi_version']:
                    raise RuntimeError(f'{label}: version probe failed; see {log}')
                if compatible:
                    if not sites or 'is not an image for this engine' in result.stderr:
                        raise RuntimeError(f'{label}: matched image was not selected')
                elif sites or 'is not an image for this engine' not in result.stderr:
                    raise RuntimeError(f'{label}: stale image was not rejected')
                results.append({'case': label, 'exit': result.returncode, 'native_sites': len(sites),
                                'log': str(log)})
    report = {'executable_sha256': data['executable_sha256'],
              'matched_image_sha256': data['image_sha256'],
              'stale_image': str(stale), 'stale_image_sha256': pi.digest(stale),
              'checker_sha256': pi.digest(Path(__file__)), 'results': results,
              'scope': 'Version probe, both loaders and modes; stale image rejection falls back to JIT/interpreter.'}
    (artifacts / 'image-pair-validation.json').write_text(json.dumps(report, indent=2) + '\n')
    print('PASS: 8 matched/stale image checks across native/hybrid and mapped/copied loaders')


if __name__ == '__main__':
    main()
