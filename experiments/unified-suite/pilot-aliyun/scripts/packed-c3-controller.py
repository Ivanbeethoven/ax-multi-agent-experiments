#!/usr/bin/env python3
"""One Packed C3 controller route using the original cohort, state and archive protocols."""
import argparse
from copy import deepcopy
from datetime import datetime,timezone
import hashlib
import json
import math
from pathlib import Path
import re
import runpy
import subprocess
import sys
import tarfile

REPO=Path(__file__).resolve().parents[4]
sys.path.insert(0,str(REPO))
from bench.runtime.common import digest,load,pinned,write_new
from bench.runtime import cohort,fleet,pilot_context
from bench.runtime.pilot_pools import pool_of,alias,fleet_node,resource_guard,active_conflicts,cross_pool_proof
from bench import suite_state as state,suite_validate,archive_check,experiment_step
from bench.suite_mount import matches


def ref(path):return dict(path=str(Path(path).resolve(strict=True)),sha256=digest(path))
def utc():return datetime.now(timezone.utc).isoformat()
ROOTS=dict(client='/srv/ax-client',metadata='/srv/ax-metadata',objects='/srv/ax-objects-0')
ENTITY_ALWAYS=['brewfs_fuse_op_total','brewfs_mount_info','brewfs_cache_hits_total','brewfs_cache_misses_total',
    'brewfs_entity_read_cache_capacity_bytes','brewfs_entity_read_cache_retained_bytes','brewfs_entity_read_cache_evictions_total']
ENTITY_WORKLOAD=['brewfs_fuse_op_total','brewfs_fuse_op_latency_seconds_count','brewfs_object_get_total','brewfs_object_get_bytes_total',
    'brewfs_object_put_total','brewfs_object_put_bytes_total','brewfs_cache_hits_total','brewfs_cache_misses_total',
    'brewfs_entity_read_cache_capacity_bytes','brewfs_entity_read_cache_retained_bytes','brewfs_entity_read_cache_evictions_total']


