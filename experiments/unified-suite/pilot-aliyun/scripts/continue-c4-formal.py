#!/usr/bin/env python3
"""Continue C4 after the old admit-only stop-receipt bug, without replay."""
import argparse
from datetime import datetime,timezone
import importlib.util
import json
from pathlib import Path
import shlex
import sqlite3
import subprocess
import sys
import time

REPO=Path(__file__).resolve().parents[4];sys.path.insert(0,str(REPO))
from bench.runtime import pilot_context
from bench.runtime.common import digest,load,pinned,write_new
from bench.runtime.pilot_pools import active_conflicts,alias,fleet_node,resource_guard
from bench.suite_plan import check_plan

PROOFS=('work','client_verify','parent_verify','native_exit','verify_exit','closure')
ROLES=dict(client='/srv/ax-client',metadata='/srv/ax-metadata',objects='/srv/ax-objects-0')
ERROR='Incomplete original C4 trial evidence: native_exit'


def ref(path):return dict(path=str(Path(path).resolve(strict=True)),sha256=digest(path))


def module(pin):
    path=pinned(pin);spec=importlib.util.spec_from_file_location(path.stem.replace('-','_'),path)
    value=importlib.util.module_from_spec(spec);spec.loader.exec_module(value);return value


def batch(request):
    old=load(pinned(request));control=Path(old['control']);out=Path(old['out'])
    if old.get('schema')!='c4-fork-batch-v1' or old.get('reviewed') is not True or load(out/'request.json')!=old:
        raise ValueError('Original exact reviewed C4 batch required')
    plan=check_plan(load(control/'suite/plan.json'));cell=next(c for c in plan['cells'] if c['id']==old['cell_id'])
    p=load(pinned(old['preparation_request']));canonical=Path(p['control'])
    controls=list(dict.fromkeys([canonical,*(Path(x) for x in p['other_lease_controls']),control]))
    runtime=pilot_context.select(control,old.get('runtime_control'))
    return old,control,out,plan,cell,canonical,controls,runtime


def preflight(request,admit_pin,out):
    old,control,batch_dir,plan,cell,_,_,runtime=batch(request)
    first=ref(Path(old['trials'][0]['out'])/'finished.json');finished=load(pinned(first));q=load(batch_dir/'qualification-request.json')
    route=dict(schema='c4-fork-route-v1',passed=True,cell_id=cell['id'],pool=cell['pool'],trial=first,
               **{k:finished[k] for k in PROOFS})
    out.mkdir(parents=True,exist_ok=False);write_new(out/'route.json',route)
    body=dict(q,route=ref(out/'route.json'),capacity=ref(batch_dir/'capacity.json'),
              cross_pool_qualification=old['cross_pool_qualification'])
    admit=module(admit_pin);_,_,_,_,gates,checked=admit.validate(body,require_overhead=False)
    verified=dict(schema='c4-formal-preflight-v1',passed=True,cell_id=cell['id'],pool=cell['pool'],
        route=checked,gate_pins={k:ref(p) for k,p in gates.items()},runtime=old['runtime_control'],
        asset=ref(Path(old['qualification'])/cell['arm_id']/'asset-receipt.json'),request=request,admit=admit_pin,
        scope='Read-only check of actual first full trial, capacity, bound gates and asset registration. The six-trial overhead gate remains pending. No workload or state change.')
    write_new(out/'result.json',verified);return ref(out/'result.json')


def recover_first(request,unit,invocation,out):
    """Archive the original fully completed off1 after a slot-exit race."""
    old,control,batch_dir,plan,cell,canonical,controls,_=batch(request)
    first=old['trials'][0];original=Path(first['out']);tag=first['attempt'];failed=load(batch_dir/'failed.json')
    command=original/'commands/0021';before=load(original/'context.json');cohort=load(original/'cohort/result.json')
    if (failed.get('passed') is not False or failed['error']!='Inspect original failed C4 substep '+str(batch_dir/'trial-1') or
            not (batch_dir/'trial-1/stderr').read_text().rstrip().endswith('Inspect failed remote command: '+str(command)) or
            not (command/'stderr').read_text().rstrip().endswith('ValueError: Slot remains occupied') or
            first['mode']!='off' or before['attempt']!=tag or before['purpose']!='observer-calibration-off' or
            cohort.get('passed') is not True or cohort.get('workload_passed') is not True or
            (original/'finished.json').exists() or (original/'archives').exists()):
        raise ValueError('Exact original off1 archive-only slot race required')
    runner=module(old['tools']['run'])
    with resource_guard(canonical,cell,plan):
        terminated=failed_unit(unit,invocation,request);state_clean(controls,control,cell,plan)
        out.mkdir(parents=True,exist_ok=False)
        write_new(out/'recovery.json',dict(original=request,original_trial=ref(original/'context.json'),
            original_cohort=ref(original/'cohort/result.json'),failure=ref(command/'stderr'),original_unit=terminated,
            scope='Archive original completed off1 only; no workload, mounts, service or state changes.'))
        write_new(out/'context.json',dict(before,out=str(out)));write_new(out/'observers.json',[])
        c=runner.Fork(out/'context.json');c.check_sources();c.archive()
        q=out/'raw/client/q'/tag
        proof=dict(passed=True,pool=c.pool,purpose=c.purpose,cohort=ref(original/'cohort/result.json'),
            client_verify=ref(q/'verify/result.json'),parent_verify=ref(q/'verify/parent.json'),
            native_exit=ref(q/'exit.json'),verify_exit=ref(q/'verify-exit.json'),closure=ref(out/'closure.json'),
            work=ref(out/'work-check.json'),archives=ref(out/'archives.json'),collector_name=before['collector_name'],
            collector_out=before['collector_out'],automatic_retry=False,recovery=ref(out/'recovery.json'))
        verify=load(pinned(proof['client_verify']))
        if not (verify.get('full_cold_child') is True and verify.get('parent_unchanged') is True):
            raise ValueError('Original full cold parent/child proof missing')
        write_new(out/'finished.json',proof)
    return ref(out/'finished.json')


