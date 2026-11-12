from copy import deepcopy
import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import time
import unittest
from unittest.mock import patch

from bench.runtime import handoff
from ssh_inventory import template


class C4TargetColdCheck(unittest.TestCase):
    def setUp(self):
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.out = self.root / 'run'
        self.calls = []
        self.original = {'info': {'id': 'ses-original'}, 'messages': [self.message('m1', 'c1')]}
        self.resumed = {**self.original, 'messages': [*self.original['messages'], self.message('m2', 'c2')]}
        self.anchor = dict(filesystem='juicefs', workspace='shared', namespace='vol', storage_identity_sha256='a'*64)
        self.workspace = '/srv/ax-pilot-session/workspace'
        state = dict(workspace=self.workspace, project=self.workspace+'/repo', config=self.workspace+'/config/opencode.json',
                     xdg={k:self.workspace+'/xdg/'+k for k in ('data','state','cache','config')})
        inv = template(['source','target'],[],[],cluster='c4-single')
        inv['campaign_id'] = 'cold-test'
        sides = {}
        for n in inv['nodes']:
            side = n['id']; cgroup='/sys/fs/cgroup/ax-slot-1'
            n.update(kind='host',host_id='i-'+side,environment_id='i-'+side,host_observer_node=side,
                     host_key_verified=True,owned_by_campaign=True,
                     data_paths=[dict(path='/srv/ax',min_free_bytes=1,min_free_inodes=1)])
            n['slots'][0]['cgroup'] = cgroup
            peer=dict(transport='ssh',alias=side,repo=n['repo'],python=n['python'],cgroup=cgroup)
            sides[side]=dict(node_id=side,peer=peer,anchor=self.anchor,session_state=state,
                mount_directory=f'/srv/ax/{side}/mount',mount_spec=dict(path=f'/srv/ax/{side}/spec.json',sha256='b'*64))
        inventory = self.pin('inventory',inv)
        self.request = dict(schema=handoff.V2,reviewed=True,mode='move',filesystem='juicefs',
                            restore_method='shared-workspace-db',session_id='ses-original',**sides,
                            export_transfer_sha256=hashlib.sha256((json.dumps(self.original,ensure_ascii=False)+'\n').encode()).hexdigest())
        for phase in handoff.PHASES:
            side = sides['source' if phase in ('quiesce','lease_check') else 'target']
            self.request[phase]=self.pin(phase,dict(schema='experiment-cohort-v1',reviewed=True,agents=1,
                inventory=inventory,workers=[dict(peer=side['peer'])]))

        target=sides['target']; self.tree='c'*64; self.result_path='/srv/ax/target/results/cold.json'
        self.expected=dict(schema='c4-target-cold-tree-v1',passed=True,full_tree_read=True,read_only=True,
            native_app_opened=False,workspace_copy_bytes=0,workspace=self.workspace,anchor=self.anchor,
            mount_spec=target['mount_spec'],mount_directory=target['mount_directory'],observed_tree_sha256=self.tree)
        remote_scripts=Path(target['peer']['repo'])/handoff.COLD_SCRIPTS
        local_scripts=Path(handoff.__file__).resolve().parents[2]/handoff.COLD_SCRIPTS
        leaf='check-c4-target-cold-tree.py'; helper='prepare-session-input.py'
        cold_native=dict(path='/srv/ax/target/spec/cold.json',sha256='a'*64)
        self.step=dict(schema='experiment-step-v1',reviewed=True,stage=handoff.COLD,inherit_environment=[],
            executable_sha256='e'*64,argv=[target['peer']['python'],str(remote_scripts/leaf),'check',
                '--request',cold_native['path'],'--sha256',cold_native['sha256'],'--out',self.result_path],
            inputs=[cold_native,target['mount_spec'],*[dict(path=str(remote_scripts/name),sha256=handoff.digest(local_scripts/name))
                                                       for name in (leaf,helper)]])
        self.step_ref=self.pin('cold-step',self.step)
        purpose='experiment'
        self.worker=dict(schema='experiment-worker-v1',reviewed=True,worker_id='a01',kind='control',purpose=purpose,
            mount=dict(spec=target['mount_spec'],directory=target['mount_directory']),requires_mount=True,
            jobs=[dict(id=handoff.COLD,step=self.step_ref,result=dict(path=self.result_path,equals=self.expected))])
        self.worker_ref=self.pin('cold-worker',self.worker)
        metric=self.pin('metric',dict(schema='experiment-metric-contract-v1',reviewed=True,source_pins=['pin'],
            bindings=[dict(observer_id='target',target_id='host',required_names=['cpu'],layer='host',unit='seconds',meaning='CPU time')]))
        workload=self.pin('workload',dict(schema='experiment-workload-contract-v1',reviewed=True,binary_pins=['pin'],input_pins=['pin']))
        qualification=self.pin('qualification',dict(passed=True,inventory_sha256=inventory['sha256']))
        overhead=self.pin('overhead',dict(passed=True,metric_contract_sha256=metric['sha256'],
                                         workload_contract_sha256=workload['sha256']))
        observers=[]
        for side, value in sides.items():
            peer={k:v for k,v in value['peer'].items() if k!='cgroup'}
            observers.append(dict(id=side,node_id=side,peer=peer))
        self.cohort=dict(schema='experiment-cohort-v1',reviewed=True,provider='ssh',purpose=purpose,agents=1,
            inventory=inventory,workers=[dict(id='a01',node_id='target',slot_id='target',peer=target['peer'],
                output='/srv/ax/target/worker',spec=self.worker_ref)],observers=observers,metric_contract=metric,
            workload_contract=workload,observer_overhead=overhead,
            **{gate:qualification for gate in ('allocation_qualification','hardware_qualification','input_qualification','filesystem_qualification')})
        self.cold_ref=self.pin('cold-cohort',self.cohort)
        self.request['target_cold_check']=dict(cohort=self.cold_ref,worker_readback=self.worker_ref,
                                             step_readback=self.step_ref,expected_tree_sha256=self.tree)
        self.mounted=dict(passed=True,anchor=self.anchor,
            mount=dict(mount_id=10,point='/srv/ax-pilot-session',filesystem='fuse.juicefs',source='vol',options='rw'),
            process=dict(pid=41,boot_id='target-boot',start_ticks=102,state='S'))
        self.status=deepcopy(self.mounted)
        self.status['process']['state']='R'
        proof={**deepcopy(self.mounted),**deepcopy(self.expected),'files_hashed':3,'bytes_hashed':40}
        self.completed=dict(passed=True,workload_passed=True,telemetry_live_check_passed=True,agents=1,ready_workers=1,
            completed_workers=1,completed_jobs=1,purpose=purpose,
            jobs=[dict(passed=True,worker='a01',job=handoff.COLD,step_sha256=self.step_ref['sha256'],
                       result_path=self.result_path,result_sha256='d'*64,exit_code=0,remaining_process_group=[],result=proof)])
        self.release=dict(spec_sha256=self.cold_ref['sha256'],workers=['a01'])

    @staticmethod
    def message(mid,cid):
        return {'info':{'id':mid},'parts':[{'type':'tool','callID':cid,'state':{'status':'completed'}}]}

    def pin(self,name,value):
        path=self.root/(name+'.json')
        path.write_text(json.dumps(value),encoding='utf-8')
        return dict(path=str(path),sha256=handoff.digest(path))

    def dispatch(self,ref,output,observer=None):
        phase=Path(output).name
        self.calls.append(phase)
        if phase==handoff.COLD:
            output.mkdir()
            handoff.write_new(output/'result.json',self.completed)
            handoff.write_new(output/'release-intent.json',self.release)
            return self.completed
        if phase=='resume':
            observer(dict(coordinator_ns=time.monotonic_ns(),
                          message=dict(type='tool_completed',session_id='ses-original',call_id='c2')))
        return dict(passed=True,jobs=[dict(result=self.resumed if phase=='export_after' else self.original)])

    def node(self,peer,args,directory,**kwargs):
        self.calls.append(args[0])
        if args[0]=='status':
            (directory/'protocol.jsonl').write_text(json.dumps(self.status)+'\n',encoding='utf-8')
            return self.status
        if args[0]=='start': return self.mounted
        return dict(anchor=self.anchor,mount_absent=True,exit_code=0)

    def run_handoff(self):
        with patch.object(handoff.cohort,'run',side_effect=self.dispatch),patch.object(handoff.transport,'one',side_effect=self.node):
            return handoff.run(self.pin('request',self.request),self.out)

    def test_v2_reads_target_before_first_export_and_pins_evidence(self):
        result=self.run_handoff()
        self.assertEqual(self.calls,['quiesce','stop','lease_check','start',handoff.COLD,'status','restore','resume','export_after'])
        self.assertEqual(result['schema'],'experiment-handoff-result-v2')
        self.assertTrue(result['state_ready_includes_target_cold_check'])
        times=result['phases']
        self.assertLessEqual(times['target-start']['end_ns'],times[handoff.COLD]['start_ns'])
        self.assertLessEqual(times[handoff.COLD]['end_ns'],times['target-cold-status']['start_ns'])
        self.assertLessEqual(times['target-cold-status']['end_ns'],times['restore']['start_ns'])
        proof=handoff.load(handoff.pinned(result['target_cold_check']))
        self.assertTrue(proof['before_first_target_export'])
        self.assertEqual(proof['proof']['observed_tree_sha256'],self.tree)
        self.assertEqual(proof['proof']['mount'],self.mounted['mount'])
        self.assertEqual(proof['proof']['process']['start_ticks'],102)
        self.assertEqual(proof['job_result_sha256'],'d'*64)
        for key in ('cohort_result','release','post_check_status'):
            self.assertTrue(handoff.pinned(proof[key]).is_file())

    def test_v1_retains_original_dispatch_order_and_result_shape(self):
        self.request['schema']=handoff.V1
        del self.request['target_cold_check']
        result=self.run_handoff()
        self.assertEqual(self.calls,['quiesce','stop','lease_check','start','restore','resume','export_after'])
        self.assertEqual(set(result),{'passed','mode','session_id','restore_method','native_session_json_transferred_bytes',
            'workspace_copy_bytes','state_ready_seconds','state_ready_includes_model_resume','first_completed_tool_ack_seconds',
            'phases','session_validation','scope','target_mount_retained','automatic_retry'})
        self.assertFalse((self.out/'target-cold-check.json').exists())

    def test_failed_or_replaced_tree_never_reaches_restore(self):
        baseline=deepcopy(self.completed); release=deepcopy(self.release)
        for change in ('tree','anchor','pid','step','release','status'):
            with self.subTest(change=change):
                self.out=self.root/change; self.calls.clear()
                self.completed=deepcopy(baseline); self.release=deepcopy(release); self.status=deepcopy(self.mounted)
                job=self.completed['jobs'][0]
                if change=='tree': job['result']['observed_tree_sha256']='f'*64
                if change=='anchor': job['result']['anchor']['namespace']='different'
                if change=='pid': job['result']['process']['start_ticks']+=1
                if change=='step': job['step_sha256']='f'*64
                if change=='release': self.release['workers']=['different']
                if change=='status': self.status['mount']['mount_id']+=1
                with self.assertRaisesRegex(ValueError,'Cold|cold'):
                    self.run_handoff()
                self.assertNotIn('restore',self.calls)
                self.assertFalse((self.out/'result.json').exists())
                self.assertEqual(handoff.load(self.out/'failed.json')['phase'],handoff.COLD)

    def test_v2_refuses_unreviewed_or_different_worker_bytes_before_dispatch(self):
        self.worker['reviewed']=False
        self.request['target_cold_check']['worker_readback']=self.pin('wrong-worker',self.worker)
        with self.assertRaisesRegex(ValueError,'reviewed experiment-worker-v1'):
            self.run_handoff()
        self.worker['reviewed']=True
        self.worker['worker_id']='different'
        self.request['target_cold_check']['worker_readback']=self.pin('wrong-worker',self.worker)
        with self.assertRaisesRegex(ValueError,'exact mounted target worker'):
            self.run_handoff()
        self.assertEqual(self.calls,[])

    def test_repinning_an_unrelated_script_as_the_step_does_not_admit_it(self):
        self.step['argv'][1]='/srv/ax/target/spec/emit-fiction.py'
        step=self.pin('replaced-step',self.step)
        self.worker['jobs'][0]['step']=step
        worker=self.pin('replaced-worker',self.worker)
        self.cohort['workers'][0]['spec']=worker
        cohort=self.pin('replaced-cohort',self.cohort)
        self.request['target_cold_check'].update(cohort=cohort,worker_readback=worker,step_readback=step)
        with self.assertRaisesRegex(ValueError,'pin the reviewed read-only tree check'):
            self.run_handoff()
        self.assertEqual(self.calls,[])

    def test_cold_check_cannot_be_silently_added_to_v1_or_omitted_from_v2(self):
        self.request['schema']=handoff.V1
        with self.assertRaisesRegex(ValueError,'requires experiment-handoff-v2'):
            self.run_handoff()
        self.request['schema']=handoff.V2
        del self.request['target_cold_check']
        with self.assertRaisesRegex(ValueError,'V2 requires'):
            self.run_handoff()
        self.assertEqual(self.calls,[])
