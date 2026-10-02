#!/usr/bin/env python3
"""Offline regression checks for cohort separation and visible completion metadata."""
import copy
import importlib.util
import json
from pathlib import Path
import tempfile

path = Path(__file__).with_name('analyze-comparison.py')
spec = importlib.util.spec_from_file_location('analysis', path)
a = importlib.util.module_from_spec(spec)
spec.loader.exec_module(a)
data = {'endpoint_version': 'visible-idle-editor-v2', 'terminal_screen_sha256': 'a' * 64,
        'harness_sha256': 'b' * 64, 'artifact_manifests': {
            name: {'build_driver_sha256': 'c' * 64, 'priority_final_render': False}
            for name in ('reference', 'candidate')}, 'samples': []}
for r in range(3):
    for name, value in (('reference', 200), ('candidate', 100)):
        data['samples'].append({'variant': name, 'round': r, 'passed': True, 'turns': 1,
            'startup_plus_turns_ms': value, 'turn_times': [{'working_indicator_cleared': True, 'idle_editor_frame': 3}]})


def rejected(fn, message):
    try:
        fn()
    except ValueError:
        return
    raise AssertionError(message)


with tempfile.TemporaryDirectory(prefix='pi-analysis-check-') as root:
    p = Path(root) / 'cohort.json'
    p.write_text(json.dumps(data))
    report = a.analyze(p, ['reference'], ['candidate'], 100, 42)
    result = report['comparisons']['reference']['candidate']['startup_plus_turns_ms']
    assert result['reduction_percent'] == 50 and result['paired_bootstrap_95_percent'] == [50, 50]
    for key in ('endpoint_version', 'terminal_screen_sha256', 'build_driver_sha256', 'harness_sha256'):
        other = copy.deepcopy(report)
        other['cohort_identity'][key] = 'different'
        rejected(lambda: a.validate_independent_cohorts([report, other]), f'accepted different {key}')
        a.validate_independent_cohorts([report, other], separate=True)
    for mutation in ('missing_driver', 'mixed_driver', 'missing_screen', 'working', 'failed'):
        broken = copy.deepcopy(data)
        if mutation == 'missing_driver':
            del broken['artifact_manifests']['candidate']['build_driver_sha256']
        elif mutation == 'mixed_driver':
            broken['artifact_manifests']['candidate']['build_driver_sha256'] = 'd' * 64
        elif mutation == 'missing_screen':
            del broken['terminal_screen_sha256']
        elif mutation == 'working':
            broken['samples'][0]['turn_times'][0]['working_indicator_cleared'] = False
        else:
            broken['samples'][0]['passed'] = False
        p.write_text(json.dumps(broken))
        rejected(lambda: a.analyze(p, ['reference'], ['candidate'], 100, 42), f'accepted {mutation}')
print('PASS: exact paired reduction; endpoint/screen/driver/harness cohort mismatch rejected; explicit separated reports accepted; missing/mixed provenance, active Working and failed rows rejected')
