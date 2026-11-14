#!/usr/bin/env python3
"""Verify and summarize fresh-remount reads for Packed and native Workspace."""
import argparse
import hashlib
import json
from pathlib import Path
import random
import statistics


def summarize(root):
    root = root.resolve()
    for line in (root / 'SHA256SUMS').read_text().splitlines():
        digest, name = line.split('  ', 1)
        path = (root / name).resolve()
        assert path.is_relative_to(root)
        with path.open('rb') as f:
            assert hashlib.file_digest(f, 'sha256').hexdigest() == digest, name
    run = json.loads((root / 'run.json').read_text())
    assert run['schema'] == 'overlay-cold-native-v1' and run['passed']
    assert run['cleanup']['mount_absent'] and all(run['cleanup'][c] for c in run['containers'])
    cells = [json.loads((root / c['label'] / 'result.json').read_text()) for c in run['cells']]
    arms = ('production', 'overlay-read', 'lower-read', 'native-overlay')
    for c in cells:
        assert c['cache_enabled'] and not any(c['steady_objects'].values())
        proof = json.loads((root / c['label'] / 'remount-proof.json').read_text())
        assert proof['clean_unmount'] and proof['measured_cache_absent_before_mount']
        assert proof['preparation_cache'] != proof['measured_cache']
        if c['cache_temperature'] == 'cold':
            assert not c['warm_s3'] and not any(c['warmup_s3'].values())
            assert c['first_objects']['read_requests'] > 0 and c['first_objects']['body_bytes'] > 0
        else:
            assert len(c['warm_s3']) == 3 and not any(c['warm_s3'][-1].values())
            assert not any(c['first_objects'].values())
        assert c['stats_settle_seconds'] >= .3
        if run['object_meter'] == 'proxy':
            assert c['first_objects']['body_bytes'] == c['first_s3']['received_bytes']
            assert c['first_objects']['read_requests'] == c['first_s3']['gets' if c['arm'] == 'native-overlay' else 'requests']
        assert c['stats_delta']['brewfs_fuse_read_ops_total'] == len(c['expected_sha256']) * (c['steady_rounds'] + 1)
        if c['arm'] in ('overlay-read', 'lower-read'):
            assert c['write_open_rejected']
    rows = []
    comparisons = []
    median = statistics.median
    for scenario in sorted({c['scenario'] for c in cells}):
        group = [c for c in cells if c['scenario'] == scenario]
        pairs = sorted({c['pair'] for c in group})
        assert len(pairs) >= 3 and pairs == list(range(1, len(pairs) + 1))
        assert len({json.dumps(c['expected_sha256'], sort_keys=True) for c in group}) == 1
        for pair in pairs:
            expected = [(a, s) for a in arms for s in ('cold', 'warm')]
            random.Random(20261113 + pair - 1).shuffle(expected)
            assert [(c['arm'], c['cache_temperature']) for c in group if c['pair'] == pair] == expected
        for arm in arms:
            for state in ('cold', 'warm'):
                values = [c for c in group if c['arm'] == arm and c['cache_temperature'] == state]
                assert sorted(c['pair'] for c in values) == pairs
                first = [c['first_round']['round_elapsed_ns'] / 1e6 for c in values]
                rows.append(dict(scenario=scenario, arm=arm, cache_temperature=state, pairs=len(values),
                    files=len(values[0]['expected_sha256']), first_round_ms=median(first), first_round_runs_ms=first,
                    mount_ms=median([c['mount_elapsed_ns'] / 1e6 for c in values]),
                    warmup_ms=median([c['warmup_elapsed_ns'] / 1e6 for c in values]),
                    steady_ms_per_round=median([c['steady_elapsed_ns'] / c['steady_rounds'] / 1e6 for c in values]),
                    first_read_requests=median([c['first_objects']['read_requests'] for c in values]),
                    first_gets=median([c['first_s3']['gets'] for c in values]) if run['object_meter'] == 'proxy' else None,
                    first_download_bytes=median([c['first_objects']['body_bytes'] for c in values]),
                    mount_gets=median([c['mount_s3']['gets'] for c in values]) if run['object_meter'] == 'proxy' else None,
                    first_redis_commands=median([sum(v.get('calls', 0) for k, v in c['first_redis_delta'].items() if k != 'info') for c in values]),
                    first_pread_total_ms=median([sum(r['elapsed_ns'] for r in c['first_round']['pread']) / 1e6 for c in values]),
                    first_open_list_ms=median([c['first_round']['open_and_list_ns'] / 1e6 for c in values])))
        for state in ('cold', 'warm'):
            overlay = next(r for r in rows if r['scenario'] == scenario and r['arm'] == 'overlay-read' and r['cache_temperature'] == state)
            lower = next(r for r in rows if r['scenario'] == scenario and r['arm'] == 'lower-read' and r['cache_temperature'] == state)
            by_pair = [{c['arm']: c for c in group if c['pair'] == pair and c['cache_temperature'] == state} for pair in pairs]
            comparisons.append(dict(scenario=scenario, cache_temperature=state,
                first_round_empty_upper_extra_pct=100 * (overlay['first_round_ms'] / lower['first_round_ms'] - 1),
                paired_first_round_extra_pct=[100 * (c['overlay-read']['first_round']['round_elapsed_ns'] / c['lower-read']['first_round']['round_elapsed_ns'] - 1) for c in by_pair]))
    return dict(passed=True, object_meter=run['object_meter'], source_commit=run['source_commit'], binary_sha256=run['binary_sha256'],
                cells=len(cells), rows=rows, empty_upper_comparisons=comparisons,
                scope=run['comparison'], cold_scope=run['cold_scope'])


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('run', type=Path)
    print(json.dumps(summarize(parser.parse_args().run), indent=2))
