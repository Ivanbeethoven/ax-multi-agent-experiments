"""Local protocol/adapter regression tests. Never starts a filesystem or cloud VM."""
from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from bench.suite_catalog import template, fio_params, small_params
from bench.suite_plan import build, check_plan, fingerprint
from bench import suite_state as state
from bench.experiment_io import write_new, digest
from bench.fio_workload import arguments, run as fio_run
from bench.runtime.workloads import counts
from bench.suite_report import costs, report, paired_blocks
from bench import archive_check
from bench.runtime.prepare import native_argv, anchor


def configured_parallel(shared=False):
    s=template();s['cases']=s['cases'][:2]
    s['input_manifest_sha256']='a'*64;s['build_manifest_sha256']='b'*64
    for pool in s['clusters']:
        pool['inventory_sha256']='c'*64
        if shared:pool['resource_ids']+=['service:shared-objects']
    return s


def configured(shared=False):
    s=configured_parallel(shared);s.pop('placement')
    return s


class PlanTests(unittest.TestCase):
    def test_all_profiles_deterministic_and_non_executing(self):
        for profile in ('pilot','main'):
            for study in ('systems','ablations'):
                a=build(template(profile,study));self.assertEqual(a,check_plan(deepcopy(a)))
                self.assertEqual(a,build(template(profile,study)))
                self.assertFalse(a['provenance_bound'])
                self.assertFalse(a['execution_enabled'])
                for c in a['cells']:
                    if c['workload']=='smallfiles':self.assertEqual(c['params']['files'],1000000)
                for b in a['blocks']:
                    members={c['id']:c['pool'] for c in a['cells'] if c['id'] in b['cells']}
                    self.assertEqual(members,b['cell_pools'])
                    self.assertEqual(set(members.values()),set(b['pools']))
                    self.assertIsNone(b['pool'])
                    self.assertEqual(b['pools'],['pool-a','pool-b'])

    def test_pool_rotation_and_tamper(self):
        c=configured();c['repetitions']=3;p=build(c)
        blocks=[b for b in p['blocks'] if b['condition']==p['blocks'][0]['condition']]
        self.assertEqual([b['pool'] for b in blocks],['pool-a','pool-b','pool-a'])
        p['cells'][0]['params']['numjobs']=123
        with self.assertRaises(ValueError):check_plan(p)

    def test_only_external_juicefs_capacity_and_hardware(self):
        for mutate in (lambda c:c.update(external_baselines=['juicefs','other']),
                       lambda c:c['clusters'][1].update(hardware_class='unmatched'),
                       lambda c:c['cases'][0]['params'].update(agents=10000)):
            c=configured();mutate(c)
            with self.assertRaises(ValueError):build(c)

    def test_cache_ablation_reuses_asset_but_layout_does_not(self):
        c=template(study='ablations');c.pop('placement');c['clusters']=c['clusters'][:1]
        p=build(c);cache=[x for x in p['cells'] if x['case']=='payload-cache']
        self.assertEqual(len({x['asset_id'] for x in cache}),1)
        layout=[x for x in p['cells'] if x['case']=='backend-layout']
        self.assertEqual(len({x['asset_id'] for x in layout}),5)
        packed=[x for x in p['cells'] if x['case']=='packed-metadata']
        self.assertTrue(all('entity-native-lower-adapter' in x['required_gates'] for x in packed))

    def test_each_workspace_is_an_individually_recorded_lifecycle(self):
        from bench.suite_support import drafts
        p=build(template('main'));c=next(x for x in p['cells'] if x['workload']=='smallfiles')
        self.assertEqual(len(c['stages']),27)
        for w in range(1,5):
            self.assertIn(f'verify-w{w:02}',c['stages'])
            self.assertIn(f'verify-end-w{w:02}',c['observation_marks'])
        self.assertLess(c['stages'].index('verify-w01'),c['stages'].index('create-w02'))
        with tempfile.TemporaryDirectory() as t:
            subset=configured();subset['cases']=[next(c for c in template()['cases'] if c['workload']=='smallfiles')]
            plan=build(subset);drafts(plan,Path(t)/'drafts');cell=plan['cells'][0]
            packet=json.loads((Path(t)/'drafts'/cell['id']/'work-packet.draft.json').read_text())
            self.assertEqual(packet['results'][0]['workspace_index'],0)
            self.assertEqual(len(packet['results'][0]['raw_logs']),8)
            self.assertIsNone(packet['results'][0]['evidence']['sha256'])
        cfg=template('main',study='ablations');cfg['clusters'][0]['agent_slots']=1;cfg['clusters'][1]['agent_slots']=1
        with self.assertRaises(ValueError):build(cfg)


class StateTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);self.db=self.root/'state.sqlite'
        self.p=build(configured(True));state.initialize(self.db,self.p)
    def tearDown(self):self.tmp.cleanup()
    def put(self,name,value):
        path=self.root/name;write_new(path,value);return state.reference(path)
    def ready(self,cells=None):
        proof=self.put('test-only-proof-'+str(len(list(self.root.glob('test-only-proof-*.json'))))+'.json',{'passed':True,'purpose':'unit-test'})
        cells=self.p['cells'] if cells is None else cells
        for c in cells:
            r=self.put('q-'+c['id']+'.json',{'schema':'suite-qualification-v1','plan_sha256':self.p['plan_sha256'],
                'cell_id':c['id'],'checks':[{'gate':g,'evidence':proof,'equals':{'passed':True}} for g in c['required_gates']]})
            state.qualify(self.db,r)
        for a in self.p['assets']:
            if a['id'] not in {c['asset_id'] for c in cells}:continue
            r=self.put('a-'+a['id']+'.json',{'schema':'suite-asset-v1','plan_sha256':self.p['plan_sha256'],'asset_id':a['id'],
                'native_binding':'unit-test-only','timings_seconds':{'import':0.01},
                'stages':{s:{'evidence':proof,'equals':{'passed':True}} for s in a['required_stages']}})
            state.register_asset(self.db,r)
        return proof
    def test_no_implicit_ready_and_no_expiry(self):
        self.assertIsNone(state.claim(self.db,'pool-a','test')['cell'])
        self.ready();a=state.claim(self.db,'pool-a','test')
        self.assertEqual(state.claim(self.db,'pool-a','test')['token'],a['token'])
        self.assertTrue(state.claim(self.db,'pool-a','test')['already_claimed'])
        with self.assertRaises(ValueError):state.claim(self.db,'pool-a','other')
        self.assertIsNone(state.status(self.db)['lease_expiry'])
    def test_shared_resource_parallel_claim_serializes(self):
        self.ready()
        with ThreadPoolExecutor(max_workers=2) as e:
            results=list(e.map(lambda pool:state.claim(self.db,pool,pool),('pool-a','pool-b')))
        self.assertEqual(sum(x['cell'] is not None for x in results),1)
    def test_tampered_nested_evidence_rolls_back_claim(self):
        proof=self.ready();Path(proof['path']).write_text('{"passed":false}')
        with self.assertRaises(ValueError):state.claim(self.db,'pool-a','test')
        self.assertEqual(state.status(self.db)['reserved_resources'],[])
    def test_started_stage_is_not_replayed(self):
        self.ready();lease=state.claim(self.db,'pool-a','test');cell=lease['cell']
        source=self.put('source.json',{'purpose':'unit-test'})
        stage=cell['stages'][0]
        step=self.put('step.json',{'schema':'experiment-step-v1','reviewed':True,'cell_id':cell['id'],'stage':stage,
            'argv':[str(Path(sys.executable).resolve()),'-c','print(123)'],'cwd':str(self.root),
            'executable_sha256':digest(Path(sys.executable).resolve()),'inputs':[source]})
        self.assertEqual(state.run_stage(self.db,lease,stage,step,self.root/'stage-a'),0)
        with self.assertRaises(Exception):state.run_stage(self.db,lease,stage,step,self.root/'stage-b')
        self.assertFalse((self.root/'stage-b').exists())
    def test_weak_closure_cannot_release(self):
        self.ready();lease=state.claim(self.db,'pool-a','test')
        proof=self.put('closure.json',{'cell_id':lease['cell']['id'],'passed':True})
        receipt=self.put('finish.json',{'schema':'suite-cell-close-v1','outcome':'failed','cell_id':lease['cell']['id'],
            'closure':{k:{'evidence':proof,'equals':{'passed':True}} for k in ('processes','mounts','debt')}})
        with self.assertRaises((ValueError,KeyError)):state.finish(self.db,lease,receipt)
        self.assertTrue(state.status(self.db)['reserved_resources'])
    def test_report_preserves_incomplete_blocks(self):
        r=report(self.db);self.assertEqual(r['accepted_blocks'],0)
        self.assertEqual(len(r['samples']),len(self.p['cells']))
        self.assertTrue(all(not b['comparisons'] for b in r['blocks']))
    def close_failed(self,lease,label):
        from bench.experiment_step import seal
        folder=self.root/label;folder.mkdir()
        write_new(folder/'failed.json',{'cell_id':lease['cell']['id'],'failure':'test interruption; retained'})
        archive=self.root/(label+'.tar.gz')
        archive_check.pack(folder,['failed.json'],archive,lease['cell']['id'],'controller')
        retained=self.root/(label+'-retention.json');seal(folder,['failed.json'],retained)
        proof=self.put(label+'-closure.json',{'cell_id':lease['cell']['id'],'all_exited':True,'all_unmounted':True,
            'pending_upload_bytes':0,'pending_upload_operations':0})
        receipt=self.put(label+'-close.json',{'schema':'suite-cell-close-v1','outcome':'failed','failure_reason':'test interruption',
            'cell_id':lease['cell']['id'],'plan_sha256':self.p['plan_sha256'],
            'closure':{k:{'evidence':proof,'equals':eq} for k,eq in
                [('processes',{'all_exited':True}),('mounts',{'all_unmounted':True}),('debt',{'pending_upload_bytes':0})]},
            'retention':{'root':str(folder),'manifest':state.reference(retained)},'archives':[state.reference(archive)]})
        state.finish(self.db,lease,receipt)
    def test_failed_evidence_closure_releases_only_after_whole_block(self):
        self.ready();first=state.claim(self.db,'pool-a','test');self.close_failed(first,'first')
        self.assertTrue(state.status(self.db)['reserved_resources'])
        second=state.claim(self.db,'pool-a','test');self.assertNotEqual(first['cell']['id'],second['cell']['id'])
        self.close_failed(second,'second');self.assertEqual(state.status(self.db)['reserved_resources'],[])
        self.assertIsNotNone(state.claim(self.db,'pool-b','other')['cell'])
        self.assertEqual(report(self.db)['accepted_blocks'],0)


