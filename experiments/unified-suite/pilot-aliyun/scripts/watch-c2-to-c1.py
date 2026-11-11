#!/usr/bin/env python3
"""Retain the original B C2 evidence and launch the one staged C1 mixed cell."""
import argparse
import json
from pathlib import Path
import re
import shlex
import sqlite3
import subprocess
import sys
import time

REPO=Path(__file__).resolve().parents[4];sys.path.insert(0,str(REPO))
from bench.runtime.common import digest,load,pinned,write_new
from bench.runtime.pilot_pools import active_conflicts,cell_resources,resource_guard
from bench.suite_report import sample as sample_report

START='39f53479d3a045ac8e11786248231fbf'
STOP='7ad2d189f7e94e70a38c781354912448'
ROLES=('client','metadata','objects')


def ref(path):
    p=Path(path)
    if not p.is_file() or p.is_symlink():raise ValueError('Original regular evidence required: '+str(p))
    return dict(path=str(p),sha256=digest(p))


def exact(pin,path):
    if pin!=ref(path):raise ValueError('Different exact source: '+str(path))
    return load(path)


def show(unit):
    data=subprocess.check_output(['systemctl','show',unit,'-pLoadState','-pActiveState','-pSubState',
        '-pMainPID','-pInvocationID','-pExecMainStatus'],text=True)
    return dict(line.split('=',1) for line in data.splitlines())


def journal(unit,invocation,argv,terminal):
    name=unit+'.service'
    rows=[json.loads(row) for row in subprocess.check_output(['journalctl','--no-pager','-o','json',
        'UNIT='+name,'INVOCATION_ID='+invocation,'_SYSTEMD_UNIT=init.scope'],text=True).splitlines()]
    expected='Started '+name+' - '+shlex.join(argv)+'.'
    starts=[r for r in rows if r.get('MESSAGE_ID')==START and r.get('MESSAGE')==expected]
    stops=[r for r in rows if r.get('MESSAGE_ID')==STOP and r.get('MESSAGE')==name+': Deactivated successfully.']
    if len(starts)!=1 or terminal and (len(stops)!=1 or starts[0]['_BOOT_ID']!=stops[0]['_BOOT_ID'] or
            int(starts[0]['__MONOTONIC_TIMESTAMP'])>=int(stops[0]['__MONOTONIC_TIMESTAMP'])):
        raise ValueError('Exact C2 journal start or normal terminal exit missing')
    keys=('UNIT','INVOCATION_ID','_BOOT_ID','__CURSOR','__MONOTONIC_TIMESTAMP','MESSAGE')
    receipt=lambda r:{key:r[key] for key in keys}
    return dict(start=receipt(starts[0]),stop=receipt(stops[0]) if terminal else None)


def wait(unit,invocation,argv,seconds):
    deadline=time.monotonic()+seconds
    while time.monotonic()<deadline:
        state=show(unit)
        if state['InvocationID'] not in ('',invocation):raise ValueError('The original C2 invocation was replaced')
        if state['ActiveState']=='failed':raise ValueError('Original C2 unit failed; no retention or C1 launch')
        if state['ActiveState']=='inactive' and state['MainPID']=='0':
            if state['ExecMainStatus']!='0':raise ValueError('Original C2 did not exit zero')
            return dict(systemd=state,**journal(unit,invocation,argv,True))
        time.sleep(15)
    raise TimeoutError('Exact C2 did not exit within the bounded wait')


