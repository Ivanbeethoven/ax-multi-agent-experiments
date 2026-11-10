#!/usr/bin/env python3
"""Run one explicitly selected fresh-mount C1 observer calibration trial."""
import argparse
from datetime import datetime,timezone
import json
from pathlib import Path
import re
import shlex
import subprocess
import sys

REPO=Path(__file__).resolve().parents[4]
sys.path.insert(0,str(REPO))
from bench.runtime.common import digest,load,write_new
from bench.runtime import cohort,fleet


def ref(path):return dict(path=str(path),sha256=digest(path))


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--control',type=Path,required=True)
    p.add_argument('--qualification',type=Path,required=True)
    p.add_argument('--product',choices=['brewfs','juicefs'],required=True)
    p.add_argument('--case',choices=['fio-read','fio-write','fio-randread','fio-randwrite','fio-randrw'],default='fio-randread')
    p.add_argument('--mode',choices=['on','off'],required=True)
    p.add_argument('--attempt',required=True)
    p.add_argument('--collector-name',required=True)
    p.add_argument('--collector-out',required=True)
    p.add_argument('--new-collector-name')
    p.add_argument('--node-tool',required=True)
    p.add_argument('--node-tool-sha256',required=True)
    p.add_argument('--out',type=Path,required=True)
    a=p.parse_args()
    if not re.fullmatch(r'cal-[a-z0-9-]{3,28}',a.attempt):raise ValueError('Short, unique calibration attempt required')
    if not all(re.fullmatch(r'ax-pilot-[a-z0-9-]+',n) for n in [a.collector_name]+([a.new_collector_name] if a.new_collector_name else [])):
        raise ValueError('Owned collector name required')
    a.out.mkdir(parents=True,exist_ok=False)
    arm='native' if a.product=='brewfs' else 'juicefs'
    plan=load(a.control/'suite/plan.json')
    cell=next(c for c in plan['cells'] if c['case']==a.case and c['arm_id']==arm)
    config=a.control/'ssh.conf';sequence=0
    def remote(role,argv):
        nonlocal sequence
        directory=a.out/'commands'/f'{sequence:03}';sequence+=1;directory.mkdir(parents=True)
        write_new(directory/'intent.json',dict(argv=argv,role=role,at=datetime.now(timezone.utc).isoformat(),automatic_retry=False))
        with (directory/'stdout').open('xb') as out,(directory/'stderr').open('xb') as err:
            r=subprocess.run(['ssh','-F',str(config),'pool-a-'+role,shlex.join(list(map(str,argv)))],stdout=out,stderr=err)
        write_new(directory/'exit.json',dict(exit_code=r.returncode))
        if r.returncode:raise RuntimeError('Remote command failed; inspect '+str(directory))
        return (directory/'stdout').read_text()
    def get(role,path,name):
        target=a.out/name;target.parent.mkdir(parents=True,exist_ok=True)
        wanted=remote(role,['sha256sum',path]).split()[0]
        subprocess.run(['scp','-F',str(config),'pool-a-'+role+':'+str(path),str(target)],check=True)
        if digest(target)!=wanted:raise ValueError('Changed transfer')
        return target
    def helper(action,**fields):
        argv=['python3',a.node_tool,action,'--asset','/srv/ax-client/a/c1-'+arm]
        for key,value in fields.items():
            if value is True:argv+=['--'+key.replace('_','-')]
            elif value is not None:argv+=['--'+key.replace('_','-'),str(value)]
        return remote('client',argv)
    # All source preparation shares this data disk. Do not time against a live
    # million-file generator or trace unpacker.
    remote('client',['test','-f','/srv/ax-client/inputs/receipts/result.json'])
    if remote('client',['sha256sum',a.node_tool]).split()[0]!=a.node_tool_sha256:
        raise ValueError('Node preparation tool changed')
    write_new(a.out/'started.json',dict(at=datetime.now(timezone.utc).isoformat(),purpose='observer-calibration-'+a.mode,
        product=a.product,cell_id=cell['id'],node_tool_sha256=a.node_tool_sha256,automatic_retry=False))
    running=remote('objects',['docker','inspect','--format','{{.State.Running}}',a.collector_name]).strip()=='true'
    collector_name=a.collector_name;collector_out=a.collector_out
    collector_tool='/srv/ax-objects-0/code-01/experiments/unified-suite/pilot-aliyun/scripts/metric-collector.py'
    if a.mode=='off' and running:
        remote('objects',['python3',collector_tool,'stop','--name',collector_name,'--out',collector_out])
    elif a.mode=='on' and not running:
        if not a.new_collector_name:raise ValueError('Explicit new collector name required; never restart an old container')
        collector_name=a.new_collector_name;collector_out='/srv/ax-objects-0/q/'+a.attempt+'-collector'
        remote('objects',['python3',collector_tool,'start','--image-receipt','/srv/ax-objects-0/assets/provenance/otel-collector.json',
            '--name',collector_name,'--out',collector_out])
    write_new(a.out/'collector.json',dict(name=collector_name,out=collector_out,enabled=a.mode=='on',native_rustfs_sender_held_enabled=True))
    base='/srv/ax-client/q/'+a.attempt
    prepstate='/srv/ax-client/m/'+a.attempt+'-p'
    mainstate='/srv/ax-client/m/'+a.attempt
    verifystate='/srv/ax-client/m/'+a.attempt+'-v'
    purpose='observer-calibration-'+a.mode
    helper('draft-mount',cell=cell['id'],purpose=purpose,out=base+'/prep')
    helper('start',join_slot=True,spec=base+'/prep/mount.reviewed.json',out=prepstate)
    helper('copy-fio',join_slot=True,cell=cell['id'],child_name=a.attempt,out=base+'/copy')
    helper('debt',cell=cell['id'],out=base+'/copy-debt')
    helper('stop',mount_run=prepstate,out=base+'/copy-exit.json')
    helper('draft-mount',cell=cell['id'],purpose=purpose,out=base+'/main')
    helper('start',join_slot=True,spec=base+'/main/mount.reviewed.json',out=mainstate)
    workerbase='/srv/ax-client/w/'+a.attempt
    helper('worker',cell=cell['id'],child_name=a.attempt,purpose=purpose,
        spec=base+'/main/mount.reviewed.json',mount_run=mainstate,out=workerbase)
    worker_ref=json.loads(remote('client',['python3','-c',
        "import hashlib,json,pathlib;p=pathlib.Path("+repr(workerbase+'/worker.reviewed.json')+");print(json.dumps({'path':str(p),'sha256':hashlib.sha256(p.read_bytes()).hexdigest()}))"]))
    qualification=a.qualification/arm;metric_ref=ref(qualification/'metrics.json')
    observers=[];roots={'client':'/srv/ax-client','metadata':'/srv/ax-metadata','objects':'/srv/ax-objects-0'}
    if a.mode=='on':
        for role,root in roots.items():
            specdir=root+'/q/'+a.attempt+'-observer';state=root+'/o/'+a.attempt
            tool=root+'/code-01/experiments/unified-suite/pilot-aliyun/scripts/make-observer-cloud-03.py'
            args=['python3',tool,'--role',role,'--product',a.product,'--metric-contract-sha256',metric_ref['sha256'],
                '--collector-name',collector_name,'--out',specdir]
            if role=='client':args+=['--mount-directory',mainstate,'--extra-mount-directory',prepstate,
                                    '--extra-mount-directory',verifystate]
            spec_ref=json.loads(remote(role,args))
            peer_ref=ref(a.control/'probes'/('pool-a-'+role)/'peer.json')
            fleet.call(peer_ref,'observe-start',state,a.out/'observer-start'/role,spec_ref)
            observers.append(dict(id='pool-a-'+role,node_id='pool-a-'+role,peer=load(peer_ref['path']),
                directory=state,spec=spec_ref,required_live_targets={'client':['filesystem'],'metadata':['redis'],'objects':['rustfs','collector']}[role]))
    s=dict(schema='experiment-cohort-v1',reviewed=True,purpose=purpose,provider='ssh',agents=1,
        inventory=ref(a.control/'fleet.json'),metric_contract=metric_ref,workload_contract=ref(qualification/'workload.json'),
        allocation_qualification=ref(a.control/'allocation.json'),hardware_qualification=ref(a.qualification/'hardware.json'),
        input_qualification=ref(qualification/'input.json'),filesystem_qualification=ref(qualification/'filesystem.json'),
        workers=[dict(id='a01',node_id='pool-a-client',slot_id='pool-a-client',
            peer=load(a.control/'probes/pool-a-client-slot/peer.json'),spec=worker_ref,output='/srv/ax-client/j/'+a.attempt)],
        observers=observers,ready_timeout_seconds=120,
                review=dict(reviewer='pilot-operator',note='One fixed-work '+a.case+' worker, fresh cache, original backend and reviewed metrics; no next cell or automatic retry.'))
    write_new(a.out/'cohort.json',s)
    def mark(label):
        for o in observers:
            peer_ref=ref(a.control/'probes'/o['id']/'peer.json')
            fleet.call(peer_ref,'observe-mark',o['directory'],a.out/'marks'/label/o['id'],label=label)
    mark('measure-start')
    result=cohort.run(ref(a.out/'cohort.json'),a.out/'cohort')
    if not result['passed']:raise ValueError('Calibration workload/telemetry failed; preserve original state')
    mark('measure-end')
    helper('debt',cell=cell['id'],out=base+'/debt')
    mark('drain-end')
    helper('stop',mount_run=mainstate,out=base+'/exit.json')
    helper('draft-mount',cell=cell['id'],purpose=purpose,out=base+'/verify-mount')
    helper('start',join_slot=True,spec=base+'/verify-mount/mount.reviewed.json',out=verifystate)
    helper('fio',join_slot=True,cell=cell['id'],child_name=a.attempt,phase='verify',out=base+'/verify')
    helper('stop',mount_run=verifystate,out=base+'/verify-exit.json')
    mark('verify-end')
    for o in observers:
        peer_ref=ref(a.control/'probes'/o['id']/'peer.json')
        fleet.call(peer_ref,'observe-stop',o['directory'],a.out/'observer-stop'/o['id'])
        role=o['id'].removeprefix('pool-a-')
        code="import sys,json;sys.path.insert(0,"+repr(roots[role]+'/code-01')+");from bench.runtime.observe import audit;print(json.dumps(audit("+repr(o['directory'])+",30,['measure-start','measure-end','drain-end','verify-end'])))"
        checked=json.loads(remote(role,['python3','-c',code]))
        if not checked['passed']:raise ValueError('Raw observer audit failed')
        write_new(a.out/'observer-stop'/o['id']/'raw-audit.json',checked)
    for path in ('copy/result.json','debt/result.json','exit.json','verify/result.json','verify-exit.json'):
        get('client',base+'/'+path,'client/'+path)
    write_new(a.out/'finished.json',dict(passed=True,at=datetime.now(timezone.utc).isoformat(),
        cohort=ref(a.out/'cohort/result.json'),client_verify=ref(a.out/'client/verify/result.json'),
        native_exit=ref(a.out/'client/exit.json'),verify_exit=ref(a.out/'client/verify-exit.json'),
        collector=ref(a.out/'collector.json'),scope='One calibration trial; relative overhead requires three explicit pairs and complete raw archive audits'))
    print(json.dumps(dict(passed=True,product=a.product,mode=a.mode,makespan_seconds=result['coordinator_makespan_seconds'],
        collector_name=collector_name,collector_out=collector_out,out=str(a.out))))


if __name__=='__main__':
    main()
