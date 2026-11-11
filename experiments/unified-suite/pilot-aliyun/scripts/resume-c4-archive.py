#!/usr/bin/env python3
"""Archive the completed fourth C4 trial and run only the two untouched trials."""
import argparse
from datetime import datetime, timezone
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import time

REPO=Path(__file__).resolve().parents[4];sys.path.insert(0,str(REPO))
from bench.runtime import c4_verify
from bench.runtime.common import digest, load, pinned, write_new
from bench.runtime.pilot_pools import resource_guard

PROOFS=('client_verify','parent_verify','native_exit','verify_exit','closure','work')
COLLECTOR=('collector_name','collector_out')


def ref(path):
    return dict(path=str(Path(path).resolve(strict=True)),sha256=digest(path))


def module(pin):
    p=pinned(pin);spec=importlib.util.spec_from_file_location(p.stem.replace('-','_'),p)
    result=importlib.util.module_from_spec(spec);spec.loader.exec_module(result);return result


def success(root,stage,trial,prior_request):
    finished=load(root/'finished.json')
    pins=dict(path=str(root/'finished.json'),sha256=digest(root/'finished.json'))
    cohort=load(pinned(finished['cohort']))
    if (load(stage/'exit.json')!={'exit_code':0} or json.loads((stage/'stdout').read_text())!=finished or
            load(root/'request.json')!=prior_request or finished.get('passed') is not True or
            finished['purpose']!='observer-calibration-'+trial['mode'] or cohort.get('passed') is not True or
            cohort.get('workload_passed') is not True):
        raise ValueError('Original trial not complete: '+str(root))
    for key in PROOFS:
        if load(pinned(finished[key])).get('passed') is not True:raise ValueError('Original trial proof failed: '+key)
    pinned(finished['archives'])
    return pins,finished


def archive_failure(previous,trial):
    stage=previous/'trial-4';root=Path(trial['out']);cmd=root/'commands/0022'
    prior_request=load(previous/'trial-4.request.json')
    intent=load(cmd/'intent.json');co=load(root/'cohort/result.json')
    if (load(previous/'failed.json')!=dict(passed=False,
            error='Inspect exact unretired continuation substep '+str(stage),automatic_retry=False) or
            load(stage/'exit.json')!={'exit_code':1} or
            not (stage/'stderr').read_text().rstrip().endswith('Inspect failed remote command: '+str(cmd)) or
            load(cmd/'exit.json')!={'exit_code':1} or
            not (cmd/'stderr').read_text().rstrip().endswith('ValueError: Unknown process still in agent slot') or
            intent['role']!='client' or intent['argv'][:2]!=['python3','-c'] or
            ('TAG='+repr(trial['attempt'])+'\n') not in intent['argv'][2] or
            'from bench.suite_support import closure' not in intent['argv'][2] or
            'Original C4 mount not normally closed' not in intent['argv'][2] or
            trial['mode']!='off' or prior_request!=load(root/'request.json') or
            prior_request!=load(root/'context.json') or prior_request['attempt']!=trial['attempt'] or
            co.get('passed') is not True or co.get('workload_passed') is not True or
            co['purpose']!='observer-calibration-off' or load(root/'observers.json')!=[] or
            any((root/name).exists() for name in ('finished.json','archives','raw'))):
        raise ValueError('Exact original fourth-trial archive-only slot race required')
    for number in range(22):
        if load(root/'commands'/f'{number:04}'/'exit.json')!={'exit_code':0}:
            raise ValueError('Original command before archive failed')
    return root,cmd,prior_request