def setup(s):
    if (s.get('schema')!='c2-to-c1-mixed-watch-v1' or s.get('reviewed') is not True or
            not 60<=s['deadline_seconds']<=21600 or not re.fullmatch('[0-9a-f]{32}',s['invocation'])):
        raise ValueError('Reviewed bounded exact C2 dependency required')
    d=load(pinned(s['retention_draft']));w=load(pinned(d['watcher']));recipe=load(pinned(d['recipe']))
    mixed=load(pinned(d['next']['draft']));pinned(d['next']['tool']);pinned(d['retainer'])
    plan=load(pinned(d['plan']));control=Path(d['control']);future=d['future']
    c2,c1=(next(c for c in plan['cells'] if c['id']==d[key]) for key in ('c2','next_cell'))
    outer=Path(w['runner_output'])
    if (d['schema']!='c2-calibration-retention-draft-v1' or d['reviewed'] is not False or d['execution_enabled'] is not False or
            w['schema']!='c4-to-c2-coordinator-watch-v1' or w['reviewed'] is not True or
            recipe['cell_id']!=w['c2_cell'] or recipe['plan']!=d['plan'] or w['recipe']!=d['recipe'] or
            c2['id']!=w['c2_cell'] or c2['workload']!='trace' or c2['arm_id']!='juicefs' or
            c1['workload']!='fio' or c1['case']!='fio-randrw' or c1['arm_id']!='juicefs' or
            {c2['pool'],c1['pool'],w['pool'],d['pool']}!={'pool-b'} or
            set(d['resource_ids'])!=set(cell_resources(plan,c2)) or set(cell_resources(plan,c2))!=set(cell_resources(plan,c1)) or
            mixed['cell_id']!=c1['id'] or mixed['predecessor']!=c2['id'] or mixed['watch']!=d['watcher'] or
            mixed['collector']!=d['next']['collector'] or future['unit']!=w['runner_unit'] or
            Path(future['outer_started'])!=outer/'started.json' or Path(future['outer_completion'])!=outer/'result.json' or
            Path(future['formal_close'])!=control/'cells'/(c2['id']+'.close.json') or
            not re.fullmatch('ax-pilot-[a-z0-9-]+',future['unit']) or
            not re.fullmatch('ax-pilot-[a-z0-9-]+',d['next']['unit'])):
        raise ValueError('Different original B C2 / C1 plan, draft or physical resources')
    launch=load(pinned(s['c2_launch']));started=exact(s['c2_started'],Path(future['outer_started']))
    launch_result=load(pinned(s['c2_launch_result']))
    argv=launch['argv'][launch['argv'].index('/usr/bin/python3'):]
    expected=['/usr/bin/python3',w['runner']['path'],'--runtime-repo',d['runtime_repo'],'--recipe',d['recipe']['path'],
        '--recipe-sha256',d['recipe']['sha256'],'--handoff',started['handoff']['path'],
        '--handoff-sha256',started['handoff']['sha256'],'--out',str(outer)]
    if (argv!=expected or launch['handoff']!=started['handoff'] or started['recipe']!=d['recipe'] or
            started['cell_id']!=c2['id'] or started['pool']!='pool-b' or started['control']!=str(control) or
            launch_result.get('passed') is not True or launch_result.get('launched') is not True or
            launch_result['unit']!=future['unit'] or launch_result['handoff']!=started['handoff']):
        raise ValueError('Exact original C2 start, handoff and launch receipts required')
    pinned(started['handoff']);pinned(w['runner'])
    start=journal(future['unit'],s['invocation'],argv,False)
    return d,mixed,plan,control,c2,c1,argv,start


