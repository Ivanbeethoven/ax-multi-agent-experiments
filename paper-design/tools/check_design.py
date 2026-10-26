#!/usr/bin/env python3
"""Validate and optionally expand the local experiment plan; never execute workloads."""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import itertools
import json
from pathlib import Path
import random
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
FIELDS = ['run_id', 'pair_id', 'group_id', 'experiment_id', 'rq_ids', 'tier',
          'configuration', 'repeat', 'order_in_pair', 'replicate_kind',
          'parameters_json', 'protocol', 'status', 'execution_enabled']


def read_json(name):
    return json.loads((ROOT / name).read_text(encoding='utf-8'),
                      parse_constant=lambda x: (_ for _ in ()).throw(ValueError(x)))


def expand(design, grid):
    rqs = {e['id']: '|'.join(e['rqs']) for e in design['experiments']}
    rng = random.Random(grid['seed'])
    rows = []
    for group in grid['groups']:
        keys = list(group['axes'])
        products = itertools.product(*(group['axes'][k] for k in keys))
        for cell, values in enumerate(products, 1):
            parameters = dict(group['fixed']) | dict(zip(keys, values))
            for repeat in range(1, group['repeats'] + 1):
                pair_id = f"{group['id']}-c{cell:03d}-r{repeat:02d}"
                configs = list(group['configurations'])
                rng.shuffle(configs)
                for order, config in enumerate(configs, 1):
                    rows.append(dict(zip(FIELDS, [
                        f'{pair_id}-{config}', pair_id, group['id'],
                        group['experiment_id'], rqs[group['experiment_id']],
                        group['tier'], config, repeat, order, group['replicate_kind'],
                        json.dumps(parameters, ensure_ascii=False, sort_keys=True, separators=(',', ':')),
                        group['protocol'], 'planned', 'false'
                    ])))
    return rows, csv_bytes(rows)


def csv_bytes(rows):
    stream = io.StringIO(newline='')
    writer = csv.DictWriter(stream, fieldnames=FIELDS, lineterminator='\n')
    writer.writeheader()
    writer.writerows(rows)
    return stream.getvalue().encode('utf-8')


def first_pass(rows, grid):
    spec = grid['first_pass']
    selected = []
    for choice in spec['selections']:
        matches = []
        for row in rows:
            if row['group_id'] != choice['group_id'] or row['repeat'] > spec['repeat_max']:
                continue
            parameters = json.loads(row['parameters_json'])
            if all(parameters.get(k) in values for k, values in choice['where'].items()):
                matches.append(row)
        if not matches:
            raise ValueError(f"First-pass selection matches no rows: {choice['group_id']}")
        for row in matches:
            item = dict(row)
            for field in ['run_id', 'pair_id']:
                item[field] = spec['run_id_prefix'] + item[field]
            item['tier'] = 'first_pass'
            selected.append(item)
    if len(selected) != spec['expected_rows'] or len({r['run_id'] for r in selected}) != len(selected):
        raise ValueError('First-pass count or uniqueness mismatch')
    if any(r['experiment_id'] == 'agent_validation' for r in selected) or spec['model_calls'] is not False:
        raise ValueError('First pass must not include model calls')
    return selected, csv_bytes(selected)


def validate(design, grid):
    errors = []
    for label, data in [('design', design), ('grid', grid)]:
        if data.get('status') != 'planned' or data.get('execution_enabled') is not False:
            errors.append(f'{label}: must remain planned with execution disabled')
    expected_rqs = {f'RQ{i}' for i in range(1, 6)}
    if {q['id'] for q in design['research_questions']} != expected_rqs:
        errors.append('Exactly RQ1 through RQ5 required')
    experiments = {e['id'] for e in design['experiments']}
    if len(experiments) != 11 or len(experiments) != len(design['experiments']):
        errors.append('Expected 11 unique experiments')
    seen = set()
    for group in grid['groups']:
        gid = group['id']
        if gid in seen or not re.fullmatch(r'[a-z][a-z0-9_]*', gid):
            errors.append(f'Invalid or duplicate group: {gid}')
        seen.add(gid)
        if group['experiment_id'] not in experiments:
            errors.append(f'{gid}: unknown experiment')
        if group['tier'] not in grid['tiers']:
            errors.append(f'{gid}: unknown tier')
        configs = group['configurations']
        if not configs or len(configs) != len(set(configs)) or not set(configs) <= set(grid['configurations']):
            errors.append(f'{gid}: invalid configurations')
        if not isinstance(group['repeats'], int) or group['repeats'] < 1:
            errors.append(f'{gid}: invalid repeat count')
        if set(group['axes']) & set(group['fixed']):
            errors.append(f'{gid}: axis overwrites fixed parameter')
        for key, values in group['axes'].items():
            if not isinstance(values, list) or not values:
                errors.append(f'{gid}: empty or non-list axis {key}')
            elif len({json.dumps(v, sort_keys=True) for v in values}) != len(values):
                errors.append(f'{gid}: duplicate axis values for {key}')
        path = (ROOT / group['protocol']).resolve()
        if not path.is_relative_to(ROOT) or not path.is_file():
            errors.append(f'{gid}: missing or out-of-scope protocol')
        if group['status'] != 'planned' or group['execution_enabled'] is not False:
            errors.append(f'{gid}: execution must be disabled')
        if not group['required_gate'].strip():
            errors.append(f'{gid}: missing capability gate')
    if {g['experiment_id'] for g in grid['groups']} != experiments:
        errors.append('Detailed groups do not cover all experiments')

    archives = read_json('trace-archives.json')['archives']
    if len(archives) != 7 or {a['case'] for a in archives} != set(design['inputs']['case_order']):
        errors.append('Expected seven frozen trace archives')
    for a in archives:
        if not re.fullmatch(r'[0-9a-f]{64}', a['sha256']) or a['bytes'] <= 0:
            errors.append(f"Bad archive identity: {a['case']}")
    if {f['id'] for f in read_json('fixtures.json')['fixtures']} != {'F1', 'F2', 'M'}:
        errors.append('Missing fixture definitions')
    live = {t['instance_id'] for t in read_json('live-tasks.json')['tasks']}
    for g in grid['groups']:
        if g['experiment_id'] == 'agent_validation' and not set(g['axes']['task']) <= live:
            errors.append(f"{g['id']}: missing frozen live task")

    by_id = {g['id']: g for g in grid['groups']}
    replay = by_id['replay_core']['fixed']
    if replay['tasks_per_batch'] != replay['cases'] * replay['copies_per_case']:
        errors.append('Replay assignment count mismatch')
    if replay['common_observation_deadline_s'] != replay['tasks_per_batch'] * replay['case_total_deadline_s'] + 300:
        errors.append('Replay common deadline mismatch')
    reuse = by_id['p_reuse']['fixed']
    if reuse['batch_deadline_s'] != reuse['prepare_deadline_s'] + max(reuse['reuse_prefixes']) * reuse['session_deadline_s'] + reuse['drain_s']:
        errors.append('Packed reuse deadline arithmetic mismatch')
    for group_id in ['scale_core', 'scale_large', 'scale_ablation']:
        p = by_id[group_id]['fixed']
        waves = (p['cases_per_worker'] + p['slots_per_worker'] - 1) // p['slots_per_worker']
        if p['batch_deadline_s'] != waves * p['case_total_deadline_s'] + 300:
            errors.append(f'{group_id}: case deadline arithmetic mismatch')
    template = read_json('run-record.template.json')
    if template['status'] != 'planned' or template['measurements'] is not None or template['outcome'] is not None:
        errors.append('Run template contains results or is not planned')
    if template['execution_enabled'] is not False or template['raw_artifacts']:
        errors.append('Run template must remain unexecuted')
    for required in ['README.md', 'reference/details.zh.md']:
        if not (ROOT / required).is_file():
            errors.append(f'Missing {required}')
    return errors


