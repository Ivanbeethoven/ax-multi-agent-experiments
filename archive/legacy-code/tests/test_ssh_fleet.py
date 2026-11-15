"""Portable SSH topology and real localhost transport, without experiment services."""
import copy
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from bench.runtime import common, cohort, fleet, preflight, prepare, transport
from bench import ssh_inventory


def save(path, value):
    path.write_text(json.dumps(value), encoding='utf-8')
    return fleet.reference(path)


def filled(count=2):
    value = ssh_inventory.template([f'a{i}' for i in range(count)], ['backend'], ['host'])
    value['campaign_id'] = 'ssh-test'
    for n in value['nodes']:
        n.update(environment_id='env-'+n['id'], host_id='machine-one', host_key_verified=True,
                 host_observer_node='host', data_paths=[{'path':'/srv/data','min_free_bytes':1,'min_free_inodes':1}])
    return value


class FleetTests(unittest.TestCase):
    def test_ssh_matrix_keeps_workload_without_fixed_machine_budget(self):
        import scale_plan
        for cluster,profile in [('C1','c1-8'),('C1','pilot-8'),('C2','main'),('C3','main'),('C4','main')]:
            plan=scale_plan.build(profile,'docker','ssh',cluster,campaign='ecs-20261101')
            old=scale_plan.build(profile,'docker','aliyun',cluster,campaign='ecs-20261101')
            self.assertEqual(plan['group_counts'],old['group_counts'])
            self.assertIsNone(plan['parallel_node_count']);self.assertFalse(plan['resource_requirements'])
            self.assertFalse({c['id'] for c in plan['cells']} & {c['id'] for c in old['cells']})
            self.assertTrue(all('roles' not in c for c in plan['clusters'].values()))

    def test_arbitrary_sizes_and_direct_containers_without_cloud_fields(self):
        for n in (1, 3, 8, 65):
            value = filled(n); result = ssh_inventory.validate(value)
            self.assertEqual(result['agent_slots'], n)
            self.assertEqual(result['host_groups'], 1)
            self.assertNotIn('private_ip', value['nodes'][0])

    def test_duplicate_environment_or_isolation_is_rejected(self):
        value = filled(); value['nodes'][1]['environment_id'] = value['nodes'][0]['environment_id']
        with self.assertRaisesRegex(ValueError, 'environment_id'): ssh_inventory.validate(value)
        value = filled(); n = value['nodes'][0]
        n['kind']='host'; n['slots'].append({'id':'second','container':None,'cgroup':None}); n['requirements']['max_agents']=2
        with self.assertRaisesRegex(ValueError, 'isolation'): ssh_inventory.validate(value)

    def test_host_observation_is_separate_from_container_probe(self):
        value=filled(); value['nodes'][0]['host_observer_node']=None
        self.assertFalse(ssh_inventory.validate(value)['host_telemetry_complete'])
        n=value['nodes'][0]
        worker={'node_id':n['id'],'slot_id':'a0','peer':{'transport':'ssh','alias':'a0','repo':n['repo'],'python':n['python']}}
        with self.assertRaisesRegex(ValueError, 'host telemetry'): ssh_inventory.validate_workers(value,[worker])
        value['nodes'][0]['host_observer_node']='a0'
        with self.assertRaisesRegex(ValueError, 'host observer'): ssh_inventory.validate(value)

    def test_cgroup_slots_allow_multiple_agents_on_one_ssh_host(self):
        value=filled(1); n=value['nodes'][0]; n['kind']='host'
        n['slots']=[{'id':f's{i}','cgroup':f'/sys/fs/cgroup/ax-test/a{i}'} for i in range(3)]
        n['requirements']['max_agents']=3
        workers=[{'node_id':'a0','slot_id':s['id'], 'peer':{'transport':'ssh','alias':'a0',
                    'repo':n['repo'],'python':n['python'],'cgroup':s['cgroup']}} for s in n['slots']]
        ssh_inventory.validate_workers(value,workers)
        with self.assertRaisesRegex(ValueError,'isolation reused'): ssh_inventory.validate_workers(value,workers+[workers[0]])

    def test_probe_transport_direct_vs_docker(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); config=root/'config'; config.write_text('Host test\n')
            known=root/'hosts'; known.write_text('pinned')
            peer={'transport':'ssh','alias':'test','ssh':{'executable':fleet.reference(Path(sys.executable).resolve()),
                  'config':fleet.reference(config),'known_hosts':fleet.reference(known)}}
            with patch.object(transport,'verify_ssh'):
                direct=transport.ssh_command(peer,['/usr/bin/python3','-c','print(1)'])
                docker=transport.ssh_command({**peer,'container':'ax-test'},['/usr/bin/python3'])
            self.assertNotIn('docker',direct[-1]);self.assertIn('docker exec -i ax-test',docker[-1])

    def test_executor_host_can_supply_observer_peer_outside_agent_slots(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);inv=filled(1);node=inv['nodes'][0];node['kind']='host'
            node['slots'][0]['cgroup']='/sys/fs/cgroup/ax-test/a0'
            save(root/'inventory.json',inv)
            receipt=json.dumps({'passed':True,'checks':{'runtime_matches':True},'pins':[]})
            result=subprocess.CompletedProcess([],0,receipt,'')
            with patch.object(transport,'ssh_command',return_value=['ssh-fixture']),patch.object(subprocess,'run',return_value=result):
                fleet.probe(root/'inventory.json','a0',None,{},root/'host-probe',ROOT,host_observer=True)
            peer=common.load(root/'host-probe/peer.json')
            self.assertIsNone(peer['cgroup']);self.assertIsNone(peer['container'])
            request=common.load(root/'host-probe/request.json')['request']
            self.assertFalse(request['executor']);self.assertFalse(request['requires_fuse'])
            with self.assertRaisesRegex(ValueError,'without an Agent slot'):
                fleet.probe(root/'inventory.json','a0','a0',{},root/'bad',ROOT,host_observer=True)

    @unittest.skipUnless(sys.platform=='linux','actual Linux probe')
    def test_probe_hashes_runtime_and_flags_drift_without_writing(self):
        with tempfile.TemporaryDirectory() as tmp:
            request={'repo':str(ROOT),'runtime_files':fleet.runtime_files(ROOT),'work_root':tmp,
                     'data_paths':[{'path':tmp,'min_free_bytes':1,'min_free_inodes':1}]}
            self.assertTrue(preflight.inspect(request)['passed'])
            request['runtime_files']['bench/runtime/node.py']='0'*64
            self.assertFalse(preflight.inspect(request)['passed'])
            self.assertFalse(list(Path(tmp).iterdir()))

    def test_inventory_dispatch_and_formal_gates(self):
        import experiment_inventory
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); inventory=filled(1)
            inv=save(root/'inv.json',inventory)
            self.assertTrue(experiment_inventory.validate(inventory)['passed'])
            work=save(root/'work.json',{'schema':'experiment-workload-contract-v1','reviewed':True,'binary_pins':['fixture'],'input_pins':['fixture']})
            metric=save(root/'metrics.json',{'schema':'experiment-metric-contract-v1','reviewed':True,'source_pins':['fixture'],
                'bindings':[{'observer_id':'backend','target_id':'native','required_names':['requests_total'],'layer':'HTTP','unit':'requests','meaning':'attempts'}]})
            qualified=save(root/'qualified.json',{'passed':True,'inventory_sha256':inv['sha256'],
                'metric_contract_sha256':metric['sha256'],'workload_contract_sha256':work['sha256']})
            n=inventory['nodes'][0]
            worker={'id':'a0','slot_id':'a0','node_id':'a0','output':'/srv/ax/out',
                    'peer':{'transport':'ssh','alias':'a0','repo':n['repo'],'python':n['python']}}
            s={'agents':1,'provider':'ssh','workers':[worker],'inventory':inv,'metric_contract':metric,'workload_contract':work,
               'observers':[{'id':n['id'],'node_id':n['id'],'peer':{'transport':'ssh','alias':n['ssh_alias'],
                   'repo':n['repo'],'python':n['python']}} for n in inventory['nodes']]}
            for k in ('allocation_qualification','hardware_qualification','input_qualification','filesystem_qualification','observer_overhead'):s[k]=qualified
            cohort.validate(s)
            changed=copy.deepcopy(s);changed['observers'][-1]['peer']['container']='ax-agent'
            with self.assertRaisesRegex(ValueError,'outside Agent isolation'):cohort.validate(changed)
            changed=copy.deepcopy(s);changed['observers'][-1]['peer']['repo']='/srv/another-repo'
            with self.assertRaisesRegex(ValueError,'runtime differs'):cohort.validate(changed)
            s['observers'].pop()
            with self.assertRaisesRegex(ValueError,'every allocated node'):cohort.validate(s)

    def test_cohort_draft_expands_pinned_peers_and_keeps_review_pending(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);inventory=filled(1);n=inventory['nodes'][0]
            peer=save(root/'peer.json',{'transport':'ssh','alias':'a0','repo':n['repo'],'python':n['python']})
            request={'inventory':save(root/'inventory.json',inventory),'workers':[
                {'id':'a0','node_id':'a0','slot_id':'a0','peer_ref':peer,'output':'/srv/ax/result'}],
                'observers':[{'id':'a0','node_id':'a0','peer_ref':peer}]}
            path=root/'request.json';save(path,request)
            result=fleet.cohort_draft(path,root/'cohort.json')
            self.assertFalse(result['reviewed']);self.assertEqual(result['agents'],1)
            self.assertNotIn('peer_ref',result['workers'][0])
            self.assertEqual(result['observers'][0]['peer']['alias'],'a0')
            (root/'peer.json').write_text('{}')
            with self.assertRaises(ValueError):fleet.cohort_draft(path,root/'tampered.json')

    def test_duplicate_real_cgroup_aborts_before_release(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);processes=[]
            workers=[{'id':f'a{i}','peer':{},'spec':{'path':'/fixture/spec','sha256':'a'*64},
                      'output':str(root/f'a{i}.json')} for i in range(2)]
            value={'schema':'experiment-cohort-v1','reviewed':True,'provider':'ssh','purpose':'experiment',
                   'agents':2,'workers':workers}
            ref=save(root/'cohort.json',value)
            source='''import json,sys
from pathlib import Path
print(json.dumps({'type':'ready','worker':sys.argv[1],'spec_sha256':'a'*64,
    'resources':{'effective':{}},'purpose':'experiment','expected_jobs':1,'workload_kind':'trace',
    'isolation':{'boot_id':'same-host','cgroup_namespace':'same-ns','cgroup_membership':'same-group'}}),flush=True)
Path(sys.argv[2]).write_text(sys.stdin.readline())
'''
            def connect_fixture(peer,args,stderr,test):
                worker=next(w for w in workers if w['output']==args[-1])
                proc=subprocess.Popen([sys.executable,'-u','-c',source,worker['id'],worker['output']],
                    stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=stderr,text=True)
                processes.append(proc);return proc
            try:
                with patch.object(cohort,'validate'),patch.object(cohort,'connect',side_effect=connect_fixture):
                    with self.assertRaisesRegex(ValueError,'share one Agent cgroup'):cohort.run(ref,root/'result')
                self.assertFalse((root/'result/release-intent.json').exists())
                self.assertFalse(common.load(root/'result/failed.json')['released'])
                for worker in workers:self.assertEqual(common.load(worker['output']),{'action':'abort'})
            finally:
                for proc in processes:
                    if proc.poll() is None:proc.terminate()
                    proc.wait(timeout=5)

    def test_trace_draft_preserves_full_input_counts_and_requires_review(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);case=root/'case';(case/'capture/blobs').mkdir(parents=True);(case/'initial').mkdir()
            (case/'capture/events.jsonl').write_text('fixture\n')
            (case/'capture/blobs/data').write_bytes(b'complete-payload')
            for n,v in [('metrics.json',{'callbacks':17,'actual_read_bytes':29,'actual_write_bytes':41}),
                        ('initial-tree.json',{}),('final-tree.json',{})]:save(case/n,v)
            manifest=save(root/'manifest.json',{p.relative_to(case).as_posix():common.digest(p) for p in case.rglob('*') if p.is_file()})
            mount=save(root/'mount.json',{'mount_point':str(root/'mounted')})
            req={'worker_id':'a0','workload_id':'trace-fixture','case':str(case),'manifest':manifest,
                 'root':str(root/'mounted/initial'),'cwd':str(root),'binary':fleet.reference(Path(sys.executable).resolve()),
                 'mount':{'spec':mount,'directory':str(root/'state')}}
            path=root/'request.json';save(path,req)
            value=prepare.trace_worker(path,root/'draft');step=common.load(root/'draft/step.json')
            self.assertFalse(value['reviewed']);self.assertFalse(step['reviewed'])
            self.assertEqual(value['jobs'][0]['result']['equals']['operations'],17)
            self.assertIn('--performance',step['argv']);self.assertNotIn('--simulate-llm',step['argv'])
            self.assertTrue(any(x['path'].endswith('capture/blobs/data') for x in step['inputs']))
            (case/'capture/blobs/data').write_bytes(b'changed')
            with self.assertRaises(ValueError):prepare.trace_worker(path,root/'bad')

    def test_template_cli_and_safe_unfilled_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'fleet.json'
            subprocess.run([sys.executable,'-m','bench.runtime.fleet','template','--executors','one','two',
                            '--services','backend','--output',str(path)],cwd=ROOT,check=True,capture_output=True)
            value=common.load(path);self.assertFalse(value['execution_enabled'])
            with self.assertRaises(ValueError):ssh_inventory.validate(value)


