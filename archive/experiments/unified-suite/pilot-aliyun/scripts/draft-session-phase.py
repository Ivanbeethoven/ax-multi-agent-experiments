#!/usr/bin/env python3
"""Freeze one native session phase worker on its executor; never execute it."""
import argparse
import json
from pathlib import Path
import re
import sys

REPO = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(REPO))
from bench.runtime.common import digest, load, pinned, write_new
from bench.runtime.prepare import AGENT_BUDGET


def ref(path):
    return dict(path=str(path), sha256=digest(path))


def draft(request, output):
    phase = request['phase']; action = phase['action']
    if action not in ('export','import','resume','inspect-writer','verify-stopped'):
        raise ValueError('Explicit supported session phase required')
    purpose = request['purpose']
    if purpose not in ('experiment','observer-calibration-on','observer-calibration-off'):
        raise ValueError('Explicit experiment or matching calibration purpose required')
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]*', request['worker_id']):
        raise ValueError('Simple worker identity required')
    needs_mount = action not in ('inspect-writer','verify-stopped')
    mount = request['mount']; config = load(pinned(mount['spec']))
    if config['purpose'] != purpose:
        raise ValueError('Worker and mount purposes must match')
    pins = [request['session_worker'], mount['spec']]
    worker = pinned(request['session_worker'])
    if action != 'verify-stopped':
        pinned(phase['binary']); pins.append(phase['binary'])
    if action == 'inspect-writer':
        if phase['mount_config'] != config['config'] or config['filesystem'] != 'brewfs':
            raise ValueError('Inspect the original BrewFS mount configuration')
        pins.append(phase['mount_config'])
    elif action == 'verify-stopped':
        if phase['mount'] != mount:
            raise ValueError('Stop check must use original mount instance')
    else:
        if (phase['workspace'] != config['mount_point']+'/workspace' or
                phase['project'] != phase['workspace']+'/repo'):
            raise ValueError('Keep the same canonical project inside the shared mount')
        if action == 'resume':
            checkpoint = load(pinned(phase['checkpoint']))
            if checkpoint['session_id'] != phase['session_id']:
                raise ValueError('Original session ID required')
            pins.append(phase['checkpoint'])
        if action == 'import':
            # The reviewed checkpoint is staged later, after source stop. It
            # must not already exist here and its exact SHA is known upfront.
            transfer = phase['transfer']
            if (not Path(transfer['path']).is_absolute() or
                    not re.fullmatch('[0-9a-f]{64}', transfer['sha256'])):
                raise ValueError('Pinned future native JSON transfer required')
            pins.append(transfer)
    output = Path(output); output.mkdir(parents=True, exist_ok=False, mode=0o700)
    write_new(output/'phase.json', phase)
    result_dir = Path(request['phase_output'])
    if not result_dir.is_absolute() or result_dir.exists():
        raise ValueError('New absolute phase result directory required')
    if result_dir.is_relative_to(config['mount_point']):
        raise ValueError('Evidence belongs outside the shared filesystem')
    pins.insert(0, ref(output/'phase.json'))
    python = Path(sys.executable).resolve(strict=True)
    step = dict(schema='experiment-step-v1', reviewed=True,
        cell_id=request['cell_id'], stage='session-'+action,
        argv=[str(python),str(worker),'--request',str(output/'phase.json'),
              '--sha256',digest(output/'phase.json'),'--out',str(result_dir)],
        cwd=str(REPO), executable_sha256=digest(python), inputs=pins,
        inherit_environment=[], review=dict(reviewer='pilot-operator',
            note='Exact single native session phase; no model retry or credentials in argv.'))
    write_new(output/'step.json', step)
    if action == 'export':
        path = result_dir/'export.json'; equals = {'info.id':phase['session_id']}
    elif action == 'inspect-writer':
        path = result_dir/'export.json'; equals = {'lease_id':None,'holder_generation':None,
            'pending_writeback_bytes':0,'workspace_id':config['anchor']['workspace']}
    else:
        path = result_dir/'result.json'; equals = {'passed':True}
        if action == 'verify-stopped':
            equals.update(original_mount_stopped=True, all_exited=True, mount_absent=True)
        else:
            equals.update(action=action, session_id=phase['session_id'])
    job = dict(id='session-'+action, workload_id=request['workload_id'],
        step=ref(output/'step.json'), result=dict(path=str(path), equals=equals))
    if action == 'resume':
        job['observe_opencode_tools'] = True
    value = dict(schema='experiment-worker-v1', reviewed=True, purpose=purpose,
        worker_id=request['worker_id'], kind='control', budget=AGENT_BUDGET,
        requires_mount=needs_mount, jobs=[job])
    if needs_mount:
        value['mount'] = mount
    write_new(output/'worker.json', value)
    return ref(output/'worker.json')


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--request', type=Path, required=True); p.add_argument('--sha256', required=True)
    p.add_argument('--out', type=Path, required=True)
    a = p.parse_args()
    print(json.dumps(draft(load(pinned(dict(path=str(a.request), sha256=a.sha256))), a.out)))
