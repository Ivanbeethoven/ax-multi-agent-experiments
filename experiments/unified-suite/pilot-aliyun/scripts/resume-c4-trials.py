#!/usr/bin/env python3
"""Reuse one fully verified C4 trial and run only the five unstarted trials."""
import argparse
from datetime import datetime,timezone
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import time

REPO=Path(__file__).resolve().parents[4];sys.path.insert(0,str(REPO))
from bench.runtime import c4_verify
from bench.c4_dispatch import require_allowed
from bench.runtime.common import digest,load,pinned,write_new
from bench.runtime.pilot_pools import resource_guard


def ref(path):return dict(path=str(Path(path).resolve(strict=True)),sha256=digest(path))


def module(pin):
    path=pinned(pin);spec=importlib.util.spec_from_file_location(path.stem.replace('-','_'),path)
    value=importlib.util.module_from_spec(spec);spec.loader.exec_module(value);return value


def capture(s,old,body,runner,first,out):
    source=Path(s['recovered_first']['path']).parent;tag=s['capture_attempt'];out.mkdir()
    context=dict(body,purpose='observer-calibration-off',attempt=tag,out=str(out),verification_tool=s['verification_tool'])
    write_new(out/'context.json',context);c=runner.Fork(out/'context.json');c.check_sources()
    original=dict(path='/srv/ax-client/q/'+old['trials'][0]['attempt']+'/verify/result.json',sha256=first['client_verify']['sha256'])
    c.helper('draft-mount',cell=c.id,purpose=c.purpose,out=c.base+'/verify-mount')
    c.helper('start',join_slot=True,spec=c.base+'/verify-mount/mount.reviewed.json',out=c.verify)
    result=c.base+'/parent-reference';verification=body['fork']['preparation']
    try:
        c.remote('client',['python3',s['verification_tool']['path'],'capture-parent-reference','--asset',c.asset,'--cell',c.id,
            '--child-name',old['trials'][0]['attempt'],'--preparation',verification['path'],'--preparation-sha256',verification['sha256'],
            '--full-reference',original['path'],'--full-reference-sha256',original['sha256'],
            '--purpose',c.purpose,'--out',result])
    finally:
        c.helper('stop',mount_run=c.verify,out=c.base+'/capture-exit.json')
    p=c.get('client',result+'/result.json','parent-reference.json');normal=c.get('client',c.base+'/capture-exit.json','capture-exit.json')
    observed=load(p);closed=load(normal);verify=load(pinned(first['client_verify']))
    if (not closed['passed'] or closed['exit_code']!=0 or not closed['mount_absent'] or observed['original_full']!=original or
            observed['content_read']!=dict(files=0,bytes=0)):
        raise ValueError('Original full proof and newly observed parent metadata did not close')
    c4_verify.reference_identity(observed,observed['binding'],None,verify)
    result=dict(local=ref(p),remote=dict(path=result+'/result.json',sha256=digest(p)),normal_stop=ref(normal))
    write_new(out/'result.json',result);return result


