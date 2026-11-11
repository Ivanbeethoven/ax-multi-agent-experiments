"""Tiny explicit protocol fixtures. No cloud calls, FUSE or dataset generation."""
from contextlib import redirect_stdout
import copy
import csv
import io
import json
import os
from pathlib import Path
import runpy
import shutil
import tempfile
import types
import unittest
from unittest.mock import patch

from bench.runtime.common import digest,load,write_new
from bench.runtime.workloads import counts
from bench.suite_catalog import template,select_pilot_files
from bench.suite_plan import build
from bench.suite_report import sample

REPO=Path(__file__).resolve().parents[1]
CONTROLLER=REPO/'experiments/unified-suite/pilot-aliyun/scripts/packed-c3-controller.py'
CODE=runpy.run_path(str(CONTROLLER))
METHODS=CODE['PackedMethods']


def small_plan():
    config=select_pilot_files(template(),100000)
    config['cases']=[next(row for row in config['cases'] if row['workload']=='smallfiles')]
    # Only a few operation rows. The declared 100k base is never generated.
    config['cases'][0]['params'].update(workers=1,hot_files=1,cow_files=1,read_ops=1,write_ops=1,mixed_ops=4)
    config['metadata_backend']='redis'
    for pool in config['clusters']:
        pool['metadata_backend']='redis';pool['observer_ids']=[pool['id']+'-'+role for role in ('client','metadata','objects')]
    plan=build(config)
    return plan,next(row for row in plan['cells'] if row['arm_id']=='packed')


