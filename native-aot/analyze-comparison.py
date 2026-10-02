#!/usr/bin/env python3
"""Analyze paired randomized rounds without pooling workloads or dropping outliers."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import random
import statistics

METRICS = ('ready_ms', 'startup_plus_turns_ms', 'startup_plus_5_turns_ms',
           'turns_ms', 'five_turns_ms', 'cpu_through_turns_ms', 'cpu_through_5_turns_ms',
           'ready_footprint_mb', 'after_turns_footprint_mb', 'after_5_turns_footprint_mb',
           'peak_footprint_mb')
WORKLOAD_KEYS = ('turns', 'pace_ms', 'requests', 'read_tools', 'replies_sha256', 'reply_sha256', 'session_persisted')


def digest(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def quantile(values, fraction):
    values = sorted(values)
    position = (len(values) - 1) * fraction
    lower = math.floor(position)
    upper = math.ceil(position)
    return values[lower] + (values[upper] - values[lower]) * (position - lower)



def cohort_identity(data, path):
    endpoint = data.get('endpoint_version') or (
        'historical-rpc-session-stop' if str(data.get('workload', '')).startswith('RPC ready + ')
        else 'historical-final-text-frame')
    manifests = data.get('artifact_manifests', {})
    measured = {sample['variant'] for sample in data.get('samples', [])}
    drivers = sorted({manifests.get(name, {}).get('build_driver_sha256') for name in measured
                      if manifests.get(name, {}).get('build_driver_sha256')})
    if endpoint == 'visible-idle-editor-v2':
        screen_hash = data.get('terminal_screen_sha256')
        if not isinstance(screen_hash, str) or len(screen_hash) != 64:
            raise ValueError(f'{path}: v2 cohort has no terminal screen module digest')
        if not measured or any(not manifests.get(name, {}).get('build_driver_sha256') for name in measured):
            raise ValueError(f'{path}: v2 cohort has no immutable build driver metadata for every measured variant')
        if len(drivers) != 1:
            raise ValueError(f'{path}: v2 cohort mixes build driver revisions')
        for sample in data['samples']:
            turns = sample.get('turn_times', [])
            if len(turns) != sample.get('turns') or not all(t.get('working_indicator_cleared') is True and t.get('idle_editor_frame', 0) > 0 for t in turns):
                raise ValueError(f'{path}: v2 sample lacks positive visible idle editor proof for every turn')
    return {'endpoint_version': endpoint, 'terminal_screen_sha256': data.get('terminal_screen_sha256'),
            'build_driver_sha256': drivers or None, 'harness_sha256': data.get('harness_sha256')}


def validate_independent_cohorts(reports, separate=False):
    identities = {json.dumps(report['cohort_identity'], sort_keys=True) for report in reports}
    if len(identities) > 1 and not separate:
        raise ValueError('Independent files differ in endpoint version, screen module, build driver or harness digest; '
                         'use --separate-cohorts to explicitly retain distinct labeled reports. Pooling is never supported.')


def analyze(path, references, variants, resamples, seed, allow_legacy_rpc=False):
    data = json.loads(path.read_text())
    identity = cohort_identity(data, path)
    grouped = {}
    # Check the complete measured cohort, including unselected variants: a failed
    # row must not disappear when someone chooses the comparison they prefer.
    samples = data.get('samples', [])
    if not samples:
        raise ValueError(f'{path}: no measured samples')
    legacy_rpc = allow_legacy_rpc and str(data.get('workload', '')).startswith('RPC ready + ')
    for sample in samples:
        # The older RPC harness wrote samples only after its correctness asserts,
        # without an explicit passed flag. Accept that schema only when requested.
        implicit_rpc_pass = legacy_rpc and 'passed' not in sample and all(
            key in sample for key in ('cpu_through_5_turns_ms', 'reply_sha256', 'turns', 'requests', 'read_tools'))
        if sample.get('passed') is not True and not implicit_rpc_pass:
            raise ValueError(f'{path}: failed/unverified sample: {sample.get("variant")} round {sample.get("round")}')
        if implicit_rpc_pass and (sample['requests'] != sample['turns'] * 2 or sample['read_tools'] != sample['turns']):
            raise ValueError(f'{path}: legacy RPC request/tool count does not match the workload')
        name, round_id = sample['variant'], sample['round']
        if not isinstance(round_id, int) or round_id < 0:
            raise ValueError(f'{path}: invalid measured round identifier')
        rounds = grouped.setdefault(name, {})
        if round_id in rounds:
            raise ValueError(f'{path}: duplicate {name} round {round_id}')
        rounds[round_id] = sample
    for key in WORKLOAD_KEYS:
        values = {json.dumps(sample.get(key), sort_keys=True) for sample in samples}
        if len(values) != 1:
            raise ValueError(f'{path}: heterogeneous measured workload/outcome: {key}')
    all_rounds = next(iter(grouped.values())).keys()
    if any(rounds.keys() != all_rounds for rounds in grouped.values()):
        raise ValueError(f'{path}: incomplete paired rounds in measured cohort')
    if len(all_rounds) < 2:
        raise ValueError(f'{path}: need at least two paired rounds')
    for name in references + (variants or []):
        if name not in grouped:
            raise ValueError(f'{path}: requested variant {name} was not measured')
    selected = variants or [name for name in grouped if name not in references]
    metrics = [key for key in METRICS if all(key in sample for sample in samples)]
    if not metrics:
        raise ValueError(f'{path}: no recognized metrics')
    for sample in samples:
        for metric in metrics:
            value = sample[metric]
            if not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
                raise ValueError(f'{path}: invalid {metric} in {sample["variant"]} round {sample["round"]}')
    summaries = {}
    for name, rounds in grouped.items():
        summaries[name] = {}
        for metric in metrics:
            values = [sample[metric] for sample in rounds.values()]
            median = statistics.median(values)
            summaries[name][metric] = {'n': len(values), 'median': median,
                'median_absolute_deviation': statistics.median(abs(x - median) for x in values),
                'minimum': min(values), 'maximum': max(values)}
    comparisons = {}
    round_ids = sorted(all_rounds)
    for reference in references:
        comparisons[reference] = {}
        for variant in selected:
            if variant == reference:
                continue
            result = {}
            for metric in metrics:
                base = [grouped[reference][r][metric] for r in round_ids]
                cand = [grouped[variant][r][metric] for r in round_ids]
                reduction = 100 * (1 - statistics.median(cand) / statistics.median(base))
                # The same sampled round indices apply to both rows, preserving
                # the host-state pairing. Resetting the seed makes this check
                # independent of JSON row/metric ordering.
                rng = random.Random(seed)
                bootstrap = []
                for _ in range(resamples):
                    indexes = [rng.randrange(len(round_ids)) for _ in round_ids]
                    b = statistics.median(base[i] for i in indexes)
                    c = statistics.median(cand[i] for i in indexes)
                    bootstrap.append(100 * (1 - c / b))
                result[metric] = {'reduction_percent': reduction,
                    'paired_bootstrap_95_percent': [quantile(bootstrap, .025), quantile(bootstrap, .975)],
                    'candidate_median': statistics.median(cand), 'reference_median': statistics.median(base),
                    'paired_rounds': len(round_ids)}
            comparisons[reference][variant] = result
    return {
        'input': str(path.resolve()), 'input_sha256': digest(path), 'cohort_identity': identity,
        'cohort': {key: data.get(key) for key in
            ('date', 'hardware', 'macos', 'platform', 'architecture', 'pi_version', 'harness_sha256',
             'pace_ms_per_delta', 'workload', 'external_observation_extension', 'completion', 'endpoint_version',
             'terminal_screen_sha256', 'limits')},
        'workload_sample': {key: samples[0].get(key) for key in WORKLOAD_KEYS},
        'validation_basis': 'Legacy RPC harness assertions plus matching request/tool/reply fields' if legacy_rpc else 'Explicit passed=true for every measured sample',
        'configs': data.get('configs', {}), 'artifact_provenance': data.get('artifacts', {}),
        'artifact_manifests': data.get('artifact_manifests', {}),
        'feature_settings': {name: {key: manifest.get(key) for key in
            ('priority_final_render', 'native_hot_layout', 'skip_trained_hot', 'smol_baked',
             'runtime_sha256', 'build_driver_sha256', 'bytecode_order_sha256', 'input_sources_validation')}
            for name, manifest in data.get('artifact_manifests', {}).items()},
        'provenance_limitation': None if data.get('artifacts') else
            'This harness output did not record executable/image hashes; attach the prepared matrix provenance separately.',
        'round_ids': round_ids, 'summary': summaries, 'comparisons': comparisons}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('inputs', type=Path, nargs='+')
    parser.add_argument('--reference', action='append', required=True,
                        help='Measured baseline name; repeat to compare multiple causal/market controls')
    parser.add_argument('--variant', action='append', help='Measured candidate name; defaults to every non-reference row')
    parser.add_argument('--resamples', type=int, default=3000)
    parser.add_argument('--seed', type=int, default=20261002)
    parser.add_argument('--allow-legacy-rpc', action='store_true',
                        help='Accept old RPC outputs that rely on harness assertions instead of a passed flag')
    parser.add_argument('--separate-cohorts', action='store_true',
                        help='Explicitly allow distinct labeled reports with different endpoint/driver/screen/harness identities; never pool samples')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.resamples < 100:
        parser.error('--resamples must be at least 100')
    reports = [analyze(path, args.reference, args.variant, args.resamples, args.seed, args.allow_legacy_rpc) for path in args.inputs]
    validate_independent_cohorts(reports, args.separate_cohorts)
    output = {'date': datetime.now(timezone.utc).isoformat(), 'analyzer_sha256': digest(__file__),
        'method': 'Descriptive paired-round bootstrap of ratio of medians; positive reduction means lower is better',
        'resamples': args.resamples, 'seed': args.seed, 'separate_cohorts': args.separate_cohorts,
        'limits': [
            'Intervals describe this host/cohort; they are not independent-host confidence guarantees.',
            'No outliers are removed. Warmups are excluded by using only measured samples.',
            'Every file is analyzed separately: paced, burst, long and independent batches are not pooled.',
            'Historical final-text and v2 visible-idle-editor endpoints have distinct identities; differing screen/driver/harness revisions require explicit separate-cohort mode.',
            'Feature metadata is retained: priority final-render gains require equally transformed stable controls and must not be attributed to AOT.',
            'Multiple metrics/comparisons are descriptive; no multiple-comparison significance correction is claimed.',
            'Wall time includes mock pacing and tool/terminal scheduling. CPU and footprint measure the Pi process.'
        ], 'reports': reports}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(output, indent=2) + '\n')
    print(f'PASS: analyzed {len(reports)} complete paired cohorts; wrote {args.output}')


if __name__ == '__main__':
    main()
