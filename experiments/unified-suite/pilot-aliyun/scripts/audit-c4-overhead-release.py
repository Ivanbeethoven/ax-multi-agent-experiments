#!/usr/bin/env python3
"""Coordinator-only evidence for a manual release after the exact C4 5% rejection."""
import argparse
from datetime import datetime,timezone
import importlib.util
import json
from pathlib import Path
import re
import sys

REPO=Path(__file__).resolve().parents[4];sys.path.insert(0,str(REPO))
from bench.runtime.common import digest,load,pinned,write_new
from bench.runtime.observe import overhead
from bench.runtime.pilot_pools import cell_resources,resource_guard

REJECTION='Original C4 observer overhead exceeded tolerance; no automatic rerun'
ROLES=('client','metadata','objects')


def ref(path):
    p=Path(path)
    if p.is_symlink() or not p.is_file():raise ValueError('Original regular receipt required: '+str(p))
    return dict(path=str(p.resolve(strict=True)),sha256=digest(p))


def checked(path):
    source=ref(path)
    return source,load(pinned(source))


def module(pin):
    p=pinned(pin);spec=importlib.util.spec_from_file_location('c4_release_continuation',p)
    value=importlib.util.module_from_spec(spec);spec.loader.exec_module(value);return value


def native_debt(q,attempt,cell,closure):
    clients=[n for n in closure['nodes'] if 'debt_samples' in n]
    if len(clients)!=1 or len(clients[0]['debt_samples'])!=2:
        raise ValueError('Original two native zero-debt samples required')
    observed=[];proofs=[]
    for i,source in enumerate(clients[0]['debt_samples']):
        name='sample-'+str(i)+'.json';pin,row=checked(q/'debt'/name)
        if (source['evidence']!=dict(path='/srv/ax-client/q/'+attempt+'/debt/'+name,sha256=pin['sha256']) or
                row.get('passed') is not True or row['cell_id']!=cell['id'] or row['at']!=source['observed_at'] or
                any(type(row[key]) is not int or row[key]!=0 for key in ('pending_upload_bytes','pending_upload_operations')) or
                not row['native_gauges'] or any(type(v) not in (int,float) or v!=0 for v in row['native_gauges'].values())):
            raise ValueError('Original pinned native zero-debt record differs')
        observed.append(datetime.fromisoformat(row['at']));proofs.append(pin)
    if (observed[1]-observed[0]).total_seconds()<1:raise ValueError('Distinct native zero-debt samples required')
    return proofs


def recovered_fourth(s,batch,original,cont):
    if 'previous_resume' not in s:return None
    prior=load(pinned(s['previous_resume']));previous=Path(prior['out'])
    if (prior.get('schema')!=s['schema'] or prior.get('reviewed') is not True or
            load(previous/'request.json')!=prior or any(s[k]!=prior[k] for k in ('original_request','recovered_first',
                'calibration_verification','verification_tool','original_batch_tool'))):
        raise ValueError('Different prior continuation for archive-only recovery')
    deployed=load(pinned(s['archive_deployment']))
    tool=[p for p in deployed['changed'] if Path(p['path']).name=='resume-c4-archive.py']
    if len(tool)!=1:raise ValueError('Pinned archive-only tool required')
    raw,command,context=module(tool[0]).archive_failure(previous,original['trials'][3])
    terminal=cont.failed_unit(s['previous_unit'],s['previous_invocation'],s['previous_resume'])
    if load(batch/'previous-unit.json')!=terminal:raise ValueError('Previous archive failure terminal proof changed')
    recovered=ref(batch/'trial-4-archive/finished.json');finished=load(pinned(recovered))
    source=load(pinned(finished['recovery']))
    if (finished['recovery']!=ref(batch/'trial-4-archive/recovery.json') or
            load(batch/'trial-4-archive/context.json')!=dict(context,out=str(batch/'trial-4-archive')) or
            source.get('original')!=s['original_request'] or source.get('previous_resume')!=s['previous_resume'] or
            source.get('original_trial')!=ref(raw/'context.json') or source.get('original_cohort')!=ref(raw/'cohort/result.json') or
            source.get('failure')!=ref(command/'stderr') or source.get('previous_unit')!=ref(batch/'previous-unit.json') or
            finished['cohort']!=source['original_cohort'] or
            load(batch/'retained-trials.json')!=[s['recovered_first'],*[ref(Path(original['trials'][i]['out'])/'finished.json')
                for i in (1,2)],recovered]):
        raise ValueError('Archived fourth trial does not retain exact original cohort and terminal archive failure')
    return dict(path=recovered['path'],previous=previous)