class PackedMethods:
    def __init__(self,context):
        self.s=load(context);self.context=Path(context);self.control=Path(self.s['control']);self.out=Path(self.s['out'])
        self.runtime=pilot_context.select(self.control,self.s.get('runtime_control'));self.fleet=load(self.runtime/'fleet.json')
        self.plan=load(self.control/'suite/plan.json');self.cell=next(c for c in self.plan['cells'] if c['id']==self.s['cell_id'])
        self.pool=pool_of(self.cell);self.id=self.cell['id'];self.arm='packed';self.product='brewfs';self.kind='smallfiles'
        if (self.cell['workload'],self.cell['arm_id'],self.cell['arm']['engine'])!=('smallfiles','packed','entity-workspace'):
            raise ValueError('Original Packed C3 cell required')
        if self.cell['params']['agents']!=1 or self.cell['params']['workspaces']!=1:raise ValueError('One complete workspace required')
        self.purpose=self.s.get('purpose','experiment');self.on=self.purpose!='observer-calibration-off'
        tag_source=str(self.control.resolve())+'\0'+digest(self.control/'suite/plan.json')+'\0'+self.id+'\0'+self.s['attempt']
        self.tag='pc3-'+self.pool+'-'+hashlib.sha256(tag_source.encode()).hexdigest()[:18]
        self.base=ROOTS['client']+'/q/'+self.tag;self.main=ROOTS['client']+'/m/'+self.tag
        self.asset=ROOTS['client']+'/a/'+self.tag;self.qualification=Path(self.s['qualification'])/'packed'
        self.binding=load(self.qualification/'binding.json') if (self.qualification/'binding.json').exists() else None
        if self.purpose=='experiment' and self.s.get('calibration_verification'):
            raise ValueError('Formal cells always require original full cold verification')

    def live_calibration(self):
        return self.purpose!='experiment' and getattr(self,'s',{}).get('calibration_verification')=='live-touched-v1'

    def calibration_node(self,action,**fields):
        b=self.binding
        common=dict(base_asset=b['base_asset'],asset=self.asset,plan=b['plan_remote'],cell=self.id,manifest=b['manifest_remote'],
                    entity_tool=self.s['entity_tool']['path'],entity_tool_sha256=self.s['entity_tool']['sha256'],
                    probe=b['tools']['probe']['path'],probe_sha256=b['tools']['probe']['sha256'])
        argv=['python3',self.s['calibration_node']['path'],action]
        for key,value in {**common,**fields}.items():
            if isinstance(value,dict):value=json.dumps(value,separators=(',',':'))
            argv+=['--'+key.replace('_','-'),str(value)]
        return self.remote('client',argv)

    def entity(self,action,**fields):
        argv=['python3',self.s['entity_tool']['path'],action]
        for key,value in fields.items():argv+=['--'+key.replace('_','-'),str(value)]
        return self.remote('client',argv)

    def check_inputs(self):
        b=self.binding
        if b is None:raise ValueError('Actual packed qualification binding required')
        base=load(pinned(b['asset_manifest']))
        if (base.get('pool','pool-a')!=self.pool or base['plan']['sha256']!=digest(self.control/'suite/plan.json') or
                base['arm']!='packed' or base['product']!='brewfs'):raise ValueError('Immutable base belongs to another pool or plan')
        matches(base['config'],self.cell['client_settings'])
        build=load(self.control/'provenance/build-manifest.json')
        for name in ('brewfs','entity_metadata_probe','smallfiles'):
            wanted=next(row['sha256'] for row in build['files'] if row['file']==name)
            pin=base['filesystem_binary'] if name=='brewfs' else b['tools']['probe' if name=='entity_metadata_probe' else 'binary']
            if pin['sha256']!=wanted:raise ValueError('Bound binary differs from frozen build')
        pins=[self.s['node_tool'],self.s['entity_tool'],base['filesystem_binary'],*b['tools'].values()]
        if self.live_calibration():
            pins.append(self.s['calibration_node'])
            full=load(pinned(self.s['full_cold_qualification']))
            if full.get('passed') is not True or full.get('cell_id')!=self.id or self.s['full_cold_qualification']!=ref(self.qualification/'filesystem.json'):
                raise ValueError('Actual retained complete cold qualification required for calibration')
            for name in ('cold_child','cold_parent'):
                proof=load(pinned(full['proofs'][name]))
                if proof.get('passed') is not True or proof.get('files')!=self.cell['params']['files']:raise ValueError('Incomplete reused original cold proof')
        for pin in pins:
            if self.remote('client',['sha256sum',pin['path']]).split()[0]!=pin['sha256']:raise ValueError('Deployed packed input changed')
        if self.remote('client',['sha256sum',b['base_asset']+'/asset.json']).split()[0]!=b['asset_manifest']['sha256']:
            raise ValueError('Original immutable base manifest changed')

    def start_observers(self):
        observers=[]
        if self.on:
            for role,root in ROOTS.items():
                node=fleet_node(self.fleet,self.pool,role);tool=self.s['observer_tools'][role]['path']
                if self.remote(role,['sha256sum',tool]).split()[0]!=self.s['observer_tools'][role]['sha256']:raise ValueError('Observer builder changed')
                argv=['python3',tool,'--role',role,'--pool',self.pool,'--hostname',node['hostname'],'--product','brewfs',
                    '--metric-contract-sha256',digest(self.qualification/'metrics.json'),'--collector-name',self.s['collector_name'],'--out',root+'/q/'+self.tag+'-observer']
                if role=='client':argv+=['--entity','--mount-directory',self.main]
                if role=='objects':
                    compose=load(self.qualification/'metrics.json')['observer_settings']['rustfs_compose']
                    argv+=['--rustfs-compose',compose['path'],'--rustfs-compose-sha256',compose['sha256']]
                spec=json.loads(self.remote(role,argv));peer=ref(self.runtime/'probes'/alias(self.pool,role)/'peer.json');directory=root+'/o/'+self.tag
                fleet.call(peer,'observe-start',directory,self.out/'observer-start'/role,spec)
                observers.append(dict(id=alias(self.pool,role),node_id=alias(self.pool,role),peer=load(peer['path']),directory=directory,spec=spec,
                    required_live_targets={'client':['filesystem'],'metadata':['redis'],'objects':['rustfs','collector']}[role]))
        write_new(self.out/'observers.json',observers)

    def execute(self,stage):
        if stage.endswith('-w01'):stage=stage[:-4]
        b=self.binding
        if stage=='observe-start':self.start_observers()
        elif stage=='create':
            self.entity('fresh',base_asset=b['base_asset'],asset=self.asset,plan=b['plan_remote'],cell=self.id,out=self.base+'/create')
            if self.live_calibration():self.calibration_node('parent-snapshot',out=self.base+'/parent-before')
        elif stage=='mount':
            # Both calibration arms retain the native metrics endpoint and counter code.
            self.helper('draft-mount',cell=self.id,purpose='experiment',out=self.base+'/main')
            self.helper('start',join_slot=True,spec=self.base+'/main/mount.reviewed.json',out=self.main)
            self.helper('worker',cell=self.id,purpose=self.purpose,root_at_mount=True,workspace=1,
                spec=self.base+'/main/mount.reviewed.json',mount_run=self.main,out=ROOTS['client']+'/w/'+self.tag)
        elif stage=='measure':
            worker=ROOTS['client']+'/w/'+self.tag+'/worker.reviewed.json';sha=self.remote('client',['sha256sum',worker]).split()[0]
            spec=dict(schema='experiment-cohort-v1',reviewed=True,purpose=self.purpose,provider='ssh',agents=1,
                inventory=ref(self.runtime/'fleet.json'),metric_contract=ref(self.qualification/'metrics.json'),workload_contract=ref(self.qualification/'workload.json'),
                allocation_qualification=ref(self.runtime/'allocation.json'),hardware_qualification=ref(self.qualification.parent/'hardware.json'),
                input_qualification=ref(self.qualification/'input.json'),filesystem_qualification=ref(self.qualification/'filesystem.json'),
                workers=[dict(id='a01',node_id=alias(self.pool,'client'),slot_id=alias(self.pool,'client'),peer=load(self.runtime/'probes'/(alias(self.pool,'client')+'-slot')/'peer.json'),
                    spec=dict(path=worker,sha256=sha),output=ROOTS['client']+'/j/'+self.tag)],
                observers=self.observers(),ready_timeout_seconds=120,review=dict(reviewer='pilot-operator',note='Original fixed eight phases on one fresh Packed entity namespace; no retry.'))
            if self.purpose=='experiment':spec['observer_overhead']=self.s['overhead']
            write_new(self.out/'cohort.json',spec);self.mark('measure-start')
            if cohort.run(ref(self.out/'cohort.json'),self.out/'cohort')['passed'] is not True:raise ValueError('Work or native telemetry failed')
            self.mark('measure-end')
        elif stage=='drain':
            if self.live_calibration():
                self.calibration_node('live-check',source=b['source'],state_file=ROOTS['client']+'/w/'+self.tag+'/draft/workload/expected-touched.tsv',
                    smallfiles=b['tools']['binary']['path'],smallfiles_sha256=b['tools']['binary']['sha256'],mount_run=self.main,out=self.base+'/live-check')
            self.entity('debt',asset=self.asset,plan=b['plan_remote'],cell=self.id,manifest=b['manifest_remote'],
                probe=b['tools']['probe']['path'],probe_sha256=b['tools']['probe']['sha256'],out=self.base+'/debt')
            self.mark('drain-end')
        elif stage=='unmount':self.helper('stop',mount_run=self.main,out=self.base+'/exit.json')
        elif stage=='verify':
            stop=self.base+'/exit.json';sha=self.remote('client',['sha256sum',stop]).split()[0]
            if self.live_calibration():
                def remote_ref(path):return dict(path=path,sha256=self.remote('client',['sha256sum',path]).split()[0])
                self.calibration_node('finish',normal_stop=stop,normal_stop_sha256=sha,
                    live_check=remote_ref(self.base+'/live-check/result.json'),parent_before=remote_ref(self.base+'/parent-before/result.json'),out=self.base+'/verify')
                self.mark('verify-end');return
            fields=dict(asset=self.asset,base_asset=b['base_asset'],plan=b['plan_remote'],cell=self.id,manifest=b['manifest_remote'],
                source=b['source'],source_proof=b['source_proof_remote'],state_file=ROOTS['client']+'/w/'+self.tag+'/draft/workload/expected-touched.tsv',
                normal_stop=stop,normal_stop_sha256=sha,private_object_env=b['private_object_env'],out=self.base+'/verify')
            for name in ('reader','verifier','oracle'):fields[name]=b['tools'][name]['path'];fields[name+'_sha256']=b['tools'][name]['sha256']
            self.entity('cold-verify',**fields);self.mark('verify-end')
        elif stage=='observe-stop':
            for observer in self.observers():
                peer=ref(self.runtime/'probes'/observer['id']/'peer.json');fleet.call(peer,'observe-stop',observer['directory'],self.out/'observer-stop'/observer['id'])
                role=observer['id'].removeprefix(self.pool+'-');repo=fleet_node(self.fleet,self.pool,role)['repo']
                code='import sys,json;sys.path.insert(0,'+repr(repo)+');from bench.runtime.observe import audit;print(json.dumps(audit('+repr(observer['directory'])+',30,'+repr(self.cell['observation_marks'])+')))'
                audit=json.loads(self.python(role,code))
                if audit['passed'] is not True:raise ValueError('Raw metric samples failed audit')
                write_new(self.out/'observer-stop'/observer['id']/'raw-audit.json',audit)
        elif stage=='archive':self.archive()
        else:raise ValueError('Unknown original C3 stage')

    def archive(self):
        archives=[];closures=[];observers=[]
        for role,root in ROOTS.items():
            code='''import sys,json,pathlib,shutil
sys.path.insert(0,REPO)
from bench.runtime.common import digest,alive,write_new
from bench.suite_support import closure
from bench.archive_check import pack
root=pathlib.Path(ROOT);q=root/'q'/TAG
identities=[];dirs=[]
if ON:
 observer=root/'o'/TAG;identities.append(json.loads((observer/'ready.json').read_text())['process'])
 dirs+=['o/'+TAG,'q/'+TAG+'-observer']
if ROLE=='client':
 mount=root/'m'/TAG;native=json.loads((mount/'process.json').read_text());identities.extend([native['process'],native['supervisor']])
 stopped=json.loads((mount/'stopped.json').read_text())
 if stopped.get('passed') is not True or stopped.get('exit_code')!=0 or stopped.get('mount_absent') is not True:raise ValueError('Original mount not normally stopped')
 if pathlib.Path('/sys/fs/cgroup/ax-slot-1/cgroup.procs').read_text().strip():raise ValueError('Agent slot not empty')
 for p in [*q.rglob('process.json'),*(root/'j'/TAG).rglob('process.json')]:
  doc=json.loads(p.read_text())
  for key in ('process','supervisor'):
   identity=doc.get(key)
   if isinstance(identity,dict) and all(k in identity for k in ('pid','boot_id','start_ticks')):identities.append(identity)
 debt=[]
 for i in (0,1):
  path=q/'debt'/('sample-'+str(i)+'.json');v=json.loads(path.read_text());raw=pathlib.Path(v['native_status']['path'])
  if digest(raw)!=v['native_status']['sha256']:raise ValueError('Native status bytes changed')
  status=json.loads(raw.read_text())
  for native,flat in [('bytes','pending_upload_bytes'),('objects','pending_upload_operations')]:
   value=status['debt'][native]
   if type(value) not in (int,float) or value!=0 or type(v[flat]) is not int or v[flat]!=int(value):raise ValueError('Native debt extraction differs')
  debt.append(dict(evidence=dict(path=str(path),sha256=digest(path)),equals={'passed':True,'cell_id':CELL},pending_bytes_field='pending_upload_bytes',pending_operations_field='pending_upload_operations',observed_at=v['at']))
 request=dict(schema='suite-closure-request-v1',cell_id=CELL,processes=identities,mount_points=[ASSET+'/mnt'],debt_samples=debt)
 request_path=q/'closure-request.json';write_new(request_path,request);closed=closure(request_path);closed['passed']=closed['all_exited'] and closed['all_unmounted']
 dirs+=['q/'+TAG,'m/'+TAG,'w/'+TAG,'j/'+TAG,'a/'+TAG]
else:
 closed=dict(passed=not any(alive(i) for i in identities),cell_id=CELL,all_exited=not any(alive(i) for i in identities),all_unmounted=True,processes=identities,scope='Only owned external observers; long-lived storage services remain running')
if closed['passed'] is not True:raise ValueError('Owned process or mount remains live')
directory=root/'q'/(TAG+'-closure');directory.mkdir(parents=True,exist_ok=False);write_new(directory/'result.json',closed);dirs.append('q/'+TAG+'-closure')
stage=root/'evidence'/TAG;stage.mkdir(parents=True,exist_ok=False);files=[]
for name in dirs:
 for p in sorted((root/name).rglob('*')):
  if 'cache' in p.relative_to(root/name).parts:continue
  if p.is_symlink():raise ValueError('Unexpected evidence symlink')
  if not p.is_file():continue
  relative=p.relative_to(root);target=stage/relative;target.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(p,target);files.append(relative.as_posix())
destination=root/'archives';destination.mkdir(exist_ok=True)
archive=pack(stage,files,destination/(TAG+'-'+ROLE+'.tar.gz'),CELL,ROLE)
write_new(directory/'archive.json',archive);print(json.dumps(dict(closure=closed,archive=archive)))
'''
            values=dict(REPO=fleet_node(self.fleet,self.pool,role)['repo'],ROOT=root,TAG=self.tag,ROLE=role,ON=self.on,CELL=self.id,ASSET=self.asset)
            response=json.loads(self.python(role,'\n'.join(key+'='+repr(value) for key,value in values.items())+'\n'+code));closures.append(response['closure'])
            archive=self.get(role,response['archive']['archive'],'archives/'+role+'.tar.gz');checked=archive_check.verify(archive,response['archive']['sha256'])
            write_new(self.out/'archives'/(role+'-check.json'),checked);archives.append(ref(archive));destination=self.out/'raw'/role;destination.mkdir(parents=True)
            with tarfile.open(archive,'r:gz') as source:
                for member in source:
                    if member.name==archive_check.MANIFEST:continue
                    target=destination/member.name;target.parent.mkdir(parents=True,exist_ok=True)
                    with source.extractfile(member) as incoming,target.open('xb') as outgoing:
                        import shutil
                        shutil.copyfileobj(incoming,outgoing)
            if self.on:
                observer=destination/'o'/self.tag
                from bench.runtime.observe import audit
                if audit(observer,30,self.cell['observation_marks'])['passed'] is not True:raise ValueError('Archived raw metrics failed audit')
                observers.append(dict(id=alias(self.pool,role),directory=str(observer),max_gap_seconds=30,marks=self.cell['observation_marks']))
        q=self.out/'raw/client/q'/self.tag;work=self.out/'raw/client/w'/self.tag/'draft/workload'
        if self.on:
            from bench.runtime.observe import prometheus
            observer=self.out/'raw/client/o'/self.tag;end=load(observer/'finished.json')
            marks=[mark for mark in end['marks'] if mark['label']=='measure-end-w01']
            if len(marks)!=1:raise ValueError('Original single measured-end mark required')
            original=observer/f"sample-{marks[0]['sample_index']:06}"/'filesystem.txt'
            values=prometheus(original.read_text());names={name.split('{',1)[0] for name in values}
            if (not set(ENTITY_WORKLOAD).issubset(names) or any(not math.isfinite(value) for name,value in values.items() if name.split('{',1)[0] in ENTITY_WORKLOAD)):
                raise ValueError('Native entity workload counters missing or invalid after original full work')
            write_new(self.out/'native-client-series.json',dict(passed=True,cell_id=self.id,source=ref(original),required_names=ENTITY_WORKLOAD,
                scope='Names in original measured-end raw sample; raw native units/labels and all other samples are retained. No missing series filled.'))
        packet=dict(cell_id=self.id,results=[dict(agent_index=0,workspace_index=0,evidence=ref(work/'result.json'),raw_logs=[ref(work/f'worker-{i}.csv') for i in range(self.cell['params']['workers'])])])
        write_new(self.out/'work-packet.json',packet);write_new(self.out/'work-check.json',suite_validate.work(self.plan,self.id,packet))
        verification=load(q/'verify/result.json');stop=load(q/'exit.json')
        if verification['passed'] is not True or stop['passed'] is not True or not all(c['passed'] for c in closures):raise ValueError('Original readback or closure failed')
        if self.live_calibration():
            if verification.get('verification_mode')!='live-touched-v1' or verification.get('full_namespace_cold') is not False:raise ValueError('Explicit live-only calibration proof required')
            proof=dict(passed=True,cell_id=self.id,verification_mode='live-touched-v1',full_namespace_cold=False,
                original_route=ref(q/'verify/result.json'),live_check=ref(q/'live-check/result.json'),
                parent_before=ref(q/'parent-before/result.json'),parent_after=ref(q/'verify/parent-after/result.json'),
                child_status=ref(q/'verify/child-status/result.json'),normal_exit=ref(q/'exit.json'),
                full_cold_qualification=self.s['full_cold_qualification'],namespace=verification['namespace'],volume=verification['volume'])
        else:
            proof=dict(passed=True,cell_id=self.id,cold_child=ref(q/'verify/child-cold/verify.json'),parent_unchanged=ref(q/'verify/parent-cold/verify.json'),
                original_route=ref(q/'verify/result.json'),normal_exit=ref(q/'exit.json'),cache_scope='One fresh entity namespace/cache; no FUSE reopen; server/page caches remain observed')
        write_new(self.out/'verification.json',proof)
        client=closures[0];closed=dict(cell_id=self.id,passed=all(c['passed'] for c in closures),all_exited=all(c['all_exited'] for c in closures),all_unmounted=all(c['all_unmounted'] for c in closures),
            pending_upload_bytes=client['pending_upload_bytes'],pending_upload_operations=client['pending_upload_operations'],nodes=closures)
        write_new(self.out/'closure.json',closed);write_new(self.out/'archives.json',archives)
        values=load(work/'result.json');metrics={};phase_metrics={}
        for phase in values['phases']:
            for key,unit in [('wall_seconds','seconds'),('io_p99_ns','nanoseconds'),('application_p99_ns','nanoseconds')]:
                phase_metrics[phase['phase']+'_'+key]=dict(value=phase[key],unit=unit,direction='lower')
        write_new(self.out/'io-metrics.json',dict(passed=True,cell_id=self.id,source=ref(work/'result.json'),metrics=phase_metrics,
            application_counts={key:values[key] for key in ('actual_read_bytes','actual_write_bytes','verified_reads','writes')}))
        for name,metric in phase_metrics.items():metrics[name]=dict(evidence=ref(self.out/'io-metrics.json'),field='metrics.'+name+'.value',unit=metric['unit'],direction=metric['direction'])
        metrics['task_wall']=dict(evidence=ref(self.out/'cohort/result.json'),field='coordinator_makespan_seconds',unit='seconds',direction='lower')
        if self.purpose=='experiment':
            checks={key:dict(evidence=ref(self.out/'verification.json'),equals={'passed':True,'cell_id':self.id}) for key in ('correctness','isolation','remote_readback','real_exit','cache_policy')}
            checks.update(work=dict(evidence=ref(self.out/'work-check.json'),equals={'passed':True}),drain=dict(evidence=ref(self.out/'closure.json'),equals={'passed':True,'cell_id':self.id}),observer_overhead=dict(evidence=self.s['overhead'],equals={'passed':True}))
            write_new(self.out/'sample.json',dict(schema='suite-sample-v1',plan_sha256=self.plan['plan_sha256'],cell_id=self.id,uninterrupted=True,recovered=False,
                checks=checks,required_observer_ids=[alias(self.pool,role) for role in ROOTS],required_marks=self.cell['observation_marks'],observers=observers,
                metrics=metrics,timings_seconds={'native_measure':values['wall_seconds']},limitations=['One agent and one pilot repetition. Fresh client entity; shared backend/page caches remain observed.']))
        else:
            if self.live_calibration():
                write_new(self.out/'finished.json',dict(passed=True,at=utc(),cohort=ref(self.out/'cohort/result.json'),
                    live_verification=ref(self.out/'verification.json'),normal_exit=ref(q/'exit.json'),work_check=ref(self.out/'work-check.json'),
                    verification_mode='live-touched-v1',full_namespace_cold=False,full_cold_qualification=self.s['full_cold_qualification'],
                    archives=archives,scope='Calibration only: unchanged eight phases, all touched bytes live, native double-zero and normal stop, parent identity; full cold qualification is referenced separately, not repeated or claimed for this trial.'))
                return
            exits=[q/'verify/child-readback/exit.json',q/'verify/parent-readback/exit.json']
            passed=all(load(path)['exit_code']==0 for path in exits)
            write_new(self.out/'reader-exits.json',dict(passed=passed,normal_reader_exits=[ref(path) for path in exits],mount_reopened=False))
            if not passed:raise ValueError('Original read-only reader did not exit normally')
            write_new(self.out/'finished.json',dict(passed=True,at=utc(),cohort=ref(self.out/'cohort/result.json'),client_verify=ref(self.out/'verification.json'),
                parent_verify=ref(q/'verify/parent-cold/verify.json'),native_exit=ref(q/'exit.json'),verify_exit=ref(self.out/'reader-exits.json'),
                archives=archives,scope='One complete calibration trial with original raw archives, single mount, native debt and original read-only child/parent readback; no formal sample'))


