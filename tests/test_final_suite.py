"""Offline regression tests for measurement validity and fail-closed execution."""
import csv
import importlib.util
import json
import os
import shutil
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch, MagicMock

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'harness'))
sys.path.insert(0,str(ROOT/'paper-design/pipeline'))
import aliyun_suite as suite
import final_plan
import packed_cost
import summarize_suite
import validate


def spec():
    return {'method':'native','brewfs':{'path':'/bin/brewfs'},'juicefs':{'path':'/bin/juicefs'},
            'bucket':'owned-test','endpoint':'http://10.0.0.1:9000','redis_url':'redis://10.0.0.2:6379/3'}


class Controls(unittest.TestCase):
    def test_workspace_uuid_comes_from_json_not_log(self):
        actual='b'*32
        value=suite.workspace_reply('log mentions '+ 'a'*32+'\n'+json.dumps({'workspace_id':actual}))
        self.assertEqual(value['workspace_id'],actual)
        with self.assertRaises(ValueError):suite.workspace_reply('log '+ 'a'*32)
    def test_three_brewfs_modes_use_s3_and_real_workspace(self):
        for method in suite.METHODS[:3]:
            s=spec();s['method']=method
            cmd,env=suite.mount_command(s,'/mnt/test','/mnt/cache','1234',0,False)
            self.assertEqual(cmd[cmd.index('--data-backend')+1],'s3')
            self.assertEqual(cmd[cmd.index('--config')+1],'/mnt/cache.json')
            self.assertNotIn('local-fs',cmd)
            self.assertEqual('--workspace' in cmd,method!='native')

    def test_both_cache_arms_really_change_process_configuration(self):
        a,ea=suite.mount_command(spec(),'/mnt/test','/cache',None,0,False)
        b,eb=suite.mount_command(spec(),'/mnt/test','/cache',None,1000,True)
        self.assertEqual(a[a.index('--fuse-cache-ttl-ms')+1],'0')
        self.assertEqual(b[b.index('--fuse-cache-ttl-ms')+1],'1000')
        self.assertEqual(ea['BREWFS_CACHE_TTL_MS'],'0')
        self.assertEqual(eb['BREWFS_CACHE_TTL_MS'],'1000')
        self.assertEqual(ea['BREWFS_FUSE_KEEP_CACHE'],'false')
        self.assertEqual(eb['BREWFS_FUSE_KEEP_CACHE'],'true')
        self.assertEqual(a[a.index('--fuse-keep-cache')+1],'false')
        self.assertEqual(b[b.index('--fuse-keep-cache')+1],'true')

    def test_main_defaults_are_not_overridden(self):
        cmd, env = suite.mount_command(spec(), '/mnt/test', '/cache', None, None, None)
        self.assertNotIn('--fuse-cache-ttl-ms', cmd)
        self.assertIsNone(env['BREWFS_CACHE_TTL_MS'])
        self.assertIsNone(env['BREWFS_FUSE_KEEP_CACHE'])
        s = spec(); s['method'] = 'juicefs'
        cmd, _ = suite.mount_command(s, '/mnt/test', '/cache', None, None, None)
        self.assertNotIn('--attr-cache', cmd)

    def test_mount_uses_data_disk_cache_and_unsets_inherited_controls(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); s=spec(); s['work_root']=str(root/'data'/'work')
            with patch.dict(os.environ,{'BREWFS_CACHE_TTL_MS':'42','BREWFS_FUSE_KEEP_CACHE':'true'}):
                session=suite.Session(s,root/'logs')
                with patch.object(suite,'find_mount',return_value={'fstype':'fuse.brewfs'}), \
                     patch.object(suite.subprocess,'Popen') as popen:
                    session.mount()
                argv=popen.call_args.args[0]; env=popen.call_args.kwargs['env']
                config=suite.load(argv[argv.index('--config')+1])
                self.assertEqual(config['cache']['cache_root'],str(session.root/'cache-1'))
                self.assertNotIn('BREWFS_CACHE_TTL_MS',env)
                self.assertNotIn('BREWFS_FUSE_KEEP_CACHE',env)
                session.mount_log.close()

    def test_juicefs_writeback_is_off_and_foreground(self):
        s=spec();s['method']='juicefs'
        cmd,_=suite.mount_command(s,'/mnt/test','/new-cache',None,1000,False)
        self.assertNotIn('--writeback',cmd);self.assertNotIn('-d',cmd)
        self.assertIn('/new-cache',cmd)

    def test_unknown_observations_remain_unknown(self):
        self.assertIsNone(suite.redis_delta({'redis':None},{'redis':None}))
        d=suite.redis_delta({'redis':{'total_commands_processed':'20'}},{'redis':{'total_commands_processed':'10'}})
        self.assertIsNone(d['total_commands_processed'])

    def test_secret_urls_rejected(self):
        for url in ['redis://user:secret@host/0','https://host/x?signature=secret']:
            with self.assertRaises(ValueError):suite.no_credentials_in_url(url)

    def test_binary_change_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/'bin';p.write_bytes(b'first')
            item={'path':str(p),'sha256':suite.digest(p)}
            suite.check_binary(item);p.write_bytes(b'second')
            with self.assertRaises(ValueError):suite.check_binary(item)


