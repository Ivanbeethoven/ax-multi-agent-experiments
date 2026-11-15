"""Configuration/receipt boundaries: no cloud resources or real mounts."""
from copy import deepcopy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from bench.suite_catalog import template, fio_params
from bench.suite_plan import build
from bench.suite_mount import settings, overlay, validate_binding
from bench.suite_support import sizing, reference, review
from bench.experiment_io import write_new
from bench.runtime import prepare, node
from bench.fio_workload import arguments, validate_raw


class DefaultsTests(unittest.TestCase):
    def test_bounded_random_work_keeps_large_file_range_and_sync(self):
        random=fio_params('randwrite',False);sequential=fio_params('write',False)
        self.assertEqual(random['numjobs']*random['file_bytes_per_job'],32*1024**3)
        self.assertEqual(random['numjobs']*random['io_bytes_per_job']//random['block_bytes'],524288)
        self.assertEqual(sequential['io_bytes_per_job'],sequential['file_bytes_per_job'])
        self.assertTrue(random['fsync_every_write'])
        delta=next(c for c in template(study='ablations')['cases'] if c['name']=='delta')
        self.assertEqual(delta['params']['children'],1)
        sizes=sizing(build(template('main')))
        fork=next(c for c in sizes['cells'] if c.get('children')==64)
        self.assertEqual(fork['reserved_memory_bytes'],512*1024**3)
        self.assertEqual(fork['eager_base_plus_children_logical_bytes'],65*1000000*4096)

    def test_invalid_settings_fail_during_planning(self):
        changes=[('fio',{'iodepth':True}),('fio',{'direct':True}),('fio',{'rw':'randread-typo'}),
                 ('fio',{'io_bytes_per_job':7}),('fio',{'fsync_every_write':False}),
                 ('trace',{'tasks_per_agent':2}),('trace',{'replay':'partial'}),('trace',{'pacing':'on'}),
                 ('handoff',{'moved_agents':2}),('fork',{'creation':'serial'})]
        for kind,fields in changes:
            with self.subTest(kind=kind,fields=fields):
                cfg=template();row=next(c for c in cfg['cases'] if c['workload']==kind);row['params'].update(fields)
                with self.assertRaises(ValueError):build(cfg)
        for mutate in (lambda c:c.update(cache_budget_bytes_per_client=0),
                       lambda c:c.update(ssd_clean_cache_bytes=1024),
                       lambda c:c['cases'][0]['arms']['native'].update(payload_cache_bytes=True),
                       lambda c:c['cases'][0].update(suite='C3')):
            cfg=template();mutate(cfg)
            with self.assertRaises(ValueError):build(cfg)
        cfg=template();cfg['cases']=[next(c for c in cfg['cases'] if c['workload']=='handoff')]
        for pool in cfg['clusters']:pool['agent_slots']=1
        with self.assertRaises(ValueError):build(cfg)  # Needs source AND target.

    def test_binding_rejects_large_implicit_caches_and_wrong_route(self):
        with tempfile.TemporaryDirectory() as t:
            folder=Path(t);plan=build(template());path=folder/'plan.json';write_new(path,plan)
            for arm_id in ('juicefs','native','packed','entity-native'):
                cell=next(c for c in plan['cells'] if c['arm_id']==arm_id)
                self.assertEqual(cell['client_settings'],settings(plan['config'],cell['arm']))
                binding={'plan':reference(path),'cell_id':cell['id']}
                cfg=overlay({'packed_manifest_key':'base/p','entity_native_manifest_key':'base/n'},settings(plan['config'],cell['arm']))
                args=(cell['arm']['product'],cell['pool'],'tikv',cell['arm']['hint'])
                validate_binding(binding,cfg,*args)
                bad=deepcopy(cfg)
                if arm_id=='juicefs':bad['cache_mib']=0
                elif arm_id=='native':bad['cache']['read_memory_bytes']=8*1024**3
                else:bad['entity_read_cache_bytes']=1
                with self.assertRaises(ValueError):validate_binding(binding,bad,*args)
                with self.assertRaises(ValueError):validate_binding(binding,cfg,args[0],'wrong-pool',args[2],args[3])
            cell=next(c for c in plan['cells'] if c['arm_id']=='native-workspace')
            binding={'plan':reference(path),'cell_id':cell['id']}
            cfg=settings(plan['config'],cell['arm'])
            with self.assertRaises(ValueError):validate_binding(binding,cfg,'brewfs',cell['pool'],'tikv','auto')

    def test_memory_cache_mount_is_plan_bound_and_checked_again_on_node(self):
        with tempfile.TemporaryDirectory() as t:
            folder=Path(t);point=folder/'mnt';point.mkdir()
            plan=build(template());cell=next(c for c in plan['cells'] if c['arm_id']=='juicefs')
            planpath=folder/'plan.json';write_new(planpath,plan)
            cfg=overlay({'metadata_url':'tikv://127.0.0.1:2379/test'},settings(plan['config'],cell['arm']))
            config=folder/'config.json';write_new(config,cfg)
            binding={'plan':reference(planpath),'cell_id':cell['id']}
            kwargs=dict(config_ref=reference(config),binary_ref=reference(Path(sys.executable)),filesystem='juicefs',
                        cluster='pool-b',point=str(point),cwd=str(folder),output=folder/'draft',environment=[],
                        fstypes=['fuse.juicefs'],metrics_address='127.0.0.1:8910',metadata_backend='tikv',suite_binding=binding)
            s=prepare.mount(**kwargs)
            cmd=folder/'command.json';write_new(cmd,review(s['command']['path'],'unit-test','configuration only, never run this fake mount'))
            s['command']=reference(cmd)
            with patch.object(node,'mounts',return_value=[]):
                command,_=node.validate_mount(s)
                self.assertIn('memory',command['argv']);self.assertIn('--cache-large-write=true',command['argv'])
                self.assertEqual(command['argv'][command['argv'].index('--cache-size')+1],'512')
                missing=deepcopy(s);missing.pop('suite_binding')
                with self.assertRaises(ValueError):node.validate_mount(missing)
            with self.assertRaises(ValueError):prepare.mount(**dict(kwargs,suite_binding=None,output=folder/'bad'))
            planpath.write_text(json.dumps(dict(plan,campaign_id='tamper')))
            with patch.object(node,'mounts',return_value=[]):
                with self.assertRaises(ValueError):node.validate_mount(s)

    def test_same_fio_bytes_cannot_hide_changed_mode_block_seed_or_job(self):
        p=fio_params('randread',True);seed=9173
        argv=arguments({'params':p,'root':'/mnt/data','seed':seed,'binary':{'path':'/usr/bin/fio'}},'measure','/tmp/fio.json')
        options=dict(x[2:].split('=',1) for x in argv[1:])
        raw={'jobs':[{'jobname':'job-0','job options':options,'read':{'io_bytes':p['io_bytes_per_job']},'write':{'io_bytes':0}}]}
        validate_raw(raw,p,seed,'measure')
        for k,v in [('rw','read'),('bs','1048576'),('randseed','42'),('fsync','0'),('ioengine','libaio'),('size','4096'),('rate_iops','1')]:
            bad=deepcopy(raw);bad['jobs'][0]['job options'][k]=v
            with self.subTest(option=k):
                with self.assertRaises(ValueError):validate_raw(bad,p,seed,'measure')
        raw['jobs'][0]['jobname']='job-99'
        with self.assertRaises(ValueError):validate_raw(raw,p,seed,'measure')


if __name__=='__main__':unittest.main()