class ParallelPlanTests(unittest.TestCase):
    def test_drafts_expose_each_pool_queue(self):
        from bench.suite_support import drafts
        p=build(configured_parallel())
        with tempfile.TemporaryDirectory() as t:
            folder=Path(t)/'drafts';drafts(p,folder)
            queues=json.loads((folder/'queues.json').read_text())
            self.assertEqual(queues['plan_sha256'],p['plan_sha256'])
            for q in queues['pools']:
                self.assertEqual(q['cells'],[c['id'] for c in p['cells'] if c['pool']==q['pool']])
                self.assertEqual(q['assets'],[a['id'] for a in p['assets'] if a['pool']==q['pool']])
    def test_product_mapping_and_exact_arm_override(self):
        cfg=configured_parallel();cfg['cases']=[next(c for c in template()['cases'] if c['workload']=='trace')]
        third=deepcopy(cfg['clusters'][0]);third.update(id='pool-c',resource_ids=['host:third'],observer_ids=['third'])
        cfg['clusters'].append(third);cfg['placement']['arm_pools']={'native':['pool-c'],'packed':['pool-a']}
        plan=build(cfg)
        for c in plan['cells']:
            expected='pool-b' if c['arm_id']=='juicefs' else 'pool-c' if c['arm_id']=='native' else 'pool-a'
            self.assertEqual(c['pool'],expected)
            self.assertEqual(c['resource_ids'],next(p['resource_ids'] for p in cfg['clusters'] if p['id']==expected))
            self.assertIn('cross-pool-hardware-equivalence',c['required_gates'])
        self.assertEqual(plan['blocks'][0]['pools'],['pool-a','pool-b','pool-c'])
    def test_explicit_repeat_rotation_and_pool_local_asset_reuse(self):
        cfg=configured_parallel();cfg['repetitions']=3
        cfg['placement']['product_pools']={'brewfs':['pool-a','pool-b'],'juicefs':['pool-b','pool-a']}
        p=build(cfg)
        first=[c for c in p['cells'] if c['case']==cfg['cases'][0]['name'] and c['arm_id']=='native']
        self.assertEqual([c['pool'] for c in first],['pool-a','pool-b','pool-a'])
        self.assertEqual(first[0]['asset_id'],first[2]['asset_id']);self.assertNotEqual(first[0]['asset_id'],first[1]['asset_id'])
    def test_mapping_capacity_hardware_and_typo_are_not_silently_ignored(self):
        for mutate in (lambda c:c['placement']['product_pools'].pop('juicefs'),
                       lambda c:c['placement']['product_pools'].update(juicefs=['missing']),
                       lambda c:c['placement']['product_pools'].update(juicefs=['pool-b','pool-b']),
                       lambda c:c['placement']['arm_pools'].update(typo=['pool-a']),
                       lambda c:c['clusters'][1].update(supported_suites=['C3']),
                       lambda c:c['clusters'][1].update(hardware_class='different'),
                       lambda c:c.update(placement=None)):
            cfg=configured_parallel();mutate(cfg)
            with self.assertRaises(ValueError):build(cfg)
    def test_legacy_mode_remains_opt_in_and_rebuilds(self):
        cfg=configured();legacy=build(cfg);check_plan(legacy)
        self.assertNotIn('cell_pools',legacy['blocks'][0]);self.assertEqual(legacy['blocks'][0]['pool'],'pool-a')
        cfg['placement']={'mode':'paired-pool'}
        explicit=build(cfg)
        self.assertEqual(legacy['cells'],explicit['cells']);self.assertEqual(legacy['blocks'],explicit['blocks'])


