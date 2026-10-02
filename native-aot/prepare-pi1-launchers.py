#!/usr/bin/env python3
"""Install direct launchers for the validated Pi 1.0 comparison configurations."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile


MODES = {
    'hybrid-tier1000': 'candidate_tier1000',
    'hybrid-tier10000': 'candidate_tier10000',
    'native-js-wasm': 'candidate_native_js_wasm',
    'native-js-wasm-low-cpu': 'candidate_native_js_wasm_gc1',
}
PREFIXES = ('JSC_', 'BUN_JSC_', 'PI_NATIVE_AOT_')


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def script(executable, environment):
    header = ('#!/bin/bash\nset -eu\n'
              'for runtime_key in "${!JSC_@}" "${!BUN_JSC_@}" "${!PI_NATIVE_AOT_@}"; do\n'
              '    unset "$runtime_key"\n'
              'done\n')
    return header + ''.join('export ' + key + '=' + shlex.quote(value) + '\n'
                            for key, value in environment.items()) + \
        'exec ' + shlex.quote(executable) + ' "$@"\n'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--matrix', type=Path, required=True)
    parser.add_argument('--artifacts', type=Path, required=True)
    args = parser.parse_args()
    directory = args.artifacts.resolve()
    manifest_path = directory / 'manifest.json'
    manifest = json.loads(manifest_path.read_text())
    if manifest['pi_version'] != '1.0.0' or not manifest.get('pi_execution_verified'):
        raise ValueError('A validated Pi 1.0 artifact is required')
    for field in ('executable', 'image'):
        if sha(manifest[field]) != manifest[field + '_sha256']:
            raise ValueError('Artifact changed: ' + field)
    matrix = json.loads(args.matrix.read_text())
    receipt = {'matrix': str(args.matrix.resolve()), 'matrix_sha256': sha(args.matrix),
               'manifest': str(manifest_path), 'manifest_sha256': sha(manifest_path),
               'generator_sha256': sha(__file__), 'pi_version': '1.0.0', 'launchers': {},
               'offline_policy': 'Benchmark PI_OFFLINE is omitted; user network/provider preferences are preserved.',
               'compatibility_scope': 'Uses already-tested configuration flags; version and environment isolation verified here.'}
    with tempfile.TemporaryDirectory(prefix='pi1-launcher-check-') as temp:
        probe = Path(temp) / 'environment probe'
        program = 'import os,sys,json; print(json.dumps({"env":dict(os.environ),"args":sys.argv[1:]}))'
        probe.write_text('#!/bin/sh\nexec ' + shlex.quote(sys.executable) + ' -c ' + shlex.quote(program) + ' "$@"\n')
        probe.chmod(0o755)
        arguments = ['spaces and π', 'literal $(false)', 'line\nbreak']
        inherited = {'PATH': '/usr/bin:/bin', 'BUN_JSC_useJIT': 'wrong', 'JSC_unknown': 'wrong',
                     'BUN_JSC_useAOTNativeTiering': 'wrong', 'PI_NATIVE_AOT_OUT': '/wrong',
                     'PI_CODING_AGENT_DIR': 'user config', 'ANTHROPIC_API_KEY': 'dummy-probe-key',
                     'PI_OFFLINE': 'user preference'}
        for mode, name in MODES.items():
            config = matrix[name]
            environment = dict(config['env'])
            environment.pop('PI_OFFLINE', None)
            if (config['command'] != [manifest['executable']]
                    or environment['PI_PACKAGE_DIR'] != str(directory)
                    or environment['BUN_JSC_aotImagePath'] != manifest['image']):
                raise ValueError('Matrix/artifact mismatch: ' + name)
            if any(not key.replace('_', '').isalnum() or not isinstance(value, str)
                   for key, value in environment.items()):
                raise ValueError('Invalid environment configuration')
            launcher = directory / ('pi-' + mode + '.sh')
            text = script(manifest['executable'], environment)
            if launcher.exists() and launcher.read_text() != text:
                raise ValueError('Refusing to replace changed launcher: ' + str(launcher))
            if not launcher.exists():
                with launcher.open('x') as stream:
                    stream.write(text)
                launcher.chmod(0o755)
            subprocess.run(['/bin/bash', '-n', str(launcher)], check=True)
            test = Path(temp) / ('probe-' + mode)
            test.write_text(script(str(probe), environment)); test.chmod(0o755)
            for env in (inherited, {'PATH': '/usr/bin:/bin'}):
                result = json.loads(subprocess.check_output([str(test), *arguments], env=env, text=True))
                assert result['args'] == arguments
                assert {k: v for k, v in result['env'].items() if k.startswith(PREFIXES)} == {
                    k: v for k, v in environment.items() if k.startswith(PREFIXES)}
                assert all(result['env'][k] == v for k, v in environment.items())
                for key in ('PI_OFFLINE', 'PI_CODING_AGENT_DIR', 'ANTHROPIC_API_KEY'):
                    assert result['env'].get(key) == env.get(key)
            version = subprocess.run([str(launcher), '--version'], env={**os.environ, 'PI_OFFLINE': '1'},
                                     capture_output=True, text=True, timeout=60, check=True)
            assert version.stdout.strip() == '1.0.0'
            receipt['launchers'][mode] = {'path': str(launcher), 'sha256': sha(launcher),
                                          'configuration': name, 'version': version.stdout.strip(),
                                          'environment_probes': 2, 'bash_syntax': True}
    receipt['complete'] = True
    proof = directory / 'pi1-launchers.provenance.json'
    with proof.open('x') as stream:
        json.dump(receipt, stream, indent=2); stream.write('\n')
    print('PASS: four direct launchers, eight isolation probes, four Pi1.0 version checks')


if __name__ == '__main__':
    main()
