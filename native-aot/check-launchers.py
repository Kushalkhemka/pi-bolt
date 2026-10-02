#!/usr/bin/env python3
"""Check launcher environment isolation without executing Pi or modifying artifacts."""
import argparse
import ast
import json
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile

import pi


PREFIXES = ('JSC_', 'BUN_JSC_', 'PI_NATIVE_AOT_')


def check(artifacts):
    data = json.loads((artifacts / 'manifest.json').read_text())
    ast.parse(Path(pi.__file__).read_text())
    original_directory = pi.ARTIFACTS
    try:
        with tempfile.TemporaryDirectory(prefix='pi-launcher-regression-') as temp:
            directory = Path(temp) / 'path with spaces'
            directory.mkdir()
            executable = directory / 'environment probe'
            program = ('import json, os, sys; '
                       'print(json.dumps({"env": dict(os.environ), "argv": sys.argv[1:]}))')
            executable.write_text('#!/bin/sh\nexec ' + shlex.quote(sys.executable) +
                                  ' -c ' + shlex.quote(program) + ' "$@"\n')
            executable.chmod(0o755)
            probe = dict(data, executable=str(executable), image=str(directory / 'image with spaces.aot'),
                         common_runtime_env={**data['common_runtime_env'], 'PI_PACKAGE_DIR': str(directory)})
            pi.ARTIFACTS = directory
            pi.write_launchers(probe)
            inherited = {
                'PATH': '/usr/bin:/bin', 'HOME': temp,
                'JSC_useJIT': 'conflicting legacy setting',
                'JSC_arbitraryUnexpectedOption': 'spaces\nnewlines; $(false)',
                'BUN_JSC_useJIT': 'wrong', 'BUN_JSC_useAOT': 'wrong',
                'BUN_JSC_numberOfGCMarkers': '999', 'BUN_JSC_useConcurrentGC': 'false',
                'BUN_JSC_aotImagePath': '/wrong/image.aot',
                'PI_NATIVE_AOT_OUT': '/unexpected/export', 'PI_NATIVE_AOT_LINKED': '1',
                'PI_NATIVE_AOT_arbitraryUnexpectedOption': 'unwanted',
                'ANTHROPIC_API_KEY': 'regression-dummy-key',
                'PI_CODING_AGENT_DIR': 'ordinary Pi configuration', 'PI_OFFLINE': '1',
                'PI_PACKAGE_DIR': '/wrong/assets', 'OTHER_VARIABLE': 'spaces\nnewlines; $(false)',
            }
            arguments = ['argument with spaces', 'literal $(false)', 'unicode π', 'line\nbreak']
            preserved = ('ANTHROPIC_API_KEY', 'PI_CODING_AGENT_DIR', 'PI_OFFLINE', 'OTHER_VARIABLE')
            for mode in pi.MODES:
                launcher = directory / ('pi-' + mode + '.sh')
                subprocess.run(['/bin/bash', '-n', str(launcher)], check=True)
                expected = pi.configuration(probe, mode)['env']
                for label, environment in [('conflicting', inherited), ('empty', {'PATH': '/usr/bin:/bin'})]:
                    child = json.loads(subprocess.check_output([str(launcher), *arguments], env=environment, text=True))
                    runtime = {k: v for k, v in child['env'].items() if k.startswith(PREFIXES)}
                    expected_runtime = {k: v for k, v in expected.items() if k.startswith(PREFIXES)}
                    assert runtime == expected_runtime, (mode, label, runtime, expected_runtime)
                    assert all(child['env'][k] == v for k, v in expected.items()), (mode, label)
                    assert child['argv'] == arguments, (mode, label)
                    if label == 'conflicting':
                        assert all(child['env'][k] == inherited[k] for k in preserved), mode
                assert launcher.stat().st_mode & 0o777 == 0o755, mode

                # Also prove the artifact's installed script matches the current
                # generator exactly. Read and compare; never regenerate artifacts.
                installed = artifacts / ('pi-' + mode + '.sh')
                expected_script = launcher.read_text()
                for old, new in ((str(executable), data['executable']),
                                 (str(directory / 'image with spaces.aot'), data['image']),
                                 (str(directory), data['common_runtime_env']['PI_PACKAGE_DIR'])):
                    expected_script = expected_script.replace(shlex.quote(old), shlex.quote(new))
                assert installed.read_text() == expected_script, f'{mode}: stored launcher differs from generator'
                subprocess.run(['/bin/bash', '-n', str(installed)], check=True)
                assert installed.stat().st_mode & 0o777 == 0o755, mode
                print(f'{mode}: isolation, intended config, credentials, arguments and stored launcher passed')
    finally:
        pi.ARTIFACTS = original_directory
    print('PASS: 7 modes; 14 probe launches; artifacts were read only')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--artifacts', type=Path, default=pi.ARTIFACTS,
                        help='Artifact directory containing manifest.json and seven generated launchers')
    args = parser.parse_args()
    check(args.artifacts.resolve())


if __name__ == '__main__':
    main()