class ParallelStateTests(unittest.TestCase):
    put=StateTests.put
    ready=StateTests.ready
    close_failed=StateTests.close_failed
    tearDown=StateTests.tearDown
    test_started_stage_is_not_replayed=StateTests.test_started_stage_is_not_replayed
    test_tampered_nested_evidence_rolls_back_claim=StateTests.test_tampered_nested_evidence_rolls_back_claim
    test_weak_closure_cannot_release=StateTests.test_weak_closure_cannot_release
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);self.db=self.root/'state.sqlite'
        self.p=build(configured_parallel());state.initialize(self.db,self.p)
    def test_same_pair_is_claimed_concurrently_and_identity_is_cell_bound(self):
        self.ready()
        with ThreadPoolExecutor(max_workers=2) as executor:
            a,b=list(executor.map(lambda pool:state.claim(self.db,pool,pool),('pool-a','pool-b')))
        self.assertEqual(a['cell']['paired_block'],b['cell']['paired_block'])
        self.assertEqual({a['cell']['arm']['product'],b['cell']['arm']['product']},{'brewfs','juicefs'})
        self.assertNotEqual(a['token'],b['token'])
        self.assertEqual(state.claim(self.db,'pool-a','pool-a')['token'],a['token'])
        with self.assertRaises(ValueError):state.claim(self.db,'pool-a','stranger')
        for wrong in (dict(a,cell=b['cell']),dict(a,owner='stranger'),dict(a,token=b['token'])):
            with state.transaction(self.db) as db:
                with self.assertRaises(ValueError):state.admitted(db,wrong)
        status=state.status(self.db)
        self.assertEqual(len(status['active_leases']),2);self.assertIsNone(status['lease_expiry'])
        self.assertTrue(all('token' not in x for x in status['active_leases']))
    def test_missing_counterpart_readiness_does_not_block_own_pool(self):
        self.ready([c for c in self.p['cells'] if c['pool']=='pool-a'])
        self.assertIsNotNone(state.claim(self.db,'pool-a','a')['cell'])
        self.assertIsNone(state.claim(self.db,'pool-b','b')['cell'])
    def test_actual_stages_on_different_pools_overlap(self):
        self.ready();leases=[state.claim(self.db,pool,pool) for pool in ('pool-a','pool-b')]
        source=self.put('parallel-stage-source.json',{'purpose':'unit-test only'})
        ready=[str(self.root/'ready-a'),str(self.root/'ready-b')];steps=[]
        for n,lease in enumerate(leases):
            # Each real child can succeed only while its counterpart is alive.
            code=('from pathlib import Path\nimport time\n'
                  f'Path({ready[n]!r}).write_text("ready")\n'
                  'deadline=time.monotonic()+5\n'
                  f'while not all(Path(p).is_file() for p in {ready!r}):\n'
                  ' if time.monotonic()>deadline:raise SystemExit(42)\n time.sleep(.01)\n')
            steps.append(self.put(f'parallel-{n}.json',{'schema':'experiment-step-v1','reviewed':True,
                'cell_id':lease['cell']['id'],'stage':lease['cell']['stages'][0],
                'argv':[str(Path(sys.executable).resolve()),'-c',code],'cwd':str(self.root),
                'executable_sha256':digest(Path(sys.executable).resolve()),'inputs':[source]}))
        with ThreadPoolExecutor(max_workers=2) as executor:
            results=list(executor.map(lambda n:state.run_stage(self.db,leases[n],leases[n]['cell']['stages'][0],
                steps[n],self.root/f'parallel-output-{n}'),range(2)))
        self.assertEqual(results,[0,0]);self.assertEqual(len(state.status(self.db)['stages']),2)
    def test_shared_service_serializes_cells_even_in_different_clusters(self):
        self.db=self.root/'shared.sqlite';self.p=build(configured_parallel(True));state.initialize(self.db,self.p)
        self.ready()
        with ThreadPoolExecutor(max_workers=2) as executor:
            leases=list(executor.map(lambda pool:state.claim(self.db,pool,pool),('pool-a','pool-b')))
        self.assertEqual(sum(x['cell'] is not None for x in leases),1)
        active=next(x for x in leases if x['cell']);self.close_failed(active,'shared-first')
        other='pool-b' if active['cell']['pool']=='pool-a' else 'pool-a'
        self.assertIsNotNone(state.claim(self.db,other,other)['cell'])
    def test_fast_pool_moves_ahead_without_releasing_slow_counterpart(self):
        self.ready();a=state.claim(self.db,'pool-a','a');b=state.claim(self.db,'pool-b','b')
        self.close_failed(a,'fast');next_a=state.claim(self.db,'pool-a','a')
        self.assertNotEqual(next_a['cell']['paired_block'],a['cell']['paired_block'])
        self.assertEqual(state.claim(self.db,'pool-b','b')['token'],b['token'])
        with state.transaction(self.db) as db:
            with self.assertRaises(ValueError):state.admitted(db,a)
        self.close_failed(b,'slow')
        status=state.status(self.db)
        self.assertEqual(status['blocks'][0]['status'],'done')
        self.assertEqual({r['cell_id'] for r in status['reserved_resources']},{next_a['cell']['id']})
        self.assertEqual(report(self.db)['accepted_blocks'],0)
    def test_cross_pool_report_joins_condition_repeat_and_preserves_incomplete(self):
        samples=[{'id':c['id'],'arm':c['arm_id'],'accepted':True,'metrics':{
            'duration':{'value':2 if c['arm_id']=='native' else 10,'unit':'s','direction':'lower'}}} for c in self.p['cells']]
        pairs=paired_blocks(self.p,list(reversed(samples)))
        self.assertTrue(all(x['complete_accepted_block'] for x in pairs))
        self.assertEqual(pairs[0]['comparisons'][0]['advantage_ratio'],5)
        self.assertEqual(pairs[0]['pools'],['pool-a','pool-b'])
        self.assertEqual(pairs[0]['cell_pools'],self.p['blocks'][0]['cell_pools'])
        samples[0]['accepted']=False
        incomplete=paired_blocks(self.p,samples)
        self.assertFalse(incomplete[0]['complete_accepted_block']);self.assertEqual(incomplete[0]['comparisons'],[])
        self.assertTrue(incomplete[1]['complete_accepted_block'])