def run(request):
    s=load(pinned(request))
    if s.get('schema')!='c4-calibration-continuation-v1' or s.get('reviewed') is not True:
        raise ValueError('Reviewed explicit continuation required')
    tools=s['tools'];cont=module(tools['continuation']);admit=module(tools['admit']);runner=module(tools['run'])
    original=module(s['original_batch_tool']);checker=module(tools['check']);pinned(tools['calibrate'])
    old,control,batch,plan,cell,canonical,controls,runtime=cont.batch(s['original_request']);out=Path(s['out'])
    require_allowed(cell)
    pool=cell['pool'];formal=control/'cells'/cell['id'];paths=[t['out'] for t in old['trials']]
    if any(Path(p).exists() for p in paths[1:]) or out.exists() or formal.exists():
        raise ValueError('Only original unstarted later trials and formal are eligible')
    first=load(pinned(s['recovered_first']));recover=load(pinned(first['recovery']))
    if (recover['original']!=s['original_request'] or recover['original_trial']!=ref(Path(paths[0])/'context.json') or
            first['cohort']!=ref(Path(paths[0])/'cohort/result.json') or first['purpose']!='observer-calibration-off'):
        raise ValueError('Different original first calibration recovery')
    body=load(batch/'qualification-request.json');body.pop('reviewed',None)
    core={k:body[k] for k in ('control','cell_id','asset','qualification','node_tool','observer_tool_name',
        'observer_tool_sha256','runtime_control','fork')}
    route=dict(schema='c4-fork-route-v1',passed=True,cell_id=cell['id'],pool=pool,trial=s['recovered_first'],
               **{k:first[k] for k in cont.PROOFS})
    policy=s['calibration_verification'];current={k:first[k] for k in ('collector_name','collector_out')}
    if policy not in (c4_verify.TOUCHED,c4_verify.FULL):raise ValueError('Explicit supported verification policy required')
    with resource_guard(canonical,cell,plan):
        cont.failed_unit(s['original_unit'],s['original_invocation'],s['original_request'])
        cont.state_clean(controls,control,cell,plan)
        out.mkdir(parents=True,exist_ok=False);write_new(out/'request.json',s);write_new(out/'route.json',route)
        admission=dict(core,reviewed=True,route=ref(out/'route.json'),capacity=ref(batch/'capacity.json'),
                       cross_pool_qualification=old['cross_pool_qualification'])
        validated=admit.validate(admission,require_overhead=False)
        write_new(out/'preflight.json',dict(passed=True,cell_id=cell['id'],pool=pool,route=validated[-1],
            gate_pins={k:ref(p) for k,p in validated[-2].items()},scope='All original non-overhead gates; no cell or service mutation.'))
        if not cont.observe_boundaries(runtime,cell,old,original,out/'original-boundaries'):
            raise ValueError('Original attempt still owns slot, mount or process')
        occupied={**old,'trials':[*old['trials'],{'attempt':s['capture_attempt']}]}
        def step(name,tool,argv):
            directory=out/name;directory.mkdir();cmd=[sys.executable,str(pinned(tool)),*map(str,argv)]
            write_new(directory/'intent.json',dict(argv=cmd,automatic_retry=False))
            with (directory/'stdout').open('xb') as stdout,(directory/'stderr').open('xb') as stderr:
                code=subprocess.run(cmd,stdout=stdout,stderr=stderr).returncode
            write_new(directory/'exit.json',dict(exit_code=code))
            if code:raise ValueError('Inspect exact unretired continuation substep '+str(directory))
        try:
            captured=capture(s,old,core,runner,first,out/'parent-reference') if policy==c4_verify.TOUCHED else None
            for number,trial in enumerate(old['trials'][1:],2):
                if any(trial[k]!=v for k,v in current.items()):raise ValueError('Actual original collector chain differs')
                details=dict(core,**current,reviewed=True,purpose='observer-calibration-'+trial['mode'],
                             attempt=trial['attempt'],out=trial['out'])
                if captured:details.update(verification_policy=c4_verify.TOUCHED,full_reference=captured['remote'],
                                           verification_tool=s['verification_tool'])
                for key in ('new_collector_name','new_collector_out'):
                    if key in trial:details[key]=trial[key]
                p=out/('trial-'+str(number)+'.request.json');write_new(p,details)
                step('trial-'+str(number),tools['calibrate'],['--request',p,'--sha256',digest(p)])
                end=load(Path(trial['out'])/'finished.json');current={k:end[k] for k in current}
            q=Path(core['qualification'])/cell['arm_id'];roots=[Path(s['recovered_first']['path']).parent,*map(Path,paths[1:])]
            overhead=checker.check(roots,q/'metrics.json',q/'workload.json',out/'overhead',policy,captured)
            admission.update(current,out=str(formal),formal_attempt=old['formal_attempt'],overhead=overhead)
            p=out/'admit.request.json';write_new(p,admission)
            step('admit',tools['admit'],['--request',p,'--sha256',digest(p)])
            step('formal',tools['run'],['--context',formal/'context.json'])
            result=dict(passed=True,cell_id=cell['id'],pool=pool,preparation=core['fork']['preparation'],
                closing=ref(control/'cells'/(cell['id']+'.close.json')),sample=ref(formal/'sample.json'),
                original_request=s['original_request'],continuation_request=request,recovered_first=s['recovered_first'],
                calibration_verification=policy,parent_reference=captured,overhead=overhead,
                no_replay=True,automatic_retry=False,next_cell=False,at=datetime.now(timezone.utc).isoformat())
            write_new(out/'result.json',result)
        except Exception as error:
            write_new(out/'failed.json',dict(passed=False,error=str(error),automatic_retry=False))
            i=0
            while True:
                i+=1
                if cont.observe_boundaries(runtime,cell,occupied,original,out/('failure-boundaries-'+str(i))):break
                time.sleep(5)
            raise
    return ref(out/'result.json')


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--request',required=True);p.add_argument('--sha256',required=True)
    a=p.parse_args();print(json.dumps(run(dict(path=a.request,sha256=a.sha256))))
