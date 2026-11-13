#!/usr/bin/env python3
"""Audit paired hot Overlay runs and summarize their measured, not predicted, cost."""
import argparse
import hashlib
import itertools
import json
from pathlib import Path
import statistics


def median(values):
    return statistics.median(values)


def summarize(root):
    root = root.resolve()
    for line in (root / 'SHA256SUMS').read_text().splitlines():
        expected, name = line.split('  ', 1)
        path = (root / name).resolve()
        assert path.is_relative_to(root)
        with path.open('rb') as f:
            assert hashlib.file_digest(f, 'sha256').hexdigest() == expected, name
    run = json.loads((root / 'run.json').read_text())
    assert run['passed'] and run['schema'] == 'hot-overlay-tax-v1'
    assert run['build_profile'] == 'optimized-release'
    assert run['cleanup']['mount_absent'] and all(run['cleanup'][c] for c in run['containers'])
    cells = [json.loads((root / c['label'] / 'result.json').read_text()) for c in run['cells']]
    for cell in cells:
        assert not any(cell['warm_s3'][-1].values()) and not any(cell['rustfs_delta'].values())
        assert cell['cache_enabled']
        assert len(cell['samples']) >= 1
        assert cell['stats_delta']['brewfs_fuse_read_ops_total'] == sum(len(s['pread']) for s in cell['samples'])
        if cell['arm'] != 'production':
            assert cell['write_open_rejected']
        if cell['arm'] == 'lower-read':
            assert cell['stats_delta']['brewfs_workspace_read_extent_control_bypasses_total'] == cell['stats_delta']['brewfs_fuse_read_ops_total']
        assert all(v.get('calls', 0) >= 0 for v in cell['redis_delta'].values())
    results = []
    for scenario in sorted({c['scenario'] for c in cells}):
        group = [c for c in cells if c['scenario'] == scenario]
        pairs = sorted({c['pair'] for c in group})
        assert len(pairs) >= 3 and pairs == list(range(1, len(pairs) + 1))
        assert len(group) == 3 * len(pairs)
        assert len({json.dumps(c['expected_sha256'], sort_keys=True) for c in group}) == 1
        arms = {}
        for arm in ('production', 'overlay-read', 'lower-read'):
            values = [c for c in group if c['arm'] == arm]
            assert sorted(c['pair'] for c in values) == pairs
            stats = ('brewfs_workspace_upper_extent_queries_total', 'brewfs_workspace_upper_extent_empty_cache_hits_total',
                     'brewfs_workspace_upper_directory_queries_total', 'brewfs_workspace_upper_directory_empty_cache_hits_total',
                     'brewfs_workspace_row_cache_hits_total')
            arms[arm] = dict(total_ms=median([c['elapsed_ns'] / 1e6 for c in values]),
                pread_total_ms=median([sum(r['elapsed_ns'] for s in c['samples'] for r in s['pread'])/1e6 for c in values]),
                open_list_total_ms=median([sum(s['open_and_list_ns'] for s in c['samples'])/1e6 for c in values]),
                lookup_stat_total_ms=median([sum(s['lookup_and_stat_ns'] for s in c['samples'])/1e6 for c in values]),
                pread_median_us=median([median([r['elapsed_ns']/1e3 for s in c['samples'] for r in s['pread']]) for c in values]),
                open_list_median_us=median([median([s['open_and_list_ns']/1e3 for s in c['samples']]) for c in values]),
                lookup_stat_median_us=median([median([s['lookup_and_stat_ns']/1e3 for s in c['samples']]) for c in values]),
                redis_commands=median([sum(v.get('calls',0) for k,v in c['redis_delta'].items() if k != 'info') for c in values]),
                redis_commands_by_name={name:median([c['redis_delta'].get(name,{}).get('calls',0) for c in values])
                    for name in sorted({k for c in values for k in c['redis_delta']})},
                counters={key:median([c['stats_delta'].get(key,0) for c in values]) for key in stats},
                runs_ms=[c['elapsed_ns']/1e6 for c in values])
        ratios = []
        for pair in pairs:
            by_arm = {c['arm']:c for c in group if c['pair'] == pair}
            order = tuple(c['arm'] for c in group if c['pair'] == pair)
            assert order == list(itertools.permutations(('production','overlay-read','lower-read')))[(pair-1)%6]
            ratios.append(dict(pair=pair, order=order,
                overlay_extra_pct=100*(by_arm['overlay-read']['elapsed_ns']/by_arm['lower-read']['elapsed_ns']-1),
                production_vs_lower_pct=100*(by_arm['production']['elapsed_ns']/by_arm['lower-read']['elapsed_ns']-1),
                control_vs_production_pct=100*(by_arm['overlay-read']['elapsed_ns']/by_arm['production']['elapsed_ns']-1)))
        results.append(dict(scenario=scenario, pairs=len(pairs), files=len(group[0]['expected_sha256']),
            rounds=len(group[0]['samples']), arms=arms, paired_ratios=ratios,
            overlay_extra_pct=100*(arms['overlay-read']['total_ms']/arms['lower-read']['total_ms']-1),
            production_vs_lower_pct=100*(arms['production']['total_ms']/arms['lower-read']['total_ms']-1),
            paired_overlay_range_pct=[min(r['overlay_extra_pct'] for r in ratios),max(r['overlay_extra_pct'] for r in ratios)]))
    return dict(passed=True, binary_sha256=run['binary_sha256'], source_commit=run['source_commit'],
                source_patch_sha256=run['source_patch_sha256'], cells=len(cells), scenarios=results,
                note='Small read-only hot-lower diagnostic; shared plan/fence/permission costs remain in lower-read.')


if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('run', type=Path)
    print(json.dumps(summarize(parser.parse_args().run), indent=2))