class WorkloadTests(unittest.TestCase):
    def test_worker_is_bound_to_mount_and_emits_only_unreviewed_drafts(self):
        from bench.runtime.workloads import worker
        with tempfile.TemporaryDirectory() as t:
            p=Path(t);mount=p/'mount.json';write_new(mount,{'mount_point':str(p/'mnt')})
            binary=p/'binary';binary.write_bytes(b'unit-test placeholder; never executed')
            request={'kind':'smallfiles','binary':state.reference(binary),'root':str(p/'mnt'/'dataset'),
                'mount':{'spec':state.reference(mount)},'worker_id':'w1','workload_id':'cell-w01',
                'params':small_params(True),'workspace':1,'seed':9173,'cwd':str(p)}
            file=p/'request.json';write_new(file,request)
            v=worker(file,p/'draft');self.assertFalse(v['reviewed'])
            step=json.loads((p/'draft'/'step.json').read_text());self.assertFalse(step['reviewed'])
            self.assertEqual(step['argv'][-4:],['--seed','9173','--workspace','1'])
            with self.assertRaises(ValueError):worker(file,p/'mnt'/'unsafe-output')
    def test_counts_include_prewarm_and_cow(self):
        p=small_params(False);v=counts(p)
        self.assertEqual(v['verified_reads'],140288)
        self.assertEqual(v['writes'],4352)
        p['mixed_ops']=1
        with self.assertRaises(ValueError):counts(p)
    def test_fixed_work_fio(self):
        r={'root':'/mnt/data/dataset','binary':{'path':'/usr/bin/fio'},'seed':2,'params':fio_params('randrw',True)}
        args=arguments(r,'measure','/tmp/fio.json')
        self.assertIn('--fsync=1',args);self.assertIn('--allow_file_create=0',args)
        self.assertFalse(any('time_based' in a for a in args))
        r['params']['direct']=True
        with self.assertRaises(ValueError):arguments(r,'measure','/tmp/fio.json')
    def test_cost_includes_interruption_and_archive_until_deletion(self):
        with tempfile.TemporaryDirectory() as t:
            p=Path(t)/'cost.json';write_new(p,{'resources':[{'id':'vm','created_at':'2026-11-01T00:00:00Z',
                'deleted_at':'2026-11-01T03:00:00Z','hourly_rate':4,'currency':'CNY'}]})
            r=costs(p);self.assertEqual(r['configuration_estimate'],12);self.assertTrue(r['all_resources_released'])
    def test_unified_native_routes_and_entity_native_cache_restriction(self):
        c={'volume_format':'workspace-entity-v1','workspace':'11111111-1111-4111-8111-111111111111',
           'entity_volume_id':'22222222-2222-4222-8222-222222222222','workspace_namespace':'unit-test',
           'entity_native_manifest_key':'base/native','entity_read_cache_bytes':0,'meta':{'backend':'tikv'},
           'data':{'backend':'s3','s3':{}},'cache':{'writeback_mode':'upload_before_commit'},
           'fuse':{'cache_ttl_ms':0,'keep_cache':False}}
        argv=native_argv('brewfs','/binary','/config',c,'/mount','pool-a',metadata_backend='tikv')
        self.assertIn('--entity-native-manifest-key',argv)
        before=anchor('brewfs',c);c['entity_native_manifest_key']='other'
        self.assertNotEqual(before,anchor('brewfs',c))
        c['entity_read_cache_bytes']=536870912
        with self.assertRaises(ValueError):native_argv('brewfs','/binary','/config',c,'/mount','pool-a',metadata_backend='tikv')
        jf={'metadata_url':'tikv://127.0.0.1:2379/test','writeback':False,'cache_dir':'/cache',
            'cache_mib':0,'buffer_mib':512,'attr_cache_seconds':0,'entry_cache_seconds':0}
        self.assertIn(jf['metadata_url'],native_argv('juicefs','/jfs','/config',jf,'/mount','pool-a',metadata_backend='tikv'))


