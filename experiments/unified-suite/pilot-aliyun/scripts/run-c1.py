#!/usr/bin/env python3
"""Run one qualified flat C1/C2/C3 cell, recording original stages in SQLite first.

No next cell, deadline, automatic retry, forced unmount or resource deletion.
Failures preserve the reservation and live state for an explicit inspection.
"""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import shlex
import subprocess
import sys
import tarfile

REPO = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(REPO))
from bench.runtime.common import digest, load, write_new
from bench.runtime import cohort, fleet
from bench import suite_state as state
from bench import archive_check, experiment_step, suite_validate

ROOTS = dict(client='/srv/ax-client', metadata='/srv/ax-metadata', objects='/srv/ax-objects-0')


def ref(path):
    path = Path(path).resolve(strict=True)
    return dict(path=str(path), sha256=digest(path))


class Cell:
    def __init__(self, context):
        self.s = load(context)
        self.out = Path(self.s['out'])
        self.control = Path(self.s['control'])
        self.plan = load(self.control/'suite/plan.json')
        self.cell = next(c for c in self.plan['cells'] if c['id'] == self.s['cell_id'])
        self.kind=self.cell['workload']
        if self.kind not in ('fio','trace','smallfiles') or self.cell['arm_id'] not in ('native','juicefs'):
            raise ValueError('Qualified flat C1/C2/C3 cell required')
        self.id = self.cell['id']; self.tag = {'fio':'c1-','trace':'c2-','smallfiles':'c3-'}[self.kind]+self.id
        self.arm = self.cell['arm_id']; self.product = self.cell['arm']['product']
        self.base = ROOTS['client']+'/q/'+self.tag
        self.main = ROOTS['client']+'/m/'+self.tag
        self.prep = self.main+'-p'; self.verify = self.main+'-v'
        self.asset = self.s.get('asset',ROOTS['client']+'/a/c1-'+self.arm)
        if self.kind=='trace' and not self.s.get('trace'):raise ValueError('Full trace bindings required')
        if self.kind=='trace' and self.cell['params']['agents']!=1:raise ValueError('This pilot runner supports exactly one trace worker')
        if self.kind=='smallfiles' and (not self.s.get('smallfiles') or self.cell['params']['agents']!=1 or self.cell['params']['workspaces']!=1):
            raise ValueError('Full bindings and exactly one C3 workspace required')
        self.qualification = Path(self.s['qualification'])/self.arm

    def small_fields(self,copy=False):
        b=self.s['smallfiles'];v=b['verifier']
        fields=dict(workspace=1,smallfiles_verifier=v['path'],smallfiles_verifier_sha256=v['sha256'])
        if copy and b.get('copier'):fields.update(smallfiles_copier=b['copier']['path'],smallfiles_copier_sha256=b['copier']['sha256'])
        return fields

    def trace_fields(self):
        t=self.s['trace']
        return dict(trace_case=t['case'],trace_manifest=t['manifest']['path'],replay_binary=t['binary']['path'],
                    trace_verifier=t['verifier']['path'],trace_verifier_sha256=t['verifier']['sha256'],cache_parent_fds=t.get('cache_parent_fds',False))

    def remote(self, role, argv):
        directory = self.out/'commands'
        directory.mkdir(exist_ok=True)
        attempt = directory/f'{len(list(directory.iterdir())):04}'
        attempt.mkdir()
        write_new(attempt/'intent.json', dict(role=role, argv=argv,
                  at=datetime.now(timezone.utc).isoformat(), automatic_retry=False))
        with (attempt/'stdout').open('xb') as out, (attempt/'stderr').open('xb') as err:
            result = subprocess.run(['ssh', '-F', str(self.control/'ssh.conf'), 'pool-a-'+role,
                                     shlex.join(list(map(str, argv)))], stdout=out, stderr=err)
        write_new(attempt/'exit.json', dict(exit_code=result.returncode))
        if result.returncode:
            raise RuntimeError('Inspect failed remote command: '+str(attempt))
        return (attempt/'stdout').read_text()

    def python(self, role, code):
        return self.remote(role, ['python3', '-c', code])

    def get(self, role, source, relative):
        target = self.out/relative; target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists(): raise ValueError('Never replace collected evidence')
        wanted = self.remote(role, ['sha256sum', source]).split()[0]
        subprocess.run(['scp', '-F', str(self.control/'ssh.conf'),
                        'pool-a-'+role+':'+source, str(target)], check=True)
        if digest(target) != wanted: raise ValueError('Changed evidence transfer')
        return target

    def helper(self, action, **fields):
        argv = ['python3', self.s['node_tool']['path'], action, '--asset', self.asset]
        for key, value in fields.items():
            if value is True: argv += ['--'+key.replace('_', '-')]
            elif value is not None and value is not False: argv += ['--'+key.replace('_', '-'), str(value)]
        return self.remote('client', argv)

    def observers(self):
        return load(self.out/'observers.json')

    def mark(self, label):
        if self.kind=='smallfiles':label+='-w01'
        for o in self.observers():
            peer = ref(self.control/'probes'/o['id']/'peer.json')
            fleet.call(peer, 'observe-mark', o['directory'], self.out/'marks'/label/o['id'], label=label)

    def execute(self, stage):
        c = self.cell
        if self.kind=='smallfiles' and stage.endswith('-w01'):stage=stage[:-4]
        if stage == 'observe-start':
            observers = []
            for role, root in ROOTS.items():
                tool = root+'/code-01/experiments/unified-suite/pilot-aliyun/scripts/'+self.s['observer_tool_name']
                args = ['python3', tool, '--role', role, '--product', self.product,
                        '--metric-contract-sha256', digest(self.qualification/'metrics.json'),
                        '--collector-name', self.s['collector_name'], '--out', root+'/q/'+self.tag+'-observer']
                if role == 'client':
                    args += ['--mount-directory', self.main, '--extra-mount-directory', self.prep,
                             '--extra-mount-directory', self.verify]
                spec = json.loads(self.remote(role, args))
                peer = ref(self.control/'probes'/('pool-a-'+role)/'peer.json')
                directory = root+'/o/'+self.tag
                fleet.call(peer, 'observe-start', directory, self.out/'observer-start'/role, spec)
                observers.append(dict(id='pool-a-'+role, node_id='pool-a-'+role, peer=load(peer['path']),
                     directory=directory, spec=spec, required_live_targets={
                         'client':['filesystem'], 'metadata':['redis'], 'objects':['rustfs','collector']}[role]))
            write_new(self.out/'observers.json', observers)
        elif stage == 'create':
            self.helper('draft-mount', cell=self.id, out=self.base+'/prep')
            self.helper('start', join_slot=True, spec=self.base+'/prep/mount.reviewed.json', out=self.prep)
            self.helper({'fio':'copy-fio','trace':'copy-trace','smallfiles':'copy-smallfiles'}[self.kind], join_slot=True, cell=self.id, out=self.base+'/copy',**(self.small_fields(True) if self.kind=='smallfiles' else {}))
            self.helper('debt', cell=self.id, out=self.base+'/copy-debt')
            self.helper('stop', mount_run=self.prep, out=self.base+'/copy-exit.json')
        elif stage == 'mount':
            self.helper('draft-mount', cell=self.id, out=self.base+'/main')
            self.helper('start', join_slot=True, spec=self.base+'/main/mount.reviewed.json', out=self.main)
            self.helper('worker', cell=self.id, spec=self.base+'/main/mount.reviewed.json',
                        mount_run=self.main, out=ROOTS['client']+'/w/'+self.tag,
                        **(self.trace_fields() if self.kind=='trace' else self.small_fields() if self.kind=='smallfiles' else {}))
        elif stage == 'measure':
            worker_path = ROOTS['client']+'/w/'+self.tag+'/worker.reviewed.json'
            worker_sha = self.remote('client', ['sha256sum', worker_path]).split()[0]
            s = dict(schema='experiment-cohort-v1', reviewed=True, purpose='experiment', provider='ssh', agents=1,
                inventory=ref(self.control/'fleet.json'), metric_contract=ref(self.qualification/'metrics.json'),
                workload_contract=ref(self.qualification/'workload.json'),
                allocation_qualification=ref(self.control/'allocation.json'),
                hardware_qualification=ref(Path(self.s['qualification'])/'hardware.json'),
                input_qualification=ref(self.qualification/'input.json'),
                filesystem_qualification=ref(self.qualification/'filesystem.json'), observer_overhead=self.s['overhead'],
                workers=[dict(id='a01',node_id='pool-a-client',slot_id='pool-a-client',
                    peer=load(self.control/'probes/pool-a-client-slot/peer.json'),
                    spec=dict(path=worker_path,sha256=worker_sha),output=ROOTS['client']+'/j/'+self.tag)],
                observers=self.observers(), ready_timeout_seconds=120,
                review=dict(reviewer='pilot-operator',note='One frozen flat cell; fixed work and all three roles observed.'))
            write_new(self.out/'cohort.json', s)
            self.mark('measure-start')
            result = cohort.run(ref(self.out/'cohort.json'), self.out/'cohort')
            if not result['passed']: raise ValueError('Workload or live telemetry failed; retain state')
            self.mark('measure-end')
        elif stage == 'drain':
            if self.kind=='trace':
                self.helper('trace-sync',join_slot=True,cell=self.id,out=self.base+'/post-replay-sync')
            self.helper('debt', cell=self.id, out=self.base+'/debt')
            self.mark('drain-end')
        elif stage == 'unmount':
            self.helper('stop', mount_run=self.main, out=self.base+'/exit.json')
        elif stage == 'verify':
            self.helper('draft-mount', cell=self.id, out=self.base+'/verify-mount')
            self.helper('start', join_slot=True, spec=self.base+'/verify-mount/mount.reviewed.json', out=self.verify)
            if self.kind=='fio':
                self.helper('fio', join_slot=True, cell=self.id, phase='verify', out=self.base+'/verify')
                self.helper('fio-base-check', join_slot=True, block_bytes=c['params']['block_bytes'], out=self.base+'/parent-verify')
            elif self.kind=='smallfiles':
                self.helper('verify-smallfiles',join_slot=True,cell=self.id,out=self.base+'/verify',
                    smallfiles_state=ROOTS['client']+'/w/'+self.tag+'/draft/workload/expected-touched.tsv',**self.small_fields())
                self.helper('smallfiles-base-check',join_slot=True,cell=self.id,out=self.base+'/parent-verify',**self.small_fields())
            else:
                self.helper('verify-trace',join_slot=True,cell=self.id,out=self.base+'/verify',
                    replay_result=ROOTS['client']+'/w/'+self.tag+'/draft/replay.json',**self.trace_fields())
                self.helper('trace-base-check',join_slot=True,cell=self.id,out=self.base+'/parent-verify',
                    trace_case=self.s['trace']['case'])
            self.helper('stop', mount_run=self.verify, out=self.base+'/verify-exit.json')
            self.mark('verify-end')
        elif stage == 'observe-stop':
            for o in self.observers():
                peer = ref(self.control/'probes'/o['id']/'peer.json')
                fleet.call(peer,'observe-stop',o['directory'],self.out/'observer-stop'/o['id'])
                role = o['id'].removeprefix('pool-a-')
                code = ('import sys,json;sys.path.insert(0,'+repr(ROOTS[role]+'/code-01')+');'
                        'from bench.runtime.observe import audit;print(json.dumps(audit('+repr(o['directory'])+
                        ',30,'+repr(c['observation_marks'])+')))')
                checked = json.loads(self.python(role, code))
                if not checked['passed']: raise ValueError('Raw observer audit failed')
                write_new(self.out/'observer-stop'/o['id']/'raw-audit.json',checked)
        elif stage == 'archive':
            self.archive()
        else: raise ValueError('Unsupported C1 stage')

    def archive(self):
        # Native command wrappers already waited for all workload descendants.
        # Recheck original mount/supervisor/observer identities on their hosts.
        closures=[];archives=[];observers=[]
        for role,root in ROOTS.items():
            dirs=['o/'+self.tag,'q/'+self.tag+'-observer']
            if role=='client': dirs += ['q/'+self.tag,'m/'+self.tag,'m/'+self.tag+'-p','m/'+self.tag+'-v',
                                        'w/'+self.tag,'j/'+self.tag]
            script = '''import sys,json,pathlib,shutil
root=pathlib.Path(ROOT);sys.path.insert(0,str(root/'code-01'))
from bench.runtime.common import alive,write_new,digest
from bench.archive_check import pack
from bench.suite_support import closure
tag=TAG;cell=CELL;dirs=DIRS
observer=root/'o'/tag
identities=[json.loads((observer/'ready.json').read_text())['process']]
mount_points=[]
if ROLE=='client':
 for suffix in ('','-p','-v'):
  state=root/'m'/(tag+suffix);p=json.loads((state/'process.json').read_text())
  identities.extend([p['process'],p['supervisor']])
  stopped=json.loads((state/'stopped.json').read_text())
  if not stopped['passed'] or stopped['exit_code']!=0 or not stopped['mount_absent']:raise ValueError('Mount did not exit normally')
 mount_points=[json.loads((root/'a'/ASSET/'asset.json').read_text())['mount_point']]
 if (pathlib.Path('/sys/fs/cgroup/ax-slot-1/cgroup.procs').read_text().strip()):raise ValueError('Agent slot still has processes')
 debt=[]
 for i in (0,1):
  original=root/'q'/tag/'debt'/('sample-'+str(i)+'.json');v=json.loads(original.read_text())
  if any(type(v[k]) not in (int,float) or v[k]!=0 for k in ('pending_upload_bytes','pending_upload_operations')):raise ValueError('Native debt is not numeric zero')
  v['source_observation']=dict(path=str(original),sha256=digest(original))
  v['pending_upload_bytes']=int(v['pending_upload_bytes']);v['pending_upload_operations']=int(v['pending_upload_operations'])
  p=root/'q'/tag/('closure-debt-'+str(i)+'.json');write_new(p,v)
  debt.append(dict(evidence=dict(path=str(p),sha256=digest(p)),equals={'passed':True,'cell_id':cell},
                   pending_bytes_field='pending_upload_bytes',pending_operations_field='pending_upload_operations',observed_at=v['at']))
 request=dict(schema='suite-closure-request-v1',cell_id=cell,processes=identities,mount_points=mount_points,debt_samples=debt)
 request_path=root/'q'/tag/'closure-normalized-request.json';write_new(request_path,request);result=closure(request_path)
 result['passed']=result['all_exited'] and result['all_unmounted']
else:
 result=dict(passed=not any(alive(p) for p in identities),cell_id=cell,processes=identities,
             all_exited=not any(alive(p) for p in identities),all_unmounted=True,owned_mounts=[])
if not result['passed']:raise ValueError('Owned process remains live')
q=root/'q'/(tag+'-closure');q.mkdir(exist_ok=False);write_new(q/'result.json',result);dirs.append('q/'+tag+'-closure')
staging=root/'evidence'/tag;staging.mkdir(parents=True,exist_ok=False);files=[]
for directory in dirs:
 for p in sorted((root/directory).rglob('*')):
  relative=p.relative_to(root)
  if 'cache' in p.relative_to(root/directory).parts:continue
  if p.is_symlink():raise ValueError('Unexpected evidence symlink')
  if not p.is_file():continue
  target=staging/relative;target.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(p,target);files.append(relative.as_posix())
out=root/'archives';out.mkdir(exist_ok=True)
checked=pack(staging,files,out/(tag+'-'+ROLE+'.tar.gz'),cell,ROLE)
write_new(q/'archive.json',checked);print(json.dumps(dict(closure=result,archive=checked)))
'''
            variables=dict(ROOT=root,TAG=self.tag,CELL=self.id,DIRS=dirs,ROLE=role,ASSET=Path(self.asset).name)
            response=json.loads(self.python(role,'\n'.join(k+'='+repr(v) for k,v in variables.items())+'\n'+script))
            closures.append(response['closure'])
            archive=self.get(role,response['archive']['archive'],'archives/'+role+'.tar.gz')
            checked=archive_check.verify(archive,response['archive']['sha256'])
            write_new(self.out/'archives'/(role+'-check.json'),checked);archives.append(ref(archive))
            # Verified regular members only, into a new local evidence root.
            destination=self.out/'raw'/role;destination.mkdir(parents=True,exist_ok=False)
            with tarfile.open(archive,'r:gz') as source:
                for member in source:
                    if member.name==archive_check.MANIFEST:continue
                    target=destination/member.name;target.parent.mkdir(parents=True,exist_ok=True)
                    with source.extractfile(member) as incoming,target.open('xb') as outgoing:
                        import shutil
                        shutil.copyfileobj(incoming,outgoing)
            observer=destination/'o'/self.tag
            from bench.runtime.observe import audit
            audit(observer,30,self.cell['observation_marks'])
            observers.append(dict(id='pool-a-'+role,directory=str(observer),max_gap_seconds=30,marks=self.cell['observation_marks']))
        client=self.out/'raw/client';q=client/'q'/self.tag
        draft=client/'w'/self.tag/'draft'
        work=draft/'workload'
        if self.kind=='fio':packet=dict(agent_index=0,evidence=ref(work/'result.json'),raw=ref(work/'fio.json'))
        elif self.kind=='smallfiles':
            packet=dict(agent_index=0,workspace_index=0,evidence=ref(work/'result.json'),
                raw_logs=[ref(work/f'worker-{i}.csv') for i in range(self.cell['params']['workers'])])
        else:
            source=self.get('client',self.s['trace']['case']+'/metrics.json','source-metrics.json')
            packet=dict(agent_index=0,evidence=ref(draft/'replay.json'),source_metrics=ref(source))
        write_new(self.out/'work-packet.json',dict(cell_id=self.id,results=[packet]))
        checked=suite_validate.work(self.plan,self.id,load(self.out/'work-packet.json'))
        write_new(self.out/'work-check.json',checked)
        full=load(q/'verify/result.json');parent=load(q/'parent-verify/result.json')
        proof=dict(passed=full['passed'] and parent['passed'] and all(c['passed'] for c in closures),cell_id=self.id,
                   cold_child=ref(q/'verify/result.json'),parent_unchanged=ref(q/'parent-verify/result.json'),
                   normal_exits=[ref(q/n) for n in ('copy-exit.json','exit.json','verify-exit.json')],
                   cache_scope='All three original supervisors validated fresh cache directories and exact suite settings; server/page caches were not dropped.')
        write_new(self.out/'verification.json',proof)
        closure=dict(cell_id=self.id,passed=all(c['passed'] for c in closures),all_exited=all(c['all_exited'] for c in closures),
                     all_unmounted=all(c['all_unmounted'] for c in closures),pending_upload_bytes=0,pending_upload_operations=0,nodes=closures)
        write_new(self.out/'closure.json',closure)
        co=load(self.out/'cohort/result.json')
        io_metrics={};metrics={}
        if self.kind=='fio':
            native=co['jobs'][0]['result']['native_jobs']
            if len(native)!=1:raise ValueError('Pilot percentile adapter requires one native fio job')
            for direction in ('read','write','sync'):
                v=native[0][direction]
                for name,unit,preference in [('bw_bytes','bytes/second','higher'),('iops','operations/second','higher')]:
                    if name in v:io_metrics[direction+'_'+name]=dict(value=v[name],unit=unit,direction=preference)
                percentiles=v.get('clat_ns' if direction!='sync' else 'lat_ns',{}).get('percentile',{})
                if '99.000000' in percentiles:
                    io_metrics[direction+'_p99_ns']=dict(value=percentiles['99.000000'],unit='nanoseconds',direction='lower')
            metric_source=ref(work/'fio.json');metric_scope='One native fio job; operation p99 is distinct from task p99.'
        elif self.kind=='smallfiles':
            values=load(work/'result.json')
            for phase in values['phases']:
                for name,unit in [('wall_seconds','seconds'),('io_p99_ns','nanoseconds'),('application_p99_ns','nanoseconds')]:
                    io_metrics[phase['phase']+'_'+name]=dict(value=phase[name],unit=unit,direction='lower')
            for name in ('actual_read_bytes','actual_write_bytes','verified_reads','writes'):
                io_metrics[name]=dict(value=values[name],unit='bytes' if name.endswith('bytes') else 'operations',direction='coverage')
            io_metrics['create_and_fsync_seconds']=dict(value=load(q/'copy/result.json')['wall_seconds'],unit='seconds',direction='lower')
            metric_source=ref(work/'result.json');metric_scope='Eight native fixed-work phases with verified raw worker streams. Full cold child and parent checks are outside measurement.'
        else:
            replay=load(draft/'replay.json')
            for name in ('actual_read_bytes','actual_write_bytes','operations'):
                io_metrics[name]=dict(value=replay[name],unit='operations' if name=='operations' else 'bytes',direction='coverage')
            io_metrics['replay_seconds']=dict(value=replay['execution_seconds'],unit='seconds',direction='lower')
            io_metrics['captured_repeated_read_ratio']=dict(value=load(source)['repeated_read_ratio'],unit='fraction',direction='descriptive')
            metric_source=ref(draft/'replay.json');metric_scope='Logical trace work; actual FUSE, S3, Redis and physical I/O are separate raw observer counters. Repeated-read ratio describes the captured input.'
        write_new(self.out/'io-metrics.json',dict(passed=True,cell_id=self.id,metrics=io_metrics,
                  source=metric_source,scope=metric_scope))
        for name,v in io_metrics.items():
            if v['direction'] not in ('lower','higher'):continue
            metrics[name]=dict(evidence=ref(self.out/'io-metrics.json'),field='metrics.'+name+'.value',unit=v['unit'],direction=v['direction'])
        metrics['task_wall']=dict(evidence=ref(self.out/'cohort/result.json'),field='coordinator_makespan_seconds',unit='seconds',direction='lower')
        checks={k:dict(evidence=ref(self.out/'verification.json'),equals={'passed':True,'cell_id':self.id})
                for k in ('correctness','isolation','remote_readback','real_exit','cache_policy')}
        checks.update(work=dict(evidence=ref(self.out/'work-check.json'),equals={'passed':True}),
                      drain=dict(evidence=ref(self.out/'closure.json'),equals={'passed':True,'cell_id':self.id}),
                      observer_overhead=dict(evidence=self.s['overhead'],equals={'passed':True}))
        sample=dict(schema='suite-sample-v1',plan_sha256=self.plan['plan_sha256'],cell_id=self.id,
                    uninterrupted=not self.s.get('recovery',False),recovered=self.s.get('recovery',False),checks=checks,required_observer_ids=[o['id'] for o in observers],
                    required_marks=self.cell['observation_marks'],observers=observers,
                    metrics=metrics,
                    timings_seconds={'native_measure':co['jobs'][0]['result'][{'fio':'wrapper_wall_seconds','trace':'execution_seconds','smallfiles':'wall_seconds'}[self.kind]]},
                    limitations=['One agent, one pilot repetition; no scaling or stable speedup conclusion.',
                                 'Fresh client caches; shared backend/page caches remain observed.'])
        write_new(self.out/'sample.json',sample);write_new(self.out/'archives.json',archives)