def summary(rows):
    groups = {}
    for row in rows:
        group = groups.setdefault(row['group_id'], {'experiment_id': row['experiment_id'],
            'tier': row['tier'], 'replicate_kind': row['replicate_kind'], 'planned_runs': 0,
            'replay_assignments': 0, 'agent_parent_runs': 0, 'agent_fork_children': 0})
        group['planned_runs'] += 1
        p = json.loads(row['parameters_json'])
        if row['experiment_id'] == 'replay':
            group['replay_assignments'] += p['tasks_per_batch']
        if row['experiment_id'] == 'scale':
            group['replay_assignments'] += p['workers'] * p['cases_per_worker']
        if row['experiment_id'] == 'agent_validation':
            group['agent_parent_runs'] += 1
            if p['condition'] == 'fork4':
                group['agent_fork_children'] += p['fork_children']
    by_tier = {tier: sum(g['planned_runs'] for g in groups.values() if g['tier'] == tier)
               for tier in ['smoke', 'core', 'extension', 'conditional']}
    return {'planned_rows_by_tier': by_tier, 'groups': groups}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--write-grid', action='store_true', help='write planned CSV and validation report only')
    args = parser.parse_args()
    try:
        design, grid = read_json('design.json'), read_json('execution-grid.json')
        errors = validate(design, grid)
        if errors:
            raise ValueError('; '.join(errors))
        rows, payload = expand(design, grid)
        starter_rows, starter_payload = first_pass(rows, grid)
        if len({r['run_id'] for r in rows}) != len(rows):
            raise ValueError('Duplicate run IDs')
        if args.write_grid:
            (ROOT / 'run-grid.csv').write_bytes(payload)
            (ROOT / 'first-pass.csv').write_bytes(starter_payload)
        elif not (ROOT / 'run-grid.csv').is_file() or (ROOT / 'run-grid.csv').read_bytes() != payload:
            raise ValueError('run-grid.csv is missing or stale; regenerate with --write-grid')
        elif not (ROOT / 'first-pass.csv').is_file() or (ROOT / 'first-pass.csv').read_bytes() != starter_payload:
            raise ValueError('first-pass.csv is missing or stale; regenerate with --write-grid')
        report = {'passed': True, 'scope': 'design consistency only; no runtime qualification or experiments',
            'execution_enabled': False, 'group_count': len(grid['groups']), 'experiments': len(design['experiments']),
            'first_pass_rows': len(starter_rows), 'first_pass_sha256': hashlib.sha256(starter_payload).hexdigest(),
            'planned_rows': len(rows), 'run_grid_sha256': hashlib.sha256(payload).hexdigest(),
            **summary(rows)}
        if args.write_grid:
            output = ROOT / 'validation'
            output.mkdir(exist_ok=True)
            (output / 'detailed-plan-check.json').write_text(json.dumps(report, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
        concise = {k:v for k,v in report.items() if k != 'groups'}
        concise['groups'] = len(grid['groups'])
        print(json.dumps(concise, ensure_ascii=False, indent=2))
        return 0
    except (OSError, KeyError, TypeError, ValueError) as exc:
        print(json.dumps({'passed': False, 'error': str(exc)}, ensure_ascii=False))
        return 1


if __name__ == '__main__':
    sys.exit(main())
