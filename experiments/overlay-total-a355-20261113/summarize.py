#!/usr/bin/env python3
"""Audit complete Overlay vs standalone Packed reads with matched caches."""
import argparse
import hashlib
import json
from pathlib import Path
import statistics


def read(path):
    return json.loads(path.read_text())


def summarize(root):
    root = root.resolve()
    for line in (root / 'SHA256SUMS').read_text().splitlines():
        digest, name = line.split('  ', 1)
        path = (root / name).resolve()
        assert path.is_relative_to(root)
        with path.open('rb') as stream:
            assert hashlib.file_digest(stream, 'sha256').hexdigest() == digest, name
    run = read(root / 'run.json')
    assert run['schema'] in ('overlay-total-v1', 'overlay-version-total-v1') and run['passed']
    versioned = run['schema'] == 'overlay-version-total-v1'
    arm_names = ('production', 'candidate', 'packed-direct') if versioned else ('production', 'packed-direct')
    temperatures_used = ('warm',) if run.get('total_settings', {}).get('temperatures') == 'warm' else ('cold', 'warm')
    assert run['build_profile'] == 'optimized-release'
    assert run['cleanup']['mount_absent'] and all(run['cleanup'][c] for c in run['containers'])
    cells = [read(root / c['label'] / 'result.json') for c in run['cells']]
    median = statistics.median
    rows, differences = [], []
    for scenario in sorted({c['scenario'] for c in cells}):
        group = [c for c in cells if c['scenario'] == scenario]
        pairs = sorted({c['pair'] for c in group})
        assert len(pairs) >= 3 and pairs == list(range(1, len(pairs) + 1))
        assert len(group) == len(temperatures_used) * len(arm_names) * len(pairs)
        for key in ('expected_sha256', 'manifest_key', 'cache_config'):
            assert len({json.dumps(c[key], sort_keys=True) for c in group}) == 1, key
        for pair in pairs:
            arms = ('production', 'packed-direct') if pair % 2 else ('packed-direct', 'production')
            if versioned:
                from itertools import permutations
                arms = list(permutations(arm_names))[(pair - 1) % 6]
            temperatures = ('cold', 'warm') if pair % 2 else ('warm', 'cold')
            if temperatures_used == ('warm',):
                temperatures = ('warm',)
            assert [(c['arm'], c['cache_temperature']) for c in group if c['pair'] == pair] == [(a, t) for t in temperatures for a in arms]
        for cell in group:
            c = cell
            if versioned:
                use_candidate = c['arm'] == 'candidate' or (
                    c['arm'] == 'packed-direct' and run['total_settings'].get('lower_build', 'baseline') == 'candidate')
                expected_binary = run['candidate_binary_sha256'] if use_candidate else run['binary_sha256']
                assert c['binary_sha256'] == expected_binary
            if run.get('detail_runner', {}).get('effective_kernel_ttl') == 'zero-checked':
                assert c['effective_kernel_ttl_checked_zero']
                for phase in ('before', 'first', 'after'):
                    stats = dict(line.split() for line in (root / c['label'] / (phase + '.stats')).read_text().splitlines()
                                 if line.startswith(('brewfs_fuse_attr_valid_ns ', 'brewfs_fuse_entry_valid_ns ')))
                    assert stats == {'brewfs_fuse_attr_valid_ns': '0', 'brewfs_fuse_entry_valid_ns': '0'}
            proof = read(root / c['label'] / 'remount-proof.json')
            assert proof['clean_unmount'] and proof['measured_cache_absent_before_mount']
            assert proof['preparation_cache'] != proof['measured_cache']
            assert proof['manifest_key'] == c['manifest_key']
            assert not any(c['steady_objects'].values())
            assert c['stats_delta']['brewfs_fuse_read_ops_total'] == len(c['expected_sha256']) * (c['steady_rounds'] + 1)
            if c['cache_temperature'] == 'cold':
                assert c['warm_s3'] == [] and not any(c['warmup_objects'].values())
                assert c['first_objects']['read_requests'] > 0 and c['first_objects']['body_bytes'] > 0
            else:
                assert len(c['warm_s3']) == 3 and not any(c['first_objects'].values())
                assert c['warmup_objects']['read_requests'] > 0
            if run['object_meter'] == 'proxy':
                assert c['first_objects']['read_requests'] == c['first_s3']['requests']
                assert c['first_objects']['body_bytes'] == c['first_s3']['received_bytes']
            if c['arm'] == 'packed-direct':
                assert c['mount_format'] == 'packed-snapshot' and c['write_open_rejected']
                assert proof['same_workspace'] is None
                for stage in ('mount_redis_delta', 'warmup_redis_delta', 'redis_delta'):
                    assert all(v.get('calls', 0) == 0 for k, v in c[stage].items() if k != 'info')
            else:
                assert c['mount_format'] == 'workspace' and proof['same_workspace']
        for temperature in temperatures_used:
            by_arm = {}
            for arm in arm_names:
                values = [c for c in group if c['arm'] == arm and c['cache_temperature'] == temperature]
                assert [c['pair'] for c in values] == pairs
                first = [c['first_round']['round_elapsed_ns'] / 1e6 for c in values]
                steady = [c['steady_elapsed_ns'] / c['steady_rounds'] / 1e6 for c in values]
                start_read = [(c['mount_elapsed_ns'] + c['first_round']['round_elapsed_ns']) / 1e6 for c in values]
                by_arm[arm] = dict(first=first, steady=steady, start_read=start_read)
                rows.append(dict(scenario=scenario, arm=arm, cache_temperature=temperature, pairs=len(pairs),
                    first_round_ms=median(first), first_round_runs_ms=first, steady_ms_per_round=median(steady),
                    steady_runs_ms_per_round=steady, mount_and_first_read_ms=median(start_read),
                    mount_ms=median([c['mount_elapsed_ns'] / 1e6 for c in values]),
                    first_requests=median([c['first_objects']['read_requests'] for c in values]),
                    first_bytes=median([c['first_objects']['body_bytes'] for c in values]),
                    mount_requests=median([c['mount_objects']['read_requests'] for c in values]),
                    mount_bytes=median([c['mount_objects']['body_bytes'] for c in values]),
                    first_redis_commands=median([sum(v.get('calls', 0) for k, v in c['first_redis_delta'].items() if k != 'info') for c in values]),
                    steady_redis_commands=median([sum(v.get('calls', 0) for k, v in c['steady_redis_delta'].items() if k != 'info') for c in values]),
                    first_upper_extent_queries=median([c['first_stats_delta'].get('brewfs_workspace_upper_extent_queries_total', 0) for c in values]),
                    first_upper_directory_queries=median([c['first_stats_delta'].get('brewfs_workspace_upper_directory_queries_total', 0) for c in values]),
                    hot_pread_us=median([median([p['elapsed_ns'] for s in c['samples'][1:] for p in s['pread']]) / 1000 for c in values]),
                    hot_open_list_us=median([median([s['open_and_list_ns'] for s in c['samples'][1:]]) / 1000 for c in values])))
            differences.append(dict(scenario=scenario, cache_temperature=temperature, measures={
                measure: dict(extra_ms=median(by_arm['production'][measure]) - median(by_arm['packed-direct'][measure]),
                    extra_pct=100 * (median(by_arm['production'][measure]) / median(by_arm['packed-direct'][measure]) - 1),
                    paired_extra_pct=[100 * (a / b - 1) for a, b in zip(by_arm['production'][measure], by_arm['packed-direct'][measure])])
                for measure in ('first', 'steady', 'start_read')}))
            if versioned:
                differences[-1]['candidate_vs_baseline'] = {
                    measure: dict(delta_ms=median(by_arm['candidate'][measure]) - median(by_arm['production'][measure]),
                        reduction_pct=100 * (1 - median(by_arm['candidate'][measure]) / median(by_arm['production'][measure])),
                        paired_reduction_pct=[100 * (1 - a / b) for a, b in zip(by_arm['candidate'][measure], by_arm['production'][measure])])
                    for measure in ('first', 'steady', 'start_read')}
                differences[-1]['candidate_vs_direct'] = {
                    measure: dict(extra_pct=100 * (median(by_arm['candidate'][measure]) / median(by_arm['packed-direct'][measure]) - 1))
                    for measure in ('first', 'steady', 'start_read')}
    return dict(passed=True, cells=len(cells), object_meter=run['object_meter'], rows=rows, differences=differences,
                source_commit=run['source_commit'], source_patch_sha256=run['source_patch_sha256'],
                binary_sha256=run['binary_sha256'], harness_sha256=run['harness_sha256'], scope=run['comparison'])


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('run', type=Path)
    print(json.dumps(summarize(parser.parse_args().run), indent=2))
