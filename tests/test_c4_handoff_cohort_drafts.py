"""Exercise the five C4 cohort drafts against the frozen validators."""
import importlib.util
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

SCRIPTS = Path(__file__).resolve().parents[1]/'experiments/unified-suite/pilot-aliyun/scripts'
spec = importlib.util.spec_from_file_location('cohort_drafts',SCRIPTS/'draft-c4-handoff-cohorts.py')
drafts = importlib.util.module_from_spec(spec)
spec.loader.exec_module(drafts)
from bench.ssh_inventory import template


class HandoffDrafts(unittest.TestCase):
    def setUp(self):
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.out = self.root/'drafts'
        self.session = 'ses_original'
        self.relay = 'http://127.0.0.1:18445/v1'
        self.cell = 'native-move-cell'
        self.binary = dict(path='/srv/ax-client/bin/opencode',sha256='a'*64)
        fleet = template(['pool-a-client','pool-b-client'],['pool-a-metadata','pool-a-objects'],[],cluster='route')
        fleet['campaign_id'] = 'frozen-route'
        for n in fleet['nodes']:
            n.update(kind='host',host_id='i-'+n['id'],environment_id='i-'+n['id'],
                     host_observer_node=n['id'],host_key_verified=True,owned_by_campaign=True,
                     data_paths=[dict(path='/srv/ax',min_free_bytes=10,min_free_inodes=10)])
            if n['slots']:
                n['slots'][0]['cgroup'] = '/sys/fs/cgroup/ax-slot-1'
        inv = self.doc('inventory',fleet)
        metric = self.doc('metrics',dict(schema='experiment-metric-contract-v1',reviewed=True,source_pins=[inv],
            bindings=[dict(observer_id=n['id'],target_id=self.target(n['id']),required_names=['actual'],layer='service',
                           unit='bytes',meaning='native bytes') for n in fleet['nodes']]))
        work = self.doc('workload',dict(schema='experiment-workload-contract-v1',reviewed=True,
                                       binary_pins=[self.binary],input_pins=[inv]))
        gates = dict(inventory=inv,metric_contract=metric,workload_contract=work)
        for name in ('allocation_qualification','hardware_qualification','input_qualification',
                     'filesystem_qualification','observer_overhead'):
            gates[name] = self.doc(name,dict(passed=True,inventory_sha256=inv['sha256'],
                                      metric_contract_sha256=metric['sha256'],workload_contract_sha256=work['sha256']))
        self.s = dict(schema=drafts.SCHEMA,cell_id=self.cell,session_id=self.session,export_transfer_sha256='b'*64,
                      opencode_sha256=self.binary['sha256'],relay_url=self.relay,gates=gates,phases={})
        self.mount = {}
        anchor = dict(filesystem='juicefs',workspace='original',namespace='namespace',
                      storage_identity_sha256='f'*64)
        for name,node in zip(('source','target'),fleet['nodes']):
            m = self.mirror(name+'-mount',dict(schema='experiment-mount-v1',reviewed=True,filesystem='juicefs',
                purpose='experiment',mount_point=drafts.MOUNT,anchor=anchor,suite_binding=dict(cell_id=self.cell)))
            self.s[name] = dict(node_id=node['id'],slot_id=node['id'],peer=self.peer(node,True),mount_spec=m,
                                mount_directory='/srv/ax-client/m/'+name)
            self.mount[name] = dict(spec=drafts.remote(m)[0],directory=self.s[name]['mount_directory'])
        self.observers = [dict(id=n['id'],node_id=n['id'],peer=self.peer(n,False),
            directory='/srv/ax/o/'+n['id'],
            spec=self.mirror('observer-'+n['id'],dict(schema='experiment-observer-v1',reviewed=True,
                observer_id=n['id'],metric_contract_sha256=metric['sha256'],targets=[dict(id=self.target(n['id']))])))
            for n in fleet['nodes']]
        self.native_overrides = {}
        self.worker_mount_override = {}
        self.lease_requires_live_client = False

    @staticmethod
    def target(node_id):
        return 'filesystem' if node_id in drafts.CLIENTS else 'native'

    def doc(self, name, value):
        p = self.root/(name+'.json')
        p.write_text(json.dumps(value),encoding='utf-8')
        return dict(path=str(p),sha256=drafts.digest(p))

    def mirror(self, name, value):
        return dict(local=self.doc(name,value),remote_path='/srv/ax/specs/'+name+'.json')

    def peer(self, node, slot):
        return self.doc(node['id']+('-slot' if slot else '-host'),dict(transport='ssh',alias=node['ssh_alias'],
            repo=node['repo'],python=node['python'],cgroup='/sys/fs/cgroup/ax-slot-1' if slot else None))

    def run_draft(self):
        bounded = dict(path='/srv/ax/repo/session-worker-bounded.py',sha256=drafts.digest(SCRIPTS/'session-worker-bounded.py'))
        for phase,(side,action,timeout) in drafts.PHASES.items():
            mounted = action!='verify-stopped'
            native = dict(action=action,total_timeout_seconds=timeout)
            if mounted:
                native.update({k:drafts.STATE[k] for k in ('workspace','project','config')},state=drafts.STATE['xdg'],
                              session_id=self.session,binary=self.binary,relay_url=self.relay)
            else:
                native['mount'] = self.mount[side]
            if phase=='resume':
                native['checkpoint'] = dict(path='/srv/ax/specs/checkpoint.json',sha256='c'*64)
            native.update(self.native_overrides.get(phase,{}))
            p = self.mirror(phase+'-phase',native)
            p_pin = drafts.remote(p)[0]
            result_dir = '/srv/ax/results/'+phase
            inputs = [p_pin,self.mount[side]['spec'],bounded]+[native[k] for k in ('binary','checkpoint') if k in native]
            step = self.mirror(phase+'-step',dict(schema='experiment-step-v1',reviewed=True,
                cell_id=self.cell,stage='session-'+action,inherit_environment=[],inputs=inputs,executable_sha256='d'*64,
                argv=['/usr/bin/python3.12',bounded['path'],'--request',p_pin['path'],'--sha256',p_pin['sha256'],'--out',result_dir]))
            expected = {'info.id':self.session} if action=='export' else (
                dict(passed=True,action='resume',session_id=self.session) if phase=='resume' else
                dict(passed=True,original_mount_stopped=True,all_exited=True,mount_absent=True))
            job = dict(id='session-'+action,workload_id=phase,step=drafts.remote(step)[0],
                result=dict(path=result_dir+('/export.json' if action=='export' else '/result.json'),equals=expected))
            if phase=='resume':
                job['observe_opencode_tools'] = True
            w = dict(schema='experiment-worker-v1',reviewed=True,purpose='experiment',kind='control',
                     budget=drafts.AGENT_BUDGET,requires_mount=mounted,worker_id='a01',jobs=[job])
            if mounted:
                w['mount'] = self.worker_mount_override.get(phase,self.mount[side])
            node = self.s[side]['node_id']
            rows = [dict(o,required_live_targets=[self.target(o['node_id'])]
                         if o['node_id'] not in drafts.CLIENTS or
                         (o['node_id']==node and (mounted or self.lease_requires_live_client)) else [])
                    for o in self.observers]
            self.s['phases'][phase] = dict(phase=p,step=step,worker=self.mirror(phase+'-worker',w),
                                          output='/srv/ax/j/'+phase,observers=rows)
        return drafts.draft(self.doc('request',self.s),self.out)

    def test_builds_five_non_executable_cohorts_and_exact_route(self):
        out = self.run_draft()
        self.assertFalse(out['execution_enabled'])
        handoff = drafts.load(out['handoff']['path'])
        self.assertFalse(handoff['reviewed'])
        drafts.handoff.validate(handoff)
        self.assertEqual(set(out['cohorts']),set(drafts.PHASES))
        for name,pin in out['cohorts'].items():
            self.assertEqual(handoff[name],pin)
            cohort = drafts.load(pin['path'])
            self.assertFalse(cohort['reviewed'])
            self.assertEqual(cohort['purpose'],'experiment')
            self.assertEqual(cohort['workers'][0]['peer'],handoff[drafts.PHASES[name][0]]['peer'])

    def test_rejects_wrong_session_or_source_mount_for_target_phase(self):
        self.native_overrides['resume'] = dict(session_id='ses_wrong')
        with self.assertRaisesRegex(ValueError,'Canonical shared session'):
            self.run_draft()
        self.native_overrides.clear()
        self.worker_mount_override['restore'] = self.mount['source']
        with self.assertRaisesRegex(ValueError,'bind its own mount'):
            self.run_draft()
        self.assertFalse(self.out.exists())

    def test_frozen_validator_rejects_incomplete_observer_coverage(self):
        self.observers.pop()
        with self.assertRaisesRegex(ValueError,'every allocated node'):
            self.run_draft()
        self.assertFalse(self.out.exists())

    def test_lease_check_cannot_expect_the_unmounted_source_to_be_live(self):
        self.lease_requires_live_client = True
        with self.assertRaisesRegex(ValueError,'live filesystem metrics: lease_check'):
            self.run_draft()
        self.assertFalse(self.out.exists())


if __name__=='__main__':
    unittest.main()
