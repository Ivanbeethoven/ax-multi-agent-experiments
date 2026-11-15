#!/usr/bin/env python3
"""Summarize every mixed-I/O pair, retaining the initial adverse tail result."""
import argparse
import importlib.util
import json
from pathlib import Path
import statistics
import sys


def summarize(compare_tool, runtime, repeats, extended=None):
    sys.path.insert(0, str(compare_tool.resolve().parent))
    spec = importlib.util.spec_from_file_location('brewfs_perf_comparison', compare_tool)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    metrics = {
        'bandwidth_with_drain_mib_s': ('runtime', 'fio-randrw', 'mixed_effective_active_plus_drain_bw_mib_s'),
        'read_mean_ms': ('fio', 'fio-randrw', 'read_total_mean_ms'),
        'write_mean_ms': ('fio', 'fio-randrw', 'write_total_mean_ms'),
        'read_p99_ms': ('fio', 'fio-randrw', 'read_total_p99_ms'),
        'write_p99_ms': ('fio', 'fio-randrw', 'write_total_p99_ms'),
        'read_ios': ('fio', 'fio-randrw', 'read_total_ios'),
        'write_ios': ('fio', 'fio-randrw', 'write_total_ios'),
        'read_p99_sufficient': ('fio_group', 'fio-randrw/group-0', 'read_p99_sample_sufficient'),
        'write_p99_sufficient': ('fio_group', 'fio-randrw/group-0', 'write_p99_sample_sufficient'),
        'drain_seconds': ('drain', 'fio-randrw', 'post_write_drain_s'),
    }
    def read_run(directory):
        receipt = json.loads((directory / 'run.json').read_text())
        assert receipt['passed'] and receipt['cleanup_exit'] == 0
        artifacts = list(directory.glob('perf-run-*-brewfs'))
        assert len(artifacts) == 1
        loaded = module.load_artifact(artifacts[0])
        assert all(loaded['drain', 'fio-randrw', name].value == 0 for name in
                   ('post_write_drain_timeout', 'drain_dirty_mib', 'drain_pending_mib', 'drain_buffer_dirty_mib'))
        row = {name: loaded[key].value for name, key in metrics.items()}
        row.update(binary_sha256=receipt['binary_sha256'], artifact=str(artifacts[0]),
                   runtime_seconds=receipt.get('runtime_seconds', 20))
        return row

    def aggregate(rows):
        for side in ('before', 'after'):
            assert len({row[side]['binary_sha256'] for row in rows}) == 1
        return dict(pairs=rows, medians={
            side: {name: statistics.median(row[side][name] for row in rows) for name in metrics}
            for side in ('before', 'after')})

    rows = []
    for pair in (1, 2, 3):
        row = {'pair': pair, 'order': ['after', 'before'] if pair == 2 else ['before', 'after']}
        for side in ('before', 'after'):
            directory = runtime / f'mixed-pair-{side}' if pair == 1 else repeats / f'pair-{pair}-{side}'
            row[side] = read_run(directory)
        rows.append(row)
    result = aggregate(rows)
    result['interpretation'] = 'All three short pairs retained. P99 sample flags refer to each individual fio run; do not pool or hide insufficient samples. Longer runs are summarized separately.'
    if extended is not None:
        rows = []
        for pair in (1, 2):
            row = dict(pair=pair, order=['before', 'after'] if pair == 1 else ['after', 'before'])
            for side in ('before', 'after'):
                row[side] = read_run(extended / f'pair-{pair}-{side}')
                assert row[side]['runtime_seconds'] == 40
                assert row[side]['read_p99_sufficient'] == row[side]['write_p99_sufficient'] == 1
            rows.append(row)
        result['extended_40s'] = aggregate(rows)
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--compare-tool', type=Path, required=True)
    parser.add_argument('--runtime', type=Path, required=True)
    parser.add_argument('--repeats', type=Path, required=True)
    parser.add_argument('--extended', type=Path)
    args = parser.parse_args()
    print(json.dumps(summarize(args.compare_tool, args.runtime, args.repeats, args.extended), indent=2))
