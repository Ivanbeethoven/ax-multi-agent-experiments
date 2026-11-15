#!/usr/bin/env python3
"""Summarize paired optimized FUSE runs, including final fsync/close."""
import argparse
import json
import math
from pathlib import Path
import statistics


def summarize(root):
    runs = json.loads((root / 'runs.json').read_text())
    groups = {}
    total = 0
    for run in runs:
        directory = root / run['directory']
        receipt = json.loads((directory / 'run.json').read_text())
        assert receipt['passed'] and len(receipt['cells']) == 6
        assert receipt['runtime_env'].get('BREWFS_METADATA_PROFILE', '0') != '1'
        for reference in receipt['cells']:
            cell = json.loads((directory / reference['label'] / 'result.json').read_text())
            assert cell['cache_enabled']
            assert cell['remote_remount_verified']
            assert all(value == 0 for value in cell['rustfs_delta'].values())
            metadata = cell['metadata_edits']
            assert metadata['count'] == 100
            assert all(value == 0 for value in metadata['rustfs_delta'].values())
            key = f"{run['interval_ms']}ms/{cell['scenario']}/{run['variant']}"
            groups.setdefault(key, []).append(dict(
                outer_pair=run['pair'], inner_pair=cell['pair'],
                total_ms=metadata['total_with_drain_ns'] / 1e6,
                active_ms=metadata['active_ns'] / 1e6,
                drain_ms=(metadata['total_with_drain_ns'] - metadata['active_ns']) / 1e6,
                hot_read_ms=cell['elapsed_ns'] / 1e6,
                open_and_list_ns=[s['open_and_list_ns'] for s in cell['samples']],
                pread_ns=[r['elapsed_ns'] for s in cell['samples'] for r in s['pread']],
                stats_delta=cell['stats_delta'],
                operation_ns=metadata['operation_ns'],
                chmod_ns=metadata['chmod_ns'], stat_ns=metadata['stat_ns'],
                redis_calls={k: v.get('calls', 0) for k, v in metadata['redis_delta'].items()},
            ))
            total += 1
    result = {'cells': total, 'groups': {}}
    for key, rows in groups.items():
        latencies = {}
        for name in ('open_and_list_ns', 'pread_ns', 'operation_ns', 'chmod_ns', 'stat_ns'):
            values = sorted(v for row in rows for v in row[name])
            latencies[name] = dict(samples=len(values), median_ns=statistics.median(values),
                                   p99_ns=values[math.ceil(len(values) * .99) - 1])
        result['groups'][key] = {
            'measurements': len(rows),
            'median_total_ms': statistics.median(r['total_ms'] for r in rows),
            'median_active_ms': statistics.median(r['active_ms'] for r in rows),
            'median_drain_ms': statistics.median(r['drain_ms'] for r in rows),
            'median_hot_read_ms': statistics.median(r['hot_read_ms'] for r in rows),
            'set_calls': [r['redis_calls'].get('set', 0) for r in rows],
            'evalsha_calls': [r['redis_calls'].get('evalsha', 0) for r in rows],
            'latencies': latencies,
            'runs': rows,
        }
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('root', type=Path)
    args = parser.parse_args()
    print(json.dumps(summarize(args.root), indent=2) + '\n')