def close_cell(c,outcome,reason=None):
    if outcome=='failed' and (not reason or load(c.out/'sample.json')['recovered'] is not True):
        raise ValueError('Explicit reason and recovered evidence required')
    files=[p.relative_to(c.out).as_posix() for p in sorted(c.out.rglob('*')) if p.is_file()]
    seal=c.out.parent/(c.id+'.retention.json');experiment_step.seal(c.out,files,seal)
    closure_ref=ref(c.out/'closure.json')
    closing=dict(schema='suite-cell-close-v1',plan_sha256=c.plan['plan_sha256'],cell_id=c.id,outcome=outcome,
                 closure={k:dict(evidence=closure_ref,equals=v) for k,v in {
                     'processes':{'all_exited':True},'mounts':{'all_unmounted':True},
                     'debt':{'pending_upload_bytes':0,'pending_upload_operations':0}}.items()},
                 retention=dict(root=str(c.out),manifest=ref(seal)),archives=load(c.out/'archives.json'),sample=ref(c.out/'sample.json'))
    if reason:closing['failure_reason']=reason
    close_path=c.out.parent/(c.id+'.close.json');write_new(close_path,closing)
    state.finish(c.control/'state.sqlite',load(c.out/'lease.json'),ref(close_path))
    print(json.dumps(dict(closed=True,outcome=outcome,cell_id=c.id,closing=ref(close_path))))


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--context',type=Path,required=True)
    p.add_argument('--stage',choices=['observe-start','create','mount','measure','drain','unmount','verify','observe-stop','archive'])
    p.add_argument('--close-failed',help='Explicit failure reason after separately recovered offline evidence; never reruns a stage')
    a=p.parse_args();c=Cell(a.context)
    if a.close_failed:
        if a.stage:raise ValueError('Choose a stage or explicit failed closure')
        close_cell(c,'failed',a.close_failed);return
    if a.stage:
        c.execute(a.stage);return
    lease=load(c.out/'lease.json')
    if lease['already_claimed']:raise ValueError('Inspect the original stage state; never restart a claimed cell')
    if lease['cell']!=c.cell:raise ValueError('Lease differs from frozen cell')
    if c.remote('client',['sha256sum',c.s['node_tool']['path']]).split()[0]!=c.s['node_tool']['sha256']:
        raise ValueError('Node preparation tool changed')
    for role,root in ROOTS.items():
        tool=root+'/code-01/experiments/unified-suite/pilot-aliyun/scripts/'+c.s['observer_tool_name']
        if c.remote(role,['sha256sum',tool]).split()[0]!=c.s['observer_tool_sha256']:
            raise ValueError('Observer builder changed')
    binary=ROOTS['client']+'/assets/bin/'+c.product
    if c.product=='brewfs':
        expected=next(x['sha256'] for x in load(c.control/'provenance/build-manifest.json')['files'] if x['file']=='brewfs')
    else:
        expected='6d450d3c151b6affede364d9a198d2505111b0612a5fb4d31613444c635174cc'
    if c.remote('client',['sha256sum',binary]).split()[0]!=expected:
        raise ValueError('Deployed client binary differs from this batch')
    if c.kind=='trace':
        for name in ('manifest','binary','verifier'):
            binding=c.s['trace'][name]
            if c.remote('client',['sha256sum',binding['path']]).split()[0]!=binding['sha256']:
                raise ValueError('Trace binding changed: '+name)
    if c.kind=='smallfiles':
        for binding in c.s['smallfiles'].values():
            if binding and c.remote('client',['sha256sum',binding['path']]).split()[0]!=binding['sha256']:
                raise ValueError('Small-file binding changed')
    for stage in c.cell['stages']:
        step=dict(schema='experiment-step-v1',reviewed=True,cell_id=c.id,stage=stage,
                  argv=[sys.executable,str(Path(__file__).resolve()),'--context',str(a.context),'--stage',stage],
                  cwd=str(REPO),executable_sha256=digest(sys.executable),inputs=[ref(a.context),ref(__file__)],
                  inherit_environment=[],review=dict(reviewer='pilot-operator',note='Exactly this original cell stage; no retries or next cell.'))
        path=c.out/'steps'/(stage+'.json');path.parent.mkdir(exist_ok=True);write_new(path,step)
        code=state.run_stage(c.control/'state.sqlite',lease,stage,ref(path),c.out/'stages'/stage)
        if code:raise RuntimeError('Stage failed; preserve reservation and inspect: '+stage)
    # All original stages and independent archive checks exist before release.
    close_cell(c,'complete')


if __name__=='__main__':
    main()