def recover(runner,root,context,out,source):
    out.mkdir()
    write_new(out/'recovery.json',source)
    write_new(out/'context.json',dict(context,out=str(out)))
    write_new(out/'observers.json',[])
    c=runner.Fork(out/'context.json');c.check_sources();c.archive()
    q=out/'raw/client/q'/c.tag
    proof=dict(passed=True,pool=c.pool,purpose=c.purpose,cohort=ref(root/'cohort/result.json'),
        client_verify=ref(q/'verify/result.json'),parent_verify=ref(q/'verify/parent.json'),
        native_exit=ref(q/'exit.json'),verify_exit=ref(q/'verify-exit.json'),closure=ref(out/'closure.json'),
        work=ref(out/'work-check.json'),archives=ref(out/'archives.json'),automatic_retry=False,
        recovery=ref(out/'recovery.json'),**{k:context[k] for k in COLLECTOR})
    for key in PROOFS:
        if load(pinned(proof[key])).get('passed') is not True:raise ValueError('Recovered original proof failed: '+key)
    verify=load(pinned(proof['client_verify']));closed=load(pinned(proof['closure']))
    if (verify.get('verification_policy')!=c4_verify.TOUCHED or verify['full_reference']!=context['full_reference'] or
            closed['pending_upload_bytes']!=0 or closed['pending_upload_operations']!=0 or
            not closed['all_exited'] or not closed['all_unmounted']):
        raise ValueError('Original cold-check, exit or zero-debt proof differs')
    write_new(out/'finished.json',proof);return ref(out/'finished.json'),proof


