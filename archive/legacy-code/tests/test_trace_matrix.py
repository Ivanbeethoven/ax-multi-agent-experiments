"""Offline qualification tests; no cloud or local FUSE success is claimed."""
import hashlib
import json
import os
from pathlib import Path
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'bench'))
sys.path.insert(0, str(ROOT/'bench/reference'))
import aliyun_suite as suite
import run_trace_matrix as matrix


class Matrix(unittest.TestCase):
    def test_three_phases_cover_general_methods_on_all_three_hosts(self):
        arms=matrix.DEFAULT_ARMS
        plan=matrix.rotation(['A','B','C'],None,arms)
        self.assertEqual(plan['planned_cells'],9)
        self.assertEqual(plan['independent_blocks'],3)
        self.assertTrue(plan['balanced_complete_blocks'])
        self.assertEqual(plan['phases'][0]['workers'],dict(zip(['A','B','C'],arms[:3])))
        for worker in plan['assignments']:
            self.assertEqual(set(worker['arms']),set(arms));self.assertEqual(len(worker['arms']),3)
        self.assertIsNone(plan['total_replay_timeout_s'])
    def test_kernel_defaults_are_the_main_selector(self):
        plan=matrix.rotation(['A','B','C'],None,matrix.DEFAULT_ARMS)
        self.assertEqual(len(matrix.DEFAULT_ARMS),3)
        with self.assertRaisesRegex(ValueError, 'readonly ML'):
            matrix.select_arms(['packed-kernel-default'])
        self.assertTrue(all(a.endswith('-kernel-default') for item in plan['assignments'] for a in item['arms']))
        self.assertTrue(all(v['requested_kernel_metadata_ttl_ms'] is None for v in plan['arm_controls'].values()))
        for bad in [[],['native-ttl0','native-ttl0'],['pretend-fork']]:
            with self.assertRaises(ValueError):matrix.select_arms(bad)
    def test_cache_and_controls_explicit_without_mutating_template(self):
        base={'fuse_ttl_ms':123,'fuse_keep_cache':True,'manifest_key':'old','redis_url':'redis://localhost/0'}
        spec=matrix.spec_for(base,'native-no-kmeta','batch','A',Path('/data/new'))
        self.assertEqual(spec['brewfs_cache']['read_ssd_bytes']+spec['brewfs_cache']['write_ssd_bytes'],8*1024**3)
        self.assertEqual(spec['brewfs_cache']['writeback_mode'],'upload_before_commit')
        self.assertEqual(spec['brewfs_cache']['compression'],'none')
        self.assertEqual(spec['cache_mib'],8192);self.assertEqual(spec['buffer_mib'],1024)
        self.assertEqual(spec['fuse_ttl_ms'],0);self.assertTrue(spec['fuse_keep_cache'])
        default=matrix.spec_for(base,'native-default','batch','A',Path('/data/next'))
        self.assertNotIn('fuse_ttl_ms',default);self.assertNotIn('fuse_keep_cache',default)
        self.assertEqual(base['fuse_ttl_ms'],123)
        with self.assertRaises(ValueError):matrix.spec_for(base,'packed-ttl0','batch','A',Path('/data/pack'))
    def test_metadata_and_page_retention_ablations_change_one_control(self):
        for prefix in ('native','overlay','packed'):
            enabled=matrix.arm_controls(prefix+'-cache-enabled')
            metadata=matrix.arm_controls(prefix+'-no-kmeta')
            retention=matrix.arm_controls(prefix+'-no-reopen-data')
            self.assertEqual(enabled['requested_kernel_metadata_ttl_ms'],1000)
            self.assertTrue(enabled['requested_open_keep_cache'])
            self.assertEqual(metadata['requested_kernel_metadata_ttl_ms'],0)
            self.assertEqual(metadata['requested_open_keep_cache'],enabled['requested_open_keep_cache'])
            self.assertEqual(retention['requested_kernel_metadata_ttl_ms'],enabled['requested_kernel_metadata_ttl_ms'])
            self.assertFalse(retention['requested_open_keep_cache'])
            self.assertIsNone(enabled['effective_fuse_reply_ttl_ms'])
        self.assertNotIn('juicefs-no-reopen-data',matrix.ARMS)
        for label in ('juicefs-cache-enabled','juicefs-no-kmeta','juicefs-ttl0'):
            self.assertIsNone(matrix.arm_controls(label)['requested_open_keep_cache'])
        controls=matrix.arm_controls('juicefs-no-kmeta')
        self.assertEqual(controls['open_keep_cache_control'],'not_applicable_native_conditional')

    def test_legacy_joint_disabled_arms_require_explicit_opt_in(self):
        self.assertEqual(matrix.arm_controls('native-ttl0')['group'],'legacy-combined-disabled')
        with self.assertRaisesRegex(ValueError,'allow_legacy_combined_cache'):
            matrix.run({},['native-ttl0'])

    def test_plan_cli_default_and_example_do_not_select_legacy(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'plan.json'
            with patch.object(sys,'argv',['run_trace_matrix.py','plan','--workers','A','B','C','--output',str(path)]):
                self.assertEqual(matrix.main(),0)
            planned=suite.load(path)
            self.assertEqual(planned['planned_cells'],9)
            for worker in planned['assignments']:
                self.assertEqual(set(worker['arms']),set(matrix.DEFAULT_ARMS))
        example=suite.load(ROOT/'bench/reference/trace-matrix.example.json')
        self.assertEqual(example['arms'],matrix.DEFAULT_ARMS)
        for label in example['arms']:
            s=matrix.spec_for({'fuse_ttl_ms':0,'fuse_keep_cache':False},label,'batch','A',Path('/data/test'),
                              'real-manifest' if label.startswith('packed-') else None)
            self.assertNotIn('fuse_ttl_ms',s);self.assertNotIn('fuse_keep_cache',s)

    def test_cgroup_limits_are_real_and_not_replay_deadlines(self):
        cmd=matrix.cgroup_command('ax-test','/cfg','/out',{'memory_bytes':8*1024**3,'cpu_percent':400,'environment_file':'/secret.env'})
        for wanted in ['--property=MemoryMax=8589934592','--property=MemorySwapMax=0','--property=CPUQuota=400%',
                       '--property=RuntimeMaxSec=infinity','--property=EnvironmentFile=/secret.env']:
            self.assertIn(wanted,cmd)
        expected={'memory_max_bytes':8,'swap_max_bytes':0,'cpu_quota_us':400000,'cpu_period_us':100000}
        actual={'memory.max':'8','memory.swap.max':'0','cpu.max':'400000 100000'}
        self.assertNotIn('--wait',cmd);self.assertNotIn('--pipe',cmd)
        self.assertEqual(suite.qualify_cgroup(expected,actual),expected)
        actual['cpu.max']='max 100000'
        with self.assertRaises(ValueError):suite.qualify_cgroup(expected,actual)
    def test_active_exited_systemd_unit_is_collected_without_wait_hang(self):
        states=['LoadState=loaded\nSubState=running\nExecMainStatus=0\n',
                'LoadState=loaded\nSubState=exited\nExecMainStatus=0\n']
        with patch.object(matrix.subprocess,'check_output',side_effect=states),patch.object(matrix.time,'sleep'):
            self.assertEqual(matrix.wait_unit('ax-owned'),(0,'exited'))
        with patch.object(matrix.subprocess,'check_output',return_value='LoadState=not-found\nSubState=dead\n'):
            self.assertEqual(matrix.wait_unit('ax-missing'),(None,'unknown'))

    def test_juicefs_has_xattr_capacity_and_compression_controls(self):
        s={'method':'juicefs','juicefs':{'path':'/bin/juicefs'},'redis_url':'redis://localhost/0','cache_mib':8192,'buffer_mib':1024}
        argv,_=suite.mount_command(s,'/mnt','/data/cache',None,0,None)
        for item in ['--enable-xattr','--enable-cap','--buffer-size','1024','--cache-size','8192']:
            self.assertIn(item,argv)
        self.assertNotIn('--writeback',argv)
        session=suite.Session.__new__(suite.Session)
        session.s={**s,'endpoint':'http://127.0.0.1:9000','bucket':'owned-test','volume_name':'test'}
        session.juicefs='/bin/juicefs'
        with patch.object(session,'command') as call:session.initialize()
        self.assertIn('--compress',call.call_args.args[1]);self.assertIn('none',call.call_args.args[1])
    def test_layout_hint_cannot_override_explicit_sparse_fetch_policy(self):
        base={'method':'native','brewfs':{'path':'/bin/brewfs'},'bucket':'owned',
              'endpoint':'http://localhost:9000','redis_url':'redis://localhost/0'}
        default,_=suite.mount_command(base,'/mnt','/cache',None,None,None)
        self.assertNotIn('--read-fetch-policy',default)
        for hint in (None,65536,262144,1048576,4194304):
            spec={**base,'read_fetch_policy':'sparse','ec_block_hint':hint}
            argv,_=suite.mount_command(spec,'/mnt','/cache',None,None,None)
            self.assertEqual(argv[argv.index('--read-fetch-policy')+1],'sparse')
        with self.assertRaisesRegex(ValueError,'read fetch policy'):
            suite.mount_command({**base,'read_fetch_policy':'typo'},'/mnt','/cache',None,None,None)
        planned=matrix.spec_for({**base,'read_fetch_policy':'sparse'},'native-default','batch','A',Path('/owned'))
        self.assertEqual(planned['read_fetch_policy'],'sparse')

    def test_worker_qualification_keeps_only_explicit_public_fields(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'worker.json'
            value={'worker':'A','runner_commit':'a'*40,'runner_dirty':False,'asset_manifest_sha256':'b'*64,
                   'image_id':'sha256:'+'c'*64,'image_digest':'sha256:'+'d'*64,'trace_archive_sha256':'e'*64,
                   'prepared':True,'Env':['do-not-copy'],
                   'rustfs_resources':{'NanoCpus':4_000_000_000,'Memory':8*1024**3,'MemorySwap':8*1024**3,'Env':['private']},
                   'redis_resources':{'CPUQuotaPerSecUSec':'1000000','MemoryMax':'2147483648','MemorySwapMax':'0','secret':'private'}}
            suite.save(path,value);copied=matrix.worker_qualification(path)
            self.assertNotIn('Env',copied);self.assertNotIn('Env',copied['rustfs_resources'])
            self.assertNotIn('secret',copied['redis_resources'])
            value['runner_dirty']=True;suite.save(path,value)
            with self.assertRaises(ValueError):matrix.worker_qualification(path)

    def test_archive_receipt_and_code_hashes_without_private_sources(self):
        with tempfile.TemporaryDirectory() as tmp:
            d=Path(tmp);out=d/'results';out.mkdir();archive=d/'saved.tar.gz'
            terminal={'run_id':'batch','worker_id':'A','terminal_reason':'completed','methods':{'test':{'status':'ok'}}}
            suite.save(out/'terminal.json',terminal)
            (out/'sample.log').write_text('safe observation')
            receipt=matrix.archive_results(out,archive,terminal)
            self.assertEqual(receipt['sha256'],suite.digest(archive))
            with tarfile.open(archive) as t:
                self.assertEqual(len(t.getnames()),len(set(t.getnames())))
                manifest=json.load(t.extractfile('results/archive-manifest.json'))
                self.assertIn('run_trace_matrix.py',manifest['code_sha256'])
                self.assertFalse(any(n.startswith('code/') for n in t.getnames()))
            self.assertEqual(receipt['known_credential_scan'],'passed')
    def test_potentially_live_unit_never_archived(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            with self.assertRaises(ValueError):
                matrix.archive_results(root,root/'archive.tar.gz',{'terminal_reason':'failed','methods':{'arm':{'status':'unknown'}}})

    def test_credential_bearing_results_never_archived(self):
        with tempfile.TemporaryDirectory() as tmp:
            d=Path(tmp);out=d/'results';out.mkdir();archive=d/'saved.tar.gz'
            (out/'unsafe.log').write_text('https://host/?Signature=private')
            with self.assertRaises(ValueError):matrix.archive_results(out,archive,{'run_id':'b','worker_id':'A','terminal_reason':'failed','methods':{'test':{'status':'failed'}}})
            self.assertFalse(archive.exists())


if __name__=='__main__':unittest.main()