def cell(context):
    request=load(context);base=runpy.run_path(str(pinned(request['base_runner'])))
    return type('PackedCell',(PackedMethods,base['Cell']),{})(context),base


def run(context,stage=None):
    c,base=cell(context)
    if stage:c.execute(stage);return
    lease=load(c.out/'lease.json')
    if lease['already_claimed'] or lease['cell']!=c.cell:raise ValueError('Original fresh cell lease required; no retry')
    c.check_inputs()
    with resource_guard(c.control,c.cell,c.plan):
        for name in c.cell['stages']:
            step=dict(schema='experiment-step-v1',reviewed=True,cell_id=c.id,stage=name,
                argv=[sys.executable,str(Path(__file__).resolve()),'run','--context',str(context),'--stage',name],cwd=str(REPO),
                executable_sha256=digest(sys.executable),inputs=[ref(context),ref(__file__),c.s['base_runner']],inherit_environment=[],
                review=dict(reviewer='pilot-operator',note='Original Packed C3 stage in the one shared controller state; no retry or next cell.'))
            path=c.out/'steps'/(name+'.json');path.parent.mkdir(exist_ok=True);write_new(path,step)
            if state.run_stage(c.control/'state.sqlite',lease,name,ref(path),c.out/'stages'/name):raise RuntimeError('Stage failed; retain original lease and state: '+name)
        base['close_cell'](c,'complete')


