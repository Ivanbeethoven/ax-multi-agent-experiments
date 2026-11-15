#!/usr/bin/env python3
"""Bind five old c123 C1 calibration cohorts; print a draft, never retain or SSH."""
import argparse
import hashlib
import json
from pathlib import Path
import runpy
import sqlite3
import sys

sys.dont_write_bytecode = True
CONTROL = Path('/srv/ax-pilot-control/c123-control-01')
PLAN_SHA = '81e604de204acec9fa250fd0b6330c7ac052e7b372439a37ce5180026c22c7c1'
PLAN_ID = 'f1b16c09ede20a8c43ace160002dec635e494632850cf52061495d62c54e790e'
RETAINER_SHA = '72df6fa18c18908635c02d9287bba66e0df530851ef933405d7c7cfb696b688c'
OLD = {
    '34d56abaf85454a2-r1-juicefs': ('fio-read', 'cal-j-rd',
        'bce745e859025f4375850243972c252272071cccf9fffca9c21f94c21f63f8a2',
        'fb5700fa42330a3374e40746058f580b88183edf8e33633c2eeb6976828d2110'),
    '34d56abaf85454a2-r1-native': ('fio-read', 'cal-n-rd',
        '1866678e55eea004a0f9ce5041b28fc005f5d5105cc033e8b4ae56bc5d7420fd',
        '6a634327cd24db69e216e151841fc1e34df603f25d71615acb9be4a879e0a455'),
    '5ca7353ba24b2a18-r1-juicefs': ('fio-write', 'cal-j-wr-q3',
        'ac1fd4b03e69a33cde34f554e3d86cde58a2b0d62a9ab73a9bbfc4f8b77dd165',
        '60b314f670f6fa206d782fff488bc96ed65d11529995022fb7f5a71c531b7249'),
    '5ca7353ba24b2a18-r1-native': ('fio-write', 'cal-n-wr',
        '4ae79387b82129633c617662515b744832be54bc7c30dd4c3ef25d8d598b9e83',
        '23fc04001e4b11d79c908f1bfcb1dd6fee29f5374689560f36465531a290b748'),
    '451ef1d28b5d875c-r1-juicefs': ('fio-randread', None,
        '412a1a71f4f94cd66a27f2d00c5bab6e1e4b11475dc72057486cb37b4a10424d',
        'f4590624662271c4e5475fbb1f4344d530f8e948d9274cc2af68765d6029fc1c'),
}
LEGACY_DIRS = {
    'off': (('juicefs/off-01', 'cal-j-o1'), ('juicefs/off-02', 'cal-j-o2'), ('juicefs/off-03', 'cal-j-o3')),
    'on': (('juicefs/on-01', 'cal-j-i1'), ('juicefs/on-02', 'cal-j-i2'), ('juicefs/on-03', 'cal-j-i3')),
}
CONTRACTS = ('inventory', 'metric_contract', 'workload_contract', 'allocation_qualification',
             'hardware_qualification', 'input_qualification', 'filesystem_qualification')


def require(value, label):
    if not value:
        raise ValueError(label)


def read(path):
    return json.loads(path.read_bytes())


def ref(path):
    require(path.is_absolute() and path.resolve(strict=True) == path and path.is_file(), f'Unpinnable file: {path}')
    with path.open('rb') as stream:
        return {'path': str(path), 'sha256': hashlib.file_digest(stream, 'sha256').hexdigest()}


def pin(value, expected):
    require(ref(expected) == value, f'Changed or misplaced original receipt: {expected}')
    return expected


def trial_route(prefix, mode, number):
    if prefix is None:
        return LEGACY_DIRS[mode][number - 1]
    tag = f'{prefix}-{mode}{number}'
    return tag, tag


def original_overhead(control, cell_id, prefix):
    if prefix is None:
        return control/'calibration/juicefs-summary-01/result.json'
    run_name = cell_id + ('-q3' if prefix == 'cal-j-wr-q3' else '')
    return control/'one-cell-runs'/run_name/'overhead/result.json'


