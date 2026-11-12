"""The storage-only path fences the old grant and rejects networking."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
import runpy
import subprocess
import sys
import tempfile
import unittest

SCRIPTS = Path(__file__).resolve().parents[1]/'experiments/unified-suite/pilot-aliyun/scripts'
RUNNER = runpy.run_path(str(SCRIPTS/'run-c4-state-reopen.py'))
GRANT = RUNNER['require_grant']
NETWORK = runpy.run_path(str(SCRIPTS/'c4-state-reopen-node.py'))['NETWORK_WRAPPER']


class StateReopen(unittest.TestCase):
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