def failed_unit(unit,invocation,request):
    name=unit if unit.endswith('.service') else unit+'.service'
    fields=('ActiveState','SubState','MainPID','ExecMainStatus','InvocationID')
    show=subprocess.check_output(['systemctl','show',name,*['-p'+k for k in fields]],text=True)
    actual=dict(line.split('=',1) for line in show.splitlines())
    if actual!={'MainPID':'0','ExecMainStatus':'1','ActiveState':'failed','SubState':'failed','InvocationID':invocation}:
        raise ValueError('Original failed C4 unit still running or different invocation')
    rows=[json.loads(line) for line in subprocess.check_output(['journalctl','--no-pager','-o','json','UNIT='+name,
        'INVOCATION_ID='+invocation,'_SYSTEMD_UNIT=init.scope'],text=True).splitlines()]
    start=[v for v in rows if v.get('MESSAGE_ID')=='39f53479d3a045ac8e11786248231fbf' and
           request['path'] in v['MESSAGE'] and request['sha256'] in v['MESSAGE']]
    end=[v for v in rows if v.get('UNIT_RESULT')=='exit-code' and v['MESSAGE']==name+": Failed with result 'exit-code'."]
    if len(start)!=1 or len(end)!=1 or start[0]['_BOOT_ID']!=end[0]['_BOOT_ID']:
        raise ValueError('Original C4 start and failed exit not found in journal')
    keys=('UNIT','INVOCATION_ID','_BOOT_ID','MESSAGE_ID','__CURSOR','__REALTIME_TIMESTAMP','MESSAGE')
    return dict(show=actual,start={k:start[0][k] for k in keys},end={k:end[0][k] for k in keys})


def state_clean(controls,control,cell,plan):
    for phase in controls:
        if active_conflicts(phase,cell,plan):raise ValueError('A physical pool lease remains: '+str(phase))
    with sqlite3.connect('file:'+str(control/'state.sqlite')+'?mode=ro',uri=True) as db:
        row=db.execute('SELECT status,result FROM cells WHERE id=?',(cell['id'],)).fetchone()
        if row!=('pending',None):raise ValueError('Formal cell did not remain pending')
        for name in ('stages','qualifications','cell_leases'):
            if db.execute('SELECT 1 FROM '+name+' WHERE cell_id=?',(cell['id'],)).fetchone():
                raise ValueError('Original formal state already contains '+name)


def observe_boundaries(runtime,cell,old,original,out,scratch=None):
    """Check every original role root, plus the dedicated observer root when routed."""
    fleet=load(runtime/'fleet.json');pool=cell['pool'];attempts=[t['attempt'] for t in old['trials']]+[old['formal_attempt']]
    checks=list(ROLES.items())
    if scratch is not None:
        if (not isinstance(scratch,dict) or scratch.get('root')!='/srv/ax-c4-observer-scratch' or
                scratch.get('role')!='objects'):
            raise ValueError('Exact dedicated C4 observer scratch root required')
        # The observer tree moved off /srv/ax-objects-0, so the original object
        # boundary check alone cannot see a live or half-started observation.
        checks.append(('objects-scratch',scratch['root']))
    out.mkdir(parents=True,exist_ok=False);all_safe=True
    for name,root in checks:
        role='objects' if name=='objects-scratch' else name
        code=original.failure_release_code(root,fleet_node(fleet,pool,role)['repo'],role,old['asset'],attempts)
        argv=['ssh','-F',str(runtime/'ssh.conf'),alias(pool,role),shlex.join(['python3','-c',code])]
        result=subprocess.run(argv,capture_output=True)
        (out/(name+'-stdout')).write_bytes(result.stdout);(out/(name+'-stderr')).write_bytes(result.stderr)
        write_new(out/(name+'-exit.json'),dict(exit_code=result.returncode))
        all_safe=all_safe and result.returncode==0 and load(out/(name+'-stdout'))['safe_for_release'] is True
    return all_safe


