"""Failure boundaries plus 64 real local worker processes (not FS performance)."""
import copy
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT));sys.path.insert(0,str(ROOT/'bench'))
from bench.runtime import common, cohort, handoff, node, prepare, transport, observe
import s3_requests


def save(path,value):
    common.write_new(path,value)
    return {'path':str(path),'sha256':common.digest(path)}


def make_cohort(root,count=2,code=None):
    script=root/'job.py'
    script.write_text(code or 'import json,sys,time\nfrom pathlib import Path\ntime.sleep(.04)\nPath(sys.argv[1]).write_text(json.dumps({"completed":True,"operations":9,"remaining_handles":0}))\n')
    peer={'transport':'local','python':sys.executable,'repo':str(ROOT),
          'pins':transport.runtime_pins(ROOT,sys.executable)}
    workers=[]
    for i in range(count):
        ident=f'w{i:03}'; result=root/f'{ident}-result.json'
        step=save(root/f'{ident}-step.json',{'schema':'experiment-step-v1','reviewed':True,
            'cell_id':'test','stage':'replay','argv':[sys.executable,str(script),str(result)],'cwd':str(root),
            'executable_sha256':common.digest(sys.executable),'inputs':[{'path':str(script),'sha256':common.digest(script)}]})
        worker=save(root/f'{ident}-worker.json',{'schema':'experiment-worker-v1','reviewed':True,
            'purpose':'adapter-test','worker_id':ident,'requires_mount':False,
            'jobs':[{'id':ident+'-job','step':step,'result':{'path':str(result),
                'equals':{'completed':True,'operations':9,'remaining_handles':0}}}]})
        workers.append({'id':ident,'spec':worker,'peer':peer,'output':str(root/(ident+'-out'))})
    return save(root/'cohort.json',{'schema':'experiment-cohort-v1','reviewed':True,
        'purpose':'adapter-test','agents':count,'workers':workers,'ready_timeout_seconds':30})


@unittest.skipUnless(sys.platform=='linux','Linux /proc and process groups required')
class CohortIntegration(unittest.TestCase):
    def test_64_workers_one_release_exact_results_and_no_replay(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); ref=make_cohort(root,64)
            result=cohort.run(ref,root/'controller')
            self.assertTrue(result['passed']);self.assertEqual(result['completed_jobs'],64)
            rows=[json.loads(l) for l in (root/'controller/events.jsonl').read_text().splitlines()]
            ready=[i for i,r in enumerate(rows) if r['message']['type']=='ready']
            started=[i for i,r in enumerate(rows) if r['message']['type']=='started']
            self.assertEqual(len(ready),64);self.assertLess(max(ready),min(started))
            self.assertEqual(sum(j['result']['operations'] for j in result['jobs']),576)
            with self.assertRaises(ValueError):cohort.run(ref,root/'controller')

    def test_failed_input_aborts_all_before_execution(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);ref=make_cohort(root)
            (root/'job.py').write_text('raise SystemExit(0)')
            with self.assertRaises(ValueError):cohort.run(ref,root/'controller')
            self.assertFalse(list(root.glob('*-result.json')))
            self.assertFalse(common.load(root/'controller/failed.json')['released'])

    def test_result_mismatch_retained_no_retry(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);ref=make_cohort(root,2,'import sys\nfrom pathlib import Path\nPath(sys.argv[1]).write_text("{}")\n')
            result=cohort.run(ref,root/'controller')
            self.assertFalse(result['passed']);self.assertEqual(result['completed_jobs'],0)
            self.assertEqual(len(list(root.glob('*-result.json'))),2)
            self.assertTrue(all(j['result']=={} and j['result_sha256'] for j in result['jobs']))

    def test_large_input_hashing_is_before_barrier_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);ref=make_cohort(root,1);worker=common.load(ref['path'])['workers'][0]
            release=json.dumps({'action':'go','spec_sha256':worker['spec']['sha256']})+'\n'
            with patch.object(node,'prepare',wraps=node.prepare) as validated,patch.object(sys,'stdin',io.StringIO(release)),patch.object(node,'emit'):
                self.assertEqual(node.worker(worker['spec'],worker['output']),0)
            self.assertEqual(validated.call_count,1)

    def test_unbounded_host_rejected_for_formal_worker(self):
        with self.assertRaises(ValueError):common.cgroup_limits(prepare.AGENT_BUDGET)