def trials(batch,original,first,cell,plan_path,admit,fourth=None):
    modes=('off','on','on','off','off','on')
    items=original['trials']
    if len(items)!=6 or tuple(t['mode'] for t in items)!=modes or len({t['attempt'] for t in items})!=6:
        raise ValueError('Exact six original counterbalanced attempts required')
    rows=[]
    for i,(mode,item) in enumerate(zip(modes,items),1):
        p=Path(first['path']) if i==1 else Path(item['out'])/'finished.json'
        if fourth and i==4:p=Path(fourth['path'])
        finished,v=checked(p);phase=None;stage_output=None;stage_request=None
        if i==1:
            original_trial=Path(item['out'])
            recovery=load(pinned(v['recovery']))
            if (finished!=first or recovery['original_trial']!=ref(original_trial/'context.json') or
                    v['cohort']!=ref(original_trial/'cohort/result.json')):
                raise ValueError('First calibration must be archive-only recovery of the original work')
        elif fourth and i==4:
            stage=fourth['previous']/f'trial-{i}'
            phase=ref(stage/'exit.json');stage_output=ref(stage/'stdout')
            stage_request=ref(fourth['previous']/f'trial-{i}.request.json')
        else:
            source=fourth['previous'] if fourth and i in (2,3) else batch
            phase,status=checked(source/f'trial-{i}'/'exit.json')
            stage_output,stdout=checked(source/f'trial-{i}'/'stdout')
            stage_request,reviewed=checked(source/f'trial-{i}.request.json')
            if (type(status['exit_code']) is not int or status['exit_code']!=0 or stdout!=v or
                    reviewed!=load(p.parent/'request.json') or reviewed['attempt']!=item['attempt'] or
                    reviewed['out']!=item['out'] or reviewed.get('reviewed') is not True):
                raise ValueError('Original calibration did not exit 0')
        if v.get('passed') is not True or v['purpose']!='observer-calibration-'+mode or v['pool']!=cell['pool']:
            raise ValueError('Original finished calibration differs from the frozen order/pool')
        root=p.parent/'raw/client';tag=item['attempt'];q=root/'q'/tag
        exits={key:admit.exit_binding(root,q,tag,plan_path,cell,original['asset'],v['purpose'],key,v[key])
               for key in ('native_exit','verify_exit')}
        if exits['native_exit']['identity']==exits['verify_exit']['identity']:
            raise ValueError('Native and cold mount must have different original process identities')
        c=load(pinned(v['closure']))
        if (c.get('passed') is not True or c.get('cell_id')!=cell['id'] or c.get('all_exited') is not True or
                c.get('all_unmounted') is not True or any(type(c[k]) is not int or c[k]!=0 for k in
                ('pending_upload_bytes','pending_upload_operations'))):
            raise ValueError('Closed calibration and two native zero-debt samples required')
        client=load(pinned(v['client_verify']));parent=load(pinned(v['parent_verify']))
        if client.get('passed') is not True or parent.get('passed') is not True or client['child_name']!=item['attempt']:
            raise ValueError('Original correct child/parent verification missing')
        if (i==1 and (client.get('full_cold_child') is not True or client.get('parent_unchanged') is not True) or
                i>1 and client.get('verification_policy')!='touched-cold-v1'):
            raise ValueError('Different full or touched calibration scope')
        checked_archives=[]
        archived=load(pinned(v['archives']));roles=('client',) if mode=='off' else ROLES
        if len(archived)!=len(roles):raise ValueError('Original role archives incomplete')
        for role,a in zip(roles,archived):
            receipt,record=checked(p.parent/'archives'/(role+'-check.json'))
            if (record.get('passed') is not True or record['kind']!=role or record['cell_id']!=cell['id'] or
                    (record['archive'],record['sha256'])!=(a['path'],a['sha256'])):
                raise ValueError('Original member-check receipt differs')
            pinned(a);checked_archives.append(dict(archive=a,member_check=receipt))
        rows.append(dict(finished=finished,stage_exit=phase,stage_output=stage_output,stage_request=stage_request,
                         recovery=v.get('recovery'),attempt=item['attempt'],mode=mode,mount_exits=exits,
                         native_debt=native_debt(q,tag,cell,c),archives=checked_archives,
                         **{k:v[k] for k in ('cohort','closure','native_exit','verify_exit','client_verify','parent_verify')}))
    return rows


