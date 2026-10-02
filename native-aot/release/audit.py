#!/usr/bin/env python3
"""Revalidate raw M5 acceptance and prove both native release policies.

Run after acceptance, outside any benchmark interval, before record-acceptance.py.
Default runs two bounded, offline --version diagnostics under the receipt's
source-denial profile. --native-selection reuses an existing proof without
executing subprocesses. Neither mode signs, builds, publishes or benchmarks.
"""
import argparse
from collections import Counter
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess


HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('release_acceptance_audit', HERE / 'acceptance.py')
acceptance = importlib.util.module_from_spec(spec)
spec.loader.exec_module(acceptance)
require = acceptance.require
read = acceptance.read
sha = acceptance.sha
save = acceptance.save
MODES = ('hybrid', 'tier10000')
GATES = {'rpc': 30, 'commands': 52, 'workers': 14, 'pty': 24}


def argument(argv, option):
    require(isinstance(argv, list) and all(isinstance(v, str) for v in argv)
            and argv.count(option) == 1, 'One ' + option + ' argument required')
    index = argv.index(option)
    require(index + 1 < len(argv), 'Missing value: ' + option)
    return Path(argv[index + 1])


def file_fact(path):
    require(path.is_file() and not path.is_symlink(), 'Regular evidence file required: ' + str(path))
    return {'sha256': sha(path), 'bytes': path.stat().st_size}


def unchanged_inputs(before, after):
    require(isinstance(before, dict) and before and before == after,
            'Acceptance frozen inputs changed')
    observed = {name: file_fact(Path(name)) for name in before}
    require(observed == before, 'A frozen acceptance consumer/tool/profile has changed')
    require({str(p.resolve()) for p in acceptance.CONSUMERS}.issubset(before),
            'Original acceptance consumer inventory is incomplete or from another checkout')
    return observed


def denial_prefix(receipt, package):
    denial = receipt.get('source_read_denial') or {}
    require(denial.get('relocated_read_probe_exit') == 0
            and isinstance(denial.get('source_read_probe_exit'), int)
            and denial['source_read_probe_exit'] != 0
            and 'Operation not permitted' in denial.get('source_read_probe_stderr', ''),
            'Positive and negative source-denial probes must both be proven')
    denied = Path(denial['root']).resolve()
    profile = Path(denial['profile'])
    sandbox = Path('/usr/bin/sandbox-exec')
    # A clean CI harness checkout can differ from the original source workspace.
    # Bind to the receipt's declared root and exact frozen SBPL/probe evidence,
    # rather than requiring this audit script to live inside the denied tree.
    require(denied.is_dir() and not package.is_relative_to(denied),
            'Declared original source root must exist; package must be relocated')
    expected = '(version 1)\n(allow default)\n(deny file-read* (subpath ' + json.dumps(str(denied), ensure_ascii=False) + '))\n'
    require(profile.read_text() == expected and sha(profile) == denial['profile_sha256']
            and sha(sandbox) == denial['sandbox_sha256'], 'Source-denial profile/tool differs')
    frozen = receipt['frozen_inputs_before']
    require(str(profile) in frozen and str(sandbox) in frozen,
            'Source-denial profile/tool not frozen by acceptance')
    return [str(sandbox), '-f', str(profile)]


def expected_variants(manifest, package, prefix, modes):
    variants = {}
    for mode in modes:
        for layout in ('fullscreen', 'regular'):
            name = 'release_' + mode + ('_regular' if layout == 'regular' else '')
            variants[name] = {
                'command': prefix + [str(acceptance.member(package, manifest['launchers'][mode]))]
                    + (['--tui-mode', 'regular'] if layout == 'regular' else []),
                'env': {'PI_PACKAGE_DIR': str(package), 'PI_OFFLINE': '1'},
                'expected_tui_mode': layout, 'test_tui_mode_switch': True,
                'keep_builtin_extensions': True, 'use_default_tools': True}
    return variants