class NativeBoundaries(unittest.TestCase):
    def test_formal_cohort_requires_inventory_bound_gates_and_all_observers(self):
        from test_deployment_plans import allocated
        import scale_plan
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            inventory=allocated(scale_plan.build('c1-8','docker','provided','C1',campaign='ecs-20261101'))
            inv=save(root/'inventory.json',inventory)
            work=save(root/'work.json',{'schema':'experiment-workload-contract-v1','reviewed':True,'binary_pins':['fixture'],'input_pins':['fixture']})
            metric=save(root/'metrics.json',{'schema':'experiment-metric-contract-v1','reviewed':True,'source_pins':['fixture'],
                'bindings':[{'observer_id':'c1-rustfs-001','target_id':'native','required_names':['requests_total'],'layer':'HTTP','unit':'requests','meaning':'attempts'}]})
            qualified=save(root/'qualified.json',{'passed':True,'inventory_sha256':inv['sha256'],
                'metric_contract_sha256':metric['sha256'],'workload_contract_sha256':work['sha256']})
            workers=[{'id':f'a{i}','node_id':f'c1-source-{i//4+1:03}',
                      'peer':{'transport':'ssh','alias':f'c1-source-{i//4+1:03}','container':f'ax-agent-{i}'},'output':f'/output/{i}'} for i in range(8)]
            s={'agents':8,'provider':'provided','workers':workers,'inventory':inv,'metric_contract':metric,'workload_contract':work,
               'observers':[{'id':n['id'],'node_id':n['id'],'peer':{'alias':n['ssh_alias']}} for n in inventory['nodes']]}
            for key in ('allocation_qualification','hardware_qualification','input_qualification','filesystem_qualification','observer_overhead'):s[key]=qualified
            cohort.validate(s)
            bad=copy.deepcopy(s);bad['observers'].pop()
            with self.assertRaisesRegex(ValueError,'every allocated node'):cohort.validate(bad)
            bad=copy.deepcopy(s);bad['workers'][4]['node_id']='c1-source-001';bad['workers'][4]['peer']['alias']='c1-source-001'
            with self.assertRaisesRegex(ValueError,'capacity'):cohort.validate(bad)
            bad=copy.deepcopy(s);bad['hardware_qualification']=save(root/'wrong.json',{'passed':True,'inventory_sha256':'0'*64})
            with self.assertRaisesRegex(ValueError,'another inventory'):cohort.validate(bad)
            bad=copy.deepcopy(s);bad['observer_overhead']=save(root/'wrong-overhead.json',{'passed':True,'metric_contract_sha256':metric['sha256'],'workload_contract_sha256':'0'*64})
            with self.assertRaisesRegex(ValueError,'another workload'):cohort.validate(bad)

    def config(self):
        return {'volume_format':'workspace-native-v2','workspace':'w','workspace_namespace':'n',
            'meta':{'backend':'redis','redis':{'url':'redis://10.0.0.2:6379/1'}},
            'data':{'backend':'s3','s3':{'bucket':'shared','endpoint':'http://10.0.0.3:9000'}},
            'cache':{'writeback_mode':'upload_before_commit'},'native_base':{},
            'fuse':{'cache_ttl_ms':1000,'keep_cache':True}}

    def test_native_profiles_no_sqlite_pack_leak_or_async_fallback(self):
        c=self.config()
        for group in ('C1','C4'):
            self.assertEqual(prepare.native_argv('brewfs','/bin/brewfs','/config',c,'/mount',group),
                             ['/bin/brewfs','mount','--config','/config','/mount','--s3-rustfs-ec-block-size-hint','auto'])
        for key, value in [('volume_format','packed-metadata-v3'),('cache',{'writeback_mode':'commit_before_upload'}),
                            ('meta',{'backend':'sqlx'})]:
            bad=copy.deepcopy(c);bad[key]=value
            with self.assertRaises(ValueError):prepare.native_argv('brewfs','/b','/c',bad,'/m','C1')
        c.update(volume_format='flat-v1',meta={'backend':'tikv'})
        self.assertIn('mount',prepare.native_argv('brewfs','/b','/c',c,'/m','C2'))
        with self.assertRaises(ValueError):prepare.native_argv('brewfs','/b','/c',c,'/m','C3')

    def test_dynamic_default_and_explicit_client_ablation(self):
        c=self.config()
        for cluster,fmt,backend in [('C1','workspace-native-v2','redis'),('C4','workspace-native-v2','redis'),
                                    ('C2','packed-metadata-v3','tikv')]:
            c['volume_format']=fmt;c['meta']['backend']=backend
            self.assertEqual(prepare.native_argv('brewfs','/b','/c',c,'/m',cluster)[-2:],
                             ['--s3-rustfs-ec-block-size-hint','auto'])
            self.assertNotIn('--s3-rustfs-ec-block-size-hint',
                             prepare.native_argv('brewfs','/b','/c',c,'/m',cluster,dynamic_block='off'))
        c['data']['s3']['rustfs_ec_block_size_hint']=65536
        with self.assertRaisesRegex(ValueError,'config hint differs'):
            prepare.native_argv('brewfs','/b','/c',c,'/m','C2')
        self.assertEqual(prepare.native_argv('brewfs','/b','/c',c,'/m','C2',dynamic_block='65536')[-1],'65536')
        with self.assertRaises(ValueError):prepare.native_argv('brewfs','/b','/c',c,'/m','C2',dynamic_block='off')

    def test_anchor_excludes_local_cache_but_binds_shared_storage(self):
        c=self.config(); expected=prepare.anchor('brewfs',c)
        c['cache']['cache_root']='/different-local-disk'
        self.assertEqual(expected,prepare.anchor('brewfs',c))
        c['data']['s3']['bucket']='other'
        self.assertNotEqual(expected,prepare.anchor('brewfs',c))

    def test_stacked_or_similar_path_is_not_exact_mount(self):
        text='1 0 0:1 / /mount rw - fuse.brewfs brewfs rw\n2 0 0:2 / /mount-other rw - fuse.juicefs jfs rw\n'
        self.assertEqual(len(common.mounts('/mount',text)),1)
        self.assertEqual(common.mounts('/moun',text),[])
        self.assertEqual(len(common.mounts('/mount',text+text.splitlines()[0]+'\n')),2)

    def test_pid_reuse_not_owned(self):
        old={'pid':42,'boot_id':'boot','start_ticks':10,'state':'S'}
        with patch.object(common,'identity',return_value={**old,'start_ticks':11}):self.assertFalse(common.alive(old))
        with patch.object(common,'identity',return_value={**old,'state':'Z'}):self.assertFalse(common.alive(old))

    def test_ssh_uses_pinned_strict_config_and_stdin(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);cfg=root/'ssh';cfg.write_text('Host worker\n HostName 10.0.0.1\n')
            hosts=root/'hosts';hosts.write_text('example trusted key')
            ref=lambda p:{'path':str(p),'sha256':common.digest(p)}
            peer={'transport':'ssh','python':'/usr/bin/python3','alias':'worker','container':'ax-agent-01',
                  'ssh':{'executable':ref(Path(sys.executable).resolve()),'config':ref(cfg),'known_hosts':ref(hosts)}}
            with patch.object(transport,'verify_ssh'):argv=transport.command(peer)
            self.assertIn('StrictHostKeyChecking=yes',argv);self.assertIn('BatchMode=yes',argv)
            self.assertIn('docker exec -i ax-agent-01',argv[-1])
            hosts.write_text('changed')
            with self.assertRaises(ValueError):transport.command(peer)

    def test_jump_host_relaxed_key_check_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);config=root/'config';config.write_text('Host worker jump\n ForwardAgent no\n')
            known=root/'hosts';known.write_text('key')
            good=f'stricthostkeychecking true\nforwardagent no\npermitlocalcommand no\nglobalknownhostsfile none\nuserknownhostsfile {known}\nproxyjump jump\n'
            bad=good.replace('stricthostkeychecking true','stricthostkeychecking false').replace('proxyjump jump','proxyjump none')
            class Reply:
                def __init__(self,stdout):self.stdout=stdout
            with patch.object(transport.subprocess,'run',side_effect=[Reply(good),Reply(bad)]):
                with self.assertRaisesRegex(ValueError,'StrictHostKeyChecking'):transport.verify_ssh('/ssh',str(config),str(known),'worker')

    @unittest.skipUnless(sys.platform=='linux','Unix control socket and real child signals')
    def test_supervisor_refuses_active_worker_and_waits_original_child(self):
        # Synthetic mountinfo, real supervisor/socket/native-child lifecycle.
        # This verifies drain control, not BrewFS/JuiceFS/FUSE qualification.
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);out=root/'supervisor';out.mkdir()
            active=root/'mounted';stopped=root/'stopped';script=root/'foreground.py'
            script.write_text('import signal,time,sys\nfrom pathlib import Path\n'
                'def stop(*args):\n Path(sys.argv[2]).write_text("stopped")\n raise SystemExit(0)\n'
                'signal.signal(signal.SIGINT,stop)\nPath(sys.argv[1]).write_text("ready")\n'
                'while True:time.sleep(.02)\n')
            s={'schema':'experiment-mount-v1','reviewed':True,'mount_point':'/test-fixture',
               'allowed_fstypes':['fuse.fixture'],'anchor':{'test':True},'budget':prepare.AGENT_BUDGET}
            ref=save(root/'mount.json',s)
            command={'argv':[sys.executable,str(script),str(active),str(stopped)],'cwd':str(root)}
            def mountinfo(_):
                return [{'mount_id':42,'filesystem':'fuse.fixture','point':'/test-fixture'}] if active.exists() and not stopped.exists() else []
            with patch.object(node,'validate_mount',return_value=(command,os.environ.copy())),\
                 patch.object(node,'cgroup_limits',return_value={'effective':prepare.AGENT_BUDGET}),\
                 patch.object(node,'mounts',side_effect=mountinfo):
                server=threading.Thread(target=node.serve,args=(ref,out));server.start()
                deadline=time.monotonic()+5
                while not (out/'ready.json').exists() and time.monotonic()<deadline:time.sleep(.01)
                try:
                    self.assertTrue(node.control(out,'acquire',worker='test')['passed'])
                    with self.assertRaisesRegex(ValueError,'workload active'):node.control(out,'stop')
                    self.assertFalse(stopped.exists())
                    node.control(out,'release',worker='test')
                    result=node.control(out,'stop');self.assertTrue(result['passed']);self.assertEqual(result['exit_code'],0)
                finally:
                    if not stopped.exists():
                        try:node.control(out,'release',worker='test')
                        except ValueError:pass
                        node.control(out,'stop')
                    server.join(5)
            self.assertFalse(server.is_alive());self.assertTrue(stopped.exists())


