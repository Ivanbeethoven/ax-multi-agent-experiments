"""Summarize paired workspace and flat reads using the same BrewFS build."""
import argparse
from collections import defaultdict
import json
from pathlib import Path
from statistics import median


ARMS = ('native', 'native-overlay')


def load(path):
    return json.loads(path.read_text(encoding='utf-8'))


def summarize(root):
    run = load(root / 'run.json')
    assert run['passed'] and run['cleanup']['mount_absent']
    assert run['cleanup'].get('redis_exit_code') == 0
    assert all(run['cleanup'][name] for name in run['containers'])
    cache, shared_binary = None, None
    trials = defaultdict(dict)
    for item in run['cells']:
        folder = root / item['label']
        value = load(folder / 'result.json')
        cfg = load(folder / 'config.json')
        arm = value['arm']
        assert arm in ARMS and arm not in trials[value['pair']]
        assert value['effective_kernel_ttl_checked_zero']
        assert cfg['volume_format'] == ('flat' if arm == 'native' else 'workspace')
        assert 'packed_manifest_key' not in cfg
        assert bool(cfg.get('workspace')) == (arm == 'native-overlay')
        settings = (cfg['fuse'], {k: v for k, v in cfg['cache'].items() if k != 'cache_root'}, cfg['layout'])
        if cache is None:
            cache = settings
            shared_binary = value['binary']
        assert settings == cache and value['binary'] == shared_binary
        assert all(not any(value[key].values()) for key in ('first_objects', 'steady_objects'))
        assert all(number > 0 for number in value['warmup_objects'].values())
        counters = defaultdict(int)
        for phase in ('first_redis_delta', 'steady_redis_delta'):
            for name, data in value[phase].items():
                if name != 'info':
                    counters[name] += int(data['calls'])
        stages = defaultdict(int)
        for row in value['samples']:
            assert len(row['pread']) == value['expected_files']
            assert sum(f['bytes'] for f in row['pread']) == value['expected_bytes']
            for name, part in row['breakdown_ns'].items():
                stages[name] += part
        if value['expected_files'] == 10000:
            pages = load(folder / 'directory-pages.json')
            assert pages['entries'] == 9999 and pages['user_getdents64_calls'] > 2
        trials[value['pair']][arm] = dict(
            elapsed_ms=value['elapsed_ns'] / 1e6, files=value['expected_files'],
            rounds=len(value['samples']), stages_ms={k: v / 1e6 for k, v in stages.items()},
            redis_commands=dict(counters), redis_calls=sum(counters.values()),
            warmup_objects=value['warmup_objects'], timed_objects=value['steady_objects'])
    assert len(trials) >= 3 and all(set(pair) == set(ARMS) for pair in trials.values())
    medians = {arm: median(p[arm]['elapsed_ms'] for p in trials.values()) for arm in ARMS}
    return dict(median_elapsed_ms=medians,
                overlay_overhead_pct=100 * (medians['native-overlay'] / medians['native'] - 1),
                pairs=[dict(pair=i, **p, overhead_pct=100 * (p['native-overlay']['elapsed_ms'] /
                                                           p['native']['elapsed_ms'] - 1))
                       for i, p in trials.items()])


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory', type=Path)
    print(json.dumps(summarize(parser.parse_args().directory), indent=2))
