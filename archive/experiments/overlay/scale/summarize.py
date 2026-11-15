"""Matched hot Overlay comparisons. Positive overhead always means slower."""
import argparse
import importlib.util
import json
from pathlib import Path
import statistics


def overhead(numerator, denominator):
    assert numerator >= 0 and denominator > 0
    return 100 * (numerator / denominator - 1)


def workspace_counters(values):
    return {k: v for k, v in values.items()
            if k.startswith('brewfs_workspace_') and k.endswith('_total')}


def summarize(root):
    root = Path(root)
    path = Path(__file__).resolve().parents[1] / 'overlay-total-a355-20261113/summarize.py'
    spec = importlib.util.spec_from_file_location('total_audit', path)
    audit = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(audit)
    audited = audit.summarize(root)
    run = json.loads((root / 'run.json').read_text())
    assert run['schema'] == 'overlay-version-total-v1'
    settings = run['total_settings']
    assert settings['temperatures'] == 'warm'
    cells = [json.loads((root / c['label'] / 'result.json').read_text()) for c in run['cells']]
    result = []
    for scenario in sorted({c['scenario'] for c in cells}):
        trials = []
        for c in (c for c in cells if c['scenario'] == scenario):
            assert c['cache_temperature'] == 'warm'
            samples = c['samples']
            for s in samples:
                assert sum(s['breakdown_ns'].values()) == s['round_elapsed_ns']
            parts = {k: sum(s['breakdown_ns'][k] for s in samples) / 1e6
                     for k in samples[0]['breakdown_ns']}
            parts['between_rounds'] = (c['elapsed_ns'] - sum(s['round_elapsed_ns'] for s in samples)) / 1e6
            assert parts['between_rounds'] >= 0
            redis = {k: v['calls'] for k, v in c['redis_delta'].items() if k != 'info'}
            counters = workspace_counters(c['stats_delta'])
            retained = {}
            for phase in ('before', 'after'):
                gauges = dict(line.split() for line in (root / c['label'] / (phase + '.stats')).read_text().splitlines()
                              if line.startswith('brewfs_workspace_row_cache_retained_bytes '))
                retained[phase] = float(gauges.get('brewfs_workspace_row_cache_retained_bytes', 0))
            wire = json.loads((root / c['label'] / 'redis-wire-audit.json').read_text())
            assert wire['passed'] and wire['monitor_closed'] and not wire['timed']
            if settings['fixture'] == 'many10000':
                assert len(c['expected_sha256']) == 10000
                assert sum(r['bytes'] for r in samples[0]['pread']) == 40960000
                proof = json.loads((root / c['label'] / 'directory-pages.json').read_text())
                assert proof['entries'] == 9999 and proof['user_getdents64_calls'] > 2
                assert proof['content_verified'] and not proof['timed']
            trials.append(dict(pair=c['pair'], arm=c['arm'], label=c['label'],
                elapsed_ms=c['elapsed_ns'] / 1e6, stages_ms=parts,
                round_elapsed_ms=[s['round_elapsed_ns'] / 1e6 for s in samples],
                warmup_ms=c['warmup_elapsed_ns'] / 1e6,
                row_cache_retained_bytes=retained,
                first_workspace_counters=workspace_counters(c['first_stats_delta']),
                steady_workspace_counters=workspace_counters(c['steady_stats_delta']),
                redis_executed_commands=sum(redis.values()), redis_commands=redis,
                untimed_single_pass_wire_audit=wire,
                workspace_counters=counters, first_objects=c['first_objects'], steady_objects=c['steady_objects']))
        values = {a: [t for t in trials if t['arm'] == a]
                  for a in ('production', 'candidate', 'packed-direct')}
        medians = {a: statistics.median(t['elapsed_ms'] for t in rows) for a, rows in values.items()}
        pairs = []
        for pair in sorted({t['pair'] for t in trials}):
            times = {t['arm']: t['elapsed_ms'] for t in trials if t['pair'] == pair}
            pairs.append(dict(pair=pair, elapsed_ms=times,
                candidate_vs_baseline_pct=overhead(times['candidate'], times['production']),
                baseline_vs_lower_pct=overhead(times['production'], times['packed-direct']),
                candidate_vs_lower_pct=overhead(times['candidate'], times['packed-direct'])))
        result.append(dict(scenario=scenario, median_elapsed_ms=medians,
            candidate_vs_baseline_pct=overhead(medians['candidate'], medians['production']),
            baseline_vs_lower_pct=overhead(medians['production'], medians['packed-direct']),
            candidate_vs_lower_pct=overhead(medians['candidate'], medians['packed-direct']),
            mean_stages_ms={a: {k: statistics.mean(t['stages_ms'][k] for t in rows)
                                  for k in rows[0]['stages_ms']} for a, rows in values.items()},
            pairs=pairs, trials=trials))
    return dict(schema='overlay-hot-scale-v1', passed=audited['passed'],
        definition='100 * (numerator / denominator - 1); positive is slower',
        timing='all timed rounds; excludes warmup and inter-phase stats collection',
        redis_scope='actual server commandstats, including Lua-internal commands; not network round trips',
        settings=settings, source_commit=run['source_commit'],
        baseline_binary_sha256=run['binary_sha256'], candidate_binary_sha256=run['candidate_binary_sha256'],
        baseline_patch_sha256=run['source_patch_sha256'], candidate_patch_sha256=run['candidate_source_patch_sha256'],
        groups=result)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory', type=Path)
    print(json.dumps(summarize(parser.parse_args().directory), indent=2))