def suites(receipt, manifest, package, prefix):
    modes = receipt.get('modes')
    require(isinstance(modes, list) and Counter(modes) == Counter(MODES), 'Both release policies required once')
    variants = expected_variants(manifest, package, prefix, modes)
    base_names = ['release_' + mode for mode in modes]
    commands = receipt.get('commands', [])
    require(Counter(c.get('label') for c in commands) == Counter(list(GATES)), 'Four original suites required once')
    parsed, gates, evidence = {}, {}, {}
    scripts = {'rpc': acceptance.LAB / 'cli-workflows.py',
               'commands': acceptance.LAB / 'interactive-commands.py',
               'workers': acceptance.LAB / 'check-pi-v1-workers.py',
               'pty': acceptance.BENCH / 'interactive_workload.py'}
    for entry in commands:
        label = entry['label']; argv = entry['command']
        require(entry.get('complete') is True and entry.get('passed') is True and entry.get('exit_code') == 0,
                'Suite command was not successful: ' + label)
        result = argument(argv, '--output'); matrix = argument(argv, '--variants-file')
        require(result.is_absolute() and matrix.is_absolute() and not result.resolve().is_relative_to(package),
                'External absolute evidence paths required')
        require(len(argv) > 1 and argv[1] == str(scripts[label])
                and str(Path(argv[0]).resolve()) in receipt['frozen_inputs_before'],
                'Suite consumer/interpreter differs')
        require(file_fact(matrix)['sha256'] == receipt['matrix_sha256'] and read(matrix) == variants,
                'Raw policy/layout matrix differs')
        names = list(variants) if label in ('commands', 'pty') else base_names
        expected_argv = [argv[0], str(scripts[label]), '--variants-file', str(matrix),
                         '--variants', *names, '--output', str(result)]
        if label == 'pty':
            cache = argument(argv, '--tool-cache')
            require(all(str((cache / name).resolve()) in receipt['frozen_inputs_before'] for name in ('fd', 'rg')),
                    'PTY helper cache was not frozen')
            expected_argv += ['--functional', '--runs', '1', '--warmups', '0', '--turns', '3',
                              '--pace-ms', '2', '--tool-cache', str(cache)]
        require(argv == expected_argv, 'Suite arguments differ: ' + label)
        log = Path(entry['log'])
        require(file_fact(result)['sha256'] == entry['result_sha256']
                and file_fact(log)['sha256'] == entry['log_sha256'], 'Raw suite evidence changed: ' + label)
        parsed[label] = read(result)
        gates[label] = acceptance.validate_suite(label, parsed[label], variants, base_names)
        evidence[label] = {'result': str(result), 'result_sha256': sha(result),
                           'log': str(log), 'log_sha256': sha(log), 'matrix': str(matrix)}
    require(gates == receipt.get('gates') == GATES and sum(gates.values()) == 120, '120 original gates required')
    exits = sum(c['observed_worker_exits'] for c in parsed['workers']['checks'])
    samples = parsed['pty']['samples']
    frames = sum(len(s['turn_times']) for s in samples)
    require(exits == 20 and len(samples) == 4 and frames == 12, '20 worker exits / four PTY cases / 12 frames required')
    return {'gates': gates, 'total_gates': 120, 'worker_exits': exits, 'pty_cases': len(samples),
            'visible_idle_frames': frames, 'pty_provider_requests': sum(s['requests'] for s in samples),
            'pty_read_tools': sum(s['read_tools'] for s in samples), 'suite_evidence': evidence}


