#!/usr/bin/env python3
"""Record fresh Pool A quiescence after a C4 base-only capacity stop."""
import argparse
import datetime
import hashlib
import json
import pathlib
import runpy
import shlex
import sqlite3
import subprocess
import sys

ROLES = dict(client='/srv/ax-client', metadata='/srv/ax-metadata', objects='/srv/ax-objects-0')
MANAGER_FIELDS = ('LoadState', 'InvocationID', 'ActiveState', 'SubState',
                  'MainPID', 'ControlPID', 'Result', 'ExecMainStatus')
REMOTE_BASE = r"""
import json, pathlib, sys
sys.path.insert(0, REPO)
from bench.runtime.common import alive, digest, load, mounts

p = pathlib.Path(PREPARATION['path'])
v = load(p)
assert digest(p) == PREPARATION['sha256'] and v['passed'] is True and v['cell_id'] == CELL
assert v['formal_admission'] is False and len(v['normal_exits']) == 2
assert not mounts(str(pathlib.Path(ASSET)/'mnt'))
proofs = [PREPARATION, v['cold_base_check'], *v['normal_exits']]
for item in proofs[1:]:
    path = pathlib.Path(item['path'])
    result = load(path)
    assert digest(path) == item['sha256'] and result['passed'] is True
    if item in v['normal_exits']:
        assert result['mount_absent'] is True and result['exit_code'] == 0
for n in (1, 2):
    path = p.parent/('start-'+str(n))/'stdout'
    start = load(path)
    assert start['passed'] is True and start['mount']['point'] == str(pathlib.Path(ASSET)/'mnt')
    assert not alive(start['process']), 'Original base mount process still live'
    proofs.append(dict(path=str(path), sha256=digest(path)))
debt = p.parent/'import-debt/result.json'
receipt = load(debt)
assert receipt['passed'] is True and receipt['cell_id'] == CELL and len(receipt['samples']) == 2
samples = [load(pathlib.Path(item['path'])) for item in receipt['samples']]
for item, sample in zip(receipt['samples'], samples):
    assert digest(pathlib.Path(item['path'])) == item['sha256'] and sample['passed'] is True
    assert sample['cell_id'] == CELL and sample['native_gauges']
    for key in ('pending_upload_bytes', 'pending_upload_operations'):
        assert type(sample[key]) is int and sample[key] == 0
    assert all(type(x) in (int, float) and x == 0 for x in sample['native_gauges'].values())
assert samples[1]['monotonic_ns']-samples[0]['monotonic_ns'] >= 1_000_000_000
print(json.dumps(dict(passed=True, sources=proofs, debt=dict(path=str(debt),sha256=digest(debt)),
                      samples=receipt['samples'], values=samples)))
"""


def require(condition, message):
    if not condition:
        raise ValueError(message)


def failed_unit(unit, invocation, request):
    show = dict(line.split('=', 1) for line in subprocess.check_output(
        ['systemctl', 'show', unit, *['-p'+k for k in MANAGER_FIELDS]], text=True).splitlines())
    require(show == dict(LoadState='loaded', InvocationID=invocation, ActiveState='failed', SubState='failed',
                         MainPID='0', ControlPID='0', Result='exit-code', ExecMainStatus='1'),
            'Original C4 manager not terminal or invocation changed')
    records = [json.loads(line) for line in subprocess.check_output(
        ['journalctl', '--no-pager', '-o', 'json', 'UNIT='+unit, 'INVOCATION_ID='+invocation,
         '_SYSTEMD_UNIT=init.scope'], text=True).splitlines()]
    starts = [r for r in records if r.get('MESSAGE_ID') == '39f53479d3a045ac8e11786248231fbf' and
              request['path'] in r['MESSAGE'] and request['sha256'] in r['MESSAGE']]
    ends = [r for r in records if r.get('UNIT_RESULT') == 'exit-code' and
            r['MESSAGE'] == unit+": Failed with result 'exit-code'."]
    require(len(starts) == len(ends) == 1 and starts[0]['_BOOT_ID'] == ends[0]['_BOOT_ID'] and
            int(starts[0]['__MONOTONIC_TIMESTAMP']) < int(ends[0]['__MONOTONIC_TIMESTAMP']),
            'Original manager start/stop journal pair missing')
    keys = ('UNIT', 'INVOCATION_ID', '_BOOT_ID', '__CURSOR', '__REALTIME_TIMESTAMP', 'MESSAGE')
    return dict(show=show, start={k:starts[0][k] for k in keys}, end={k:ends[0][k] for k in keys})