class ConcurrencyS3(unittest.TestCase):
    class Client:
        def __init__(self,p):self.p=p;self.lock=threading.Lock();self.active=0;self.peak=0
        def get_object(self,**kw):
            with self.lock:self.active+=1;self.peak=max(self.peak,self.active)
            time.sleep(.01)
            i=int(kw['Key'].rsplit('o',1)[1]);data=s3_requests.payload(self.p['seed'],i,self.p['object_bytes'])
            status=200
            if 'Range' in kw:
                a,b=map(int,kw['Range'][6:].split('-'));data=data[a:b+1];status=206
            with self.lock:self.active-=1
            return {'Body':io.BytesIO(data),'ResponseMetadata':{'HTTPStatusCode':status,'HTTPHeaders':{}}}

    def test_global_budget_split_and_exact_request_coverage(self):
        p=s3_requests.plan(4,32,8192,42,7,2)
        self.assertEqual([s3_requests.shard_budget(p,i) for i in range(2)],[4,3])
        seen=[]
        with tempfile.TemporaryDirectory() as tmp:
            for i in range(2):
                out=Path(tmp)/str(i);client=self.Client(p)
                s3_requests.run('read',client,p,'bucket','run-owned',out,'range',shard=i)
                result=common.load(out/'result.json')
                self.assertEqual(result['requests'],16);self.assertLessEqual(client.peak,s3_requests.shard_budget(p,i))
                self.assertGreater(client.peak,1)
                seen += [json.loads(l)['index'] for l in (out/'requests.jsonl').read_text().splitlines()]
        self.assertEqual(sorted(seen),list(range(32)))
        with self.assertRaises(ValueError):s3_requests.plan(4,32,8192,42,1,2)

    def test_failed_response_keeps_identity_bytes_and_unstarted_count(self):
        p=s3_requests.plan(2,12,8192,42,1,1)
        class BadClient:
            def get_object(self,**kwargs):
                return {'Body':io.BytesIO(b'bad'),'ResponseMetadata':{'HTTPStatusCode':206,'HTTPHeaders':{}}}
        with tempfile.TemporaryDirectory() as tmp:
            out=Path(tmp)/'failed'
            with self.assertRaises(ValueError):s3_requests.run('read',BadClient(),p,'bucket','owned-run',out,'range')
            row=json.loads((out/'requests.jsonl').read_text())
            self.assertEqual(row['index'],0);self.assertEqual(row['application_bytes'],3)
            self.assertFalse(row['content_equal']);self.assertGreaterEqual(row['seconds'],0)
            result=common.load(out/'failed.json')
            self.assertEqual(result['attempted_requests'],1);self.assertEqual(result['not_started_requests'],11)


