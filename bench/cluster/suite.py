"""Export the approved four-group workload and raw-result directory layout."""

import argparse
import copy
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def build(suite):
    jobs = []
    for group, definition in suite['groups'].items():
        configurations = definition['configurations']
        if len(configurations) * suite['repetitions'] != definition['runs']:
            raise ValueError(f'Run count does not match configurations: {group}')
        for repeat, seed in enumerate(suite['seeds'], 1):
            for index, configuration in enumerate(configurations, 1):
                parameters = {key: copy.deepcopy(value) for key, value in definition.items()
                              if key not in ('configurations', 'runs')}
                parameters.update(copy.deepcopy(configuration))
                identifier = f'{group}-{index:02d}-r{repeat}'
                jobs.append({'id': identifier, 'group': group, 'repeat': repeat, 'seed': seed,
                             'configuration': index, 'parameters': parameters,
                             'output': f'{group}/{index:02d}/r{repeat}', 'state': 'planned'})
    return {'schema': 'ax-four-group-plan-v1', 'run_id': suite['run_id'],
            'resource_limits': suite['resource_limits'], 'jobs': jobs,
            'counts': {group: value['runs'] for group, value in suite['groups'].items()},
            'total_runs': len(jobs)}


def export(plan, directory):
    directory = directory.resolve()
    if (directory / "plan.json").exists():
        raise FileExistsError("Plan already exists: " + str(directory))
    directory.mkdir(parents=True, exist_ok=True)
    for name in ('hosts', 'config', 'versions', 'logs'):
        (directory / name).mkdir(exist_ok=True)
    for job in plan['jobs']:
        out = directory / job['output']
        out.mkdir(parents=True)
        (out / 'job.json').write_text(json.dumps(job, indent=2) + '\n', encoding='utf-8')
    (directory / 'plan.json').write_text(json.dumps(plan, indent=2) + '\n', encoding='utf-8')
    (directory / 'status.json').write_text(json.dumps({
        'run_id': plan['run_id'], 'completed_runs': 0, 'total_runs': plan['total_runs'],
        'jobs': {job['id']: {'state': 'planned'} for job in plan['jobs']}}, indent=2) + '\n', encoding='utf-8')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, default=ROOT / 'configs/experiment-suite.json')
    parser.add_argument('--out', type=Path)
    options = parser.parse_args()
    suite = json.loads(options.config.read_text(encoding='utf-8'))
    plan = build(suite)
    if options.out:
        export(plan, options.out)
    print(json.dumps({'groups': plan['counts'], 'total_runs': plan['total_runs']}))


if __name__ == '__main__':
    main()
