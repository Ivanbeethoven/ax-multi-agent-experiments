#!/usr/bin/env python3
"""Summarize retained per-operation data without changing it."""
import argparse
import json
from pathlib import Path
from statistics import median

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('runs', type=Path, nargs='+')
args = parser.parse_args()
runs = [(path, json.loads((path / 'run.json').read_text())) for path in args.runs]
assert len({path.resolve() for path, _ in runs}) == len(runs), 'duplicate run directory'
assert all(run['passed'] for _, run in runs), 'failed runs are diagnostic evidence, not performance results'
assert len({(run['source_commit'], run['source_patch_sha256'], run['binary_sha256'], run['producer_sha256'],
             json.dumps(run['images'], sort_keys=True)) for _, run in runs}) == 1, 'different builds/backends cannot be pooled'
rows = []
for scenario in ('two-files', 'modified-upper'):
    cells = []
    for path, run in runs:
        for row in run['cells']:
            if row['scenario'] == scenario:
                cell = json.loads((path / row['label'] / 'result.json').read_text())
                cell['run'] = str(path.resolve())
                cells.append(cell)
    assert len({len(cell['samples']) for cell in cells}) == 1, 'different repetition counts'
    assert len({json.dumps(cell['expected_sha256'], sort_keys=True) for cell in cells}) == 1, 'different inputs'
    groups = {enabled: [cell for cell in cells if cell['cache_enabled'] == enabled] for enabled in (False, True)}
    summary = {}
    for enabled, group in groups.items():
        summary['on' if enabled else 'off'] = dict(
            total_ms=median(cell['elapsed_ns'] / 1e6 for cell in group),
            pread_median_us=median(median(read['elapsed_ns'] / 1e3 for sample in cell['samples'] for read in sample['pread']) for cell in group),
            pread_by_path_us={path: median(median(read['elapsed_ns'] / 1e3 for sample in cell['samples']
                                                  for read in sample['pread'] if read['path'] == path) for cell in group)
                              for path in group[0]['expected_sha256']},
            logical_read_bytes_per_run=[sum(read['bytes'] for sample in cell['samples'] for read in sample['pread']) for cell in group],
            fuse_reads=[cell['stats_delta']['brewfs_fuse_read_ops_total'] for cell in group],
            open_list_median_us=median(median(sample['open_and_list_ns'] / 1e3 for sample in cell['samples']) for cell in group),
            extent_queries=[cell['stats_delta']['brewfs_workspace_upper_extent_queries_total'] for cell in group],
            extent_hits=[cell['stats_delta']['brewfs_workspace_upper_extent_empty_cache_hits_total'] for cell in group],
            directory_queries=[cell['stats_delta']['brewfs_workspace_upper_directory_queries_total'] for cell in group],
            directory_hits=[cell['stats_delta']['brewfs_workspace_upper_directory_empty_cache_hits_total'] for cell in group],
            redis_calls=[sum(value.get('calls', 0) for key, value in cell['redis_delta'].items() if key != 'info') for cell in group],
            redis_zrangebylex=[cell['redis_delta'].get('zrangebylex', {}).get('calls', 0) for cell in group],
            rustfs_requests=[cell['rustfs_delta']['requests'] for cell in group],
            rustfs_received_bytes=[cell['rustfs_delta']['received_bytes'] for cell in group],
        )
    summary['n_pairs'] = len(groups[False])
    summary['included_runs'] = [path.name for path, _ in runs]
    summary['paired_time_reduction_pct'] = [100 * (1 - next(c for c in groups[True] if (c['run'], c['pair']) == (off['run'], off['pair']))['elapsed_ns'] / off['elapsed_ns']) for off in groups[False]]
    rows.append(dict(scenario=scenario, **summary))
print(json.dumps(rows, ensure_ascii=False, indent=2))