def main():
    p = argparse.ArgumentParser(description=__doc__)
    for name in ('batch', 'outer', 'out'):
        p.add_argument('--'+name, type=pathlib.Path, required=True)
    for name in ('batch-sha256', 'outer-sha256', 'unit', 'invocation'):
        p.add_argument('--'+name, required=True)
    a = p.parse_args()
    pilot = pathlib.Path('/srv/ax-pilot-control')
    require(a.out.parent.is_relative_to(pilot/'preparation') and not a.out.exists() and a.out.resolve() == a.out,
            'Fresh output in coordinator preparation required')
    require(a.unit.startswith('ax-pilot-pa-') and a.unit.endswith('.service'),
            'Exact original Pool A service name required')

    def digest(path):
        with path.open('rb') as stream:
            return hashlib.file_digest(stream, 'sha256').hexdigest()

    def bind(path, sha=None):
        path = path.resolve(strict=True)
        value = dict(path=str(path), sha256=digest(path))
        require(sha is None or value['sha256'] == sha, 'Changed original source: '+str(path))
        return value

    def read(path):
        return json.loads(path.read_bytes())

    def write(path, data):
        with path.open('x', encoding='utf-8') as stream:
            json.dump(data, stream, indent=2)
            stream.write('\n')

    batch_pin, outer_pin = bind(a.batch, a.batch_sha256), bind(a.outer, a.outer_sha256)
    batch = read(a.batch)
    run_pin = batch['tools']['run']
    run = pathlib.Path(run_pin['path'])
    repo = run.parents[4]
    require(repo.parent == pilot and repo.name.startswith('code-'), 'Original frozen coordinator runtime required')
    bind(run, run_pin['sha256'])
    sys.path.insert(0, str(repo))
    from bench.runtime import pilot_context
    from bench.runtime.common import pinned
    from bench.runtime.pilot_pools import active_conflicts, alias, fleet_node, resource_guard
    from bench.suite_plan import check_plan

    control, original = (pathlib.Path(batch[k]).resolve(strict=True) for k in ('control', 'out'))
    require(batch['schema'] == 'c4-fork-batch-v1' and batch['reviewed'] is True and
            read(original/'request.json') == batch and read(original/'capacity-budget/exit.json')['exit_code'] == 2 and
            read(original/'failed.json')['error'] == 'Inspect original failed C4 substep '+str(original/'capacity-budget') and
            not any((original/x).exists() for x in ('qualification-request.json', 'trial-1', 'result.json')),
            'Original C4 must have stopped at capacity before admission or calibration')
    tools = run.parent
    probes = runpy.run_path(str(tools/'run-c4-batch.py'))
    plans = [check_plan(read(control/'suite/plan.json'))]
    prep = read(pinned(batch['preparation_request']))
    canonical = pathlib.Path(prep['control']).resolve(strict=True)
    plans.append(check_plan(read(canonical/'suite/plan.json')))
    cell = next(c for c in plans[0]['cells'] if c['id'] == batch['cell_id'])
    require(cell['pool'] == 'pool-a' and any(c['pool'] == 'pool-a' and set(c['resource_ids']) == set(cell['resource_ids'])
            for c in plans[1]['cells']), 'Original A physical resources must match canonical plan')
    runtime = pilot_context.select(control, batch.get('runtime_control'))
    fleet = read(runtime/'fleet.json')
    cap = read(original/'capacity-after.json')
    prepared = {k:cap['nodes']['client']['preparation'][k] for k in ('path', 'sha256')}
    attempts = [t['attempt'] for t in batch['trials']]+[batch['formal_attempt']]

    with resource_guard(canonical, cell, plans[0]):
        closed = failed_unit(a.unit, a.invocation, outer_pin)
        controls = sorted(x.parent for x in pilot.glob('*/state.sqlite'))
        require({canonical, control}.issubset(controls) and all(not active_conflicts(c, cell, plans[0]) for c in controls),
                'A or shared resource still leased')
        with sqlite3.connect((control/'state.sqlite').as_uri()+'?mode=ro', uri=True) as db:
            require(db.execute('SELECT status,result FROM cells WHERE id=?', (cell['id'],)).fetchone() == ('pending', None),
                    'Original formal cell state changed')
        results = {}

        def fetch(role, script):
            target = alias('pool-a', role)
            argv = ['ssh', '-o', 'ConnectTimeout=5', '-o', 'BatchMode=yes', '-F', str(runtime/'ssh.conf'),
                    target, shlex.join(['python3', '-I', '-B', '-'])]
            result = subprocess.run(argv, input=script, text=True, capture_output=True, timeout=20)
            require(result.returncode == 0, 'Boundary probe failed for '+role+': '+result.stderr.strip())
            return dict(role=role, script_sha256=hashlib.sha256(script.encode()).hexdigest(),
                        result=json.loads(result.stdout))

        for role, root in ROLES.items():
            node = fleet_node(fleet, 'pool-a', role)
            results[role] = fetch(role, probes['failure_release_code'](root, node['repo'], role, batch['asset'], attempts))
            require(results[role]['result']['safe_for_release'] is True, 'Original '+role+' boundary still live')
        node = fleet_node(fleet, 'pool-a', 'client')
        values = dict(REPO=node['repo'], PREPARATION=prepared, ASSET=batch['asset'], CELL=cell['id'])
        base = fetch('client', ''.join(k+'='+repr(v)+'\n' for k,v in values.items())+REMOTE_BASE)
        require(base['result']['passed'] is True, 'Original closed base and zero debt required')
        manager = dict(x.split('=', 1) for x in subprocess.check_output(
            ['systemctl', 'show', a.unit, *['-p'+k for k in MANAGER_FIELDS]], text=True).splitlines())
        require(manager == closed['show'],
                'Original unit changed during boundary probes')
        a.out.mkdir()
        write(a.out/'boundary.json', dict(source_batch=batch_pin, source_outer=outer_pin, unit=closed,
              runtime=batch['runtime_control'], live=results, closed_base=base, controls=list(map(str, controls))))
        result = dict(schema='c123-pool-a-retention-quiescence-v1', passed=True, reviewed=True, pool='pool-a',
                      observed_at=datetime.datetime.now(datetime.timezone.utc).isoformat(), unit=a.unit,
                      manager=manager, resource_ids=cell['resource_ids'], no_active_pool_lease=True,
                      agent_slot_empty=True, all_workload_processes_exited=True, all_mounts_absent=True,
                      pending_upload_bytes=0, pending_upload_operations=0,
                      source_pins=[bind(original/'failed.json'), bind(original/'capacity-after.json'),
                                   bind(a.out/'boundary.json')])
        write(a.out/'result.json', result)
        print(json.dumps(bind(a.out/'result.json')))


if __name__ == '__main__':
    main()
