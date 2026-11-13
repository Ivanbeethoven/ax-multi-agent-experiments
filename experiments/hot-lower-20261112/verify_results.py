#!/usr/bin/env python3
"""Verify retained hashes, complete pairs, and the hot-read admission checks."""
import argparse
import hashlib
import json
from pathlib import Path


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def verify(root):
    root = root.resolve()
    files = 0
    for line in (root / 'SHA256SUMS').read_text().splitlines():
        expected, relative = line.split('  ', 1)
        path = (root / relative).resolve()
        assert path.is_relative_to(root), 'hash path escapes the run'
        assert digest(path) == expected, f'hash mismatch: {relative}'
        files += 1
    run = json.loads((root / 'run.json').read_text())
    assert run['passed'], f'{root.name} is not a completed run'
    if run.get('schema') == 'hot-overlay-local-v1':
        assert all(run['cleanup'][name] for name in run['containers'])
        assert run['cleanup']['mount_absent']
        cells = [json.loads((root / c['label'] / 'result.json').read_text()) for c in run['cells']]
        enabled_states = {'both': (False, True), 'on': (True,), 'off': (False,)}[run.get('cache_state', 'both')]
        for scenario in ('two-files', 'modified-upper'):
            group = [c for c in cells if c['scenario'] == scenario]
            pairs = {c['pair'] for c in group}
            assert len(pairs) >= 3 and pairs == set(range(1, len(pairs) + 1))
            assert len(group) == len(enabled_states) * len(pairs)
            assert {(c['pair'], c['cache_enabled']) for c in group} == {(p, enabled) for p in pairs for enabled in enabled_states}
            assert len({json.dumps(c['expected_sha256'], sort_keys=True) for c in group}) == 1
        for cell in cells:
            if edits := cell.get('metadata_edits'):
                assert edits['count'] == len(edits['operation_ns'])
                assert edits['total_with_drain_ns'] >= edits['active_ns'] > 0
                if edits.get('latency_schema') == 'chmod-stat-v2':
                    assert len(edits['chmod_ns']) == len(edits['stat_ns']) == edits['count']
                    assert all(a > 0 and b > 0 and a + b == total for a, b, total in
                               zip(edits['chmod_ns'], edits['stat_ns'], edits['operation_ns']))
                assert cell['remote_remount_verified']
            if run.get('metadata_sync', 'remote') != 'remote':
                assert cell['remote_remount_verified']
                assert digest(root / cell['label'] / 'metadata-journal.bin') == cell['metadata_journal_sha256']
                assert len(cell['remote_remount_samples']['pread']) == len(cell['expected_sha256'])
            assert not any(cell['warm_s3'][-1].values())
            assert not any(cell['rustfs_delta'].values())
            assert cell['rustfs_before']['inflight'] == cell['rustfs_after']['inflight'] == 0
            samples = cell['samples']
            reads = [read for sample in samples for read in sample['pread']]
            assert cell['stats_delta']['brewfs_fuse_read_ops_total'] == len(reads)
            assert all(read['elapsed_ns'] > 0 for read in reads)
            measured = sum(sample['open_and_list_ns'] + sample['lookup_and_stat_ns'] for sample in samples)
            measured += sum(read['elapsed_ns'] for read in reads)
            assert 0 < measured <= cell['elapsed_ns']
            assert all(value.get('calls', 0) >= 0 for value in cell['redis_delta'].values())
        return dict(run=root.name, passed=True, files_verified=files, cells=len(cells))
    assert run['cleanup_exit'] == 0
    assert list(root.glob('perf-run-*-brewfs/perf.complete'))
    return dict(run=root.name, passed=True, files_verified=files, kind='compose guard')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('runs', type=Path, nargs='+')
    args = parser.parse_args()
    print(json.dumps([verify(path) for path in args.runs], indent=2))