class HandoffTests(unittest.TestCase):
    def test_shared_database_requires_identical_workspace_local_xdg(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            phase=save(root/'phase.json',dict(schema='experiment-cohort-v1',agents=1,workers=[dict(peer={})]))
            state=dict(workspace='/shared/workspace',project='/shared/workspace/repo',
                config='/shared/workspace/config/opencode.json',
                xdg={name:'/shared/workspace/source/'+name for name in ('data','state','cache','config')})
            source=dict(anchor={'workspace':'w'},node_id='a',mount_directory='/old',peer={},session_state=state)
            target=dict(source,node_id='b',mount_directory='/new',session_state=copy.deepcopy(state))
            s=dict(mode='move',restore_method='shared-workspace-db',source=source,target=target,
                **{name:phase for name in ('quiesce','lease_check','restore','resume','export_after')})
            handoff.validate(s)
            bad=copy.deepcopy(s);bad['target']['session_state']['xdg']['data']='/shared/workspace/fresh-data'
            with self.assertRaisesRegex(ValueError,'same workspace-local'):handoff.validate(bad)
            bad=copy.deepcopy(s)
            for side in ('source','target'):bad[side]['session_state']['xdg']['data']='/outside'
            with self.assertRaisesRegex(ValueError,'inside the shared workspace'):handoff.validate(bad)

    def test_shared_database_never_stages_or_imports_session_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            before={'info':{'id':'s'},'messages':[{'info':{'id':'m1'},'parts':[
                {'type':'tool','callID':'old','state':{'status':'completed'}}]}]}
            after=copy.deepcopy(before)
            after['messages'].append({'info':{'id':'m2'},'parts':[
                {'type':'tool','callID':'new','state':{'status':'completed'}}]})
            anchor={'workspace':'w'}
            source=dict(peer={},mount_directory='/source',anchor=anchor,session_state={'workspace':'/shared/workspace'})
            target=dict(peer={},mount_directory='/target',anchor=anchor,mount_spec={'path':'/mount','sha256':'a'*64})
            s=dict(schema='experiment-handoff-v1',reviewed=True,mode='move',filesystem='juicefs',session_id='s',
                restore_method='shared-workspace-db',source=source,target=target,
                export_transfer_sha256=hashlib.sha256((json.dumps(before,ensure_ascii=False)+'\n').encode()).hexdigest(),
                **{name:{'phase':name} for name in ('quiesce','lease_check','restore','resume','export_after')})
            def reply(ref,output,observe=None):
                if ref['phase']=='resume':
                    observe(dict(coordinator_ns=time.monotonic_ns(),message=dict(type='tool_completed',session_id='s',call_id='new')))
                exported=after if ref['phase']=='export_after' else before
                return dict(passed=True,jobs=[dict(result=exported)])
            ref=save(root/'handoff.json',s)
            with patch.object(handoff,'validate'),patch.object(cohort,'run',side_effect=reply),patch.object(transport,'one',return_value={
                    'passed':True,'mount_absent':True,'exit_code':0,'anchor':anchor}) as remote:
                result=handoff.run(ref,root/'out')
            self.assertTrue(result['passed']);self.assertEqual(result['native_session_json_transferred_bytes'],0)
            self.assertEqual([call.args[1][0] for call in remote.call_args_list],['stop','start'])
            self.assertNotIn('stage-export',result['phases'])
            self.assertEqual(result['session_validation']['new_completed_tools'],1)

    def test_identity_and_placement_cannot_be_substituted(self):
        source={'anchor':{'workspace':'w'},'node_id':'a','mount_directory':'/old','physical_host_id':'p1'}
        target={**source,'node_id':'b','mount_directory':'/new','physical_host_id':'p2'}
        s={'source':source,'target':target,'mode':'move','claim_physical_move':True}
        for mutate in ('anchor','same-node','same-physical'):
            bad=copy.deepcopy(s)
            if mutate=='anchor':bad['target']['anchor']={'workspace':'different'}
            elif mutate=='same-node':bad['target']['node_id']='a'
            else:bad['target']['physical_host_id']='p1'
            with self.assertRaises(ValueError):handoff.validate(bad)

    def test_failed_source_release_never_starts_target(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            before={'info':{'id':'s'},'messages':[{'info':{'id':'m'},'parts':[{'type':'tool','state':{'status':'completed'}}]}]}
            s={'schema':'experiment-handoff-v1','reviewed':True,'filesystem':'brewfs','session_id':'s',
               'quiesce':{},'lease_check':{},'source':{'peer':{},'mount_directory':'/source','anchor':{'workspace':'w'}},'target':{}}
            ref=save(root/'handoff.json',s)
            replies=[{'passed':True,'jobs':[{'result':before}]},
                     {'passed':True,'jobs':[{'result':{'workspace_id':'w','lease_id':'still-held','holder_generation':7,'pending_writeback_bytes':0}}]}]
            with patch.object(handoff,'validate'),patch.object(cohort,'run',side_effect=replies),patch.object(transport,'one',return_value={
                    'passed':True,'mount_absent':True,'exit_code':0,'anchor':{'workspace':'w'}}) as remote:
                with self.assertRaisesRegex(ValueError,'lease'):handoff.run(ref,root/'out')
            self.assertEqual(remote.call_count,1)
            self.assertEqual(common.load(root/'out/failed.json')['phase'],'lease_check')


class MetricsContractTests(unittest.TestCase):
    @unittest.skipUnless(sys.platform=='linux','Linux collector and Unix control socket')
    def test_collector_process_phase_markers_stop_and_audit(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            ref=save(root/'observer.json',{'schema':'experiment-observer-v1','reviewed':True,
                     'role':'host-only','interval_seconds':.25,'metric_contract_sha256':'a'*64,
                     'targets':[],'cgroups':[],'processes':[],'data_paths':[tmp]})
            out=root/'collector'
            self.assertTrue(observe.start(ref,out)['passed'])
            try:
                self.assertTrue(observe.request(out,'mark','measurement-start')['passed'])
                self.assertTrue(observe.request(out,'mark','application-finished')['passed'])
            finally:
                stopped=observe.request(out,'stop')
            self.assertTrue(stopped['passed'])
            receipt=observe.audit(out,3,['measurement-start','application-finished'])
            self.assertTrue(receipt['passed']);self.assertGreaterEqual(receipt['samples'],4)

    def test_overhead_pairs_bind_work_and_reject_regressions(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);pairs=[]
            for index in range(3):
                pair={}
                for mode in ('on','off'):
                    value={'passed':True,'workload_passed':True,'purpose':'observer-calibration-'+mode,
                           'agents':8,'coordinator_makespan_seconds':10.1 if mode=='on' else 10,
                           'metric_contract_sha256':'a'*64,'workload_contract_sha256':'b'*64,
                           'jobs':[{'passed':True,'kind':'s3','seconds':1.01 if mode=='on' else 1,
                                    'result':{'requests':16,'application_bytes':65536,'global_concurrency':8,'shard':0}}]}
                    pair[mode]=save(root/f'{index}-{mode}.json',value)
                pairs.append(pair)
            plan={'schema':'experiment-observer-overhead-plan-v1','reviewed':True,'pairs':pairs,
                  'max_relative_change':.05,'metric_contract_sha256':'a'*64,'workload_contract_sha256':'b'*64}
            ref=save(root/'overhead.json',plan)
            self.assertTrue(observe.overhead(ref)['passed'])
            for pair in pairs:
                value=common.load(pair['on']['path']);value['coordinator_makespan_seconds']=20
                Path(pair['on']['path']).write_text(json.dumps(value))
                pair['on']['sha256']=common.digest(pair['on']['path'])
            Path(ref['path']).write_text(json.dumps(plan));ref['sha256']=common.digest(ref['path'])
            self.assertFalse(observe.overhead(ref)['passed'])
            value['workload_contract_sha256']='c'*64
            Path(pairs[-1]['on']['path']).write_text(json.dumps(value));pairs[-1]['on']['sha256']=common.digest(pairs[-1]['on']['path'])
            Path(ref['path']).write_text(json.dumps(plan));ref['sha256']=common.digest(ref['path'])
            with self.assertRaisesRegex(ValueError,'contract'):observe.overhead(ref)

    def test_missing_nonfinite_and_duplicate_required_samples(self):
        target={'format':'prometheus','required_names':['requests_total']}
        for raw in ('different 1\n','requests_total NaN\n','requests_total 1\nrequests_total 2\n'):
            with self.assertRaises(ValueError):observe.required_values(target,raw)
        self.assertEqual(observe.required_values(target,'requests_total{kind="GET"} 9\n')['requests_total{kind="GET"}'],9)

    @unittest.skipUnless(sys.platform=='linux','actual Linux /proc collection')
    def test_raw_collection_reset_gap_and_tamper_rejection(self):
        state={'count':5}
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200);self.end_headers();self.wfile.write(f'requests_total {state["count"]}\n'.encode())
            def log_message(self,*args):pass
        server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
        thread=threading.Thread(target=server.serve_forever);thread.start()
        try:
            with tempfile.TemporaryDirectory() as tmp:
                root=Path(tmp)
                target={'id':'service','kind':'http','url':f'http://127.0.0.1:{server.server_port}/metrics',
                        'format':'prometheus','required_names':['requests_total'],'counter_names':['requests_total']}
                s={'targets':[target],'cgroups':[],'processes':[],'data_paths':[tmp]}
                a=observe.snapshot(s,root,0);self.assertTrue(a['passed'])
                state['count']=9;b=observe.snapshot(s,root,1,a['counters']);self.assertTrue(b['passed'])
                history=[{**v,'sample_sha256':common.digest(root/f'sample-{i:06}'/'sample.json')} for i,v in enumerate([a,b])]
                save(root/'finished.json',{'passed':True,'samples':2,'history':history,
                    'marks':[{'label':label,'sample_index':i,'monotonic_ns':v['monotonic_end_ns']} for i,(label,v) in enumerate([('begin',a),('end',b)])]})
                self.assertTrue(observe.audit(root,3,['begin','end'])['passed'])
                with self.assertRaises(ValueError):observe.audit(root,0,['begin'])
                (root/'sample-000001/service.txt').write_text('modified')
                with self.assertRaises(ValueError):observe.audit(root,3,['begin'])
                state['count']=1;c=observe.snapshot(s,root,2,b['counters']);self.assertFalse(c['passed'])
        finally:server.shutdown();server.server_close();thread.join()


if __name__=='__main__':unittest.main()