def rejected_gate(batch,rows):
    failure,v=checked(batch/'failed.json')
    if v.get('passed') is not False or v.get('error')!=REJECTION or v.get('automatic_retry') is not False:
        raise ValueError('Not the exact pre-formal observer overhead rejection')
    plan,plan_value=checked(batch/'overhead/plan.json');result,value=checked(batch/'overhead/result.json')
    expected=[dict(off=rows[i]['cohort'],on=rows[j]['cohort']) for i,j in ((0,1),(3,2),(4,5))]
    recalculated=overhead(plan)
    if (plan_value.get('max_relative_change')!=.05 or plan_value['pairs']!=expected or recalculated['passed'] is not False or
            not any(v>.05 for v in recalculated['relative_changes'].values()) or
            {k:v for k,v in value.items() if k!='verification'}!=recalculated):
        raise ValueError('Original 5% rejection must recompute from the six exact cohort receipts')
    verification=load(pinned(value['verification']))
    if (value['verification']!=ref(batch/'overhead/verification.json') or verification.get('policy')!='touched-cold-v1' or
            [v['proof'] for v in verification['trials']]!=[r['client_verify'] for r in rows]):
        raise ValueError('Original verification scope or proof chain changed')
    return dict(failure=failure,plan=plan,result=result,verification=value['verification'],
                threshold=.05,relative_changes=recalculated['relative_changes'])


def boundaries(batch,unit):
    directories=[(int(m[1]),p) for p in batch.iterdir() if p.is_dir() and
                 (m:=re.fullmatch(r'failure-boundaries-([1-9][0-9]*)',p.name))]
    if not directories:raise ValueError('Resumed batch produced no terminal physical probes')
    directory=max(directories)[1];released=[]
    ended=int(unit['end']['__REALTIME_TIMESTAMP'])*1000
    for role in ROLES:
        stdout=directory/(role+'-stdout');status=directory/(role+'-exit.json')
        proof,row=checked(stdout);exit_pin,exit_value=checked(status)
        if (type(exit_value['exit_code']) is not int or exit_value['exit_code']!=0 or row.get('safe_for_release') is not True or
                any(row[k] for k in ('live','mounts','unknown_startup_directories','slot')) or not row['original_processes'] or
                not batch.joinpath('failed.json').stat().st_mtime_ns<=stdout.stat().st_mtime_ns<=status.stat().st_mtime_ns<=ended):
            raise ValueError('Original resumed terminal physical boundary not clear: '+role)
        released.append(dict(role=role,source=proof,exit=exit_pin))
    return released


def collector(last,recipe):
    end=load(pinned(last['finished']));argv=recipe['commands']['draft_six_and_formal']
    expected={key:argv[argv.index('--'+key.replace('_','-'))+1] for key in ('collector_name','collector_out')}
    if any(end[k]!=v for k,v in expected.items()):raise ValueError('Original final collector differs from frozen C2 recipe')
    tag=last['attempt'];root=Path(last['finished']['path']).parent/'raw/objects';name=expected['collector_name']
    src,rows=checked(root/'q'/(tag+'-observer')/'service-identities.json')
    found=[x for x in rows if x['name']=='/'+name]
    if len(found)!=1:raise ValueError('Exact final collector identity missing')
    service=found[0];observer=root/'o'/tag
    finish,state=checked(observer/'finished.json');spec,cfg=checked(root/'q'/(tag+'-observer')/'observer.json')
    sample,v=checked(observer/f"sample-{state['samples']-1:06}"/'sample.json')
    if (state.get('passed') is not True or v.get('passed') is not True or v['index']!=state['samples']-1 or
            state['history'][-1]['sample_sha256']!=sample['sha256'] or state['spec_sha256']!=spec['sha256'] or
            state['target_states']['collector']['state']!='live' or v['target_states']['collector']['state']!='live' or
            not any(p['pid']==service['pid'] and p in cfg['processes'] for p in v['processes'])):
        raise ValueError('Final native collector sample was not live and identity-bound')
    return expected,dict(service=service,identities=src,spec=spec,observer_finished=finish,last_sample=sample,
                         current_liveness='C2 locked preflight must probe the inherited collector again')


