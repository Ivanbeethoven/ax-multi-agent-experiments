"""Summarize diagnostic timings separately from the normal performance pairs."""
import argparse
from collections import defaultdict
import json
from pathlib import Path
import statistics


def read(path):
    return json.loads(path.read_text())


def cpu(directory):
    rows = []
    for path in sorted(directory.rglob('cpu-*.json')):
        data = read(path)
        assert data['diagnostic_only'] and len(data['durations_ns']) == 20000
        times = data['durations_ns']
        rows.append(dict(mode=path.stem, calls=len(times),
            total_ms={name: sum(t[i] for t in times) / 1e6 for i, name in enumerate(data['columns'])},
            median_us={name: statistics.median(t[i] for t in times) / 1e3
                       for i, name in enumerate(data['columns'])},
            redis_commands={k: value['calls'] - data['redis_before'].get(k, {}).get('calls', 0)
                            for k, value in data['redis_after'].items() if k != 'info'}))
    assert len(rows) == 2
    return rows


def stages(directory):
    run = read(directory / 'run.json')
    assert run['passed'] and run['diagnostic_only']
    groups = defaultdict(lambda: dict(counters=defaultdict(float), opens=0, caller_open_ns=0))
    for cell in run['cells']:
        group = groups[(cell['scenario'], cell['arm'])]
        for sample in read(directory / cell['label'] / 'result.json')['samples']:
            assert sample['diagnostic_only']
            group['opens'] += len(sample['pread'])
            group['caller_open_ns'] += sample['breakdown_ns']['file_open']
            for key, value in sample['server_profile_delta'].items():
                group['counters'][key] += value
    result = []
    for (scenario, arm), group in sorted(groups.items()):
        counters = group['counters']
        timed = {}
        for key, value in counters.items():
            if key.startswith('brewfs_hot_path_calls_total{'):
                name = key.split('"')[1]
                ns = counters[f'brewfs_hot_path_nanoseconds_total{{stage="{name}"}}']
                timed[name] = dict(calls=value, total_ms=ns / 1e6,
                                   mean_us=ns / value / 1e3 if value else None)
        server = {}
        for op in ('lookup', 'getattr', 'open', 'read', 'flush', 'release', 'opendir', 'readdir'):
            count = counters[f'brewfs_fuse_op_total{{op="{op}"}}']
            seconds = counters[f'brewfs_fuse_op_latency_seconds_sum{{op="{op}"}}']
            server[op] = dict(calls=count, mean_us=seconds / count * 1e6 if count else None)
        assert server['open']['calls'] == group['opens']
        result.append(dict(scenario=scenario, arm=arm, stages=timed, fuse=server,
            caller_open_mean_us=group['caller_open_ns'] / group['opens'] / 1e3))
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--results', type=Path, required=True)
    parser.add_argument('--baseline-profile', type=Path, required=True)
    args = parser.parse_args()
    probe = read(args.results / 'profile-stages/small-files-32-pair1-production-warm/metadata-expired-probe.json')
    assert probe['diagnostic_only'] and not any(probe['payload_hot'].values())
    print(json.dumps(dict(diagnostic_only=True,
        timing='Server stages overlap; caller open includes LOOKUP; CPU-profile elapsed times are not acceptance measurements.',
        stages=stages(args.results / 'profile-stages'),
        cpu_baseline=cpu(args.baseline_profile), cpu_candidate=cpu(args.results / 'profile-existing'),
        metadata_expired_probe=probe), indent=2))
