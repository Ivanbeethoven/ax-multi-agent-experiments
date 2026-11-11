#!/usr/bin/env python3
"""Continue one unstarted flat cell after an offline checker repair; no trials rerun."""
import argparse
from datetime import datetime,timezone
import json
from pathlib import Path
import shlex
import subprocess
import sys

REPO=Path(__file__).resolve().parents[4]
sys.path.insert(0,str(REPO))
from bench.runtime.common import digest,load,pinned,write_new
from bench.runtime import pilot_context
from bench.runtime.pilot_pools import pool_of,alias,fleet_node,active_conflicts,resource_guard,cross_pool_proof
from bench import suite_state

def ref(path):return dict(path=str(Path(path).resolve(strict=True)),sha256=digest(path))


def calibrations(original,overhead):
    trials=original['trials']
    if len(trials)!=6 or [trial['mode'] for trial in trials]!=['off','on','on','off','off','on']:raise ValueError('Original counterbalanced six trials required')
    if len({trial['out'] for trial in trials})!=6:raise ValueError('A trial cannot be reused')
    cohort=[];finished=[]
    for trial in trials:
        root=Path(trial['out']);f=load(root/'finished.json');c=load(root/'cohort/result.json')
        if f.get('passed') is not True or f['cohort']!=ref(root/'cohort/result.json') or c.get('purpose')!='observer-calibration-'+trial['mode']:
            raise ValueError('Original complete calibration and purpose required')
        cohort.append(ref(root/'cohort/result.json'));finished.append(ref(root/'finished.json'))
    wanted=[dict(off=cohort[i],on=cohort[j]) for i,j in ((0,1),(3,2),(4,5))]
    if overhead.get('passed') is not True or overhead.get('pairs')!=wanted:raise ValueError('Recheck must use exactly the six original trials')
    return finished