class PackedController(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
    def tearDown(self):self.tmp.cleanup()
    def put(self,name,value):
        path=self.root/name;path.parent.mkdir(parents=True,exist_ok=True);write_new(path,value)
        return CODE['ref'](path)

    def test_pair_plan_pins_six_distinct_fixed_work_trials(self):
        values=[]
        for i,mode in enumerate(('off','on','on','off','off','on')):
            values.append(dict(control='one-original-control',cell_id='packed',qualification='one-qualification',runtime_control={'fixed':'runtime'},
                base_runner={'fixed':'flat'},entity_tool={'fixed':'native'},node_tool={'fixed':'node'},observer_tools={'fixed':'observer'},
                attempt='fixture-'+str(i),out=str(self.root/str(i)),purpose='observer-calibration-'+mode))
        pins=[self.put(str(i)+'.json',value) for i,value in enumerate(values)]
        schedule=CODE['pair_schedule'](pins)
        self.assertEqual(schedule,[dict(off=values[0]['out'],on=values[1]['out']),dict(on=values[2]['out'],off=values[3]['out']),dict(off=values[4]['out'],on=values[5]['out'])])
        broken=copy.deepcopy(values);broken[1]['runtime_control']={'changed':'runtime'}
        with self.assertRaises(ValueError):CODE['pair_schedule']([self.put('b'+str(i)+'.json',v) for i,v in enumerate(broken)])
        Path(pins[0]['path']).write_text('{}')
        with self.assertRaises(ValueError):CODE['pair_schedule'](pins)

    def test_single_mount_route_never_reopens_the_entity(self):
        c=METHODS.__new__(METHODS);c.id='test-packed';c.main='/fixture/main';c.asset='/fixture/child';c.base='/fixture/evidence';c.tag='fixture'
        c.purpose='observer-calibration-off';c.binding=dict(base_asset='/fixture/base',plan_remote='/fixture/plan',manifest_remote='/fixture/manifest',source='/fixture/source',
            source_proof_remote='/fixture/source-proof',private_object_env='/outside/private.env',tools={name:dict(path='/fixture/'+name,sha256='a'*64) for name in ('probe','reader','verifier','oracle')})
        calls=[];c.entity=lambda action,**fields:calls.append(('entity',action,fields));c.helper=lambda action,**fields:calls.append(('node',action,fields))
        c.remote=lambda *a:'b'*64+'  stop';c.mark=lambda label:calls.append(('mark',label,{}))
        for stage in ('create-w01','mount-w01','drain-w01','unmount-w01','verify-w01'):c.execute(stage)
        self.assertEqual([action for kind,action,_ in calls if kind=='node'],['draft-mount','start','worker','stop'])
        worker=next(fields for kind,action,fields in calls if action=='worker')
        self.assertTrue(worker['root_at_mount']);self.assertEqual(worker['workspace'],1)
        self.assertEqual(next(fields for kind,action,fields in calls if action=='draft-mount')['purpose'],'experiment')
        cold=next(fields for kind,action,fields in calls if action=='cold-verify')
        self.assertEqual(cold['normal_stop_sha256'],'b'*64);self.assertEqual(cold['base_asset'],'/fixture/base')
        self.assertEqual([action for kind,action,_ in calls if kind=='entity'],['fresh','debt','cold-verify'])

    def fixture(self,purpose):
        plan,cell=small_plan();c=METHODS.__new__(METHODS)
        c.plan=plan;c.cell=cell;c.id=cell['id'];c.pool=cell['pool'];c.tag='unit-fixture';c.purpose=purpose;c.on=purpose!='observer-calibration-off'
        c.out=self.root/'controller';c.out.mkdir();c.asset=str(self.root/'client/a'/c.tag)
        roots={role:str(self.root/role) for role in ('client','metadata','objects')}
        c.fleet={'nodes':[dict(id=c.pool+'-'+role,repo=str(REPO)) for role in roots]}
        dead=dict(pid=999999999,boot_id='f014e48c-92cf-4b1f-a074-8e12c2f16d01',start_ticks=1)
        def put(path,value):path.parent.mkdir(parents=True,exist_ok=True);write_new(path,value)
        def python(role,code):
            stream=io.StringIO()
            with redirect_stdout(stream):exec(code,{})
            return stream.getvalue()
        def get(role,source,relative):
            target=c.out/relative;target.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(source,target);return target
        c.python=python;c.get=get
        client=Path(roots['client']);q=client/'q'/c.tag;mount=client/'m'/c.tag;work=client/'w'/c.tag/'draft/workload'
        put(mount/'process.json',dict(process=dead,supervisor=dead));put(mount/'stopped.json',dict(passed=True,exit_code=0,mount_absent=True))
        put(q/'exit.json',dict(passed=True,exit_code=0,mount_absent=True));put(q/'verify/result.json',dict(passed=True))
        for name in ('child','parent'):
            put(q/'verify'/(name+'-cold')/'verify.json',dict(passed=True,scope='Unit fixture only, no remote namespace was read'))
            put(q/'verify'/(name+'-readback')/'exit.json',dict(exit_code=0))
            put(q/'verify'/(name+'-readback')/'process.json',dict(process=dead))
        for i in (0,1):
            native=q/'debt'/('native-'+str(i)+'.json');put(native,dict(debt=dict(bytes=0,objects=0)))
            put(q/'debt'/('sample-'+str(i)+'.json'),dict(passed=True,cell_id=c.id,pending_upload_bytes=0,pending_upload_operations=0,
                native_status=CODE['ref'](native),at=f'2026-01-01T00:00:0{i}+00:00'))
        put(q/'debt/process.json',dict(process=dead));put(client/'j'/c.tag/'job-0000/process.json',dict(process=dead))
        put(Path(c.asset)/'asset.json',dict(scope='Unit fixture, no filesystem'))
        p=cell['params'];values={**counts(p),**{key:p[key] for key in ('workers','hot_files','cow_files','read_ops','write_ops','mixed_ops')},
            'seed':plan['config']['seed'],'workspace':1,'wall_seconds':.1,'phases':[]}
        phases={'cold-read':1,'lower-prewarm':2,'hot-read':1,'cow':1,'upper-prewarm':2,'hot-write':1,'upper-rewarm':2,'mixed':4}
        work.mkdir(parents=True)
        with (work/'worker-0.csv').open('w',newline='') as stream:
            writer=csv.DictWriter(stream,fieldnames=['phase','operation','file','read_bytes','write_bytes','io_ns','application_ns']);writer.writeheader()
            for phase,n in phases.items():
                writes=0
                for operation in range(n):
                    write=phase in ('cow','hot-write') or phase=='mixed' and operation%4==3;writes+=write
                    writer.writerow(dict(phase=phase,operation=operation,file=0,read_bytes=0 if write else 4096,write_bytes=4096 if write else 0,io_ns=1,application_ns=2))
                values['phases'].append(dict(phase=phase,reads=n-writes,writes=writes,wall_seconds=.01,io_p99_ns=1,application_p99_ns=2))
        put(work/'result.json',values)
        put(c.out/'cohort/result.json',dict(passed=True,workload_passed=True,purpose=purpose,coordinator_makespan_seconds=.11,
            jobs=[dict(passed=True,kind='smallfiles',seconds=.1,result=values)]))
        overhead=self.put('overhead.json',dict(passed=True,max_relative_change=.05));c.s={'overhead':overhead}
        if c.on:
            for role,root in roots.items():
                observer=Path(root)/'o'/c.tag;put(observer/'ready.json',dict(process=dead));history=[]
                for i in (0,1):
                    folder=observer/f'sample-{i:06}';raw=folder/'filesystem.txt';folder.mkdir(parents=True)
                    raw.write_text(''.join(name+' 1\n' for name in CODE['ENTITY_WORKLOAD']))
                    path=folder/'sample.json';put(path,dict(index=i,passed=True,monotonic_start_ns=(i+1)*1000000000,monotonic_end_ns=(i+1)*1000000000+1,
                        files={'filesystem.txt':dict(bytes=raw.stat().st_size,sha256=digest(raw))}))
                    history.append(dict(index=i,sample_sha256=digest(path)))
                put(observer/'finished.json',dict(passed=True,samples=2,history=history,marks=[dict(label=mark,sample_index=1,monotonic_ns=3000000000) for mark in cell['observation_marks']]))
        return c,roots

    @unittest.skipUnless(os.name=='posix','Original closure reads Linux process/mount identity')
    def test_original_archive_closure_and_sample_accept_single_mount(self):
        c,roots=self.fixture('experiment');read_text=Path.read_text
        def fixture_read(path,*args,**kwargs):
            return '' if str(path)=='/sys/fs/cgroup/ax-slot-1/cgroup.procs' else read_text(path,*args,**kwargs)
        with patch.dict(METHODS.archive.__globals__,ROOTS=roots),patch.object(Path,'read_text',fixture_read):c.archive()
        self.assertTrue(load(c.out/'closure.json')['all_exited']);self.assertEqual(load(c.out/'closure.json')['pending_upload_operations'],0)
        self.assertEqual(len(load(c.out/'archives.json')),3)
        checked=sample(CODE['ref'](c.out/'sample.json'),c.plan,c.cell)
        self.assertTrue(checked['accepted'],checked['reasons'])
        request=load(c.out/'raw/client/q'/c.tag/'closure-request.json')
        self.assertEqual(request['mount_points'],[c.asset+'/mnt']);self.assertGreaterEqual(len(request['processes']),6)
        self.assertFalse((c.out/'raw/client/m'/(c.tag+'-v')).exists())

    @unittest.skipUnless(os.name=='posix','Original closure reads Linux process/mount identity')
    def test_off_calibration_archives_raw_work_without_formal_sample(self):
        c,roots=self.fixture('observer-calibration-off');read_text=Path.read_text
        def fixture_read(path,*args,**kwargs):
            return '' if str(path)=='/sys/fs/cgroup/ax-slot-1/cgroup.procs' else read_text(path,*args,**kwargs)
        with patch.dict(METHODS.archive.__globals__,ROOTS=roots),patch.object(Path,'read_text',fixture_read):c.archive()
        result=load(c.out/'finished.json');self.assertTrue(result['passed']);self.assertFalse((c.out/'sample.json').exists())
        self.assertFalse(load(c.out/'reader-exits.json')['mount_reopened']);self.assertEqual(len(result['archives']),3)
        self.assertTrue(load(result['parent_verify']['path'])['passed'])


if __name__=='__main__':unittest.main()