def calibrate(context):
    c,_=cell(context)
    if c.purpose not in ('observer-calibration-on','observer-calibration-off'):raise ValueError('Explicit calibration purpose required')
    if active_conflicts(c.control,c.cell):raise ValueError('Physical pool is reserved; do not calibrate')
    with resource_guard(c.control,c.cell,c.plan):
        c.out.mkdir(parents=True,exist_ok=False);c.check_inputs();write_new(c.out/'started.json',dict(at=utc(),cell_id=c.id,purpose=c.purpose,request=ref(context),automatic_retry=False))
        tool=fleet_node(c.fleet,c.pool,'objects')['repo']+'/experiments/unified-suite/pilot-aliyun/scripts/metric-collector.py'
        running=c.remote('objects',['docker','inspect','--format','{{.State.Running}}',c.s['collector_name']]).strip()=='true'
        if not c.on and running:c.remote('objects',['python3',tool,'stop','--name',c.s['collector_name'],'--out',c.s['collector_out']])
        if c.on and not running:
            name=c.s.get('new_collector_name')
            if not name:raise ValueError('Fresh explicit collector name required')
            target='/srv/ax-objects-0/q/'+c.tag+'-collector'
            c.remote('objects',['python3',tool,'start','--image-receipt','/srv/ax-objects-0/assets/provenance/otel-collector.json','--name',name,'--out',target])
            c.s['collector_name']=name;c.s['collector_out']=target
        write_new(c.out/'collector.json',dict(name=c.s['collector_name'],out=c.s['collector_out'],enabled=c.on,native_sender_held_enabled=True))
        for stage in c.cell['stages']:c.execute(stage)