def run(request):
    s=load(pinned(request))
    if s.get('schema')!='c4-admit-continuation-v1' or s.get('reviewed') is not True:
        raise ValueError('Separate reviewed C4 admit-only continuation required')
    old,control,batch_dir,plan,cell,canonical,controls,runtime=batch(s['original_request']);out=Path(s['out'])
    if out.exists() or (control/'cells'/cell['id']).exists() or (batch_dir/'result.json').exists():
        raise ValueError('Original formal never started and a fresh continuation path required')
    checked=load(pinned(s['preflight']))
    if checked.get('passed') is not True or checked['request']!=s['original_request'] or checked['admit']!=s['fixed_admit']:
        raise ValueError('Actual first-trial continuation preflight required')
    expected={key:ref(batch_dir/path) for key,path in dict(failed='failed.json',admit_stderr='admit/stderr',
              original_admit_request='admit-request.json',overhead='overhead/result.json',route='route.json').items()}
    if any(s[key]!=pin for key,pin in expected.items()):raise ValueError('Original failure and six completed trial pins changed')
    failure=load(pinned(s['failed']));failed_exit=load(batch_dir/'admit/exit.json')
    if (failure['passed'] is not False or failure['error']!='Inspect original failed C4 substep '+str(batch_dir/'admit') or
            failed_exit['exit_code']!=1 or not pinned(s['admit_stderr']).read_text().rstrip().endswith('ValueError: '+ERROR)):
        raise ValueError('Continuation is only for the observed old admit-only stop-receipt error')
    admit=module(s['fixed_admit']);original=module(s['original_batch_tool'])
    body=load(pinned(s['original_admit_request']));validated=admit.validate(body)
    if validated[2]!=cell or validated[-1]!=checked['route'] or body['overhead']!=s['overhead'] or body['route']!=s['route']:
        raise ValueError('Original admit request, exact route and read-only preflight differ')
    trials=[]
    for number,t in enumerate(old['trials'],1):
        p=ref(Path(t['out'])/'finished.json');v=load(pinned(p))
        if (v.get('passed') is not True or v['purpose']!='observer-calibration-'+t['mode'] or
                v['pool']!=cell['pool'] or load(batch_dir/('trial-'+str(number))/'exit.json')['exit_code']!=0):
            raise ValueError('Each original calibration must have completed normally')
        trials.append(p)
    with resource_guard(canonical,cell,plan):
        original_unit=failed_unit(s['original_unit'],s['original_invocation'],s['original_request'])
        state_clean(controls,control,cell,plan)
        out.mkdir(parents=True,exist_ok=False);write_new(out/'request.json',s)
        write_new(out/'original-unit.json',original_unit);write_new(out/'original-trials.json',trials)
        if not observe_boundaries(runtime,cell,old,original,out/'original-boundaries'):
            raise ValueError('Original calibration mount/process/slot still present')
        def step(name,argv):
            directory=out/name;directory.mkdir();write_new(directory/'intent.json',dict(argv=argv,automatic_retry=False))
            with (directory/'stdout').open('xb') as stdout,(directory/'stderr').open('xb') as stderr:
                code=subprocess.run(argv,stdout=stdout,stderr=stderr).returncode
            write_new(directory/'exit.json',dict(exit_code=code))
            if code:raise ValueError('Inspect original continuation substep: '+str(directory))
        try:
            step('admit',[sys.executable,str(pinned(s['fixed_admit'])),'--request',s['original_admit_request']['path'],
                          '--sha256',s['original_admit_request']['sha256']])
            formal=Path(body['out'])
            step('formal',[sys.executable,str(pinned(old['tools']['run'])),'--context',str(formal/'context.json')])
            result=dict(passed=True,cell_id=cell['id'],pool=cell['pool'],preparation=body['fork']['preparation'],
                closing=ref(control/'cells'/(cell['id']+'.close.json')),sample=ref(formal/'sample.json'),
                original_request=s['original_request'],continuation_request=request,original_calibrations=trials,
                no_replay=True,automatic_retry=False,next_cell=False,at=datetime.now(timezone.utc).isoformat())
            write_new(out/'result.json',result)
        except Exception as e:
            write_new(out/'failed.json',dict(passed=False,error=str(e),automatic_retry=False))
            i=0
            while True:
                i+=1
                if observe_boundaries(runtime,cell,old,original,out/('failure-boundaries-'+str(i))):break
                time.sleep(5)
            raise
    return ref(out/'result.json')


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);sub=p.add_subparsers(dest='action',required=True)
    a=sub.add_parser('preflight');a.add_argument('--admit',required=True);a.add_argument('--admit-sha256',required=True)
    a.add_argument('--out',type=Path,required=True)
    b=sub.add_parser('run')
    c=sub.add_parser('recover-first');c.add_argument('--unit',required=True);c.add_argument('--invocation',required=True)
    c.add_argument('--out',type=Path,required=True)
    for mode in (a,b,c):mode.add_argument('--request',required=True);mode.add_argument('--sha256',required=True)
    x=p.parse_args();request=dict(path=x.request,sha256=x.sha256)
    if x.action=='preflight':value=preflight(request,dict(path=x.admit,sha256=x.admit_sha256),x.out)
    elif x.action=='recover-first':value=recover_first(request,x.unit,x.invocation,x.out)
    else:value=run(request)
    print(json.dumps(value))
