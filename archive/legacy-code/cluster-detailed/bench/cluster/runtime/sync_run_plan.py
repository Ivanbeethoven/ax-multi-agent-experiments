"""Refresh a stopped run's plans and progress, retaining its raw results."""

import argparse
import copy
import datetime
import json
from pathlib import Path
import shutil
import sys


def reconcile(status, plans):
    result = copy.deepcopy(status)
    for group, plan in plans.items():
        record = result['groups'][group]
        previous = record['conditions']
        conditions = {}
        for condition in plan['conditions']:
            cid = condition['id']
            item = copy.deepcopy(previous.get(cid, {}))
            item.update(systems=condition['systems'], repetitions=condition['repetitions'])
            expected = [f'{system}/r{repeat}' for system in condition['systems']
                        for repeat in range(1, condition['repetitions'] + 1)]
            item['runs'] = {key: value for key, value in item.get('runs', {}).items() if key in expected}
            for run in item['runs'].values():
                if run.get('state') == 'running':
                    run['state'] = 'interrupted'
            states = [item['runs'].get(key, {}).get('state', 'planned') for key in expected]
            item['state'] = ('passed' if all(state == 'passed' for state in states)
                             else 'pending-feature' if all(state == 'pending-feature' for state in states)
                             else 'failed' if 'failed' in states
                             else 'incomplete' if any(state != 'planned' for state in states)
                             else 'planned')
            conditions[cid] = item
        record['conditions'] = conditions
        record['total_conditions'] = len(conditions)
        record['total_system_runs'] = plan['counts']['system_runs']
        record['completed_conditions'] = sum(item['state'] == 'passed' for item in conditions.values())
        record['pending_feature_conditions'] = sum(item['state'] == 'pending-feature' for item in conditions.values())
        completed = sum(run.get('state') == 'passed' for item in conditions.values() for run in item['runs'].values())
        record['completed_runs'] = record['completed_system_runs'] = completed
    result['totals'] = {
        name: sum(group.get(source, 0) for group in result['groups'].values())
        for name, source in [('conditions', 'total_conditions'), ('system_runs', 'total_system_runs'),
                             ('completed_conditions', 'completed_conditions'), ('completed_runs', 'completed_system_runs')]
    }
    result['phase'] = 'paused'
    result['updated_at'] = datetime.datetime.now(datetime.timezone.utc).isoformat()
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo-root', type=Path, required=True)
    parser.add_argument('--run-root', type=Path, required=True)
    parser.add_argument('--check-only', action='store_true')
    options = parser.parse_args()
    sys.path.insert(0, str(options.repo_root.resolve()))
    from bench.cluster.plan import build_plan

    campaign = json.loads((options.repo_root / 'configs/campaign-direct.json').read_text(encoding='utf-8'))
    design = json.loads((options.repo_root / 'configs/cluster.json').read_text(encoding='utf-8'))
    plans = {group: build_plan(design, group) for group in campaign['groups']}
    for group, plan in plans.items():
        for field, count in campaign['expected_counts'][group].items():
            if plan['counts'][field] != count:
                raise ValueError(f'{group}: unexpected {field}')
    state_path = options.run_root / 'status.json'
    current = json.loads(state_path.read_text(encoding='utf-8'))
    updated = reconcile(current, plans)
    if not options.check_only:
        history = options.run_root / 'plan-history'
        revision = 1
        while (history / f'revision-{revision:03d}').exists():
            revision += 1
        backup = history / f'revision-{revision:03d}'
        backup.mkdir(parents=True)
        shutil.copy2(state_path, backup / 'status.json')
        for group, plan in plans.items():
            path = options.run_root / 'plan' / f'{group}.json'
            if path.exists():
                shutil.copy2(path, backup / path.name)
            temporary = path.with_suffix('.json.tmp')
            temporary.write_text(json.dumps(plan, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
            temporary.replace(path)
        temporary = state_path.with_suffix('.json.tmp')
        temporary.write_text(json.dumps(updated, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        temporary.replace(state_path)
    print(json.dumps(updated['totals']))


if __name__ == '__main__':
    main()