def closed(d,plan,control,c2,c1,mixed):
    f=d['future'];path=Path(f['outer_completion']);outer=load(path);outer_ref=ref(path)
    inner=exact(outer['run'],Path(f['inner_completed']));request=exact(outer['request'],Path(f['request']))
    closing=exact(outer['closing'],Path(f['formal_close']));formal=Path(f['formal_close']).with_name(c2['id'])
    collector=d['next']['collector']
    if (outer.get('passed') is not True or outer['cell_id']!=c2['id'] or outer['pool']!='pool-b' or
            inner.get('passed') is not True or inner['cell_id']!=c2['id'] or inner['closing']!=outer['closing'] or
            inner['final_collector_name']!=collector['collector_name'] or any(outer[k]!=v for k,v in collector.items()) or
            request['reviewed'] is not True or request['cell_id']!=c2['id'] or request['pool']!='pool-b' or
            request['out']!=str(Path(f['inner_completed']).parent) or
            load(Path(request['out'])/'started.json')['request']!=outer['request'] or
            closing['outcome']!='complete' or closing['cell_id']!=c2['id'] or closing['plan_sha256']!=plan['plan_sha256'] or
            closing['retention']['root']!=str(formal) or not pinned(closing['retention']['manifest']).is_file() or
            [t['attempt'] for t in request['trials']]!=d['attempts'] or [t['mode'] for t in request['trials']]!=d['modes']):
        raise ValueError('C2 did not complete its exact request and frozen plan')
    with sqlite3.connect((control/'state.sqlite').as_uri()+'?mode=ro',uri=True) as db:
        status,pin=db.execute('SELECT status,result FROM cells WHERE id=?',(c2['id'],)).fetchone()
        states=dict(db.execute('SELECT name,status FROM stages WHERE cell_id=?',(c2['id'],)))
        mixed_status=db.execute('SELECT status FROM cells WHERE id=?',(c1['id'],)).fetchone()
        mixed_stages=db.execute("SELECT 1 FROM stages WHERE cell_id=? AND status!='pending'",(c1['id'],)).fetchone()
    if (status!='complete' or json.loads(pin)!=outer['closing'] or states!=dict.fromkeys(c2['stages'],'complete') or
            mixed_status!=('pending',) or mixed_stages or any(Path(mixed[k]).exists() for k in ('request','latest'))):
        raise ValueError('C2 SQLite not closed or C1 already started')
    for db in control.parent.glob('*/state.sqlite'):
        if active_conflicts(db.parent,c2,plan):raise ValueError('A B lease still owns the pool: '+str(db))
    c=load(formal/'closure.json');closure=ref(formal/'closure.json')
    if (c['cell_id']!=c2['id'] or c['passed'] is not True or c['all_exited'] is not True or c['all_unmounted'] is not True or
            c['pending_upload_bytes']!=0 or c['pending_upload_operations']!=0 or
            any(check['evidence']!=closure for check in closing['closure'].values())):
        raise ValueError('Original C2 native closure not complete')
    sample=exact(closing['sample'],formal/'sample.json')
    if sample.get('uninterrupted') is not True or sample.get('recovered') is not False:
        raise ValueError('Original uninterrupted C2 sample required')
    acceptance=sample_report(closing['sample'],plan,c2)
    if acceptance['accepted'] is not True:raise ValueError('Original C2 sample failed independent acceptance')
    if closing['archives']!=load(formal/'archives.json') or len(closing['archives'])!=3:
        raise ValueError('Three owned original C2 role archives required')
    checks=[]
    for role,pin in zip(ROLES,closing['archives']):
        source=formal/'archives'/(role+'.tar.gz');check=formal/'archives'/(role+'-check.json');v=load(check)
        if (pin!=ref(source) or v.get('passed') is not True or v['kind']!=role or v['cell_id']!=c2['id'] or
                (v['archive'],v['sha256'])!=(pin['path'],pin['sha256'])):
            raise ValueError('Original formal C2 archive differs: '+role)
        checks.append(ref(check))
    return dict(result=outer_ref,request=outer['request'],run=outer['run'],closing=outer['closing'],
        closure=closure,sample=closing['sample'],archive_checks=checks,accepted=True)