def policy_env(mode, package, manifest):
    env = {'PI_PACKAGE_DIR': str(package), 'PI_OFFLINE': '1',
           'BUN_JSC_resolveAllScopeSlotsStatically': 'true',
           'BUN_JSC_evaluateObjectLiteralValuesFirst': 'true',
           'BUN_JSC_definePlainInstanceFieldsInConstructor': 'true',
           'BUN_JSC_useJIT': 'true', 'BUN_JSC_useAOT': 'true', 'BUN_JSC_numberOfGCMarkers': '2',
           'BUN_JSC_useAOTMappedImages': 'true', 'BUN_JSC_aotImagePath': str(package / manifest['image']),
           'BUN_JSC_useAOTNativeTiering': 'true' if mode == 'tier10000' else 'false',
           'BUN_JSC_verboseAOTCompilation': 'true', 'BUN_JSC_verboseDiskCache': 'true'}
    if mode == 'tier10000':
        env['BUN_JSC_thresholdForAOTNativeTiering'] = '10000'
    return env


def native_summary(stdout, stderr):
    sites = re.findall(r'\(module (\d+) start \d+ kind \d+\) is at ', stderr)
    graphs = re.findall(r'BytecodeCache: accepted prelinked module graph v(\d+) modules (\d+)', stderr)
    require(stdout.strip() == '1.0.0' and sites and graphs and set(graphs) == {('4', '3')}
            and ' is not an image for this engine' not in stderr, 'Mapped native/prelinked version proof failed')
    return {'version': stdout.strip(), 'native_sites': len(sites),
            'jsc_builtin_sites': sites.count(str(0xeb17ffff)), 'bun_builtin_sites': sites.count(str(0xb017ffff)),
            'mapped_image': 'AOT: mapped read-only image ' in stderr,
            'prelinked_graph': {'version': 4, 'modules': 3}}


