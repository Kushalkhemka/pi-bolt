#!/usr/bin/env python3
"""Bounded offline acceptance of relocated M5 Pi release launchers.

Runs the existing unchanged correctness suites. It performs no benchmarks,
builds, dependency installation, clipboard access or remote provider requests.
"""
import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import platform
import re
import signal
import stat
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
LAB = ROOT / 'native-aot'
BENCH = ROOT / 'benchmarks'
ENDPOINT = 'visible-idle-editor-v3-alternate-screen'
CONSUMERS = [Path(__file__).resolve(), LAB / 'cli-workflows.py', LAB / 'pi.py', LAB / 'lab.py',
    LAB / 'interactive-commands.py', LAB / 'check-pi-v1-workers.py',
    BENCH / 'interactive_workload.py', BENCH / 'terminal_screen.py', BENCH / 'benchmark.py',
    BENCH / 'platform_metrics.py', Path(sys.executable).resolve()]


def require(condition, message):
    if not condition:
        raise ValueError(message)


def sha(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def stamp():
    return datetime.now(timezone.utc).isoformat()


def read(path):
    return json.loads(Path(path).read_text())


def save(path, value):
    temporary = path.with_suffix('.tmp')
    with temporary.open('x') as stream:
        json.dump(value, stream, indent=2); stream.write('\n')
    temporary.replace(path)


def member(package, relative):
    require(isinstance(relative, str) and relative and '\\' not in relative
            and not any(ord(c) < 32 or ord(c) == 127 for c in relative),
            'Nonempty POSIX package member required')
    parsed = PurePosixPath(relative)
    require(not parsed.is_absolute() and all(p not in ('', '.', '..') for p in relative.split('/')),
            'Package member must be a clean relative path: ' + relative)
    path = package.joinpath(*parsed.parts)
    require(path.resolve().is_relative_to(package) and path.is_file() and not path.is_symlink(),
            'Package member must be an internal regular file: ' + relative)
    return path


def package_state(package):
    require(package.is_dir() and not package.is_symlink(), 'Real package directory required')
    manifest = read(package / 'release.json')
    require(manifest.get('schema') == 1 and manifest.get('pi_version') == '1.0.0'
            and manifest.get('target') == 'darwin-arm64' and manifest.get('cpu_family') == 'Apple M5'
            and manifest.get('package_root') == '.', 'Release schema/version/target differs')
    require(re.fullmatch(r'1\.0\.0-m5\.[1-9][0-9]*', manifest.get('version', '')),
            'Versioned M5 release required')
    require(manifest.get('launchers') == {'hybrid': 'bin/pi', 'tier10000': 'bin/pi-tier10000'},
            'Both public release policies required')
    declared = manifest.get('files')
    require(isinstance(declared, dict) and declared and not {'release.json', 'SHA256SUMS'} & set(declared),
            'Complete non-self-referential release file inventory required')
    actual = set()
    for path in package.rglob('*'):
        require(not path.is_symlink(), 'Release may not depend on external symlinks: ' + str(path))
        require(path.is_dir() or path.is_file(), 'Unsupported release member type')
        if path.is_file():
            actual.add(path.relative_to(package).as_posix())
    require(actual == set(declared) | {'release.json', 'SHA256SUMS'}, 'Missing or undeclared release files')
    snapshot = {}
    for relative, expected in declared.items():
        path = member(package, relative)
        require(isinstance(expected, dict) and isinstance(expected.get('mode'), int),
                'Release file SHA/bytes/integer mode required')
        observed = {'sha256': sha(path), 'bytes': path.stat().st_size,
                    'mode': stat.S_IMODE(path.stat().st_mode)}
        require(observed == {k: expected.get(k) for k in ('sha256', 'bytes', 'mode')},
                'Release file differs from manifest: ' + relative)
        snapshot[relative] = observed
    manifest_path = member(package, 'release.json')
    snapshot['release.json'] = {'sha256': sha(manifest_path), 'bytes': manifest_path.stat().st_size,
                               'mode': stat.S_IMODE(manifest_path.stat().st_mode)}
    checksums = member(package, 'SHA256SUMS')
    expected_rows = {**declared, 'release.json': snapshot['release.json']}
    expected_table = ''.join(f"{fact['sha256']}  {name}\n" for name, fact in sorted(expected_rows.items()))
    require(checksums.read_text() == expected_table, 'Release checksum table differs')
    snapshot['SHA256SUMS'] = {'sha256': sha(checksums), 'bytes': checksums.stat().st_size,
                             'mode': stat.S_IMODE(checksums.stat().st_mode)}
    for field in ('executable', 'image'):
        require(manifest.get(field) in declared, 'Undeclared release ' + field)
        member(package, manifest[field])
    for relative in manifest['launchers'].values():
        require(relative in declared and os.access(member(package, relative), os.X_OK),
                'Executable public launcher missing')
    require(os.access(member(package, manifest['executable']), os.X_OK), 'Native executable is not executable')
    require(read(member(package, 'package.json'))['version'] == '1.0.0',
            'Packaged Pi metadata must match original harness input')
    return manifest, snapshot


def frozen_inputs(tool_cache):
    require(tool_cache.is_dir(), 'Existing offline fd/rg cache required')
    files = list(CONSUMERS)
    for name in ('fd', 'rg'):
        path = tool_cache / name
        require(path.is_file() and os.access(path, os.X_OK), 'Missing executable offline helper: ' + str(path))
        files.append(path.resolve())
    return {str(p): {'sha256': sha(p), 'bytes': p.stat().st_size} for p in sorted(set(files))}


def validate_suite(label, data, variants, base_names):
    all_names = list(variants)
    if label == 'pty':
        samples = data.get('samples', [])
        require(Counter(s.get('variant') for s in samples) == Counter(all_names)
                and not data.get('warmups'), 'Full-PTY policy/layout coverage differs')
        require(data.get('endpoint_version') == ENDPOINT and data.get('configs') == variants,
                'Full-PTY endpoint/launch configuration differs')
        for sample in samples:
            config = variants[sample['variant']]; mode = config['expected_tui_mode']
            require(sample.get('passed') is True and sample.get('session_persisted') is True
                    and len(sample.get('functional', {})) == 6
                    and all(v is True for v in sample['functional'].values()), 'Full-PTY original gate failed')
            require(sample.get('turns') == 3 and sample.get('read_tools') == 3 and sample.get('requests') == 6,
                    'Full-PTY real read/provider proof differs')
            require(sample.get('tui_mode') == mode
                    and (sample.get('alternate_screen_entries', 0) > 0) == (mode == 'fullscreen'),
                    'Full-PTY actual layout differs')
            timings = sample.get('turn_times', [])
            frames = [t.get('idle_editor_frame', 0) for t in timings]
            require(len(frames) == 3 and all(t.get('working_indicator_cleared') is True for t in timings)
                    and all(a < b for a,b in zip([0] + frames[:-1], frames)), 'Full-PTY visible idle proof missing')
        return 6 * len(all_names)
    checks = data.get('checks', []); names = all_names if label == 'commands' else base_names
    count = {'rpc': 15, 'commands': 13, 'workers': 7}[label]
    require(data.get('complete') is True and Counter(c.get('name') for c in checks) == Counter(names),
            'Original suite policy coverage differs: ' + label)
    for check in checks:
        config = variants[check['name']]
        require(check.get('passed') is True and len(check.get('gates', [])) == count,
                'Original correctness assertions failed: ' + label)
        require(check.get('env') == config['env']
                and check.get('command', [])[:len(config['command'])] == config['command'],
                'Suite did not launch declared package wrapper: ' + label)
        if label == 'rpc':
            require(check.get('retry_resume_projection') == 'durable_context_edit', 'Durable session restore missing')
        if label == 'commands':
            require(check.get('tui_mode') == config['expected_tui_mode']
                    and 'settings_tui_mode_roundtrip' in check['gates'], 'Settings/layout roundtrip missing')
        if label == 'workers':
            require(check.get('worker_count', 0) > 0 and check['worker_count'] == check.get('observed_worker_exits'),
                    'Codemode/image worker exits incomplete')
            require('native_helper_load_no_clipboard_access' in check['gates'], 'Native helper loading missing')
    return count * len(names)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--package', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--modes', choices=('hybrid', 'tier10000'), nargs='+', default=['hybrid', 'tier10000'])
    parser.add_argument('--tool-cache', type=Path, default=Path.home() / '.pi/agent/bin')
    parser.add_argument('--deny-source-root', type=Path, help='Deny application file reads beneath original checkout using macOS sandbox-exec')
    parser.add_argument('--timeout-seconds', type=int, default=600)
    args = parser.parse_args()
    require(60 <= args.timeout_seconds <= 1800 and len(set(args.modes)) == len(args.modes),
            'Unique release modes and bounded timeout required')
    require(not args.package.is_symlink(), 'Package root must not be a symlink')
    package = args.package.resolve(); output = args.output.resolve()
    evidence = output.with_name(output.stem + '-evidence')
    require(not output.is_relative_to(package) and not evidence.is_relative_to(package),
            'Acceptance evidence must not modify package')
    require(not output.exists() and not output.with_suffix('.tmp').exists() and not evidence.exists(),
            'Fresh receipt and evidence directory required; preserve previous attempts')
    output.parent.mkdir(parents=True, exist_ok=True); evidence.mkdir(exist_ok=False)
    report = {'complete': False, 'started': stamp(), 'package': str(package),
              'scope': 'Relocated release preflight and original correctness suites; no benchmarks',
              'commands': [], 'gates': {}}
    save(output, report)
    try:
        run_acceptance(args, package, output, evidence, report)
    except BaseException as error:
        report.update(complete=False, error=repr(error), finished=stamp())
        save(output, report)
        raise