def retain(d,proof,out):
    r=d['retention'];root=Path(r['out'])
    if any(Path(r[k]).exists() for k in ('out','archive','archive_check')):
        raise ValueError('C2 retention already exists; no second attempt')
    tool=pinned(d['retainer'])
    cmd=[sys.executable,str(tool),'--request',proof['request']['path'],'--request-sha256',proof['request']['sha256'],
         '--completed-sha256',proof['run']['sha256'],'--unit',d['future']['unit'],'--out',str(root)]
    logged(out,'retention',cmd)
    end=load(Path(r['result']));archive=Path(r['archive']);check=load(Path(r['archive_check']))
    expected=[dict(tag=tag,mode=mode,roles=list(ROLES if mode=='on' else ROLES[:1]),passed=True)
              for tag,mode in zip(d['attempts'],d['modes'])]
    if (end.get('passed') is not True or end['cell_id']!=d['c2'] or end['trials']!=expected or
            ref(root/'request.json')['sha256']!=proof['request']['sha256'] or
            ref(root/'completed.json')['sha256']!=proof['run']['sha256'] or
            check.get('passed') is not True or check['kind']!='six-calibration-trials' or check['cell_id']!=d['c2'] or
            dict(path=check['archive'],sha256=check['sha256'])!=ref(archive)):
        raise ValueError('Retained archive does not cover the original six C2 sets')
    return dict(result=ref(Path(r['result'])),archive=ref(archive),check=ref(Path(r['archive_check'])))


def logged(out,name,cmd):
    stage=out/name;stage.mkdir()
    write_new(stage/'intent.json',dict(argv=cmd,automatic_retry=False))
    with (stage/'stdout').open('xb') as o,(stage/'stderr').open('xb') as e:
        code=subprocess.run(cmd,stdout=o,stderr=e).returncode
    write_new(stage/'exit.json',dict(exit_code=code))
    if code:raise ValueError('Original dependency step failed: '+name)


def run(request,check_only):
    s=load(pinned(request));d,mixed,plan,control,c2,c1,argv,start=setup(s);unit=d['future']['unit']
    state=show(unit);out=Path(s['out'])
    if (not out.is_absolute() or out.exists() or state['InvocationID']!=s['invocation'] or state['ActiveState']!='active'):
        raise ValueError('Only bind a fresh watcher to the exact active C2 invocation')
    if check_only:return dict(ready=True,check_only=True,waiting_for=state,start=start,workers_contacted=False)
    out.mkdir(parents=True,exist_ok=False);write_new(out/'request.json',s)
    write_new(out/'waiting.json',dict(systemd=state,start=start,workers_contacted=False,automatic_retry=False))
    try:
        terminal=wait(unit,s['invocation'],argv,s['deadline_seconds'])
        write_new(out/'c2-terminal.json',terminal)
        with resource_guard(control,c2,plan):
            proof=closed(d,plan,control,c2,c1,mixed)
        write_new(out/'c2-closed.json',proof)
        sealed=retain(d,proof,out)
        write_new(out/'c2-retained.json',sealed)
        next_unit=d['next']['unit'];current=show(next_unit)
        if current['LoadState']!='not-found' or current['MainPID']!='0':
            raise ValueError('C1 already has a service; never launch twice')
        tool=pinned(d['next']['tool']);draft=d['next']['draft'];pinned(draft);pinned(proof['result'])
        cmd=['systemd-run','--unit='+next_unit,'--collect','--property=Type=exec','--property=Restart=no',
            '--property=StandardOutput=append:'+str(out/'c1.stdout'),'--property=StandardError=append:'+str(out/'c1.stderr'),
            sys.executable,str(tool),'--runtime-repo',d['runtime_repo'],'run',
            '--draft',draft['path'],'--draft-sha256',draft['sha256'],
            '--completed',proof['result']['path'],'--completed-sha256',proof['result']['sha256']]
        logged(out,'c1-launch',cmd)
        receipt=dict(passed=True,c2_closed=ref(out/'c2-closed.json'),c2_retained=ref(out/'c2-retained.json'),
                     c1_unit=next_unit,automatic_retry=False,scope='C1 launch accepted; its own fresh branch and physical preflight remain mandatory.')
        write_new(out/'result.json',receipt)
        return receipt
    except Exception as error:
        write_new(out/'failed.json',dict(passed=False,error=str(error),automatic_retry=False,
            c1_launch_intent=(out/'c1-launch/intent.json').exists()))
        raise


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--request',required=True);p.add_argument('--sha256',required=True)
    p.add_argument('--check-only',action='store_true');a=p.parse_args()
    print(json.dumps(run(dict(path=a.request,sha256=a.sha256),a.check_only)))
