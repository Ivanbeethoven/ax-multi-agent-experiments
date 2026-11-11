#!/usr/bin/env python3
"""Check that a Packed C3 pool is idle before handing it to the next workload."""

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import runpy
import shlex
import sqlite3
import subprocess
import sys

REPO = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(REPO))
from bench.runtime.common import digest, load, pinned, write_new
from bench.runtime.pilot_pools import active_conflicts, fleet_node, resource_guard


def ref(p):
    return dict(path=str(Path(p).resolve(strict=True)), sha256=digest(p))


def systemd_idle(unit):
    properties = ('MainPID', 'ActiveState', 'SubState', 'ExecMainStatus')
    args = ['systemctl', 'show', unit, *(token for field in properties for token in ('-p', field))]
    output = subprocess.check_output(args, text=True)
    state = dict(line.split('=', 1) for line in output.splitlines())
    if (state['MainPID'] != '0' or state['ExecMainStatus'] != '0' or
            (state['ActiveState'], state['SubState']) not in (('inactive', 'dead'), ('active', 'exited'))):
        raise ValueError('Original Packed pipeline is still active or has failed')
    return state


def handoff(context, pipeline, unit, out):
    m = runpy.run_path(str(Path(__file__).with_name('packed-c3-controller.py')))
    c, _ = m['cell'](context)
    if c.purpose != 'experiment' or c.pool != 'pool-a':
        raise ValueError('Original formal Pool A Packed context required')
    pipeline_result = load(pipeline)
    if pipeline_result.get('passed') is not True or pipeline_result.get('one_cell') != c.id:
        raise ValueError('Original one-cell pipeline did not finish successfully')
    systemd = systemd_idle(unit)
    with resource_guard(c.control, c.cell, c.plan):
        if active_conflicts(c.control, c.cell, c.plan):
            raise ValueError('A formal lease is still active')
        with sqlite3.connect((c.control / 'state.sqlite').as_uri() + '?mode=ro', uri=True) as db:
            status, result = db.execute('select status,result from cells where id=?', (c.id,)).fetchone()
        if status != 'complete':
            raise ValueError('Original formal cell is not complete')
        closing_pin = json.loads(result)
        closing = load(pinned(closing_pin))
        if closing['cell_id'] != c.id or closing['outcome'] != 'complete':
            raise ValueError('Formal closure and state differ')
        verification = load(c.out / 'verification.json')
        if not verification['passed'] or verification.get('full_namespace_cold') is False:
            raise ValueError('Original complete cold checks required')
        for name in ('cold_child', 'parent_unchanged'):
            value = load(pinned(verification[name]))
            if (value['passed'] is not True or value['files'] != c.cell['params']['files'] or
                    value['logical_file_bytes'] != c.cell['params']['files'] * 4096):
                raise ValueError('Original complete cold readback is missing')
        ssh = ['ssh', '-F', str(c.runtime / 'ssh.conf')]
        code = '''
import json, pathlib, sys
sys.path.insert(0, CODE)
from bench.runtime.common import alive, digest, mounts
p=pathlib.Path(ROOT)/'q'/(TAG+'-closure')/'result.json'
v=json.loads(p.read_text())
if not v['passed'] or not v['processes']:
    raise ValueError('Original process closure missing')
result=dict(path=str(p), sha256=digest(p), any_process_alive=any(alive(i) for i in v['processes']))
if ROLE=='client':
    result.update(mount_present=bool(mounts(POINT)), slot_occupied=bool(pathlib.Path('/sys/fs/cgroup/ax-slot-1/cgroup.procs').read_text().strip()))
print(json.dumps(result))
'''
        boundaries = {}
        for role, root in m['ROOTS'].items():
            node = fleet_node(c.fleet, c.pool, role)
            values = dict(CODE=node['repo'], ROOT=root, TAG=c.tag, ROLE=role, POINT=c.asset + '/mnt')
            source = '\n'.join(k + '=' + repr(v) for k, v in values.items()) + '\n' + code
            actual = json.loads(subprocess.check_output(ssh + [c.pool + '-' + role, 'python3 -'], input=source, text=True))
            if any(actual.get(k) for k in ('any_process_alive', 'mount_present', 'slot_occupied')):
                raise ValueError('Original process, slot or mount still active on ' + role)
            boundaries[role] = actual
        collector = c.s['collector_name']
        command = ['docker', 'inspect', '--format', '{{.State.Running}}', collector]
        if subprocess.check_output(ssh + [c.pool + '-objects', shlex.join(command)], text=True).strip() != 'true':
            raise ValueError('Original running collector identity differs')
        value = dict(passed=True, pool=c.pool, cell_id=c.id, at=datetime.now(timezone.utc).isoformat(), tag=c.tag,
                     state='complete', no_active_pool_lease=True, pipeline=ref(pipeline), unit=unit, systemd=systemd,
                     closing=closing_pin, original_processes_and_mounts=boundaries, collector_name=collector,
                     collector_out=c.s['collector_out'], formal_out=str(c.out), full_cold=ref(c.out / 'verification.json'),
                     scope='Read-only current closure check. Does not launch a workload or stop any service.')
        out.parent.mkdir(parents=True, exist_ok=True)
        write_new(out, value)
    print(json.dumps(value))


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--context', type=Path, required=True)
    p.add_argument('--pipeline', type=Path, required=True)
    p.add_argument('--unit', required=True)
    p.add_argument('--out', type=Path, required=True)
    args = p.parse_args()
    handoff(args.context, args.pipeline, args.unit, args.out)