class RuntimePin(unittest.TestCase):
    def setUp(self):
        self.pin={'repository':suite.RUSTFS_REPO,'branch':suite.RUSTFS_BRANCH,'frozen_commit':'a'*40,
                  'resolved_by':'test','resolved_at':'2026-10-28T00:00:00+00:00',
                  'built_from_commit':'a'*40,'brewfs_client_sha':'b'*40,
                  'image_reference':'private/rustfs@sha256:'+'c'*64,'image_digest':'sha256:'+'c'*64}
        self.image={'Id':'sha256:running','Config':{'Labels':{'org.opencontainers.image.revision':'a'*40}},
                    'RepoDigests':['other@sha256:'+'d'*64,'private/rustfs@sha256:'+'c'*64]}
    def replies(self,actual='sha256:running'):
        return ['a'*40+'\tbranch',json.dumps([self.image]),json.dumps(actual),'true']
    def test_matching_digest_need_not_be_first(self):
        with patch.object(suite,'capture',side_effect=self.replies()):
            self.assertEqual(suite.runtime_pin(self.pin,'rustfs')['image_id'],'sha256:running')
    def test_running_image_mismatch_rejected(self):
        with patch.object(suite,'capture',side_effect=self.replies('different')):
            with self.assertRaises(ValueError):suite.runtime_pin(self.pin,'rustfs')
    def test_short_sha_rejected_before_network(self):
        self.pin['frozen_commit']='abcd123'
        with patch.object(suite,'capture') as c:
            with self.assertRaises(ValueError):suite.runtime_pin(self.pin,'rustfs')
            c.assert_not_called()
    def test_branch_move_rejected(self):
        with patch.object(suite,'capture',return_value='b'*40+'\tbranch'):
            with self.assertRaises(ValueError):suite.runtime_pin(self.pin,'rustfs')