def audit(request,unit,invocation,watch,out):
    s=load(pinned(request));w=load(pinned(watch));batch=Path(s['out']);cont=module(s['tools']['continuation'])
    if s.get('schema')!='c4-calibration-continuation-v1' or s.get('reviewed') is not True or load(batch/'request.json')!=s:
        raise ValueError('Original exact resumed C4 required')
    original,control,_,plan,cell,canonical,_,_=cont.batch(s['original_request'])
    if (cell['pool']!='pool-b' or s.get('calibration_verification')!='touched-cold-v1' or w.get('reviewed') is not True or
            w.get('schema')!='c4-to-c2-coordinator-watch-v1' or w['c4_cell']!=cell['id'] or w['pool']!=cell['pool'] or
            w['fork_control']!=str(control) or w['c4_original_request']!=s['original_request']):
        raise ValueError('Exact C4/C2 original review and physical pool required')
    if out is not None and (out.parent!=control or not re.fullmatch(r'handoff-b-c4-overhead-to-c2-[0-9]+',out.name) or
                            out.exists() or Path(w['handoff']).is_relative_to(out)):
        raise ValueError('Use a fresh, distinct overhead-only output; never the success watcher path')
    stop=cont.failed_unit(unit,invocation,request)
    recipe=load(pinned(w['recipe']));c2_plan=load(pinned(recipe['plan']));c2=next(c for c in c2_plan['cells'] if c['id']==w['c2_cell'])
    if c2['pool']!=cell['pool'] or set(cell_resources(c2_plan,c2))!=set(cell_resources(plan,cell)):
        raise ValueError('Different C2 physical pool')
    with resource_guard(canonical,cell,plan):
        controls=sorted(p.parent for p in canonical.parent.glob('*/state.sqlite'))
        cont.state_clean(controls,control,cell,plan)
        if any(p.exists() for p in (batch/'result.json',batch/'admit.request.json',batch/'admit',batch/'formal',
                                   control/'cells'/cell['id'],Path(w['handoff']),Path(w['runner_output']))):
            raise ValueError('Formal work or the next workload already started; no calibration-only release')
        failure=load(batch/'failed.json')
        if failure!=dict(passed=False,error=REJECTION,automatic_retry=False):
            raise ValueError('Not the exact pre-formal observer overhead rejection')
        fourth=recovered_fourth(s,batch,original,cont)
        rows=trials(batch,original,s['recovered_first'],cell,control/'suite/plan.json',module(s['tools']['admit']),fourth)
        gate=rejected_gate(batch,rows)
        physical=boundaries(batch,stop);expected,seen=collector(rows[-1],recipe)
        receipt=dict(schema='c4-calibration-only-release-v1',passed=True,pool=cell['pool'],cell_id=cell['id'],
            c4_accepted=False,formal_started=False,outcome='rejected_overhead_before_formal',
            at=datetime.now(timezone.utc).isoformat(),original_request=s['original_request'],continuation=request,
            unit=stop,gate=gate,trials=rows,boundaries=physical,last_observed_collector=seen,
            lease_controls=[str(p) for p in controls],c2_watch_request=watch,recipe=w['recipe'],
            scope='Physical release only; observer gate remains rejected. No SSH, service change, workload or automatic handoff.')
        if out is None:return receipt
        out.mkdir(parents=True,exist_ok=False);write_new(out/'physical-release.json',receipt)
        result=dict(schema='c4-calibration-only-handoff-v1',safe_for_handoff=True,pool=cell['pool'],cell_id=cell['id'],
            c4_accepted=False,formal_started=False,outcome=receipt['outcome'],next_owner=w['next_owner'],
            closing=ref(out/'physical-release.json'),**expected,automatic_launch=False,
            scope='Manual C2 only after confirming the strict success watcher has issued no launch and is retired. Keep the C2 locked preflight.')
        write_new(out/'result.json',result);return ref(out/'result.json')


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('request','request-sha256','unit','invocation','c2-watch-request','c2-watch-sha256'):
        p.add_argument('--'+name,required=True)
    p.add_argument('--out',type=Path,help='Optional fresh, separate receipt folder; default only prints the audit')
    a=p.parse_args();print(json.dumps(audit(dict(path=a.request,sha256=a.request_sha256),a.unit,a.invocation,
        dict(path=a.c2_watch_request,sha256=a.c2_watch_sha256),a.out),indent=2))
