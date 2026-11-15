#!/usr/bin/env python3
"""Additive mean syscall decomposition; preserves original per-round samples."""
import argparse
import json
from pathlib import Path
import statistics


def summarize(root):
    cells = [json.loads(path.read_text()) for path in sorted(root.glob('*/result.json'))]
    assert cells, 'no completed cells'
    result = []
    groups = sorted({(c['scenario'], c['arm'], c['cache_temperature']) for c in cells})
    for scenario, arm, temperature in groups:
        group = [c for c in cells if (c['scenario'], c['arm'], c['cache_temperature']) ==
                 (scenario, arm, temperature)]
        runs = []
        for cell in group:
            samples = cell['samples'][1:]
            assert len(samples) == cell['steady_rounds']
            for sample in samples:
                assert sum(sample['breakdown_ns'].values()) == sample['round_elapsed_ns']
            components = {key: statistics.mean(s['breakdown_ns'][key] for s in samples) / 1e6
                          for key in samples[0]['breakdown_ns']}
            components['between_rounds'] = (cell['steady_elapsed_ns'] -
                sum(s['round_elapsed_ns'] for s in samples)) / len(samples) / 1e6
            assert components['between_rounds'] >= 0
            runs.append(dict(label=cell['label'], pair=cell['pair'],
                             round_ms=cell['steady_elapsed_ns'] / len(samples) / 1e6,
                             components_ms=components))
        result.append(dict(scenario=scenario, arm=arm, temperature=temperature,
                           pairs=len(runs), mean_round_ms=statistics.mean(r['round_ms'] for r in runs),
                           mean_components_ms={k: statistics.mean(r['components_ms'][k] for r in runs)
                                               for k in runs[0]['components_ms']}, runs=runs))
    return dict(schema='overlay-open-breakdown-v1', aggregation='mean of per-run means', groups=result)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('directory', type=Path)
    args = parser.parse_args()
    print(json.dumps(summarize(args.directory), indent=2))