def role_config(retainer, cell, tag, mode, cohort):
    roots = retainer['ROOTS']
    pool = cell['pool']
    workers = cohort['workers']
    require(len(workers) == 1, 'Expected exactly one original worker')
    worker = workers[0]
    require(worker['node_id'] == 'pool-a-client' and worker['output'] == f"{roots['client']}/j/{tag}" and
            worker['spec']['path'] == f"{roots['client']}/w/{tag}/worker.reviewed.json", 'Original worker tag mismatch')
    observers = retainer['observer_roles'](cohort, pool)
    require(set(observers) == (set(roots) if mode == 'on' else set()), 'Original on/off observer role mismatch')
    configs = {}
    for role in (list(roots) if mode == 'on' else ['client']):
        observer = observers.get(role)
        if observer:
            require(observer['directory'] == f'{roots[role]}/o/{tag}' and
                    observer['spec']['path'] == f'{roots[role]}/q/{tag}-observer/observer.json',
                    'Original observer tag mismatch')
        peer = worker['peer'] if role == 'client' else observer['peer']
        require(peer['transport'] == 'ssh' and peer['alias'] == f'{pool}-{role}' and peer['pins'],
                'Original peer binding mismatch')
        configs[role] = dict(code=peer['repo'], pool=pool, root=roots[role], role=role, tag=tag, mode=mode,
            bundle='c123-cal-recovery-'+cell['id'], cell_id=cell['id'], marks=cell['observation_marks'],
            code_pins=peer['pins'], worker_spec=worker['spec'] if role == 'client' else None,
            observer_sha256=observer['spec']['sha256'] if observer else None)
    return configs


def trial_draft(control, retainer, cell, prefix, mode, number, pair_ref, details, overhead):
    dirname, tag = trial_route(prefix, mode, number)
    relative = 'calibration/'+dirname
    directory = control/relative
    result_path = pin(pair_ref, directory/'cohort/result.json')
    detail = details[str(directory)]
    require(detail['mode'] == mode, 'Calibration trial mode mismatch')
    finish_path = pin(detail['finished'], directory/'finished.json')
    finish = read(finish_path)
    require(finish['passed'] is True and finish['cohort'] == pair_ref, 'Original calibration did not close')
    for key, name in {'client_verify':'client/verify/result.json', 'native_exit':'client/exit.json',
                      'verify_exit':'client/verify-exit.json', 'collector':'collector.json'}.items():
        pin(finish[key], directory/name)
    for name in ('exit.json', 'verify-exit.json'):
        value = read(directory/'client'/name)
        require(value['passed'] is True and value['exit_code'] == 0 and value['mount_absent'] is True,
                'Original client exit is not clean')
    require(read(directory/'client/verify/result.json')['passed'] is True, 'Original verification failed')
    started = read(directory/'started.json')
    product = 'juicefs' if cell['id'].endswith('-juicefs') else 'brewfs'
    require(started['cell_id'] == cell['id'] and started['product'] == product and
            started['purpose'] == f'observer-calibration-{mode}' and started['automatic_retry'] is False,
            'Foreign calibration trial')
    cohort_path = directory/'cohort.json'
    cohort = read(cohort_path)
    require(ref(cohort_path)['sha256'] == ref(directory/'cohort/spec.json')['sha256'], 'Original cohort spec drift')
    result = read(result_path)
    for key in ('metric_contract', 'workload_contract'):
        require(result[key+'_sha256'] == cohort[key]['sha256'] == overhead[key+'_sha256'], 'Changed trial contract')
    require(result['passed'] is True and result['purpose'] == cohort['purpose'] == started['purpose'] and
            cohort['reviewed'] is True, 'Original cohort not accepted')
    contracts = {key: cohort[key] for key in CONTRACTS}
    for entry in contracts.values():
        path = Path(entry['path'])
        require(path.is_relative_to(control), 'Expected coordinator-local original contract')
        pin(entry, path)
    roles = role_config(retainer, cell, tag, mode, cohort)
    audits = {}
    if mode == 'on':
        for role in roles:
            path = directory/'observer-stop'/f'pool-a-{role}'/'raw-audit.json'
            original = read(path)
            require(original['passed'] is True and original['samples'] > 0 and
                    original['marks'] == cell['observation_marks'] and
                    original['metric_contract_sha256'] == result['metric_contract_sha256'], 'Invalid original raw audit')
            audits[role] = ref(path)
    collector = read(directory/'collector.json')
    require(collector['enabled'] is (mode == 'on') and
            Path(collector['out']).parent == Path(retainer['ROOTS']['objects'])/'q', 'Foreign original collector')
    # The old launcher sometimes reused a collector. Retain the original binding verbatim.
    files, skipped = retainer['file_list'](control, [relative])
    manifest = retainer['stable_snapshot'](control, files)
    return dict(pair=number, mode=mode, tag=tag, coordinator_directory=relative, started=ref(directory/'started.json'),
        finished=detail['finished'], cohort=pair_ref, contracts=contracts, collector_receipt=finish['collector'],
        collector=collector, coordinator_files=manifest, omitted=skipped, original_raw_audits=audits, node_pack=roles)


