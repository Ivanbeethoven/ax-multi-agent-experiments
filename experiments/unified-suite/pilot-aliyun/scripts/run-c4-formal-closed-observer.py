#!/usr/bin/env python3
"""Keep the original formal C4 stages; replace only closed-observer packing."""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sqlite3
import sys


def require(ok, message):
    if not ok:
        raise ValueError(message)


def digest(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream,'sha256').hexdigest()


def load(path):
    return json.loads(Path(path).read_bytes())


def pinned(value):
    path = Path(value['path'])
    require(path.is_absolute() and not path.is_symlink() and digest(path)==value['sha256'],
            'Changed or non-absolute pinned file')
    return path


def module(pin, name):
    path = pinned(pin)
    spec = importlib.util.spec_from_file_location(name,path)
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


def overlay_inputs(original_pin, calibration_overlay, helper, worker_code):
    runner = pinned(original_pin)
    require(runner.name=='run-c4.py', 'Exact original C4 runner required')
    sharing = module(calibration_overlay,'c4_original_archive_overlay')
    source = sharing.archive_inputs(helper,worker_code)
    return sharing, source


def stage_spec(original, context, cell_id, stage, request, s):
    runner = Path(original.__file__)
    command = [sys.executable,str(runner.resolve()),'--context',str(context),'--stage',stage]
    inputs = [original.ref(context),original.ref(runner)]
    if stage=='archive':
        command = [sys.executable,s['wrapper']['path'],'--request',request['path'],
                   '--sha256',request['sha256'],'--archive-stage']
        inputs.extend([s['wrapper'],s['calibration_overlay'],s['helper'],request])
    return dict(schema='experiment-step-v1',reviewed=True,cell_id=cell_id,stage=stage,
                argv=command,cwd=str(original.REPO),executable_sha256=digest(sys.executable),
                inputs=inputs,inherit_environment=[])


def archive_authorized(original, c, lease, spec):
    """Only the exact original executor may invoke the archive-only child."""
    path = c.out/'steps/archive.json'
    require(load(path)==spec, 'Original reserved archive step differs')
    expected = original.ref(path)
    output = c.out/'stages/archive'
    with sqlite3.connect('file:'+str(c.control/'state.sqlite')+'?mode=ro',uri=True) as db:
        db.row_factory = sqlite3.Row
        original.suite_state.admitted(db,lease)
        rows = {r['name']:r for r in db.execute('SELECT name,status,spec_sha,output FROM stages WHERE cell_id=?',(c.id,))}
    require(set(rows)==set(c.cell['stages']) and all(rows[k]['status']=='complete' for k in c.cell['stages'][:-1]) and
            tuple(rows['archive'])[1:]==('started',expected['sha256'],str(output)),
            'Exact archive stage is not started after original verification and observer stop')
    started = load(output/'started.json')
    require(started['cell_id']==c.id and started['stage']=='archive' and
            started['spec_sha256']==expected['sha256'] and started['wrapper_pid']==os.getppid() and
            started['automatic_retry'] is False and not (output/'finished.json').exists(),
            'The original one-step executor did not start this archive child')


def run(request, archive_stage=False):
    s = load(pinned(request))
    require(s['schema']=='c4-formal-archive-overlay-v1' and s['reviewed'] is True and
            pinned(s['wrapper'])==Path(__file__).resolve(),
            'Reviewed exact formal archive-only request required')
    # Load the original runner before any bench import; running the overlay in
    # its own subprocess makes the pinned original repo own all bench modules.
    original = module(s['original_runner'],'c4_original_formal')
    require(Path(original.suite_state.__file__).resolve()==original.REPO/'bench/suite_state.py' and
            Path(original.suite_report.__file__).resolve()==original.REPO/'bench/suite_report.py',
            'Loaded suite state/report differ from the original runner repo')
    sharing, source = overlay_inputs(s['original_runner'],s['calibration_overlay'],s['helper'],s['worker_code'])
    context = pinned(s['context'])
    c = original.Fork(context)
    if not archive_stage:
        # Preserve the isolated original runtime and the archive/readback entries.
        cell = c.cell
        arm = cell.get('arm') or {}
        if (cell.get('suite'), cell.get('workload'), cell.get('arm_id'),
                arm.get('product'), arm.get('engine')) == ('C4', 'fork', 'native', 'brewfs', 'flat-v1'):
            raise ValueError('Historical C4 BrewFS Flat full-copy dispatch is retired; existing evidence remains readable')
    require(context==c.out/'context.json' and c.id==s['cell_id'] and c.pool==s['pool'] and
            c.tag==s['attempt'] and c.purpose=='experiment' and c.verification_policy==original.c4_verify.FULL and
            c.cell['stages'][-3:]==['verify','observe-stop','archive'] and c.cell['stages'].count('archive')==1,
            'Frozen formal context must retain original full cold verification and archive order')
    lease = load(c.out/'lease.json')
    require(not lease['already_claimed'] and lease['cell']==c.cell,
            'Only a newly claimed explicit C4 cell may run')
    if archive_stage:
        spec = stage_spec(original,context,c.id,'archive',request,s)
        archive_authorized(original,c,lease,spec)
        closed_fork = sharing.archive_fork(original.Fork,source,s['helper']['sha256'],s['worker_code'])
        closed_fork(context).execute('archive')
        return

    c.check_sources()
    for stage in c.cell['stages']:
        step = stage_spec(original,context,c.id,stage,request,s)
        path = c.out/'steps'/(stage+'.json')
        path.parent.mkdir(exist_ok=True)
        original.write_new(path,step)
        if original.suite_state.run_stage(c.control/'state.sqlite',lease,stage,original.ref(path),c.out/'stages'/stage):
            raise RuntimeError('Original C4 stage failed; keep its reservation')
    acceptance = original.suite_report.sample(original.ref(c.out/'sample.json'),c.plan,c.cell)
    original.write_new(c.out/'acceptance.json',acceptance)
    if not acceptance['accepted']:
        raise ValueError('C4 sample was not accepted: '+str(acceptance['reasons']))
    original.common.close_cell(c,'complete')


if __name__=='__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--request',required=True)
    parser.add_argument('--sha256',required=True)
    parser.add_argument('--archive-stage',action='store_true')
    a = parser.parse_args()
    run(dict(path=a.request,sha256=a.sha256),a.archive_stage)