@unittest.skipUnless(sys.platform=='linux' and os.environ.get('AX_TEST_LOCAL_SSH')=='1',
                     'set AX_TEST_LOCAL_SSH=1 as root for isolated localhost sshd fixture')
class RealSSHTests(unittest.TestCase):
    def test_strict_ssh_probe_and_observer_lifecycle(self):
        if os.geteuid()!=0 or not Path('/usr/sbin/sshd').exists():self.skipTest('root and existing sshd required')
        runtime=Path('/run/sshd');created=not runtime.exists()
        if created:runtime.mkdir(mode=0o755)
        try:
            with tempfile.TemporaryDirectory(prefix='ax-ssh-', dir='/root') as tmp:
                root=Path(tmp)
                for key in ('host','client'):
                    subprocess.run(['ssh-keygen','-q','-t','ed25519','-N','','-f',str(root/key)],check=True)
                (root/'authorized').write_text((root/'client.pub').read_text())
                with socket.socket() as sock:sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
                (root/'sshd').write_text(f'ListenAddress 127.0.0.1\nPort {port}\nHostKey {root}/host\n'
                    f'PidFile {root}/pid\nAuthorizedKeysFile {root}/authorized\n'
                    'PasswordAuthentication no\nKbdInteractiveAuthentication no\nPermitRootLogin prohibit-password\n'
                    'UsePAM yes\nAllowUsers root\nLogLevel VERBOSE\n')
                pub=(root/'host.pub').read_text().split()
                (root/'known_hosts').write_text(f'[127.0.0.1]:{port} {pub[0]} {pub[1]}\n')
                (root/'ssh_config').write_text(f'Host fixture\n HostName 127.0.0.1\n Port {port}\n User root\n'
                    f' IdentityFile {root}/client\n IdentitiesOnly yes\n StrictHostKeyChecking yes\n'
                    f' UserKnownHostsFile {root}/known_hosts\n GlobalKnownHostsFile none\n ForwardAgent no\n PermitLocalCommand no\n')
                with (root/'server.log').open('w') as log:
                    server=subprocess.Popen(['/usr/sbin/sshd','-D','-e','-f',str(root/'sshd')],stdout=log,stderr=log)
                    observer_state=root/'observer';peer_ref=None
                    try:
                        deadline=time.monotonic()+5
                        while True:
                            try:
                                with socket.create_connection(('127.0.0.1',port),timeout=.2):break
                            except OSError:
                                if time.monotonic()>deadline:raise RuntimeError((root/'server.log').read_text())
                                time.sleep(.05)
                        inv=ssh_inventory.template(['fixture'],[],[])
                        n=inv['nodes'][0];n.update(role='observer',kind='host',repo=str(ROOT),python=sys.executable,work_root=tmp)
                        inv_path=root/'inv.json';save(inv_path,inv)
                        ssh={k:fleet.reference(p) for k,p in [('executable',Path('/usr/bin/ssh')),('config',root/'ssh_config'),('known_hosts',root/'known_hosts')]}
                        try:result=fleet.probe(inv_path,'fixture',None,ssh,root/'probe',ROOT)
                        except ValueError as error:
                            raise AssertionError((root/'probe/ssh.stderr.log').read_text()+'\n'+(root/'server.log').read_text()) from error
                        self.assertTrue(result['passed']);peer_ref=fleet.reference(root/'probe/peer.json')
                        observer=save(root/'observe.json',{'schema':'experiment-observer-v1','reviewed':True,'role':'host-only',
                            'interval_seconds':.25,'metric_contract_sha256':'a'*64,'targets':[],'cgroups':[],
                            'processes':[],'data_paths':[tmp]})
                        fleet.call(peer_ref,'observe-start',str(observer_state),root/'start',observer)
                        self.assertTrue(fleet.call(peer_ref,'observe-status',str(observer_state),root/'status')['passed'])
                        self.assertTrue(fleet.call(peer_ref,'observe-stop',str(observer_state),root/'stop')['passed'])
                        self.assertTrue((observer_state/'finished.json').exists())
                        (root/'known_hosts').write_text('')
                        with self.assertRaises(ValueError):fleet.call(peer_ref,'observe-status',str(observer_state),root/'tampered')
                    finally:
                        if peer_ref and observer_state.exists() and not (observer_state/'finished.json').exists():
                            from bench.runtime import observe
                            observe.request(observer_state,'stop')
                        server.terminate();server.wait(timeout=5)
        finally:
            if created:runtime.rmdir()


if __name__=='__main__':unittest.main()