def run_acceptance(args, package, output, evidence, report):
    require(sys.platform == 'darwin' and platform.machine() == 'arm64', 'Native macOS ARM64 acceptance required')
    cpu = subprocess.check_output(['sysctl', '-n', 'machdep.cpu.brand_string'], text=True).strip()
    require(cpu == 'Apple M5', 'This release accepts Apple M5 only')
    manifest, before = package_state(package)
    macos = subprocess.check_output(['sw_vers', '-productVersion'], text=True).strip()
    version = lambda text: tuple(int(p) for p in text.split('.'))
    require(version(macos) >= version(manifest['minimum_macos']), 'macOS is below the declared minimum')
    inputs = frozen_inputs(args.tool_cache.resolve())
    prefix = []
    if args.deny_source_root:
        denied = args.deny_source_root.resolve()
        # A reviewed CI checkout can differ from the workspace that built the
        # artifact. Bind the denied workspace to the recorded build driver.
        source_probe = denied / 'native-aot' / 'build-pi.js'
        require(denied.is_dir() and source_probe.is_file() and not source_probe.is_symlink()
                and source_probe.resolve().is_relative_to(denied)
                and sha(source_probe) == manifest['source_provenance']['build_driver_sha256'],
                'Deny-source-root must contain the recorded original build driver')
        require(not package.is_relative_to(denied) and not evidence.is_relative_to(denied),
                'Relocated package and evidence must be outside denied source root')
        sandbox = Path('/usr/bin/sandbox-exec')
        require(sandbox.is_file() and os.access(sandbox, os.X_OK), 'macOS sandbox-exec unavailable')
        profile = evidence / 'deny-original-source.sb'
        profile.write_text('(version 1)\n(allow default)\n(deny file-read* (subpath '
                           + json.dumps(str(denied), ensure_ascii=False) + '))\n')
        prefix = [str(sandbox), '-f', str(profile)]
        for path in (sandbox, profile, source_probe):
            inputs[str(path)] = {'sha256': sha(path), 'bytes': path.stat().st_size}
        positive = subprocess.run(prefix + ['/bin/cat', str(package / 'release.json')],
                                  stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True, timeout=10)
        negative = subprocess.run(prefix + ['/bin/cat', str(source_probe)],
                                  stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True, timeout=10)
        require(positive.returncode == 0 and negative.returncode != 0,
                'Sandbox must allow relocated metadata and reject original source reads')
        report['source_read_denial'] = {'root': str(denied), 'profile': str(profile),
            'source_read_probe_path': str(source_probe), 'source_read_probe_sha256': sha(source_probe),
            'profile_sha256': sha(profile), 'sandbox_sha256': sha(sandbox),
            'relocated_read_probe_exit': positive.returncode, 'source_read_probe_exit': negative.returncode,
            'source_read_probe_stderr': negative.stderr, 'scope': 'Application and its child processes; harness remains unsandboxed'}
    def current_inputs():
        current = frozen_inputs(args.tool_cache.resolve())
        for filename in inputs.keys() - current.keys():
            path = Path(filename)
            current[filename] = {'sha256': sha(path), 'bytes': path.stat().st_size}
        return current
    base_names = ['release_' + mode for mode in args.modes]
    variants = {}
    for mode, name in zip(args.modes, base_names):
        for layout in ('fullscreen', 'regular'):
            variant = name + ('_regular' if layout == 'regular' else '')
            variants[variant] = {'command': prefix + [str(member(package, manifest['launchers'][mode]))]
                + (['--tui-mode', 'regular'] if layout == 'regular' else []),
                'env': {'PI_PACKAGE_DIR': str(package), 'PI_OFFLINE': '1'},
                'expected_tui_mode': layout, 'test_tui_mode_switch': True,
                'keep_builtin_extensions': True, 'use_default_tools': True}
    matrix = evidence / 'variants.json'; matrix.write_text(json.dumps(variants, indent=2) + '\n')
    report.update({'complete': False, 'started': stamp(), 'scope': 'Relocated Apple M5 Pi release correctness, no performance measurement',
        'package': str(package), 'release_version': manifest['version'], 'release_status': manifest['release_status'],
        'macos': macos, 'hardware': cpu, 'modes': args.modes, 'before': before, 'frozen_inputs_before': inputs,
        'matrix_sha256': sha(matrix), 'commands': [], 'gates': {}, 'limits': [
            'Bounded original CLI fixtures cover dynamic extension loading/reload, dialogs, session restore, workers and visible idle.',
            'Resource fields in original functional receipts are diagnostics, not performance benchmarks.',
            'Manifest checks establish package consistency, not publisher authenticity or Apple notarization.',
            'Does not establish all providers/OAuth/MCP/plugins, clipboard side effects, production soak or other CPU/OS targets.',
            'Standalone relocation tests need the old build path unavailable to prove absence of hidden external fallbacks.']})
    save(output, report)
    env = {k: os.environ[k] for k in ('PATH', 'HOME', 'TMPDIR', 'LANG', 'LC_ALL', 'USER', 'LOGNAME') if k in os.environ}
    env.update(PI_BENCH_PACKAGE_DIR=str(package), PI_OFFLINE='1', DO_NOT_TRACK='1', PYTHONDONTWRITEBYTECODE='1')
    plans = [('rpc', LAB / 'cli-workflows.py', base_names),
             ('commands', LAB / 'interactive-commands.py', list(variants)),
             ('workers', LAB / 'check-pi-v1-workers.py', base_names),
             ('pty', BENCH / 'interactive_workload.py', list(variants))]
    try:
        for label, script, names in plans:
            require(package_state(package)[1] == before and current_inputs() == inputs,
                    'Package or correctness consumers changed before suite')
            result = evidence / (label + '.json'); log = evidence / (label + '.log')
            command = [sys.executable, str(script), '--variants-file', str(matrix), '--variants', *names,
                       '--output', str(result)]
            if label == 'pty':
                command += ['--functional', '--runs', '1', '--warmups', '0', '--turns', '3', '--pace-ms', '2',
                            '--tool-cache', str(args.tool_cache.resolve())]
            fact = {'label': label, 'command': command, 'complete': False, 'started': stamp(), 'log': str(log)}
            report['commands'].append(fact); save(output, report)
            with log.open('x') as stream:
                process = subprocess.Popen(command, cwd=evidence, env=env, stdout=stream,
                                           stderr=subprocess.STDOUT, start_new_session=True)
                try:
                    code = process.wait(timeout=args.timeout_seconds)
                except BaseException:
                    try:
                        os.killpg(process.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                    process.wait(); fact.update(exit_code=process.returncode, interrupted=True); raise
            fact.update(exit_code=code, finished=stamp(), log_sha256=sha(log))
            if result.is_file():
                fact['result_sha256'] = sha(result)
            require(code == 0, 'Original release correctness suite failed: ' + label)
            report['gates'][label] = validate_suite(label, read(result), variants, base_names)
            fact.update(complete=True, passed=True); save(output, report)
        report['after'] = package_state(package)[1]
        report['frozen_inputs_after'] = current_inputs()
        require(report['after'] == before and report['frozen_inputs_after'] == inputs
                and sha(matrix) == report['matrix_sha256'], 'Release/consumer/matrix changed during acceptance')
        require(report['gates'] == {'rpc': 15*len(args.modes), 'commands': 26*len(args.modes),
                                   'workers': 7*len(args.modes), 'pty': 12*len(args.modes)}, 'Scoped gates incomplete')
        report.update(complete=True, finished=stamp()); save(output, report)
        print('PASS: relocated release original correctness suites; ' + str(output))
    except BaseException as error:
        report.update(complete=False, error=repr(error), finished=stamp())
        for fact in report['commands']:
            for kind in ('log',):
                p = Path(fact[kind])
                if p.is_file():
                    fact[kind + '_sha256'] = sha(p)
        try:
            report['after'] = package_state(package)[1]
        except BaseException as final_error:
            report['after_snapshot_error'] = repr(final_error)
        save(output, report); raise


if __name__ == '__main__':
    main()
