#!/usr/bin/env python3
"""One C4 trial or admitted formal cell; the outer batch owns canonical locks."""
import argparse
from datetime import datetime,timezone
import importlib.util
import json
from pathlib import Path
import sys
import tarfile
import time

REPO=Path(__file__).resolve().parents[4];sys.path.insert(0,str(REPO))
loader=importlib.util.spec_from_file_location('c4_common_runner',Path(__file__).with_name('run-c1.py'))
common=importlib.util.module_from_spec(loader);loader.loader.exec_module(common)
from bench.runtime.common import digest,load,pinned,write_new
from bench.runtime import cohort,fleet,pilot_context
from bench.runtime.pilot_pools import pool_of,alias,fleet_node
from bench import suite_state,suite_validate,suite_report,archive_check,experiment_step
from bench.suite_plan import check_plan


def ref(path):return dict(path=str(path),sha256=digest(path))


class Fork(common.Cell):
    def __init__(self,context):
        self.context=Path(context);self.s=load(context);self.out=Path(self.s['out']);self.control=Path(self.s['control'])
        self.runtime=pilot_context.select(self.control,self.s.get('runtime_control'))
        self.plan=check_plan(load(self.control/'suite/plan.json'))
        self.cell=next(c for c in self.plan['cells'] if c['id']==self.s['cell_id'])
        self.id=self.cell['id'];self.pool=pool_of(self.cell);self.kind='fork';self.arm=self.cell['arm_id']
        self.product=self.cell['arm']['product'];self.fleet=load(self.runtime/'fleet.json')
        if (self.cell['workload']!='fork' or self.arm not in ('native','juicefs') or
                self.cell['params']['agents']!=1 or self.cell['params']['children']!=1):
            raise ValueError('Explicit one-child C4 baseline only')
        self.purpose=self.s.get('purpose','experiment');self.tag=self.s['attempt']
        import re
        if not re.fullmatch('[a-z0-9-]{3,48}',self.tag):raise ValueError('Explicit unique trial path required')
        self.base='/srv/ax-client/q/'+self.tag;self.main='/srv/ax-client/m/'+self.tag
        self.verify=self.main+'-v';self.mount_dirs=[self.main,self.verify];self.asset=self.s['asset']
        self.qualification=Path(self.s['qualification'])/self.arm

    def check_sources(self):
        self.check_asset_binary()
        for source in (self.s['node_tool'],self.s['fork']['tool'],self.s['fork']['preparation']):
            if self.remote('client',['sha256sum',source['path']]).split()[0]!=source['sha256']:
                raise ValueError('Original C4 worker/tool/preparation changed')
        for role in ('client','metadata','objects'):
            tool=fleet_node(self.fleet,self.pool,role)['repo']+'/experiments/unified-suite/pilot-aliyun/scripts/'+self.s['observer_tool_name']
            if self.remote(role,['sha256sum',tool]).split()[0]!=self.s['observer_tool_sha256']:
                raise ValueError('Actual observer builder changed')

    def fork_helper(self,action,**fields):
        source=self.s['fork']['preparation']
        argv=['python3',self.s['fork']['tool']['path'],action,'--asset',self.asset,'--cell',self.id,
            '--child-name',self.tag,'--preparation',source['path'],'--preparation-sha256',source['sha256']]
        for key,value in fields.items():argv+=['--'+key.replace('_','-'),str(value)]
        return self.remote('client',argv)

    def execute(self,stage):
        if stage=='observe-start' and self.purpose=='observer-calibration-off':
            write_new(self.out/'observers.json',[]);return
        if stage=='create':
            self.fork_helper('draft',out=self.base+'/child-draft');return
        if stage=='mount':
            self.helper('draft-mount',cell=self.id,purpose=self.purpose,out=self.base+'/main')
            self.helper('start',join_slot=True,spec=self.base+'/main/mount.reviewed.json',out=self.main)
            self.fork_helper('worker',out='/srv/ax-client/w/'+self.tag,mount_spec=self.base+'/main/mount.reviewed.json',
                mount_run=self.main,purpose=self.purpose);return
        if stage=='measure':
            path='/srv/ax-client/w/'+self.tag+'/worker.reviewed.json'
            worker=dict(path=path,sha256=self.remote('client',['sha256sum',path]).split()[0])
            s=dict(schema='experiment-cohort-v1',reviewed=True,purpose=self.purpose,provider='ssh',agents=1,
                inventory=ref(self.runtime/'fleet.json'),metric_contract=ref(self.qualification/'metrics.json'),
                workload_contract=ref(self.qualification/'workload.json'),allocation_qualification=ref(self.runtime/'allocation.json'),
                hardware_qualification=ref(Path(self.s['qualification'])/'hardware.json'),
                input_qualification=ref(self.qualification/'input.json'),filesystem_qualification=ref(self.qualification/'filesystem.json'),
                workers=[dict(id='a01',node_id=alias(self.pool,'client'),slot_id=alias(self.pool,'client'),
                    peer=load(self.runtime/'probes'/(alias(self.pool,'client')+'-slot')/'peer.json'),spec=worker,
                    output='/srv/ax-client/j/'+self.tag)],observers=self.observers(),ready_timeout_seconds=120)
            if self.purpose=='experiment':s['observer_overhead']=self.s['overhead']
            write_new(self.out/'cohort.json',s)
            # Synchronous observer ACKs precede cohort go; terminal ACKs precede
            # measure-end and the separate drain stage. No timestamp guessing.
            self.mark('measure-start');result=cohort.run(ref(self.out/'cohort.json'),self.out/'cohort')
            if not result['passed']:raise ValueError('Original C4 cohort or telemetry failed')
            self.mark('measure-end');return
        if stage=='drain':
            start=time.monotonic_ns();self.helper('debt',cell=self.id,out=self.base+'/debt');end=time.monotonic_ns()
            write_new(self.out/'drain-timing.json',dict(passed=True,cell_id=self.id,start_ns=start,end_ns=end,
                seconds=(end-start)/1e9,scope='Coordinator wall interval including native two-sample debt proof and SSH.'))
            self.mark('drain-end');return
        if stage=='verify':
            self.helper('draft-mount',cell=self.id,purpose=self.purpose,out=self.base+'/verify-mount')
            self.helper('start',join_slot=True,spec=self.base+'/verify-mount/mount.reviewed.json',out=self.verify)
            self.fork_helper('verify',out=self.base+'/verify')
            self.helper('stop',mount_run=self.verify,out=self.base+'/verify-exit.json')
            self.mark('verify-end');return
        if stage=='archive':self.archive();return
        super().execute(stage)

    def archive(self):
        # Enumerate only the two mounts that this trial actually started.
        archives=[];closures=[];observers=[];observed={o['id'] for o in self.observers()}
        roots=dict(client='/srv/ax-client',metadata='/srv/ax-metadata',objects='/srv/ax-objects-0')
        for role,root in roots.items():
            has_observer=alias(self.pool,role) in observed
            if role!='client' and not has_observer:continue
            dirs=(['o/'+self.tag,'q/'+self.tag+'-observer'] if has_observer else [])
            if role=='client':dirs+=['q/'+self.tag,'m/'+self.tag,'m/'+self.tag+'-v','w/'+self.tag,'j/'+self.tag]
            variables=dict(ROOT=root,CODE=fleet_node(self.fleet,self.pool,role)['repo'],TAG=self.tag,CELL=self.id,
                ROLE=role,DIRS=dirs,HAS_OBSERVER=has_observer,ASSET=self.asset)
            script='''import json,pathlib,sys,shutil
root=pathlib.Path(ROOT);sys.path.insert(0,CODE)
from bench.runtime.common import alive,load,write_new,digest
from bench.suite_support import closure
from bench.archive_check import pack
identities=[]
if HAS_OBSERVER:identities.append(load(root/'o'/TAG/'ready.json')['process'])
if ROLE=='client':
 for suffix in ('','-v'):
  state=root/'m'/(TAG+suffix);p=load(state/'process.json');identities.extend([p['process'],p['supervisor']])
  end=load(state/'stopped.json')
  if not end['passed'] or end['exit_code']!=0 or not end['mount_absent']:raise ValueError('Original C4 mount not normally closed')
 job=root/'j'/TAG
 identities.append(load(job/'ready.json')['process'])
 identities.append(load(job/'job-0000/process.json')['process'])
 creator=load(root/'w'/TAG/'measure/create-process.json')
 if creator['identity'] is not None:identities.append(creator['identity'])
 if load(root/'w'/TAG/'measure/create-exit.json')['exit_code']!=0:raise ValueError('Native creation process did not exit normally')
 finished=load(job/'job-0000/finished.json')
 if not finished['passed'] or finished['remaining_process_group']:raise ValueError('C4 leaf descendants remain')
 if pathlib.Path('/sys/fs/cgroup/ax-slot-1/cgroup.procs').read_text().strip():raise ValueError('Slot remains occupied')
 debt=[]
 for i in (0,1):
  p=root/'q'/TAG/'debt'/('sample-'+str(i)+'.json');v=load(p)
  debt.append(dict(evidence=dict(path=str(p),sha256=digest(p)),equals={'passed':True,'cell_id':CELL},
    pending_bytes_field='pending_upload_bytes',pending_operations_field='pending_upload_operations',observed_at=v['at']))
 request=dict(schema='suite-closure-request-v1',cell_id=CELL,processes=identities,
    mount_points=[load(pathlib.Path(ASSET)/'asset.json')['mount_point']],debt_samples=debt)
 p=root/'q'/TAG/'closure-request.json';write_new(p,request);result=closure(p)
 result['passed']=result['all_exited'] and result['all_unmounted']
else:result=dict(passed=not any(alive(p) for p in identities),cell_id=CELL,
 all_exited=not any(alive(p) for p in identities),all_unmounted=True,processes=identities)
if not result['passed']:raise ValueError('Original C4 processes or mounts remain')
q=root/'q'/(TAG+'-closure');q.mkdir();write_new(q/'result.json',result);DIRS.append('q/'+TAG+'-closure')
staging=root/'evidence'/TAG;staging.mkdir(parents=True);names=[]
for directory in DIRS:
 for p in sorted((root/directory).rglob('*')):
  relative=p.relative_to(root)
  if 'cache' in p.relative_to(root/directory).parts:continue
  if p.is_symlink():raise ValueError('Evidence symlink')
  if p.is_file():
   target=staging/relative;target.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(p,target);names.append(relative.as_posix())
out=root/'archives';out.mkdir(exist_ok=True);archive=pack(staging,names,out/(TAG+'-'+ROLE+'.tar.gz'),CELL,ROLE)
print(json.dumps(dict(closure=result,archive=archive)))'''
            response=json.loads(self.python(role,'\n'.join(k+'='+repr(v) for k,v in variables.items())+'\n'+script))
            closures.append(response['closure']);path=self.get(role,response['archive']['archive'],'archives/'+role+'.tar.gz')
            checked=archive_check.verify(path,response['archive']['sha256']);write_new(self.out/'archives'/(role+'-check.json'),checked)
            archives.append(ref(path));destination=self.out/'raw'/role;destination.mkdir(parents=True)
            with tarfile.open(path,'r:gz') as source:
                for member in source:
                    if member.name==archive_check.MANIFEST:continue
                    target=destination/member.name;target.parent.mkdir(parents=True,exist_ok=True)
                    with source.extractfile(member) as incoming,target.open('xb') as outgoing:
                        import shutil
                        shutil.copyfileobj(incoming,outgoing)
            if has_observer:
                from bench.runtime.observe import audit
                audit(destination/'o'/self.tag,30,self.cell['observation_marks'])
                observers.append(dict(id=alias(self.pool,role),directory=str(destination/'o'/self.tag),
                                      max_gap_seconds=30,marks=self.cell['observation_marks']))
        q=self.out/'raw/client/q'/self.tag;work=self.out/'raw/client/w'/self.tag/'measure'
        measured=load(work/'result.json')
        packet=dict(cell_id=self.id,results=[dict(agent_index=0,evidence=ref(work/'result.json'),
            clock=ref(work/'clock.json'),clock_source=measured['clock'])])
        write_new(self.out/'work-packet.json',packet)
        write_new(self.out/'work-check.json',suite_validate.work(self.plan,self.id,packet))
        verified=load(q/'verify/result.json');normal=[load(q/n) for n in ('exit.json','verify-exit.json')]
        proof=dict(passed=verified['passed'] and all(v['passed'] and v['exit_code']==0 and v['mount_absent'] for v in normal),
            cell_id=self.id,cold_child=ref(q/'verify/result.json'),parent_unchanged=ref(q/'verify/parent.json'),
            normal_exits=[ref(q/n) for n in ('exit.json','verify-exit.json')])
        write_new(self.out/'verification.json',proof)
        client_closure=next(c for c in closures if 'debt_samples' in c)
        closed=dict(cell_id=self.id,passed=all(c['passed'] for c in closures),all_exited=all(c['all_exited'] for c in closures),
            all_unmounted=all(c['all_unmounted'] for c in closures),
            pending_upload_bytes=client_closure['pending_upload_bytes'],pending_upload_operations=client_closure['pending_upload_operations'],nodes=closures)
        write_new(self.out/'closure.json',closed);write_new(self.out/'archives.json',archives)
        if self.purpose!='experiment':return
        metrics={name:dict(evidence=ref(work/'result.json'),field=name,unit='seconds',direction='lower') for name in
            ('create_to_first_read_seconds','create_command_seconds','first_read_seconds','creation_fsync_seconds','mutation_fsync_seconds','wall_seconds')}
        metrics['drain_seconds']=dict(evidence=ref(self.out/'drain-timing.json'),field='seconds',unit='seconds',direction='lower')
        metrics['cold_verification_seconds']=dict(evidence=ref(q/'verify/result.json'),field='cold_verification_seconds',unit='seconds',direction='lower')
        checks={k:dict(evidence=ref(self.out/'verification.json'),equals={'passed':True,'cell_id':self.id}) for k in
            ('correctness','isolation','remote_readback','real_exit','cache_policy')}
        checks.update(work=dict(evidence=ref(self.out/'work-check.json'),equals={'passed':True}),
            drain=dict(evidence=ref(self.out/'closure.json'),equals={'passed':True,'cell_id':self.id}),
            observer_overhead=dict(evidence=self.s['overhead'],equals={'passed':True}))
        write_new(self.out/'sample.json',dict(schema='suite-sample-v1',plan_sha256=self.plan['plan_sha256'],cell_id=self.id,
            uninterrupted=True,recovered=False,checks=checks,required_observer_ids=[o['id'] for o in observers],
            required_marks=self.cell['observation_marks'],observers=observers,metrics=metrics,
            timings_seconds={'native_measure':measured['wall_seconds']},limitations=['One 100k child; no scaling or physical-host-move claim.']))


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--context',type=Path,required=True);p.add_argument('--stage')
    a=p.parse_args();c=Fork(a.context)
    if a.stage:c.execute(a.stage);return
    lease=load(c.out/'lease.json')
    if lease['already_claimed'] or lease['cell']!=c.cell:raise ValueError('Only a newly claimed explicit C4 cell may run')
    c.check_sources()
    for stage in c.cell['stages']:
        step=dict(schema='experiment-step-v1',reviewed=True,cell_id=c.id,stage=stage,
            argv=[sys.executable,str(Path(__file__).resolve()),'--context',str(a.context),'--stage',stage],
            cwd=str(REPO),executable_sha256=digest(sys.executable),inputs=[ref(a.context),ref(__file__)],inherit_environment=[])
        path=c.out/'steps'/(stage+'.json');path.parent.mkdir(exist_ok=True);write_new(path,step)
        if suite_state.run_stage(c.control/'state.sqlite',lease,stage,ref(path),c.out/'stages'/stage):
            raise RuntimeError('Original C4 stage failed; keep its reservation')
    acceptance=suite_report.sample(ref(c.out/'sample.json'),c.plan,c.cell)
    write_new(c.out/'acceptance.json',acceptance)
    if not acceptance['accepted']:raise ValueError('C4 sample was not accepted: '+str(acceptance['reasons']))
    common.close_cell(c,'complete')


if __name__=='__main__':main()
