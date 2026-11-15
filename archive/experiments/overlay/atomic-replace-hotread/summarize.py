"""Validate and summarize matched trials; keep raw results unmodified."""
import argparse
from collections import defaultdict
import json
from pathlib import Path
from statistics import median


ARMS = ('production', 'candidate', 'packed-direct')


def read(path):
    return json.loads(path.read_text(encoding='utf-8'))


def differences(times):
    before, after, lower = (times[a] for a in ARMS)
    return dict(after_vs_before_pct=100 * (after / before - 1),
                before_vs_lower_pct=100 * (before / lower - 1),
                after_vs_lower_pct=100 * (after / lower - 1))


def summarize(root):
    run = read(root / 'run.json')
    assert run['passed'] and run['cleanup']['mount_absent']
    assert all(run['cleanup'][name] for name in run['containers'])
    assert run['cleanup'].get('redis_exit_code', 0) == 0
    assert run['total_settings']['lower_build'] == 'candidate'
    trials = []
    comparisons = defaultdict(dict)
    manifests = set()
    cache_configs = []
    for item in run['cells']:
        directory = root / item['label']
        raw = read(directory / 'result.json')
        assert raw['cache_temperature'] == 'warm' and raw['effective_kernel_ttl_checked_zero']
        assert not any(raw[k][f] for k in ('first_objects', 'steady_objects')
                       for f in ('read_requests', 'body_bytes'))
        assert raw['warmup_objects']['read_requests'] > 0
        assert raw['warmup_objects']['body_bytes'] > 0
        count = raw['expected_files']
        if count == 10000:
            pages = read(directory / 'directory-pages.json')
            assert pages['entries'] == 9999 and pages['user_getdents64_calls'] > 2
        else:
            pages = None
            assert count == 2
        cfg = read(directory / 'config.json')
        cache_configs.append(({k: v for k, v in cfg['cache'].items() if k != 'cache_root'}, cfg['fuse']))
        assert cfg['volume_format'] == ('packed-snapshot' if raw['arm'] == 'packed-direct' else 'workspace')
        stages = defaultdict(int)
        for sample in raw['samples']:
            assert len(sample['pread']) == count
            assert sum(row['bytes'] for row in sample['pread']) == raw['expected_bytes']
            assert sum(sample['breakdown_ns'].values()) == sample['round_elapsed_ns']
            for k, ns in sample['breakdown_ns'].items():
                stages[k] += ns
        assert sum(stages.values()) <= raw['elapsed_ns']
        commands = defaultdict(int)
        for phase in ('first_redis_delta', 'steady_redis_delta'):
            for command, values in raw[phase].items():
                if command != 'info' and values['calls']:
                    commands[command] += int(values['calls'])
        assert all(c > 0 for c in commands.values())
        if raw['arm'] == 'packed-direct':
            assert not commands and raw['write_open_rejected']
        workspace = {k: int(v) for k, v in raw['stats_delta'].items()
                     if k.startswith(('brewfs_workspace_', 'brewfs_meta_open_')) and k.endswith('_total')}
        assert all(v >= 0 for v in workspace.values())
        trial = dict(pair=raw['pair'], arm=raw['arm'], elapsed_ms=raw['elapsed_ns'] / 1e6,
                     files=count, bytes_per_round=raw['expected_bytes'], rounds=len(raw['samples']),
                     stages_ms={k: v / 1e6 for k, v in stages.items()},
                     redis_commands=dict(commands), redis_calls=sum(commands.values()), workspace=workspace,
                     first_objects=raw['first_objects'], steady_objects=raw['steady_objects'],
                     warmup_objects=raw['warmup_objects'], directory_pages=pages)
        arm, pair = trial['arm'], trial['pair']
        assert arm not in comparisons[pair]
        comparisons[pair][arm] = trial['elapsed_ms']
        manifests.add(raw['manifest_key'])
        trials.append(trial)
    assert len(manifests) == 1 and len(comparisons) >= 3
    assert all(config == cache_configs[0] for config in cache_configs)
    assert all(set(times) == set(ARMS) for times in comparisons.values())
    assert len({(t['files'], t['bytes_per_round'], t['rounds']) for t in trials}) == 1
    by_arm = {a: [t for t in trials if t['arm'] == a] for a in ARMS}
    times = {a: median(t['elapsed_ms'] for t in rows) for a, rows in by_arm.items()}
    stage_names = ('file_open', 'directory_open', 'directory_list', 'file_pread')
    return dict(settings=run['total_settings'], files=trials[0]['files'], rounds=trials[0]['rounds'],
                trials=trials, pairs=[dict(pair=k, elapsed_ms=v, **differences(v)) for k, v in comparisons.items()],
                median_elapsed_ms=times, **differences(times),
                median_stages_ms={a: {k: median(t['stages_ms'][k] for t in rows) for k in stage_names}
                                  for a, rows in by_arm.items()},
                median_redis_calls={a: median(t['redis_calls'] for t in rows) for a, rows in by_arm.items()})


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory', type=Path)
    print(json.dumps(summarize(parser.parse_args().directory), indent=2))