def collect(context,cloud_route,base_preparation,shared):
    c,_=cell(context);qroot=Path(c.s['qualification']);c.out=qroot;qroot.mkdir(parents=True,exist_ok=False)
    if active_conflicts(c.control,c.cell):raise ValueError('Pool reserved; do not collect qualification concurrently')
    route_path=c.get('client',cloud_route,'route/result.json');route=load(route_path)
    base_path=c.get('client',base_preparation,'base/result.json');base=load(base_path)
    if route.get('passed') is not True or base.get('passed') is not True or route.get('cell_id')!=c.id or base.get('cell_id')!=c.id:
        raise ValueError('Actual matching full cloud Packed qualification required')
    base_asset=c.get('client',base['asset']['path'],'assets/packed/asset.json');asset=load(base_asset)
    if ref(base_asset)['sha256']!=base['asset']['sha256'] or route['full_base']['sha256']!=digest(base_asset):raise ValueError('Cloud route used another immutable base')
    private=c.get('client',route['asset']['path'],'route/private-asset.json')
    if digest(private)!=route['asset']['sha256'] or load(private).get('immutable_parent')!=base['asset']:raise ValueError('Cloud qualification child did not bind the original parent')
    files=c.cell['params']['files'];folder=qroot/'packed';folder.mkdir()
    collected={}
    for name,pin in [('source',base['source']),('full_base_check',base['full_base_check']),('base_normal_exit',base['normal_exit']),
                     ('work_check',route['work_check']),('debt',route['native_debt']),('native_exit',route['normal_exit']),('original_readback',route['original_readback'])]:
        path=c.get('client',pin['path'],'proofs/'+name+'.json')
        if digest(path)!=pin['sha256'] or load(path).get('passed') is not True:raise ValueError('Changed or failed native cloud proof: '+name)
        collected[name]=ref(path)
    cold=load(collected['original_readback']['path'])
    for name,key in [('cold_child','cold_child'),('cold_parent','parent_unchanged')]:
        original=cold[key];path=c.get('client',original['path'],'proofs/'+name+'.json');value=load(path)
        required=('passed','expected_tree_equal','expected_hardlinks_equal','namespace_unchanged','authority_unchanged','volume_rows_unchanged')
        if digest(path)!=original['sha256'] or not all(value.get(k) is True for k in required) or value.get('files')!=files or value.get('logical_file_bytes')!=files*4096:
            raise ValueError('Original full cold proof is incomplete: '+name)
        collected[name]=ref(path)
    source=load(collected['source']['path']);base_check=load(collected['full_base_check']['path'])
    if any(value.get('files')!=files or value.get('bytes_checked')!=files*4096 for value in (source,base_check)):
        raise ValueError('Complete declared original source/base required')
    if asset['plan']['sha256']!=digest(c.control/'suite/plan.json') or asset.get('pool','pool-a')!=c.pool:raise ValueError('Base uses another plan or pool')
    matches(asset['config'],c.cell['client_settings'])
    hardware=load(shared/'hardware.json')
    if hardware.get('passed') is not True or hardware.get('inventory_sha256')!=digest(c.runtime/'fleet.json') or hardware.get('pool','pool-a')!=c.pool:
        raise ValueError('Same actual hardware/runtime qualification required')
    write_new(qroot/'hardware.json',hardware)
    producer=c.get('client',asset['producer']['path'],'base/producer-command.json')
    if digest(producer)!=asset['producer']['sha256']:raise ValueError('Original producer changed')
    argv=load(producer)['argv'];source_directory=argv[argv.index('--source-directory')+1]
    tools=c.s['packed_tools']
    if set(tools)!=set(('binary','probe','reader','verifier','oracle')):raise ValueError('Five exact Packed tool bindings required')
    for pin in tools.values():
        if c.remote('client',['sha256sum',pin['path']]).split()[0]!=pin['sha256']:raise ValueError('Cloud Packed tool changed')
    binding=dict(base_asset=str(Path(base['asset']['path']).parent),asset_manifest=ref(base_asset),pool=c.pool,
        plan_remote=asset['plan']['path'],manifest_remote=asset['build_manifest']['path'],source=source_directory,
        source_proof_remote=base['source']['path'],private_object_env=c.s['private_object_env'],tools=tools)
    write_new(folder/'binding.json',binding)
    proof=dict(passed=True,cell_id=c.id,files=files,bytes_checked=files*4096,source=collected['source'],immutable_base=ref(base_asset),
        original_cloud_route=ref(route_path),original_cloud_base=ref(base_path),proofs=collected,
        scope='Actual cloud complete Packed base and a different private child; all eight phases and original child/parent cold checks. No performance acceptance.')
    write_new(folder/'input.json',proof);write_new(folder/'filesystem.json',proof)
    metrics=deepcopy(load(shared/'native/metrics.json'))
    for entry in metrics['bindings']:
        if entry['observer_id']==alias(c.pool,'client') and entry['target_id']=='filesystem':
            entry['required_names']=ENTITY_ALWAYS;entry['workload_required_names']=ENTITY_WORKLOAD
            entry['meaning']='Entity native FUSE/object/cache counters. Always names are checked at the barrier; complete workload series are checked in the original measured-end raw sample. Remote debt is decoded by the native metadata probe.'
    metrics['source_pins']+=[ref(route_path),ref(base_path),ref(context),ref(__file__)]
    write_new(folder/'metrics.json',metrics)
    write_new(folder/'workload.json',dict(schema='experiment-workload-contract-v1',reviewed=True,binary_pins=[tools['binary']],
        input_pins=[ref(folder/'input.json'),collected['source'],ref(c.control/'suite/plan.json')],params=c.cell['params'],seed=c.plan['config']['seed'],
        scope='One fresh private entity at the immutable packed root, unchanged eight phases and every original raw worker stream'))
    write_new(qroot/'collection.json',dict(passed=True,at=utc(),cell_id=c.id,cloud_route=ref(route_path),base=ref(base_path),
        actual_inventory=ref(c.runtime/'fleet.json'),source_tool=ref(__file__),request=ref(context)))