class EvidenceTests(unittest.TestCase):
    def test_smallfile_work_receipt_rejects_duplicate_workspace_and_missing_phase_work(self):
        import csv
        from bench.suite_validate import work
        with tempfile.TemporaryDirectory() as t:
            folder=Path(t);cfg=configured();cfg['cases']=[next(c for c in template()['cases'] if c['workload']=='smallfiles')]
            params=cfg['cases'][0]['params'];params.update(workspaces=2,workers=1,hot_files=4,cow_files=2,read_ops=4,write_ops=4,mixed_ops=4)
            plan=build(cfg);cell=plan['cells'][0];packet={'results':[]}
            phase_counts={'cold-read':(4,0),'lower-prewarm':(8,0),'hot-read':(4,0),'cow':(0,2),
                'upper-prewarm':(4,0),'hot-write':(0,4),'upper-rewarm':(4,0),'mixed':(3,1)}
            for w in range(2):
                out=folder/str(w);out.mkdir();log=out/'worker-0.csv'
                with log.open('w',newline='') as stream:
                    writer=csv.writer(stream);writer.writerow(['phase','operation','file','version','read_bytes','write_bytes','io_ns','application_ns'])
                    for phase,(reads,writes) in phase_counts.items():
                        for op in range(reads+writes):writer.writerow([phase,op,op%4,0,4096 if op<reads else 0,0 if op<reads else 4096,1,2])
                value=dict(counts(params),seed=9173,workspace=w+1,
                    **{k:params[k] for k in ('workers','hot_files','cow_files','read_ops','write_ops','mixed_ops')},
                    phases=[{'phase':ph,'reads':a,'writes':b} for ph,(a,b) in phase_counts.items()])
                result=out/'result.json';write_new(result,value)
                packet['results'].append({'agent_index':0,'workspace_index':w,'evidence':state.reference(result),'raw_logs':[state.reference(log)]})
            self.assertTrue(work(plan,cell['id'],packet)['passed'])
            wrong=deepcopy(packet);wrong['results'][1]['workspace_index']=0
            with self.assertRaises(ValueError):work(plan,cell['id'],wrong)
            log=Path(packet['results'][0]['raw_logs'][0]['path'])
            lines=log.read_text().splitlines();log.write_text('\n'.join(lines[:-1])+'\n')
            packet['results'][0]['raw_logs'][0]=state.reference(log)
            with self.assertRaises(ValueError):work(plan,cell['id'],packet)

    def test_archive_complete_members_hashes_and_corruption(self):
        with tempfile.TemporaryDirectory() as t:
            p=Path(t);root=p/'raw';root.mkdir();(root/'out.log').write_bytes(b'abc');(root/'failure.json').write_text('{"failed":true}')
            file=p/'archive.tar.gz';v=archive_check.pack(root,['out.log','failure.json'],file,'cell','client')
            self.assertEqual(v['file_members'],2);self.assertTrue(archive_check.verify(file,v['sha256'])['passed'])
            with file.open('ab') as f:f.write(b'corruption')
            with self.assertRaises(ValueError):archive_check.verify(file,v['sha256'])
    def test_archive_unsafe_link_or_missing_manifest_rejected(self):
        import tarfile
        with tempfile.TemporaryDirectory() as t:
            p=Path(t)/'bad.tar.gz'
            with tarfile.open(p,'w:gz') as archive:
                item=tarfile.TarInfo('link');item.type=tarfile.SYMTYPE;item.linkname='/etc/passwd';archive.addfile(item)
            with self.assertRaises(ValueError):archive_check.verify(p,digest(p))
            for name in ('../x','/etc/x','C:/x','foo//bar'):
                with self.assertRaises(ValueError):archive_check.safe(name)
    def test_interrupted_sample_and_gap_excluded_without_erasing_metrics(self):
        from bench.suite_report import sample, CHECKS
        with tempfile.TemporaryDirectory() as t:
            p=Path(t);plan=build(configured());cell=plan['cells'][0]
            proof=p/'proof.json';write_new(proof,{'passed':True,'cell_id':cell['id'],'workload':cell['workload'],
                'workload_signature':fingerprint(cell['params']),'max_relative_change':.05,'seconds':2.0})
            ref=state.reference(proof)
            value={'schema':'suite-sample-v1','plan_sha256':plan['plan_sha256'],'cell_id':cell['id'],'uninterrupted':False,'recovered':True,
                'checks':{k:{'evidence':ref,'equals':{'passed':True}} for k in CHECKS},
                'required_observer_ids':plan['config']['clusters'][0]['observer_ids'],
                'observers':[{'id':o,'directory':str(p),'max_gap_seconds':30,'marks':['measure-start','measure-end','drain-end','verify-end']}
                             for o in plan['config']['clusters'][0]['observer_ids']],
                'metrics':{'duration':{'evidence':ref,'field':'seconds','unit':'s','direction':'lower'}},'timings_seconds':{'waiting':120}}
            file=p/'sample.json';write_new(file,value)
            with patch('bench.suite_report.audit',return_value={'passed':True}):
                r=sample(state.reference(file),plan,cell)
                self.assertFalse(r['accepted']);self.assertEqual(r['metrics']['duration']['value'],2.0)
                value.update(uninterrupted=True,recovered=False);file.write_text(json.dumps(value))
                self.assertTrue(sample(state.reference(file),plan,cell)['accepted'])
            with patch('bench.suite_report.audit',side_effect=ValueError('telemetry gap')):
                self.assertFalse(sample(state.reference(file),plan,cell)['accepted'])
    def test_fork_burst_waits_all_and_preserves_failed_child(self):
        from bench.fork_burst import run
        with tempfile.TemporaryDirectory() as t:
            p=Path(t);inputfile=p/'input.json';write_new(inputfile,{'purpose':'unit-test'})
            children=[]
            for i in range(3):
                step=p/f'c{i}.json';write_new(step,{'schema':'experiment-step-v1','reviewed':True,'cell_id':'test','stage':f'fork-c{i}',
                    'argv':[str(Path(sys.executable).resolve()),'-c',f'raise SystemExit({int(i==1)})'],
                    'cwd':str(p),'executable_sha256':digest(Path(sys.executable).resolve()),'inputs':[state.reference(inputfile)]})
                children.append({'id':f'c{i}','step':state.reference(step)})
            spec=p/'burst.json';write_new(spec,{'schema':'suite-fork-burst-v1','reviewed':True,'cell_id':'test','parallelism':3,'children':children})
            v=run(state.reference(spec),p/'output');self.assertFalse(v['passed'])
            self.assertEqual(len(v['results']),3);self.assertEqual(v['results'][1]['exit_code'],1)
            with self.assertRaises(ValueError):run(state.reference(spec),p/'output')