def run(request,sha):
    s=load(pinned(dict(path=str(request),sha256=sha)))
    if s.get('schema')!='flat-cell-check-continuation-v1' or s.get('reviewed') is not True:raise ValueError('Explicit reviewed continuation required')
    original=load(pinned(s['original_request']));control=Path(original['control']);plan=load(control/'suite/plan.json')
    cell=next(c for c in plan['cells'] if c['id']==original['cell_id']);pool=pool_of(cell)
    if original.get('reviewed') is not True or cell['workload'] not in ('fio','trace','smallfiles') or cell['arm_id'] not in ('native','juicefs'):raise ValueError('Original flat request required')
    runtime=pilot_context.select(control,original.get('runtime_control'));fleet=load(runtime/'fleet.json');q=Path(original['qualification'])/cell['arm_id']
    old=Path(original['out']);failure=load(pinned(s['original_failure']))
    if Path(s['original_failure']['path'])!=old/'failed.json' or failure.get('passed') is not False or failure.get('cell_id')!=cell['id']:
        raise ValueError('Retain the original failed checker invocation')
    if load(old/'check/exit.json')['exit_code']==0 or (old/'admit').exists() or (old/'formal').exists():raise ValueError('Continuation only follows failed summary before original admission')
    overhead_path=pinned(s['overhead']);overhead=load(overhead_path);finished=calibrations(original,overhead)
    if overhead.get('metric_contract_sha256')!=digest(q/'metrics.json') or overhead.get('workload_contract_sha256')!=digest(q/'workload.json'):
        raise ValueError('Recheck changed metric or fixed-work contract')
    checker=pinned(s['checker']);pinned(s['checker_dependencies']);check_exit=load(pinned(s['recheck_exit']))
    if check_exit['exit_code']!=0:raise ValueError('New checker invocation failed')
    latest_path=pinned(s['latest_check']);latest=load(latest_path)
    age=(datetime.now(timezone.utc)-datetime.fromisoformat(latest['at'])).total_seconds()
    if latest.get('passed') is not True or latest['build_manifest_sha256']!=plan['config']['build_manifest_sha256'] or not 0<=age<=900:
        raise ValueError('Actual latest-branch receipt within fifteen minutes required')
    for name in ('admit','run'):pinned(original['tools'][name])
    cross=pinned(original['cross_pool_qualification']);cross_pool_proof(cross,plan,runtime/'fleet.json')
    out=Path(s['out']);formal=control/'cells'/cell['id']
    with resource_guard(control,cell,plan):
        if active_conflicts(control,cell,plan):raise ValueError('Physical pool is reserved')
        with suite_state.transaction(control/'state.sqlite') as db:
            status=db.execute('SELECT status FROM cells WHERE id=?',(cell['id'],)).fetchone()
            if not status or status[0]!='pending' or db.execute('SELECT 1 FROM stages WHERE cell_id=?',(cell['id'],)).fetchone() or formal.exists():
                raise ValueError('Formal cell must never have started; no state reset/retry')
        out.mkdir(parents=True,exist_ok=False)
        write_new(out/'started.json',dict(at=datetime.now(timezone.utc).isoformat(),cell_id=cell['id'],request=ref(request),
            original_request=s['original_request'],original_failure=s['original_failure'],calibrations=finished,overhead=s['overhead'],
            checker=ref(checker),checker_dependencies=s['checker_dependencies'],latest_check=s['latest_check'],calibrations_reexecuted=False,automatic_retry=False))
        def command(name,argv):
            folder=out/name;folder.mkdir();write_new(folder/'intent.json',dict(argv=list(map(str,argv)),automatic_retry=False))
            with (folder/'stdout').open('xb') as stdout,(folder/'stderr').open('xb') as stderr:code=subprocess.run(list(map(str,argv)),stdout=stdout,stderr=stderr).returncode
            write_new(folder/'exit.json',dict(exit_code=code))
            if code:raise RuntimeError('Inspect preserved continuation stage: '+name)
            return (folder/'stdout').read_text()
        try:
            for role in ('client','metadata','objects'):
                node=fleet_node(fleet,pool,role);tool=node['repo']+'/experiments/unified-suite/pilot-aliyun/scripts/'+original['observer_tool']['name']
                check="import pathlib,hashlib,json; p=pathlib.Path("+repr(tool)+"); v={'boot_id':pathlib.Path('/proc/sys/kernel/random/boot_id').read_text().strip(),'observer_sha256':hashlib.sha256(p.read_bytes()).hexdigest()};"
                if role=='client':check+="v['slot']=pathlib.Path('/sys/fs/cgroup/ax-slot-1/cgroup.procs').read_text();v['node_sha256']=hashlib.sha256(pathlib.Path("+repr(original['node_tool']['path'])+").read_bytes()).hexdigest();"
                check+='print(json.dumps(v))'
                value=json.loads(command('preflight-'+role,['ssh','-F',runtime/'ssh.conf',alias(pool,role),shlex.join(['python3','-c',check])]))
                if value['boot_id']!=load(runtime/'probes'/alias(pool,role)/'probe.json')['boot_id'] or value['observer_sha256']!=original['observer_tool']['sha256']:
                    raise ValueError('Current host or observer changed')
                if role=='client' and (value['slot'].strip() or value['node_sha256']!=original['node_tool']['sha256']):raise ValueError('Slot active or node helper changed')
            admit=['python3',pinned(original['tools']['admit']),'--control',control,'--qualification',q.parent,'--overhead',overhead_path,'--cell',cell['id'],
                '--node-tool',original['node_tool']['path'],'--node-tool-sha256',original['node_tool']['sha256'],'--observer-tool-name',original['observer_tool']['name'],
                '--observer-tool-sha256',original['observer_tool']['sha256'],'--collector-name',original['final_collector_name'],'--latest-check',latest_path,
                '--cross-pool-qualification',cross,'--owner',original['owner'],'--out',formal]
            if original.get('runtime_control'):admit+=['--runtime-control',original['runtime_control']['path'],'--runtime-control-sha256',original['runtime_control']['sha256']]
            command('admit',admit)
            command('formal',['python3',pinned(original['tools']['run']),'--context',formal/'context.json'])
            closing=control/'cells'/(cell['id']+'.close.json')
            write_new(out/'result.json',dict(passed=True,cell_id=cell['id'],closing=ref(closing),overhead=s['overhead'],
                original_failure=s['original_failure'],calibrations_reexecuted=False,automatic_retry=False,next_cell=False))
        except Exception as error:
            write_new(out/'failed.json',dict(passed=False,cell_id=cell['id'],error=str(error),automatic_retry=False,calibrations_reexecuted=False));raise


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--request',type=Path,required=True);p.add_argument('--sha256',required=True)
    a=p.parse_args();run(a.request,a.sha256)
