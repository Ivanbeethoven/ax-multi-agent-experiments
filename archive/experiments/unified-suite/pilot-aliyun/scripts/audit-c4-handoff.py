#!/usr/bin/env python3
"""Certify a normally closed C4 batch before the next workload takes its pool."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
import subprocess
import sys

REPO=Path(__file__).resolve().parents[4];sys.path.insert(0,str(REPO))
from bench.runtime import pilot_context
from bench.runtime.common import digest,load,pinned,write_new
from bench.runtime.pilot_pools import active_conflicts,alias,fleet_node,resource_guard
from bench.suite_plan import check_plan

ROOTS=dict(client='/srv/ax-client',metadata='/srv/ax-metadata',objects='/srv/ax-objects-0')


def ref(path):
    return dict(path=str(Path(path).resolve(strict=True)),sha256=digest(path))


def unit_status(unit,request,invocation):
    fields=('LoadState','ActiveState','SubState','MainPID','ExecMainStatus','InvocationID','ExecStart')
    name=unit if unit.endswith('.service') else unit+'.service'
    show=subprocess.run(['systemctl','show',name,*['-p'+k for k in fields]],capture_output=True,text=True)
    actual=dict(line.split('=',1) for line in show.stdout.splitlines())
    if (actual.get('MainPID','0')!='0' or actual.get('ExecMainStatus','0')!='0' or
            (actual.get('ActiveState'),actual.get('SubState')) not in (('active','exited'),('inactive','dead')) or
            actual.get('InvocationID') not in ('',None,invocation)):
        raise ValueError('Original systemd batch did not exit successfully')
    # Transient Type=exec units without RemainAfterExit can be unloaded. The
    # original PID 1 journal pair survives, keyed by actual invocation and boot.
    rows=[json.loads(line) for line in subprocess.check_output(['journalctl','--no-pager','-o','json',
        'UNIT='+name,'INVOCATION_ID='+invocation,'_SYSTEMD_UNIT=init.scope'],text=True).splitlines()]
    starts=[r for r in rows if r.get('MESSAGE_ID')=='39f53479d3a045ac8e11786248231fbf' and
            request['path'] in r['MESSAGE'] and request['sha256'] in r['MESSAGE']]
    stops=[r for r in rows if r.get('MESSAGE_ID')=='7ad2d189f7e94e70a38c781354912448' and
           r['MESSAGE']==name+': Deactivated successfully.']
    if (len(starts)!=1 or len(stops)!=1 or starts[0]['_BOOT_ID']!=stops[0]['_BOOT_ID'] or
            int(starts[0]['__MONOTONIC_TIMESTAMP'])>=int(stops[0]['__MONOTONIC_TIMESTAMP'])):
        raise ValueError('Original start and normal-exit journal records are missing')
    keys=('UNIT','INVOCATION_ID','_BOOT_ID','MESSAGE_ID','__CURSOR','__REALTIME_TIMESTAMP','__MONOTONIC_TIMESTAMP','MESSAGE')
    return dict(systemd=actual,start={k:starts[0][k] for k in keys},stop={k:stops[0][k] for k in keys})


def boundary_code(values):
    return '\n'.join(k+'='+repr(v) for k,v in values.items())+'''
import json,pathlib,subprocess,sys
sys.path.insert(0,CODE)
from bench.runtime.common import alive,digest,mounts
root=pathlib.Path(ROOT)
read=lambda p:json.loads(p.read_bytes())
proofs=[];identities=[];points=set()
for tag in TAGS:
 path=root/'q'/(tag+'-closure')/'result.json'
 value=read(path)
 if value.get('passed') is not True or value.get('cell_id')!=CELL or not value['processes']:
  raise ValueError('Missing original successful closure '+tag)
 proofs.append(dict(path=str(path),sha256=digest(path)))
 identities.extend(value['processes'])
 points.update(m['point'] for m in value.get('mount_points',[]))
 if ROLE=='client':
  for suffix in ('','-v'):
   path=root/'m'/(tag+suffix)/'stopped.json';end=read(path)
   if not end['passed'] or end['exit_code']!=0 or not end['mount_absent']:
    raise ValueError('Original C4 mount did not exit normally '+tag+suffix)
   proofs.append(dict(path=str(path),sha256=digest(path)))
if ROLE=='client':
 p=pathlib.Path(PREPARATION['path']);v=read(p)
 if digest(p)!=PREPARATION['sha256'] or not v['passed'] or v['cell_id']!=CELL or len(v['normal_exits'])!=2:
  raise ValueError('Original base preparation changed')
 for pin in v['normal_exits']:
  p=pathlib.Path(pin['path']);end=read(p)
  if digest(p)!=pin['sha256'] or not end['passed'] or end['exit_code']!=0 or not end['mount_absent']:
   raise ValueError('Original base mount did not exit normally')
  proofs.append(pin)
 points.add(read(pathlib.Path(ASSET)/'asset.json')['mount_point'])
 slot=pathlib.Path('/sys/fs/cgroup/ax-slot-1/cgroup.procs').read_text().strip()
else:slot=None
collector=None
if ROLE=='objects':
 path=pathlib.Path(COLLECTOR_OUT)/'started.json';started=read(path)
 fmt='{{json .Id}}\u0009{{json .State.Running}}\u0009{{json (index .Config.Labels "ax.pilot.owner")}}'
 container,running,owner=[json.loads(s) for s in subprocess.check_output(['docker','inspect','--format',fmt,COLLECTOR_NAME],text=True).strip().split('\u0009')]
 if container!=started['container_id'] or running is not True or owner!=COLLECTOR_NAME:
  raise ValueError('Surviving collector identity differs from original startup')
 collector=dict(name=COLLECTOR_NAME,out=COLLECTOR_OUT,container_id=container,running=running,
                source=dict(path=str(path),sha256=digest(path)))
live=[p for p in identities if alive(p)]
mounted=[m for point in sorted(points) for m in mounts(point)]
result=dict(passed=not (live or mounted or slot),live=live,mounted=mounted,slot=slot,
            original_processes=identities,proofs=proofs,collector=collector)
print(json.dumps(result));raise SystemExit(0 if result['passed'] else 2)
'''


def audit(request,unit,invocation,out,next_owner,continuation=None):
    s=load(pinned(request));control=Path(s['control']);plan=check_plan(load(control/'suite/plan.json'))
    cell=next(c for c in plan['cells'] if c['id']==s['cell_id']);pool=cell['pool']
    batch=Path(s['out']);actual_request=request;result_dir=batch
    if continuation:
        resumed=load(pinned(continuation))
        if (resumed.get('schema') not in ('c4-admit-continuation-v1','c4-calibration-continuation-v1') or resumed.get('reviewed') is not True or
                resumed['original_request']!=request):raise ValueError('Different original C4 continuation')
        actual_request=continuation;result_dir=Path(resumed['out'])
        if load(result_dir/'request.json')!=resumed:raise ValueError('Actual continuation request differs')
    result_pin=ref(result_dir/'result.json');result=load(pinned(result_pin))
    if continuation and (result.get('continuation_request')!=continuation or result.get('no_replay') is not True):
        raise ValueError('Actual original no-replay continuation result required')
    closing=load(pinned(result['closing']));sample=load(pinned(result['sample']))
    if (s.get('schema')!='c4-fork-batch-v1' or s.get('reviewed') is not True or load(batch/'request.json')!=s or
            result.get('passed') is not True or result['pool']!=pool or result['cell_id']!=cell['id'] or
            closing['cell_id']!=cell['id'] or closing['outcome']!='complete' or closing['sample']!=result['sample'] or
            sample.get('uninterrupted') is not True or sample.get('recovered') is not False):
        raise ValueError('Original accepted C4 batch and sample required')
    formal=control/'cells'/cell['id'];acceptance_pin=ref(formal/'acceptance.json')
    accepted=load(pinned(acceptance_pin))
    if accepted.get('accepted') is not True or accepted['sample']!=result['sample']:
        raise ValueError('Original suite sample acceptance required')
    preparation=load(pinned(s['preparation_request']));canonical=Path(preparation['control'])
    controls=list(dict.fromkeys([canonical,*(Path(p) for p in preparation['other_lease_controls']),control]))
    runtime=pilot_context.select(control,s.get('runtime_control'));fleet=load(runtime/'fleet.json')
    tags=[(t['attempt'],t['mode']) for t in s['trials']]+[(s['formal_attempt'],'on')]
    current=load(Path(s['trials'][-1]['out'])/'finished.json');context=load(formal/'context.json')
    collector={k:current[k] for k in ('collector_name','collector_out')}
    if current.get('passed') is not True or any(context[k]!=v for k,v in collector.items()):
        raise ValueError('Formal collector differs from the actual final calibration')
    with resource_guard(canonical,cell,plan):
        actual_unit=unit_status(unit,actual_request,invocation)
        with sqlite3.connect('file:'+str(control/'state.sqlite')+'?mode=ro',uri=True) as db:
            status,encoded=db.execute('SELECT status,result FROM cells WHERE id=?',(cell['id'],)).fetchone()
            if status!='complete' or json.loads(encoded)!=result['closing']:
                raise ValueError('Original C4 result is not the closed SQLite cell')
        for phase in controls:
            if active_conflicts(phase,cell,plan):raise ValueError('A lease still owns this physical pool: '+str(phase))
        out.mkdir(parents=True,exist_ok=False)
        boundaries=[]
        for role,root in ROOTS.items():
            node=fleet_node(fleet,pool,role)
            code=boundary_code(dict(CODE=node['repo'],ROOT=root,ROLE=role,CELL=cell['id'],ASSET=s['asset'],
                TAGS=[tag for tag,mode in tags if role=='client' or mode=='on'],PREPARATION=result['preparation'],
                COLLECTOR_NAME=collector['collector_name'],COLLECTOR_OUT=collector['collector_out']))
            argv=['ssh','-F',str(runtime/'ssh.conf'),alias(pool,role),'python3 -']
            write_new(out/(role+'-intent.json'),dict(argv=argv,stdin_sha256=hashlib.sha256(code.encode()).hexdigest(),read_only=True))
            response=subprocess.run(argv,input=code,text=True,capture_output=True)
            (out/(role+'-stdout')).write_text(response.stdout);(out/(role+'-stderr')).write_text(response.stderr)
            write_new(out/(role+'-exit.json'),dict(exit_code=response.returncode))
            if response.returncode or json.loads(response.stdout).get('passed') is not True:
                raise ValueError('Original boundary or collector probe failed: '+role)
            boundaries.append(dict(role=role,source=ref(out/(role+'-stdout'))))
        handoff=dict(schema='c4-pool-handoff-v1',passed=True,safe_for_handoff=True,pool=pool,cell_id=cell['id'],
            at=datetime.now(timezone.utc).isoformat(),next_owner=next_owner,closing=result_pin,formal_closing=result['closing'],
            acceptance=acceptance_pin,request=request,continuation=continuation,unit=dict(name=unit,**actual_unit),
            checked_lease_controls=[str(p) for p in controls],boundaries=boundaries,**collector,
            scope='Original accepted C4; all 7 attempt identities exited and mounts absent; slot empty; one live original collector retained. No workload, configuration or sealed archive changed.')
        write_new(out/'result.json',handoff)
    return ref(out/'result.json')


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--request',required=True);p.add_argument('--sha256',required=True)
    p.add_argument('--unit',required=True);p.add_argument('--invocation',required=True)
    p.add_argument('--continuation');p.add_argument('--continuation-sha256')
    p.add_argument('--out',required=True,type=Path);p.add_argument('--next-owner',required=True)
    a=p.parse_args()
    if bool(a.continuation)!=bool(a.continuation_sha256):p.error('Continuation path and SHA256 must be supplied together')
    continuation=dict(path=a.continuation,sha256=a.continuation_sha256) if a.continuation else None
    print(json.dumps(audit(dict(path=a.request,sha256=a.sha256),a.unit,a.invocation,a.out,a.next_owner,continuation)))