@unittest.skipUnless(os.environ.get('UNIFIED_NATIVE_TESTS')=='1','explicit local native-adapter test enabled with UNIFIED_NATIVE_TESTS=1')
class NativeTests(unittest.TestCase):
    def test_fio_five_modes_partial_random_work_and_corruption(self):
        binary=Path('/usr/bin/fio');self.assertTrue(binary.is_file())
        with tempfile.TemporaryDirectory(prefix='ax-unified-fio-') as t:
            out=Path(t)
            for mode in ('read','write','randread','randwrite','randrw'):
                root=out/mode;root.mkdir();p=fio_params(mode,True)
                p.update(file_bytes_per_job=262144,io_bytes_per_job=131072,block_bytes=4096,numjobs=2)
                r={'root':str(root),'binary':state.reference(binary),'seed':9173,'params':p}
                for phase in ('prepare','measure','verify'):
                    result=fio_run(r,phase,out/(mode+'-'+phase));self.assertTrue(result['passed'])
                    if phase=='measure':
                        from bench.suite_validate import work
                        cfg=template();cfg['cases']=[next(c for c in cfg['cases'] if c['name']=='fio-'+mode)]
                        cfg['cases'][0]['params']=p;plan=build(cfg);cell=plan['cells'][0]
                        packet={'results':[{'evidence':state.reference(out/(mode+'-'+phase)/'result.json'),
                                            'raw':state.reference(out/(mode+'-'+phase)/'fio.json')}]}
                        self.assertTrue(work(plan,cell['id'],packet)['passed'])
            with (root/'fio-0000.dat').open('r+b') as f:f.write(b'broken')
            with self.assertRaises(ValueError):fio_run(r,'verify',out/'corrupt-verify')
    def test_smallfiles_local_clone_raw_counts_and_isolation(self):
        binary=Path(os.environ['SMALLFILES_BINARY']);self.assertTrue(binary.is_file())
        with tempfile.TemporaryDirectory(prefix='ax-unified-small-') as t:
            p=Path(t);base=p/'base';child=p/'child'
            def cmd(mode,root,label,*args):
                return subprocess.run([str(binary),mode,'--root',str(root),'--output',str(p/label),'--files','128',*args],
                    capture_output=True,text=True)
            self.assertEqual(cmd('prepare',base,'prepared').returncode,0)
            self.assertEqual(cmd('verify-base',base,'verified').returncode,0)
            shutil.copytree(base,child)
            x=cmd('run',child,'run','--hot-files','32','--workers','4','--read-ops','64','--cow-files','16',
                  '--write-ops','32','--mixed-ops','32','--workspace','7')
            self.assertEqual(x.returncode,0,x.stderr)
            v=json.loads(x.stdout);self.assertEqual(v['verified_reads'],280);self.assertEqual(v['writes'],56)
            statefile=str(p/'run'/'expected-touched.tsv')
            self.assertEqual(cmd('verify-touched',child,'touched','--state',statefile).returncode,0)
            self.assertEqual(cmd('verify-touched',base,'isolated','--state',statefile,'--expect-base','1').returncode,0)
            self.assertEqual(cmd('verify-base',base,'base-again').returncode,0)
            first=(p/'run'/'expected-touched.tsv').read_text().splitlines()[1].split('\t')[0];index=int(first)
            with (child/'small'/f'd{index%1024:04}'/f'f{index:07}').open('r+b') as f:f.write(b'bad')
            self.assertNotEqual(cmd('verify-touched',child,'corrupt','--state',statefile).returncode,0)
            self.assertNotEqual(cmd('run',child,'run').returncode,0)


if __name__=='__main__':unittest.main()
