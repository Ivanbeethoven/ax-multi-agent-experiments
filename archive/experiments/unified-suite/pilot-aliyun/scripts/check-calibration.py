#!/usr/bin/env python3
"""Check explicit calibration pairs offline; never runs or selects trials."""
import argparse
import json
from pathlib import Path
import sys

REPO = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(REPO))
from bench.runtime.common import digest, load, write_new
from bench.runtime.observe import overhead


def ref(path):
    path = Path(path).resolve(strict=True)
    return dict(path=str(path), sha256=digest(path))


def check(pairs, metric, workload, pool='pool-a'):
    if pool not in ('pool-a', 'pool-b'):
        raise ValueError('Explicit supported pool required')
    if len(pairs) < 3:
        raise ValueError('At least three explicitly listed pairs required')
    plan = dict(schema='experiment-observer-overhead-plan-v1', reviewed=True,
                metric_contract_sha256=digest(metric), workload_contract_sha256=digest(workload),
                max_relative_change=.05, pairs=[], pool=pool)
    details = []
    seen = set()
    for pair in pairs:
        row = {}
        for mode in ('on', 'off'):
            root = Path(pair[mode]).resolve(strict=True)
            if root in seen:
                raise ValueError('A trial cannot be reused in another pair')
            seen.add(root)
            finished = load(root/'finished.json')
            if finished['passed'] is not True:
                raise ValueError('Incomplete trial')
            cohort = root/'cohort/result.json'
            if finished['cohort'] != ref(cohort):
                raise ValueError('Changed cohort result')
            # Qualification requires both the measurement and its original
            # cold readback / normal exit, not just a successful fio command.
            for key in ('client_verify', 'native_exit', 'verify_exit'):
                evidence = finished[key]
                if digest(evidence['path']) != evidence['sha256']:
                    raise ValueError('Changed trial evidence')
                if load(evidence['path'])['passed'] is not True:
                    raise ValueError('Trial closure or verification failed')
            if finished.get('parent_verify'):
                evidence=finished['parent_verify']
                if digest(evidence['path'])!=evidence['sha256'] or load(evidence['path'])['passed'] is not True:
                    raise ValueError('Original parent verification changed or failed')
            original = load(root/'cohort.json')
            if any(w['node_id'] != pool+'-client' for w in original['workers']):
                raise ValueError('Calibration workers belong to another pool')
            expected = {pool+'-'+role for role in ('client', 'metadata', 'objects')}
            actual = {o['id'] for o in original.get('observers', [])}
            if actual != (expected if mode == 'on' else set()):
                raise ValueError('Calibration observer roles differ from the selected pool/mode')
            if mode == 'on':
                for role in ('client', 'metadata', 'objects'):
                    audit = root/'observer-stop'/(pool+'-'+role)/'raw-audit.json'
                    if load(audit)['passed'] is not True:
                        raise ValueError('Missing raw observer audit')
            row[mode] = ref(cohort)
            result = load(cohort)
            details.append(dict(mode=mode, root=str(root), finished=ref(root/'finished.json'),
                                makespan_seconds=result['coordinator_makespan_seconds'],
                                task_seconds=[j['seconds'] for j in result['jobs']]))
        plan['pairs'].append(row)
    return plan, details


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--pairs', type=Path, required=True, help='JSON array of explicit on/off trial directories')
    p.add_argument('--metric-contract', type=Path, required=True)
    p.add_argument('--workload-contract', type=Path, required=True)
    p.add_argument('--out', type=Path, required=True)
    p.add_argument('--pool', choices=('pool-a', 'pool-b'), default='pool-a')
    a = p.parse_args()
    plan, details = check(load(a.pairs), a.metric_contract, a.workload_contract, a.pool)
    a.out.mkdir(parents=True, exist_ok=False)
    write_new(a.out/'plan.json', plan)
    result = overhead(ref(a.out/'plan.json'))
    result.update(trial_details=details, pair_selection=ref(a.pairs),
                  limitation='Small pilot. Native built-in counters stay enabled in both arms. '
                  'This checks external collectors on the listed workload, not every workload or concurrency.')
    write_new(a.out/'result.json', result)
    print(json.dumps(dict(passed=result['passed'], relative_changes=result['relative_changes'],
                          paired_relative_changes=result['paired_relative_changes'])))
    return 0 if result['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
