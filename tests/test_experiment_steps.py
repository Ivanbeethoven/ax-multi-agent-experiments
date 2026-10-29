"""Provider-independent planning and single-step failure/retention boundaries."""
from copy import deepcopy
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'harness'))
import experiment_inventory as inventory
import experiment_step as step
from experiment_io import load, digest, write_new
import host_preflight as host
import fs_step
import s3_requests as s3
import session_check as session
import final_plan
import ml_import


def filled(mode='parallel', provider='provided'):
    value = inventory.template(provider, mode); value['campaign_id'] = 'test-campaign'
    for i,n in enumerate(value['nodes']):
        n.update(ssh_alias='worker'+str(i),private_ip=f'10.1.0.{i+10}',resource_id='host'+str(i),
                 host_key_verified=True,hardware_class='class-a',work_root='/srv/ax/test',
                 data_mounts=[f'/mnt/d{x}' for x in range(4)])
    return value


class AllocationTests(unittest.TestCase):
    def test_both_providers_and_topologies(self):
        for provider in ('aliyun','provided'):
            for mode,count in (('parallel',9),('sequential',3)):
                self.assertEqual(inventory.validate(filled(mode,provider))['nodes'],count)
                with self.assertRaises(ValueError): inventory.validate(inventory.template(provider,mode))

    def test_shared_parallel_resources_and_migration_alias_rejected(self):
        for pool in ('C1-A','C2-P','C4-M'):
            data=filled(); data['pools'][pool][0]=data['pools']['C1-G'][0]
            with self.assertRaises(ValueError): inventory.validate(data)
        data=filled(); data['pools']['C4-M'][1]=data['pools']['C4-M'][0]
        with self.assertRaises(ValueError): inventory.validate(data)

    def test_wrong_hardware_path_and_disk_budget_rejected(self):
        for change in ({'hardware_class':'other'},{'work_root':'/mnt/d0/child'},
                       {'data_mounts':['/mnt/one']},{'host_key_verified':False}):
            data=filled(); data['nodes'][0].update(change)
            with self.assertRaises(ValueError): inventory.validate(data)

    def test_os_partitions_and_shared_disks_rejected(self):
        for disks in ([{'8:0'},{'8:16'}],[{'8:16'},{'8:16'}],[{'8:16','8:32'}]):
            with self.assertRaises(ValueError): host.distinct_data_disks({'8:0'},disks)
        host.distinct_data_disks({'8:0'},[{'8:16'},{'8:32'},{'8:48'},{'8:64'}])
        tree={'blockdevices':[{'type':'part','maj:min':'8:17','children':[{'type':'disk','maj:min':'8:16'}]}]}
        self.assertEqual(host.backing_disks(tree),{'8:16'})

    def test_optional_migration_is_paired_and_c4_only(self):
        base=final_plan.build(); extended=final_plan.build(include_migration=True)
        self.assertEqual(len(base['cells']),318); self.assertEqual(len(extended['cells']),342)
        c4=final_plan.build(cluster='C4',include_migration=True); self.assertEqual(len(c4['cells']),42)
        cells=[c for c in c4['cells'] if c['stage']=='M']
        for pair in {c['paired_batch'] for c in cells}:
            group=[c for c in cells if c['paired_batch']==pair]
            self.assertEqual({c['method'] for c in group},{'native-overlay-stay','native-overlay-move','juicefs-stay','juicefs-move'})
            self.assertTrue(all(not c['execution_enabled'] and c['cluster_id']=='C4' for c in group))


class StepTests(unittest.TestCase):
    def make(self, root, code='print("ok")'):
        script=root/'operation.py'; script.write_text(code)
        spec={'schema':'experiment-step-v1','reviewed':True,'cell_id':'smoke','stage':'one',
              'argv':[sys.executable,str(script)],'cwd':str(root),'executable_sha256':digest(sys.executable),
              'inputs':[{'path':str(script),'sha256':digest(script)}]}
        path=root/'step.json';write_new(path,spec);return path

    def test_changed_input_blocks_before_start(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); spec=self.make(root); (root/'operation.py').write_text('raise SystemExit(9)')
            with self.assertRaises(ValueError):step.execute(spec,digest(spec),root/'out')
            self.assertFalse((root/'out').exists())

    def test_success_single_failure_no_retry_and_environment_isolation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); spec=self.make(root,'import os\nassert "BREWFS_HIDDEN_TEST" not in os.environ\nprint("once")\nraise SystemExit(7)')
            with patch.dict(os.environ,{'BREWFS_HIDDEN_TEST':'bad'}):
                self.assertEqual(step.execute(spec,digest(spec),root/'out'),7)
            result=load(root/'out/finished.json'); self.assertFalse(result['passed']);self.assertTrue(result['completed'])
            self.assertEqual((root/'out/stdout.log').read_text().strip(),'once')
            with self.assertRaises(FileExistsError):step.execute(spec,digest(spec),root/'out')

    def test_retention_checks_copies_and_rejects_escape_or_secrets(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); data=root/'data';data.mkdir();(data/'result.json').write_text('{}')
            manifest=root/'retention.json';step.seal(data,['result.json'],manifest)
            copied=root/'copy';shutil.copytree(data,copied)
            self.assertTrue(step.verify(copied,manifest,digest(manifest))['passed'])
            (copied/'result.json').write_text('bad')
            with self.assertRaises(ValueError):step.verify(copied,manifest,digest(manifest))
            for files in (['../retention.json'],['credentials.env'],['result.json','result.json']):
                with self.assertRaises(ValueError):step.seal(data,files,root/'bad.json')