def run(request):
    s=load(pinned(request));first_resume=load(pinned(s['previous_resume']));out=Path(s['out'])
    if (s.get('schema')!='c4-calibration-continuation-v1' or s.get('reviewed') is not True or
            first_resume.get('schema')!=s['schema'] or first_resume.get('reviewed') is not True or
            any(s[k]!=first_resume[k] for k in ('original_request','recovered_first','calibration_verification','verification_tool')) or
            s['calibration_verification']!=c4_verify.TOUCHED or
            any(s['tools'][k]!=first_resume['tools'][k] for k in ('continuation','admit','check')) or
            s['original_batch_tool']!=first_resume['original_batch_tool']):
        raise ValueError('Exact prior reviewed C4 continuation required')
    prior=Path(first_resume['out'])
    if load(prior/'request.json')!=first_resume or out.exists():raise ValueError('Fresh output and exact prior request required')
    tools=s['tools'];cont=module(tools['continuation']);admit=module(tools['admit']);runner=module(tools['run'])
    original=module(s['original_batch_tool']);checker=module(tools['check']);pinned(tools['calibrate'])
    old,control,batch,plan,cell,canonical,controls,runtime=cont.batch(s['original_request'])
    paths=[Path(t['out']) for t in old['trials']];pool=cell['pool'];formal=control/'cells'/cell['id']
    original.trial_order(old['trials'],old['formal_attempt'])
    if any(p.exists() for p in (*paths[4:],formal,out)) or (prior/'result.json').exists():
        raise ValueError('Only original unstarted trials five, six and formal are eligible')
    first=load(pinned(s['recovered_first']));first_recovery=load(pinned(first['recovery']))
    if (first['purpose']!='observer-calibration-off' or first_recovery['original']!=s['original_request'] or
            first['cohort']!=ref(paths[0]/'cohort/result.json')):
        raise ValueError('Different first full cold trial')
    completed=[s['recovered_first']];current={k:first[k] for k in COLLECTOR}
    for n in (2,3):
        trial=old['trials'][n-1];req=load(prior/f'trial-{n}.request.json')
        if any(req[k]!=v or trial[k]!=v for k,v in current.items()):
            raise ValueError('Original collector chain differs')
        pin,end=success(paths[n-1],prior/f'trial-{n}',trial,req)
        completed.append(pin);current={k:end[k] for k in COLLECTOR}
    raw,cmd,context=archive_failure(prior,old['trials'][3])
    capture=load(prior/'parent-reference/result.json')
    if (any(context[k]!=v or old['trials'][3][k]!=v for k,v in current.items()) or
            context['verification_policy']!=s['calibration_verification'] or context['full_reference']!=capture['remote'] or
            context['verification_tool']!=s['verification_tool'] or digest(pinned(capture['local']))!=capture['remote']['sha256']):
        raise ValueError('Original fourth trial parent reference or collector changed')
    route=load(prior/'route.json');body=load(batch/'qualification-request.json')
    core={k:body[k] for k in ('control','cell_id','asset','qualification','node_tool','observer_tool_name',
        'observer_tool_sha256','runtime_control','fork')}
    admission=dict(core,reviewed=True,route=ref(prior/'route.json'),capacity=ref(batch/'capacity.json'),
        cross_pool_qualification=old['cross_pool_qualification'])
    if route['trial']!=s['recovered_first'] or any(route[k]!=first[k] for k in cont.PROOFS):
        raise ValueError('Original route does not pin the first full cold trial')
    with resource_guard(canonical,cell,plan):
        stopped=cont.failed_unit(s['previous_unit'],s['previous_invocation'],s['previous_resume'])
        cont.state_clean(controls,control,cell,plan)
        out.mkdir(parents=True,exist_ok=False);write_new(out/'request.json',s)
        write_new(out/'previous-unit.json',stopped)
        validated=admit.validate(admission,require_overhead=False)
        write_new(out/'preflight.json',dict(passed=True,pool=pool,cell_id=cell['id'],route=validated[-1]))
        occupied={**old,'trials':[*old['trials'],{'attempt':first_resume['capture_attempt']}]}
        if not cont.observe_boundaries(runtime,cell,occupied,original,out/'original-boundaries'):
            raise ValueError('Original attempt still owns slot, mount or process')
        def step(name,tool,argv):
            d=out/name;d.mkdir();command=[sys.executable,str(pinned(tool)),*map(str,argv)]
            write_new(d/'intent.json',dict(argv=command,automatic_retry=False))
            with (d/'stdout').open('xb') as stdout,(d/'stderr').open('xb') as stderr:
                code=subprocess.run(command,stdout=stdout,stderr=stderr).returncode
            write_new(d/'exit.json',dict(exit_code=code))
            if code:raise ValueError('Inspect exact unretired continuation substep '+str(d))
        try:
            recovery=out/'trial-4-archive'
            pin,end=recover(runner,raw,context,recovery,dict(original=s['original_request'],
                previous_resume=s['previous_resume'],original_trial=ref(raw/'context.json'),
                original_cohort=ref(raw/'cohort/result.json'),failure=ref(cmd/'stderr'),
                previous_unit=ref(out/'previous-unit.json'),scope='Archive only; no workload, mount or service changes.'))
            completed.append(pin);current={k:end[k] for k in COLLECTOR}
            write_new(out/'retained-trials.json',completed)
            for n in (5,6):
                trial=old['trials'][n-1]
                if any(trial[k]!=v for k,v in current.items()):raise ValueError('Actual original collector chain differs')
                req=dict(core,**current,reviewed=True,purpose='observer-calibration-'+trial['mode'],
                    attempt=trial['attempt'],out=trial['out'],verification_policy=s['calibration_verification'],
                    full_reference=capture['remote'],verification_tool=s['verification_tool'])
                for key in ('new_collector_name','new_collector_out'):
                    if key in trial:req[key]=trial[key]
                p=out/f'trial-{n}.request.json';write_new(p,req)
                step(f'trial-{n}',tools['calibrate'],['--request',p,'--sha256',digest(p)])
                _,end=success(paths[n-1],out/f'trial-{n}',trial,req);current={k:end[k] for k in COLLECTOR}
            q=Path(core['qualification'])/cell['arm_id']
            overhead=checker.check([Path(p['path']).parent for p in completed]+paths[4:],q/'metrics.json',
                q/'workload.json',out/'overhead',s['calibration_verification'],capture)
            admission.update(current,out=str(formal),formal_attempt=old['formal_attempt'],overhead=overhead)
            p=out/'admit.request.json';write_new(p,admission)
            step('admit',tools['admit'],['--request',p,'--sha256',digest(p)])
            step('formal',tools['run'],['--context',formal/'context.json'])
            write_new(out/'result.json',dict(passed=True,cell_id=cell['id'],pool=pool,preparation=core['fork']['preparation'],
                closing=ref(control/'cells'/(cell['id']+'.close.json')),sample=ref(formal/'sample.json'),
                original_request=s['original_request'],continuation_request=request,recovered_first=s['recovered_first'],
                recovered_fourth=completed[3],calibration_verification=s['calibration_verification'],parent_reference=capture,
                overhead=overhead,no_replay=True,automatic_retry=False,next_cell=False,at=datetime.now(timezone.utc).isoformat()))
        except Exception as error:
            write_new(out/'failed.json',dict(passed=False,error=str(error),automatic_retry=False))
            i=0
            while True:
                i+=1
                if cont.observe_boundaries(runtime,cell,occupied,original,out/f'failure-boundaries-{i}'):break
                time.sleep(5)
            raise
    return ref(out/'result.json')


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--request',required=True);p.add_argument('--sha256',required=True)
    a=p.parse_args();print(json.dumps(run(dict(path=a.request,sha256=a.sha256))))