def capacity_gate(path,c):
    value=load(path);filesystem=load(c.qualification/'filesystem.json')
    if (value.get('schema')!='packed-c3-capacity-v1' or value.get('passed') is not True or value.get('cell_id')!=c.id or
            value.get('pool')!=c.pool or value.get('plan_sha256')!=c.plan['plan_sha256'] or
            value.get('inventory_sha256')!=digest(c.runtime/'fleet.json') or value.get('extra_full_copies',0)<7 or
            not .2<=value.get('reserve_fraction',0)<1 or not value.get('rows') or any(row.get('passed') is not True for row in value['rows']) or
            value.get('base_preparation',{}).get('sha256')!=filesystem['original_cloud_base']['sha256'] or
            value.get('qualification',{}).get('sha256')!=filesystem['original_cloud_route']['sha256'] or not value.get('source_pins')):
        raise ValueError('Actual Packed parent/child capacity, seven further copies and twenty percent reserve required')
    for pin in [value['before'],value['after'],*value['source_pins']]:pinned(pin)
    return value


def admit(context,overhead,latest,cross,capacity):
    c,_=cell(context)
    with resource_guard(c.control,c.cell,c.plan):
        if active_conflicts(c.control,c.cell):raise ValueError('Physical pool is reserved; do not admit')
        return admit_locked(context,overhead,latest,cross,capacity)