class FakeS3:
    def __init__(self):self.objects={};self.bodies=[];self.short=False
    def put_object(self,**kw):
        if kw['IfNoneMatch']!='*' or kw['Key'] in self.objects:raise ValueError('already exists')
        self.objects[kw['Key']]=kw['Body']
        return {'ResponseMetadata':{'HTTPStatusCode':200,'HTTPHeaders':{}}}
    def get_object(self,**kw):
        data=self.objects[kw['Key']];status=200
        if 'Range' in kw:
            start,end=map(int,kw['Range'][6:].split('-'));data=data[start:end+1];status=206
        if self.short:data=data[:-1]
        body=io.BytesIO(data);self.bodies.append(body)
        return {'Body':body,'ResponseMetadata':{'HTTPStatusCode':status,'HTTPHeaders':{}}}


class WorkloadTests(unittest.TestCase):
    def test_s3_identical_plan_full_range_and_failed_partial_preserved(self):
        p=s3.plan(2,4,8192,42);self.assertEqual(p,s3.plan(2,4,8192,42));client=FakeS3()
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);s3.run('upload',client,p,'bucket','run-1',root/'put')
            for mode in ('full','range'):
                s3.run('read',client,p,'bucket','run-1',root/mode,mode)
                self.assertEqual(load(root/mode/'result.json')['requests'],4)
            with self.assertRaises(ValueError):s3.run('upload',client,p,'bucket','run-1',root/'duplicate')
            self.assertTrue((root/'duplicate/failed.json').exists())
            client.short=True
            with self.assertRaises(ValueError):s3.run('read',client,p,'bucket','run-1',root/'short','range')
            self.assertTrue(all(b.closed for b in client.bodies));self.assertFalse((root/'short/result.json').exists())

    @unittest.skipUnless(os.name=='posix','file/directory fsync and FUSE-compatible semantics tested on Linux')
    def test_fork_challenge_and_first_use_steps(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);source=root/'source';fs_step.fixture(source,5,4096)
            expected=fs_step.tree(source);child=root/'child';shutil.copytree(source,child)
            name='d000000/f00000000'
            self.assertTrue(fs_step.first_read(child,name,expected[name]['sha256'])['passed'])
            fs_step.mutate(child);self.assertEqual(fs_step.tree(source),expected);self.assertNotEqual(fs_step.tree(child),expected)
            with self.assertRaises(ValueError):fs_step.mutate(child)
            payload=root/'payload';payload.write_bytes(b'new')
            self.assertTrue(fs_step.durable_write(child,'fresh',payload)['passed'])
            with self.assertRaises(FileExistsError):fs_step.durable_write(child,'fresh',payload)

    def test_ml_import_rejects_partial_target_and_wrong_bytes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);source=root/'source';source.mkdir();target=root/'target';target.mkdir()
            (source/'f').write_bytes(b'abc');manifest=root/'manifest.tsv';manifest.write_text(digest(source/'f')+'\t3\tf\n')
            result=ml_import.import_files(source,manifest,target,1,3);self.assertEqual(result['bytes'],3)
            with self.assertRaises(FileExistsError):ml_import.import_files(source,manifest,target,1,3)

    def test_session_restore_and_real_new_tool_required(self):
        before={'info':{'id':'s1'},'messages':[{'info':{'id':'m1'},'parts':[{'type':'tool','state':{'status':'completed'}}]}]}
        checkpoint=session.checkpoint(before);self.assertTrue(session.check_after(checkpoint,before,False)['passed'])
        with self.assertRaises(ValueError):session.check_after(checkpoint,before,True)
        after=deepcopy(before);after['messages'].append({'info':{'id':'m2'},'parts':[{'type':'tool','state':{'status':'completed'}}]})
        self.assertTrue(session.check_after(checkpoint,after,True)['passed'])
        after['messages'][0]['parts'][0]['state']['status']='error'
        with self.assertRaises(ValueError):session.check_after(checkpoint,after,True)
        before['messages'][0]['parts'][0]['state']['status']='running'
        with self.assertRaises(ValueError):session.checkpoint(before)


if __name__=='__main__':unittest.main()
