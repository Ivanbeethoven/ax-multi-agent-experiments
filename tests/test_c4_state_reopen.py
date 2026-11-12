"""The storage-only path fences the old grant and rejects networking."""
from copy import deepcopy
from contextlib import redirect_stdout
from datetime import datetime, timedelta, timezone
import io
import json
import os
from pathlib import Path
import runpy
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

SCRIPTS = Path(__file__).resolve().parents[1]/'experiments/unified-suite/pilot-aliyun/scripts'
RUNNER = runpy.run_path(str(SCRIPTS/'run-c4-state-reopen.py'))
GRANT = RUNNER['require_grant']
WORKER = runpy.run_path(str(SCRIPTS/'c4-state-reopen-node.py'))
NETWORK = WORKER['NETWORK_WRAPPER']


class StateReopen(unittest.TestCase):
    def group_wait(self, samples, *, other_group=None):
        clock, queried = [0.0], []
        samples = iter(samples)
        last = []
        def members(pgid):
            nonlocal last
            queried.append(pgid)
            if pgid != 51:
                return [other_group] if other_group is not None else []
            last = next(samples,last)
            return last
        def sleep(seconds):
            clock[0] += seconds
        result = WORKER['wait_export_group'](51,members,timeout=0.5,poll=0.25,
                                             monotonic=lambda:clock[0],sleep=sleep)
        return result, queried

    def test_original_group_already_empty(self):
        actual, queried = self.group_wait([[]])
        self.assertEqual(queried,[51])
        self.assertEqual(actual['group_members_after_parent_exit'],[])
        self.assertEqual(actual['group_members_after'],[])
        self.assertEqual(actual['group_closure_wait_seconds'],0.0)

    def test_original_group_exits_naturally_after_parent(self):
        actual, queried = self.group_wait([[24,23],[]])
        self.assertEqual(queried,[51,51])
        self.assertEqual(actual['group_members_after_parent_exit'],[23,24])
        self.assertEqual(actual['group_members_after'],[])
        self.assertEqual(actual['group_closure_wait_seconds'],0.25)

    def test_live_descendant_remains_visible_at_timeout(self):
        actual, queried = self.group_wait([[23,24],[24],[24]])
        self.assertEqual(queried,[51,51,51])
        self.assertEqual(actual['group_members_after_parent_exit'],[23,24])
        self.assertEqual(actual['group_members_after'],[24])
        self.assertEqual(actual['group_closure_wait_seconds'],0.5)

    def test_unrelated_process_group_is_neither_queried_nor_affected(self):
        actual, queried = self.group_wait([[23],[]],other_group=74)
        self.assertEqual(queried,[51,51])
        self.assertNotIn(74,actual['group_members_after_parent_exit'])
        self.assertEqual(actual['group_members_after'],[])

    def test_local_authorization_binds_exact_v2_launch_without_ssh(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            draft, request, authorization, output = [root/name for name in
                                                     ('draft.json','request.json','authorization.json','run')]
            route = dict(schema='pilot-c4-state-reopen-draft-v1', source_pool='pool-a',target_pool='pool-b',
                         reviewed=False, execution_enabled=False, qualification_only=True, model_calls_allowed=0)
            draft.write_text(json.dumps(route),encoding='utf-8')
            pin = dict(path=str(draft),sha256=RUNNER['sha256'](draft))
            spec = {field:pin for field in RUNNER['V2_FIELDS']}
            export_worker = SCRIPTS/'c4-state-reopen-node.py'
            spec.update(schema=RUNNER['REQUEST_V2'],attempt='c4-ab-check',
                        worker=dict(path=str(export_worker),sha256=RUNNER['sha256'](export_worker)))
            request.write_text(json.dumps(spec),encoding='utf-8')
            req_sha, runner_sha = RUNNER['sha256'](request), RUNNER['sha256'](SCRIPTS/'run-c4-state-reopen.py')
            with patch('subprocess.check_output') as ssh, redirect_stdout(io.StringIO()):
                RUNNER['record_launch_authorization']([
                    '--request',str(request),'--sha256',req_sha,'--runtime-repo',str(root),
                    '--out',str(output),'--authorization-out',str(authorization)])
                ssh.assert_not_called()
            auth_ref = dict(path=str(authorization),sha256=RUNNER['sha256'](authorization))
            value = RUNNER['read_pin'](auth_ref)
            self.assertNotIn('reviewed',value)
            self.assertIs(value['formal_sample'],False)
            self.assertFalse(output.exists())
            verify = RUNNER['verify_launch']
            verify(spec,route,value,req_sha,runner_sha,root,output)
            for altered in (None, {**value,'formal_sample':True}, {**value,'model_calls_allowed':False},
                            {**value,'reviewed':True}, {**value,'attempt':'different-attempt'}):
                with self.subTest(altered=altered), self.assertRaisesRegex(ValueError,'exact diagnostic launch'):
                    verify(spec,route,altered,req_sha,runner_sha,root,output)
            for different in (
                    ('0'*64,runner_sha,root,output),
                    (req_sha,'0'*64,root,output),
                    (req_sha,runner_sha,root,root/'other-output')):
                with self.subTest(different=different), self.assertRaisesRegex(ValueError,'exact diagnostic launch'):
                    verify(spec,route,value,*different)
            for wrong in ({**route,'source_pool':'pool-b','target_pool':'pool-a'},
                          {**route,'model_calls_allowed':1}, {**route,'model_calls_allowed':False}):
                with self.subTest(wrong=wrong), self.assertRaisesRegex(ValueError,'only the A-to-B no-model'):
                    verify(spec,wrong,value,req_sha,runner_sha,root,output)
            for wrong in ({k:v for k,v in spec.items() if k!='current_boots'}, {**spec,'reviewed':True}):
                with self.subTest(wrong=wrong), self.assertRaisesRegex(ValueError,'current boot proof'):
                    verify(wrong,route,value,req_sha,runner_sha,root,output)
            with self.assertRaisesRegex(ValueError,'pinned no-model OpenCode export worker'):
                verify({**spec,'worker':pin},route,value,req_sha,runner_sha,root,output)
            authorization.write_text('{}',encoding='utf-8')
            with self.assertRaisesRegex(ValueError,'Pinned launch file changed'):
                RUNNER['read_pin'](auth_ref)

    def test_v1_still_requires_explicit_review_and_refuses_v2_auth(self):
        route = dict(schema='pilot-c4-state-reopen-draft-v1',model_calls_allowed=0,
                     source_pool='pool-b',target_pool='pool-a')
        verify = RUNNER['verify_launch']
        spec = dict(schema=RUNNER['REQUEST_V1'],reviewed=True)
        verify(spec,route,None,None,None,None,None)
        with self.assertRaisesRegex(ValueError,'Explicit reviewed execution'):
            verify(dict(spec,reviewed=False),route,None,None,None,None,None)
        with self.assertRaisesRegex(ValueError,'Explicit reviewed execution'):
            verify(spec,route,{},None,None,None,None)

    def test_v2_uses_only_a_storage_and_the_two_original_clients(self):
        def pool(letter, subnet):
            roles = {role:dict(instance_id=f'i-{letter}-{role}', hostname=f'{letter}-{role}',
                              data_disk_ids=[f'd-{letter}-{role}'], private_ip=f'10.20.{subnet}.{index}')
                     for index,role in enumerate(('client','metadata','objects'),1)}
            return dict(pool_id='pool-'+letter,roles=roles)
        a, b = pool('a',2), pool('b',3)
        original = {'pools':[a,b]}
        roles = {**a['roles'],'target':b['roles']['client']}
        route = lambda selected: {'pools':[dict(pool_id='pool-c4-state',roles=selected)]}
        check = RUNNER['check_authorized_storage']
        check(original,route(roles))
        for key in ('metadata','objects'):
            with self.subTest(key=key), self.assertRaisesRegex(ValueError,'requires A storage'):
                check(original,route({**roles,key:b['roles'][key]}))

    def boot_evidence(self):
        now = datetime(2026, 11, 12, 8, 0, tzinfo=timezone.utc)
        draft = dict(path='/draft.json',sha256='0'*64)
        route = dict(original_inventory=dict(path='/inventory.json',sha256='1'*64))
        frozen = {side:dict(hostname=f'executor-{index}',instance_id=f'i-{index}',data_disk_ids=[f'd-{index}'])
                  for index,side in enumerate(('source','target'),1)}
        rows = {side:dict(**node,boot_id=f'00000000-0000-0000-0000-{index:012}',
                          work_root='/srv/ax-client',device='253:16',slot=dict(RUNNER['SLOT']))
                for index,(side,node) in enumerate(frozen.items(),1)}
        value = dict(schema=RUNNER['CURRENT_BOOTS'],read_only=True,at=now.isoformat(),draft=draft,
                     original_inventory=route['original_inventory'],clients=rows)
        return value, draft, route, frozen, now

    def test_reviewed_current_boot_rechecks_actual_identity_before_reuse(self):
        value, draft, route, frozen, now = self.boot_evidence()
        rows = RUNNER['check_current_boots'](value,draft,route,frozen,now=now)
        original = rows['source']
        self.assertEqual(RUNNER['current_identity'](dict(original),original,frozen['source']),
                         {'hostname':original['hostname'],'boot_id':original['boot_id']})
        for change in (
                {'boot_id':'00000000-0000-0000-0000-000000000003'},
                {'instance_id':'i-wrong'},
                {'data_disk_ids':['d-other']},
                {'device':'253:17'},
                {'slot':{**RUNNER['SLOT'],'cgroup.procs':'12345'}},
                {'slot':{**RUNNER['SLOT'],'memory.max':'1073741824'}}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                RUNNER['current_identity']({**original,**change},original,frozen['source'])

    def test_current_boot_evidence_cannot_be_reused_for_another_run_or_host(self):
        value, draft, route, frozen, now = self.boot_evidence()
        for mutation in (
                {'draft':{**draft,'sha256':'2'*64}},
                {'original_inventory':{**route['original_inventory'],'sha256':'3'*64}},
                {'at':(now-timedelta(seconds=901)).isoformat()},
                {'at':(now+timedelta(seconds=1)).isoformat()},
                {'clients':{'source':value['clients']['source']}}):
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                RUNNER['check_current_boots']({**value,**mutation},draft,route,frozen,now=now)
        duplicate = deepcopy(value)
        duplicate['clients']['target']['boot_id'] = duplicate['clients']['source']['boot_id']
        with self.assertRaisesRegex(ValueError,'two distinct executors'):
            RUNNER['check_current_boots'](duplicate,draft,route,frozen,now=now)

    def test_source_grant_cannot_admit_mount_after_actual_rotation(self):
        source = dict(side='source', generation=1, attempt='state-reopen-test')
        target = dict(side='target', generation=2, attempt='state-reopen-test')
        GRANT(source, source, 'source')
        GRANT(target, target, 'target')
        for grant, side in ((source,'source'),(source,'target'),(target,'source')):
            with self.subTest(grant=grant,side=side), self.assertRaises(PermissionError):
                GRANT(grant,target,side)

    @unittest.skipUnless(sys.platform.startswith('linux'), 'Linux network namespace required')
    def test_native_export_never_runs_in_parent_network(self):
        with tempfile.TemporaryDirectory() as tmp:
            proof = Path(tmp)/'network.json'
            result = subprocess.run([sys.executable,'-c',NETWORK,os.readlink('/proc/self/ns/net'),
                str(proof),'/usr/bin/true','fake'],capture_output=True,text=True)
            self.assertNotEqual(result.returncode,0)
            self.assertIn('isolated, loopback-only', result.stderr)
            self.assertFalse(proof.exists())


if __name__ == '__main__':
    unittest.main()