def admit_locked(context,overhead,latest,cross,capacity):
    c,_=cell(context);q=c.qualification;asset_path=q.parent/'assets/packed/asset.json';asset=load(asset_path)
    if c.purpose!='experiment':raise ValueError('Formal admission requires an experiment context')
    for name in ('input.json','filesystem.json'):
        if load(q/name).get('passed') is not True:raise ValueError('Actual cloud Packed qualification failed')
    if asset.get('pool','pool-a')!=c.pool or asset['plan']['sha256']!=digest(c.control/'suite/plan.json'):raise ValueError('Prepared base differs from this cell')
    matches(asset['config'],c.cell['client_settings'])
    for name,field in [('build-manifest.json','build_manifest_sha256'),('input-manifest.json','input_manifest_sha256')]:
        if digest(c.control/'provenance'/name)!=c.plan['config'][field]:raise ValueError('Frozen provenance changed')
    fresh=load(latest)
    if fresh.get('passed') is not True or fresh.get('build_manifest_sha256')!=c.plan['config']['build_manifest_sha256']:raise ValueError('Latest-branch batch receipt required')
    for name in ('hardware.json',):
        hardware=load(q.parent/name)
        if hardware.get('passed') is not True or hardware.get('inventory_sha256')!=digest(c.runtime/'fleet.json'):raise ValueError('Current exact hardware required')
    allocation=load(c.runtime/'allocation.json')
    if allocation.get('passed') is not True or allocation.get('inventory_sha256')!=digest(c.runtime/'fleet.json'):raise ValueError('Current exact allocation required')
    checked=load(overhead)
    if (checked.get('passed') is not True or checked.get('metric_contract_sha256')!=digest(q/'metrics.json') or
            checked.get('workload_contract_sha256')!=digest(q/'workload.json') or any(value>.05 for value in checked['relative_changes'].values())):
        raise ValueError('Three exact original calibration pairs within five percent required')
    capacity_gate(capacity,c)
    c.out.mkdir(parents=True,exist_ok=False);c.check_inputs()
    write_new(c.out/'capacity.json',dict(passed=True,cell_id=c.id,hardware=ref(q.parent/'hardware.json'),packed_budget=ref(capacity)))
    source=dict(passed=True,cell_id=c.id,actual_base=ref(asset_path),actual_inventory=ref(c.runtime/'fleet.json'),
        latest_branch=ref(latest),build_manifest=ref(c.control/'provenance/build-manifest.json'),input_manifest=ref(c.control/'provenance/input-manifest.json'),
        cache_checked=True,scope='Frozen suite settings checked against actual original Packed asset; new private upper created by the original create stage')
    write_new(c.out/'provenance-cache.json',source)
    gates={'exact-build-input-inventory':c.out/'provenance-cache.json','native-route-and-metadata':q/'filesystem.json',
        'effective-cache-and-durability':c.out/'provenance-cache.json','host-capacity-and-maintenance':c.out/'capacity.json',
        'complete-telemetry-and-overhead':overhead,'full-base-validation-once':q/'filesystem.json','client-cache-evidence':c.out/'provenance-cache.json'}
    if 'cross-pool-hardware-equivalence' in c.cell['required_gates']:
        if cross is None:raise ValueError('Actual cross-pool proof required')
        cross_pool_proof(cross,c.plan,c.runtime/'fleet.json');gates['cross-pool-hardware-equivalence']=cross
    if set(gates)!=set(c.cell['required_gates']):raise ValueError('All and only frozen cell gates required')
    qualification=dict(schema='suite-qualification-v1',plan_sha256=c.plan['plan_sha256'],cell_id=c.id,
        checks=[dict(gate=name,evidence=ref(gates[name]),equals={'passed':True}) for name in c.cell['required_gates']])
    write_new(c.out/'qualification.json',qualification)
    original=load(q/'filesystem.json')['proofs'];normal=load(pinned(original['base_normal_exit']))
    if normal.get('exit_code')!=0 or normal.get('mount_absent') is not True:raise ValueError('Original base still mounted')
    receipt=dict(schema='suite-asset-v1',plan_sha256=c.plan['plan_sha256'],asset_id=c.cell['asset_id'],
        native_binding=dict(asset=ref(asset_path),packed_manifest=asset['config']['packed_manifest_key'],base_namespace=asset['config']['workspace']),
        timings_seconds={},
        stages={name:dict(evidence=pin,equals={'passed':True}) for name,pin in [('source-verify',original['source']),('import',load(q/'filesystem.json')['original_cloud_base']),('full-base-verify',original['full_base_check']),('cache-reset',original['base_normal_exit'])]})
    # Only record a measured producer duration, never synthesize a zero.
    producer_exit=c.get('client',str(Path(asset['producer']['path']).parent/'exit.json'),'producer-exit.json');duration=load(producer_exit)['wall_seconds']
    receipt['timings_seconds']={'producer':duration};write_new(c.out/'asset.json',receipt)
    state.qualify(c.control/'state.sqlite',ref(c.out/'qualification.json'))
    with state.transaction(c.control/'state.sqlite') as db:exists=db.execute('select 1 from assets where id=?',(c.cell['asset_id'],)).fetchone() is not None
    if not exists:state.register_asset(c.control/'state.sqlite',ref(c.out/'asset.json'))
    lease=state.claim(c.control/'state.sqlite',c.pool,c.s['owner']);write_new(c.out/'lease.json',lease)
    if lease['already_claimed'] or lease['cell']!=c.cell:raise ValueError('Unexpected reservation; inspect original shared state')
    request=deepcopy(c.s);request['overhead']=ref(overhead);write_new(c.out/'context.json',request)
    print(json.dumps(dict(admitted=True,cell_id=c.id,context=ref(c.out/'context.json'))))