class Results(unittest.TestCase):
    def test_packed_trace_uses_existing_lower_without_copy(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); mount=root/'mount';mount.mkdir();lower=mount/'trace-root';lower.mkdir()
            case=root/'case';case.mkdir();suite.save(case/'initial-tree.json',{'file':'expected'})
            out=root/'out';out.mkdir();session=MagicMock();session.mp=mount;session.out=out
            session.s={'method':'native-overlay-pack'}
            self.assertEqual(suite.prepare_trace_root(session,case,lambda _: {'file':'expected'}),lower)
            session.command.assert_not_called()
            self.assertEqual(suite.load(out/'trace-preparation.json')['mode'],'existing_packed_lower')
            with self.assertRaises(ValueError):suite.prepare_trace_root(session,case,lambda _: {'file':'wrong'})

    def test_native_trace_materializes_initial_tree(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);case=root/'case';case.mkdir();suite.save(case/'initial-tree.json',{})
            mount=root/'mount';mount.mkdir();out=root/'out';out.mkdir()
            session=MagicMock();session.mp=mount;session.out=out;session.s={'method':'native'}
            suite.prepare_trace_root(session,case,lambda _: {})
            self.assertEqual(session.command.call_args.args[0],'initial-copy')
            self.assertEqual(suite.load(out/'trace-preparation.json')['mode'],'materialized_copy')
    def test_json_summary_does_not_mix_controls_or_failed_runs(self):
        with tempfile.TemporaryDirectory() as tmp:
            roots=[]
            for i,ttl in enumerate([0,1000,0]):
                root=Path(tmp)/str(i);root.mkdir();roots.append(root)
                suite.save(root/'result.json',{'status':'failed' if i==2 else 'ok','method':'native','mode':'diagnostics'})
                suite.save(root/'ttl-1.json',{'measurement_scope':'application','status':'ok','mode':'stat',
                    'files':10,'file_bytes':1024,'operations':100,'seed':42,'requested_controls':{'ttl_ms':ttl},
                    'wall_seconds':1,'iops':100,'read_bytes':0,'write_bytes':0})
            report=summarize_suite.summarize(roots)
            self.assertEqual(report['status_counts'],{'ok':2,'failed':1})
            self.assertEqual(len(report['entries']),2)
            self.assertTrue(all(x['samples']==1 for x in report['entries']))

    def make_csv(self,path,status='ok',number='3',rep='1'):
        with path.open('w',newline='') as f:
            csv.writer(f).writerows([validate.HEADER,['cell','native','read','same',rep,number,'1','1','1',status,'']])
    def test_invalid_numeric_fields(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/'data.csv'
            for n in ['nan','inf','-1']:
                self.make_csv(p,number=n);self.assertTrue(validate.validate_file(p))
            for r in ['0','-2','1.5']:
                self.make_csv(p,rep=r);self.assertTrue(validate.validate_file(p))
    def test_all_new_states_accepted(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/'data.csv'
            for state in suite.STATES:
                self.make_csv(p,status=state);self.assertFalse(validate.validate_file(p))
    def test_slow_success_retained_failed_value_excluded(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/'data.csv';out=Path(tmp)/'summary.json'
            self.make_csv(p,number='1')
            with p.open('a',newline='') as f:
                csv.writer(f).writerows([['c2','native','read','same','2','9999','9999','9999','9','failed',''],
                                        ['c3','native','read','same','3','3','1','1','1','ok','']])
            subprocess.run([sys.executable,str(ROOT/'paper-design/pipeline/summarize.py'),str(p),'--output-json',str(out)],
                           check=True,stdout=subprocess.PIPE)
            entry=json.loads(out.read_text())['entries'][0]
            self.assertEqual(entry['throughput_mibps']['median'],2)
            self.assertEqual(entry['status_counts'],{'ok':2,'failed':1})
            self.assertNotIn('cache-hit',entry['annotations'])
    def test_cleanup_failure_cannot_report_success(self):
        session=MagicMock();session.out=Path('/unused');session.root=Path('/work')
        session.unmount.side_effect=RuntimeError('still mounted')
        with patch.object(suite,'Session',return_value=session),patch.object(suite,'Observer'),\
             patch.object(suite,'diagnostics'),patch.object(suite,'save') as save:
            rc=suite.run({'method':'native'},'diagnostics','/unused')
            self.assertEqual(rc,1)
            self.assertEqual(save.call_args.args[1]['status'],'failed')
            self.assertIn('still mounted',save.call_args.args[1]['cleanup_error'])


class PlanAndCost(unittest.TestCase):
    def test_all_rqs_and_pairing(self):
        p=final_plan.build();ids=[c['id'] for c in p['cells']]
        self.assertEqual(len(ids),len(set(ids)))
        self.assertEqual({c['rq'] for c in p['cells']},{'RQ1','RQ2','RQ3','RQ4','RQ5'})
        self.assertIsNone(p['total_replay_timeout_s'])
        self.assertTrue(all(not c['execution_enabled'] for c in p['cells']))
        self.assertFalse(any('persisted-layout-probe' in c['requires'] for c in p['cells'] if c['rq']!='RQ3'))
    def test_same_seed_reproducible(self):self.assertEqual(final_plan.build(1,7),final_plan.build(1,7))
    def test_first_build_cost_included_once(self):
        d={'unit':'requests','native':{'F':0,'Q':[5,5,5],'U':[0,0,0],'G':[0,0,0]},
           'pack':{'F':10,'Q':[1,1,1],'U':[0,0,0],'G':[0,0,0]}}
        result=packed_cost.account(d)
        self.assertEqual(result['first_observed_crossover_K'],3)
        self.assertEqual(result['rows'][-1]['pack'],13)
        d['pack']['F']=100
        self.assertIsNone(packed_cost.account(d)['first_observed_crossover_K'])
    def test_wall_time_not_additive(self):
        with self.assertRaises(ValueError):packed_cost.account({'unit':'wall_seconds'})


@unittest.skipUnless(os.environ.get('AX_FS_PROBE'),'set AX_FS_PROBE for actual Linux filesystem tests')
class RustIntegration(unittest.TestCase):
    def test_non_sparse_corpus_fixed_counts_and_corruption(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)/'corpus';binary=os.environ['AX_FS_PROBE']
            def call(mode,ops=20,sample=1):
                return subprocess.run([binary,mode,str(root),'8','8192',str(ops),'42',str(sample)],capture_output=True,text=True)
            self.assertEqual(call('prepare').returncode,0)
            self.assertEqual(call('prepare').returncode,1,'must not overwrite an old corpus')
            self.assertEqual(call('verify').returncode,0)
            for mode,count in [('read',20*8192),('sparse4k',20*4096)]:
                result=json.loads(call(mode).stdout);self.assertEqual(result['read_bytes'],count)
                self.assertEqual(result['operations'],20)
            result=json.loads(call('stat',sample=0).stdout)
            self.assertEqual(result['latency_samples'],0);self.assertIsNone(result['p99_us'])
            f=next(root.rglob('f*'));self.assertGreater(f.stat().st_blocks,0)
            with f.open('r+b') as out:out.write(b'badbytes')
            self.assertEqual(call('verify').returncode,1)

    def test_open_loop_keeps_all_late_operations(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)/'corpus';binary=os.environ['AX_FS_PROBE']
            subprocess.run([binary,'prepare',str(root),'2','4096','2','42','0'],check=True,stdout=subprocess.PIPE)
            result=json.loads(subprocess.check_output([binary,'write',str(root),'2','4096','20','42','1','1','1'],text=True))
            self.assertEqual(result['operations'],20)
            self.assertEqual(result['on_time_fraction'],0)
            self.assertIsNotNone(result['arrival_response_p95_us'])


@unittest.skipUnless(os.environ.get('AX_TRACE_CASE') and os.environ.get('AX_TRACE_BINARY'),'set AX_TRACE_CASE/BINARY for real recorded preflight replay')
class RealTraceIntegration(unittest.TestCase):
    def test_original_rust_performance_replay(self):
        sys.path.insert(0,str(ROOT/'scripts'))
        from full_trace_common import tree
        with tempfile.TemporaryDirectory() as tmp:
            parent=Path(tmp);case=parent/'input';case.mkdir()
            source=Path(os.environ['AX_TRACE_CASE'])
            for name in ['initial','capture']:shutil.copytree(source/name,case/name,symlinks=True)
            shutil.copy2(source/'metrics.json',case/'metrics.json')
            suite.save(case/'initial-tree.json',tree(case/'initial'))
            inventory={f.relative_to(case).as_posix():suite.digest(f) for f in case.rglob('*') if f.is_file() and not f.is_symlink()}
            manifest=parent/'manifest.json';suite.save(manifest,inventory)
            binary=os.environ['AX_TRACE_BINARY']
            s=spec();s.update(work_root=str(parent/'work'),replayer={'path':binary,'sha256':suite.digest(binary)},
                trace_case=str(case),trace_manifest=str(manifest),trace_manifest_sha256=suite.digest(manifest),qualify_reopen=True)
            session=suite.Session(s,parent/'output')
            # This test exercises real POSIX replay, not FUSE/cloud qualification.
            with patch.object(session,'mount'),patch.object(session,'unmount'),patch.object(suite,'snapshot',return_value={'redis':None}):
                suite.trace(session)
            result=suite.load(session.out/'trace-result.json')
            self.assertTrue(result['completed']);self.assertFalse(result['strict_correctness'])
            self.assertIsNone(result['total_timeout_s'])


class Dispatch(unittest.TestCase):
    def test_duplicate_metadata_reservation_rejected(self):
        module_spec=importlib.util.spec_from_file_location('dispatch',ROOT/'infra/alicloud/dispatch.py')
        m=importlib.util.module_from_spec(module_spec);module_spec.loader.exec_module(m)
        a={'id':'one','host':'root@10.0.0.1','repository':'/repo','spec':'/cfg','output':'/out1','work_root':'/work1',
           'environment_file':'/secret.env','redis_reservation':'redis://10.0.0.2/1','mode':'trace'}
        b={**a,'id':'two','host':'root@10.0.0.3','output':'/out2','work_root':'/work2'}
        with self.assertRaises(ValueError):m.validate({'concurrency':2,'jobs':[a,b]})
        b['redis_reservation']='redis://10.0.0.2/2';m.validate({'concurrency':2,'jobs':[a,b]})
        cmd=m.start_command(a)
        self.assertIn('--property=RuntimeMaxSec=infinity',cmd)
        self.assertIn('--property=RemainAfterExit=yes',cmd)


if __name__=='__main__':unittest.main()