def build(control, retainer_path):
    tool = ref(retainer_path)
    require(tool['sha256'] == RETAINER_SHA, 'Original compatible node retainer SHA256 differs')
    tool_repo = retainer_path.parents[4]
    deps = [ref(tool_repo/path) for path in ('bench/archive_check.py', 'bench/experiment_step.py',
                'bench/runtime/common.py', 'bench/runtime/observe.py', 'bench/runtime/pilot_pools.py')]
    retainer = runpy.run_path(str(retainer_path))
    plan_path = control/'suite/plan.json'
    plan_ref = pin({'path': str(plan_path), 'sha256': PLAN_SHA}, plan_path)
    plan = read(plan_ref)
    require(plan['plan_sha256'] == PLAN_ID, 'Original plan identity differs')
    db_path = control/'state.sqlite'
    wal = db_path.with_name('state.sqlite-wal')
    # The old state is frozen. An immutable connection cannot create a WAL or shared-memory file.
    require(not wal.exists() or wal.stat().st_size == 0, 'Original state has unmerged WAL; do not assume a frozen snapshot')
    db_ref = ref(db_path)
    rows = {}
    with sqlite3.connect(db_path.as_uri()+'?mode=ro&immutable=1', uri=True) as db:
        for cell_id, status, result in db.execute('SELECT id,status,result FROM cells WHERE status=?', ('complete',)):
            if cell_id in OLD:
                rows[cell_id] = json.loads(result)
    require(set(rows) == set(OLD), 'One or more original C1 cells are not complete')
    cells = []
    for cell_id, (case, prefix, close_sha, overhead_sha) in OLD.items():
        matches = [c for c in plan['cells'] if c['id'] == cell_id]
        require(len(matches) == 1 and matches[0]['case'] == case and matches[0]['pool'] == 'pool-a', 'Changed frozen C1 cell')
        cell = matches[0]
        closing_path = control/'cells'/(cell_id+'.close.json')
        closing = read(pin(rows[cell_id], closing_path))
        require(rows[cell_id]['sha256'] == close_sha and closing['cell_id'] == cell_id and
                closing['outcome'] == 'complete' and closing['plan_sha256'] == PLAN_ID, 'Original C1 closure mismatch')
        for check in closing['closure'].values():
            value = read(pin(check['evidence'], control/'cells'/cell_id/'closure.json'))
            require(check['equals'].items() <= value.items(), 'Historical formal closure not clean')
        sample = read(pin(closing['sample'], control/'cells'/cell_id/'sample.json'))
        overhead_ref = sample['checks']['observer_overhead']['evidence']
        overhead = read(pin(overhead_ref, original_overhead(control, cell_id, prefix)))
        require(overhead_ref['sha256'] == overhead_sha and overhead['passed'] is True and
                len(overhead['pairs']) == 3 and len(overhead['trial_details']) == 6, 'Wrong original three-pair result')
        details = {t['root']: t for t in overhead['trial_details']}
        require(len(details) == 6, 'Original calibration directories are not unique')
        trials = [trial_draft(control, retainer, cell, prefix, mode, number, pair[mode], details, overhead)
                  for number, pair in enumerate(overhead['pairs'], 1) for mode in ('off', 'on')]
        cells.append(dict(cell_id=cell_id, case=case, closing=rows[cell_id], overhead=overhead_ref, trials=trials))
    require(ref(db_path) == db_ref and (not wal.exists() or wal.stat().st_size == 0), 'Frozen old SQLite changed during audit')
    return dict(schema='c123-old-c1-calibration-retention-draft-v1', coordinator_receipts_verified=True,
        approved_for_execution=False, workers_contacted=False, worker_raw_verified=False, measurements_started=False,
        requires_quiescent_canonical_pool_a_lock=True, original_control=str(control), plan=ref(plan_path),
        original_state=db_ref, node_retainer=tool, coordinator_retainer_dependencies=deps, cells=cells)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--retainer', type=Path, required=True)
    parser.add_argument('--draft', action='store_true', help='Print complete payloads and file pins to stdout only')
    args = parser.parse_args()
    draft = build(CONTROL, args.retainer)
    encoded = json.dumps(draft, sort_keys=True, separators=(',', ':'))
    if args.draft:
        print(encoded)
        return
    trials = [t for cell in draft['cells'] for t in cell['trials']]
    print(json.dumps(dict(passed=True, cells=len(draft['cells']), trials=len(trials),
        expected_worker_archives=sum(len(t['node_pack']) for t in trials),
        original_raw_audits=sum(len(t['original_raw_audits']) for t in trials),
        coordinator_files=sum(len(t['coordinator_files']) for t in trials),
        draft_sha256=hashlib.sha256(encoded.encode()).hexdigest(), approved_for_execution=False,
        workers_contacted=False, worker_raw_verified=False)))


if __name__ == '__main__':
    main()