def pair_schedule(contexts):
    if len(contexts)!=6:raise ValueError('Exactly the declared three off/on pairs required')
    docs=[load(pinned(pin)) for pin in contexts]
    modes=['off','on','on','off','off','on']
    if [doc.get('purpose') for doc in docs]!=['observer-calibration-'+mode for mode in modes]:
        raise ValueError('Frozen counterbalanced off-on/on-off/off-on order required')
    if len({doc['out'] for doc in docs})!=6 or len({doc['attempt'] for doc in docs})!=6:raise ValueError('Six fresh distinct trials required')
    for key in ('control','cell_id','qualification','runtime_control','base_runner','entity_tool','node_tool','observer_tools','calibration_verification','calibration_node','full_cold_qualification'):
        if any(doc.get(key)!=docs[0].get(key) for doc in docs[1:]):raise ValueError('Calibration changed the fixed route: '+key)
    return [dict(off=docs[0]['out'],on=docs[1]['out']),dict(on=docs[2]['out'],off=docs[3]['out']),dict(off=docs[4]['out'],on=docs[5]['out'])]


def pairs(request,out):
    specification=load(request)
    if specification.get('schema')!='packed-c3-pairs-v1' or specification.get('reviewed') is not True:raise ValueError('Reviewed explicit pair plan required')
    schedule=pair_schedule(specification['contexts']);first,_=cell(Path(specification['contexts'][0]['path']))
    # The existing checker binds its three raw-audit paths to pool-a. Never forge aliases for another pool.
    if first.pool!='pool-a':raise ValueError('Current original calibration checker supports pool-a; do not alias another pool')
    out.mkdir(parents=True,exist_ok=False);write_new(out/'intent.json',dict(at=utc(),request=ref(request),pairs=schedule,automatic_retry=False,formal_sample=False))
    for number,pin in enumerate(specification['contexts'],1):
        path=pinned(pin);directory=out/f'trial-{number}';directory.mkdir()
        argv=[sys.executable,str(Path(__file__).resolve()),'calibrate','--context',str(path)]
        write_new(directory/'intent.json',dict(argv=argv,context=pin,automatic_retry=False))
        with (directory/'stdout').open('xb') as stdout,(directory/'stderr').open('xb') as stderr:
            code=subprocess.run(argv,stdout=stdout,stderr=stderr).returncode
        write_new(directory/'exit.json',dict(exit_code=code))
        if code:raise RuntimeError('Original calibration failed; no retry: '+str(directory))
    write_new(out/'pairs.json',schedule);checker=pinned(specification['checker'])
    argv=[sys.executable,str(checker),'--pairs',str(out/'pairs.json'),'--metric-contract',str(first.qualification/'metrics.json'),
        '--workload-contract',str(first.qualification/'workload.json'),'--out',str(out/'overhead')]
    write_new(out/'check-intent.json',dict(argv=argv,checker=specification['checker']))
    with (out/'check.stdout').open('xb') as stdout,(out/'check.stderr').open('xb') as stderr:code=subprocess.run(argv,stdout=stdout,stderr=stderr).returncode
    write_new(out/'check-exit.json',dict(exit_code=code))
    if code:raise RuntimeError('Three original pairs did not pass the existing overhead gate')
    write_new(out/'result.json',dict(passed=True,at=utc(),pairs=ref(out/'pairs.json'),overhead=ref(out/'overhead/result.json'),
        contexts=specification['contexts'],formal_sample=False,scope='Three complete fixed-work trials per mode with raw role archives; no formal cell claim or sample'))


def main():
    parser=argparse.ArgumentParser(description=__doc__);sub=parser.add_subparsers(dest='action',required=True)
    for action in ('calibrate','run','collect','admit'):
        command=sub.add_parser(action);command.add_argument('--context',type=Path,required=True)
        if action=='run':command.add_argument('--stage')
        if action=='collect':
            command.add_argument('--cloud-route',required=True);command.add_argument('--base-preparation',required=True);command.add_argument('--shared-qualification',type=Path,required=True)
        if action=='admit':
            command.add_argument('--overhead',type=Path,required=True);command.add_argument('--latest',type=Path,required=True);command.add_argument('--cross-pool-qualification',type=Path)
            command.add_argument('--capacity-budget',type=Path,required=True)
    command=sub.add_parser('pairs');command.add_argument('--request',type=Path,required=True);command.add_argument('--out',type=Path,required=True)
    args=parser.parse_args()
    if args.action=='run':run(args.context,args.stage)
    elif args.action=='calibrate':calibrate(args.context)
    elif args.action=='collect':collect(args.context,args.cloud_route,args.base_preparation,args.shared_qualification)
    elif args.action=='admit':admit(args.context,args.overhead,args.latest,args.cross_pool_qualification,args.capacity_budget)
    else:pairs(args.request,args.out)


if __name__=='__main__':main()
