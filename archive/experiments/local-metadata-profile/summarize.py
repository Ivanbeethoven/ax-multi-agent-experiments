#!/usr/bin/env python3
"""Summarize real Redis diagnostics; nested/concurrent stage times are not summed."""
import argparse
import json
import statistics
from pathlib import Path


def commands(text):
    result = {}
    for line in text.splitlines():
        if line.startswith('cmdstat_'):
            name, values = line[8:].split(':', 1)
            result[name] = int(dict(v.split('=') for v in values.split(','))['calls'])
    return result


def summarize(rows):
    result = {}
    for mode in sorted({r['mode'] for r in rows}):
        group = [r for r in rows if r['mode'] == mode]
        stages = group[0].get('stages') or {}
        result[mode] = {
            'runs': len(group),
            'total_ms': [r['total_with_drain_ns'] / 1e6 for r in group],
            'median_total_ms': statistics.median(r['total_with_drain_ns'] / 1e6 for r in group),
            'median_active_ms': statistics.median(r['active_ns'] / 1e6 for r in group),
            'median_drain_ms': statistics.median((r['total_with_drain_ns'] - r['active_ns']) / 1e6 for r in group),
            'redis_commands': [
                {k: after.get(k, 0) - before.get(k, 0) for k in sorted(before.keys() | after.keys())}
                for r in group for before, after in [(commands(r['redis_before']), commands(r['redis_after']))]
            ],
            'stages': {
                stage: {
                    'median_total_ms': statistics.median(r['stages'][stage]['total_ns'] / 1e6 for r in group),
                    'calls': [r['stages'][stage]['calls'] for r in group],
                }
                for stage in stages
            },
        }
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('results', type=Path, nargs='+')
    args = parser.parse_args()
    rows = [row for path in args.results for row in json.loads(path.read_text())]
    print(json.dumps({'units': 'wall milliseconds; nested/concurrent stages are NOT additive',
                      'modes': summarize(rows)}, indent=2))