def native_proof(args, package, manifest, prefix, report):
    pair = {name: file_fact(package / manifest[field])
            for field, name in (('executable', 'pi-native'), ('image', 'pi-native.aot'))}
    if args.native_selection:
        path = args.native_selection.resolve(); proof = read(path)
        logs = path.with_name(path.stem + '-evidence')
        require(proof.get('complete') is True and proof.get('before') == proof.get('after') == pair,
                'Existing native proof belongs to a different pair')
    else:
        require(acceptance.sys.platform == 'darwin' and acceptance.platform.machine() == 'arm64',
                'Native diagnostics require the accepted macOS ARM64 host')
        cpu = subprocess.check_output(['/usr/sbin/sysctl', '-n', 'machdep.cpu.brand_string'], text=True).strip()
        require(cpu == 'Apple M5', 'Native diagnostics require the accepted base M5 host')
        path = args.output.with_name(args.output.stem + '-native-selection.json')
        logs = path.with_name(path.stem + '-evidence')
        require(not path.exists() and not logs.exists(), 'Fresh native proof/log paths required')
        logs.mkdir(); proof = {'complete': False, 'before': pair, 'runs': {},
            'scope': 'Direct relocated native version diagnostics; both policy flags; source reads denied; no benchmark'}
        save(path, proof)
    profile = Path(prefix[2])
    if args.native_selection:
        require(proof.get('profile_sha256') == sha(profile), 'Native proof source-denial profile differs')
    proof['profile_sha256'] = sha(profile)
    summaries = {}
    for mode in MODES:
        config = policy_env(mode, package, manifest)
        argv = prefix + [str(package / manifest['executable']), '--version']
        out = logs / (mode + '.stdout'); err = logs / (mode + '.stderr')
        if args.native_selection:
            run = proof['runs'][mode]
            require(run.get('command') == argv and run.get('env') == config and run.get('exit_code') == 0,
                    'Existing native policy/config differs: ' + mode)
            require('cwd' not in run or run['cwd'] == str(package), 'Existing native working directory differs: ' + mode)
        else:
            environment = {k: os.environ[k] for k in ('PATH', 'HOME', 'TMPDIR', 'LANG', 'LC_ALL') if k in os.environ}
            environment.update(config, DO_NOT_TRACK='1')
            with out.open('x') as stdout, err.open('x') as stderr:
                result = subprocess.run(argv, cwd=package, env=environment, stdout=stdout, stderr=stderr, timeout=30)
            run = {'command': argv, 'cwd': str(package), 'env': config, 'exit_code': result.returncode}
            proof['runs'][mode] = run; save(path, proof)
            require(result.returncode == 0, 'Native version process failed: ' + mode)
        summary = native_summary(out.read_text(), err.read_text())
        require(summary['mapped_image'] and summary['jsc_builtin_sites'] > 0 and summary['bun_builtin_sites'] > 0,
                'Native mapped engine builtin selection missing: ' + mode)
        hashes = {'stdout': sha(out), 'stderr': sha(err)}
        if args.native_selection:
            require(run.get('logs') == hashes and all(run.get(k) == v for k,v in summary.items()),
                    'Native proof logs/counters differ: ' + mode)
        else:
            run.update(summary, logs=hashes); save(path, proof)
        summaries[mode] = summary
    require(file_fact(package / manifest['executable']) == pair['pi-native']
            and file_fact(package / manifest['image']) == pair['pi-native.aot'], 'Native pair changed during diagnostics')
    if not args.native_selection:
        proof.update(after=pair, complete=True); save(path, proof)
    report.update(native_selection=summaries, native_selection_receipt=str(path),
                  native_selection_sha256=sha(path), file_pair=pair)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--package', type=Path, required=True)
    parser.add_argument('--receipt', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--native-selection', type=Path, help='Reuse and fully recheck existing native proof/logs; no subprocesses')
    args = parser.parse_args()
    require(not args.package.is_symlink(), 'Real package root required')
    package = args.package.resolve(); args.output = args.output.resolve()
    require(not args.output.exists() and not args.output.with_suffix('.tmp').exists()
            and not args.output.is_relative_to(package), 'Fresh external audit output required')
    args.output.parent.mkdir(parents=True, exist_ok=True)
    report = {'complete': False, 'started': acceptance.stamp(),
              'scope': 'Raw unsigned M5 acceptance + native policy audit; no production or performance certification',
              'package': str(package), 'acceptance_receipt': str(args.receipt.resolve())}
    save(args.output, report)
    try:
        receipt = read(args.receipt)
        require(receipt.get('complete') is True and Path(receipt['package']).resolve() == package,
                'Completed acceptance must belong to this exact package path')
        manifest, snapshot = acceptance.package_state(package)
        require(manifest['release_status'] == 'unsigned-candidate', 'This audit only certifies scoped unsigned candidate acceptance')
        require(receipt['release_version'] == manifest['version'] and receipt['release_status'] == manifest['release_status']
                and receipt.get('hardware') == 'Apple M5', 'Acceptance release/host differs')
        require(snapshot == receipt.get('before') == receipt.get('after'), 'Package differs from accepted snapshot')
        inputs = unchanged_inputs(receipt['frozen_inputs_before'], receipt['frozen_inputs_after'])
        prefix = denial_prefix(receipt, package)
        report.update(suites(receipt, manifest, package, prefix), source_read_denial=receipt['source_read_denial'])
        report['acceptance_receipt_sha256'] = sha(args.receipt)
        native_proof(args, package, manifest, prefix, report)
        require(acceptance.package_state(package)[1] == snapshot, 'Package changed during audit')
        require(unchanged_inputs(inputs, inputs) == inputs, 'Frozen inputs changed during audit')
        report.update(complete=True, package_unchanged=True, frozen_inputs_unchanged=True,
                      package_snapshot_files=len(snapshot), frozen_input_count=len(inputs), finished=acceptance.stamp())
        save(args.output, report)
        print('PASS: 120 gates, 20 worker exits, four PTY cases / 12 frames, both mapped native policies')
    except BaseException as error:
        report.update(complete=False, error=repr(error), finished=acceptance.stamp())
        save(args.output, report)
        raise


if __name__ == '__main__':
    main()
