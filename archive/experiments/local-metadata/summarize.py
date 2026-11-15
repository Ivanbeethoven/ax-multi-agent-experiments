#!/usr/bin/env python3
"""Summarize the local Redis diagnostic; timings include final remote drain."""
import argparse
import csv
import json
import statistics
from collections import defaultdict
from pathlib import Path


def commands(raw):
    result = {}
    for line in raw.splitlines():
        if line.startswith('cmdstat_'):
            name, fields = line.removeprefix('cmdstat_').split(':', 1)
            result[name] = int(dict(field.split('=', 1) for field in fields.split(','))['calls'])
    return result


def summarize(rows):
    groups = defaultdict(list)
    for row in rows:
        assert row['final_value'] == 99 and row['operations'] == 100
        assert len(row['operation_ns']) == 100
        assert row['total_with_drain_ns'] >= row['active_ns']
        before, after = commands(row['redis_before']), commands(row['redis_after'])
        delta = {key: after.get(key, 0) - before.get(key, 0) for key in set(before) | set(after)}
        assert all(value >= 0 for value in delta.values())
        # INFO is the measurement itself; commands inside Lua remain counted.
        delta.pop('info', None)
        groups[row['mode']].append(dict(pair=row['pair'], total_ms=row['total_with_drain_ns'] / 1e6,
                                       active_ms=row['active_ns'] / 1e6,
                                       drain_ms=(row['total_with_drain_ns'] - row['active_ns']) / 1e6,
                                       redis_calls=delta))
    result = {'scope': 'debug-test diagnostic; not release/FUSE performance', 'modes': {}}
    for mode, pairs in sorted(groups.items()):
        assert len(pairs) == 3 and {row['pair'] for row in pairs} == {0, 1, 2}
        result['modes'][mode] = dict(total_ms_median=statistics.median(row['total_ms'] for row in pairs),
                                    set_calls=[row['redis_calls'].get('set', 0) for row in pairs],
                                    pairs=pairs)
    return result


def summarize_fuse(root, extra=None):
    result = {'scope': 'optimized FUSE diagnostic; sync modes run in fixed order', 'groups': []}
    directories = [root / ('fuse-' + mode) for mode in ('remote', 'local-journal', 'local-buffered')]
    if extra:
        directories.append(extra)
    for directory in directories:
        run = json.loads((directory / 'run.json').read_text())
        mode = run['metadata_sync']
        assert run['passed']
        groups = defaultdict(list)
        for entry in run['cells']:
            cell = json.loads((directory / entry['label'] / 'result.json').read_text())
            assert cell['remote_remount_verified']
            edits = cell['metadata_edits']
            assert edits['count'] == len(edits['operation_ns']) == 100
            assert edits['total_with_drain_ns'] >= edits['active_ns'] > 0
            groups[(cell['scenario'], cell['cache_enabled'])].append(cell)
        for (scenario, cached), cells in sorted(groups.items()):
            assert len(cells) == 3
            phases = [cell['metadata_edits'] for cell in cells]
            result['groups'].append(dict(mode=mode, scenario=scenario, read_cache=cached,
                flush_interval_ms=None if mode == 'remote' else int(run['runtime_env'].get('BREWFS_METADATA_FLUSH_MS', 10)),
                total_ms_median=statistics.median(p['total_with_drain_ns'] / 1e6 for p in phases),
                total_ms=[p['total_with_drain_ns'] / 1e6 for p in phases],
                active_ms=[p['active_ns'] / 1e6 for p in phases],
                set_calls=[p['redis_delta'].get('set', {}).get('calls', 0) for p in phases],
                rustfs_zero=all(not any(p['rustfs_delta'].values()) for p in phases),
                remote_remounts_verified=len(cells)))
    return result


def summarize_guard(directory):
    run = json.loads((directory / 'run.json').read_text())
    assert run['passed'] and run['cleanup_exit'] == 0
    tables = list(directory.rglob('fully-drained-throughput.tsv'))
    assert len(tables) == 1
    rows = []
    with tables[0].open() as stream:
        for source in csv.DictReader(stream, delimiter='\t'):
            tool = source['tool']
            jobs = json.loads((tables[0].parent / 'results' / (tool + '.json')).read_text())['jobs']
            assert jobs and all(job['error'] == 0 for job in jobs)
            assert len(jobs) == 1, 'This summary requires fio group_reporting'
            row = {key: value if key == 'tool' else float(value) for key, value in source.items()}
            for direction in ('read', 'write'):
                io = jobs[0][direction]
                row[direction + '_p99_ms'] = io['clat_ns']['percentile']['99.000000'] / 1e6 if io['io_bytes'] else None
            rows.append(row)
    return dict(run=str(directory), binary_sha256=run['binary_sha256'], direct=run['direct'], rows=rows)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('results', type=Path)
    parser.add_argument('--fuse-root', type=Path)
    parser.add_argument('--fuse-extra', type=Path)
    parser.add_argument('--guard-runs', type=Path, nargs='*', default=[])
    args = parser.parse_args()
    result = summarize(json.loads(args.results.read_text()))
    if args.fuse_root:
        result['fuse'] = summarize_fuse(args.fuse_root, args.fuse_extra)
    if args.guard_runs:
        result['write_guards'] = [summarize_guard(path) for path in args.guard_runs]
    print(json.dumps(result, indent=2))
