"""A runtime refresh keeps placements, original receipts and one state database."""
import copy
import json
from pathlib import Path
import tempfile
import unittest

from bench.runtime import pilot_context as context


class RuntimeRefresh(unittest.TestCase):
    def fixture(self,r):
        old=r/'original';new=r/'runtime';old.mkdir();new.mkdir()
        node=dict(id='pool-a-client',role='executor',repo='/srv/ax-client/code-01',
                  host_id='i-original',data_disk_ids=['d-original'],slots=[dict(cgroup='/sys/fs/cgroup/ax-slot-1')])
        for root,repo in [(old,'/srv/ax-client/code-01'),(new,'/srv/ax-client/code-06')]:
            fleet=dict(nodes=[{**node,'repo':repo}]);(root/'fleet.json').write_text(json.dumps(fleet))
            (root/'ssh.conf').write_text('fixed private endpoints')
            (root/'allocation.json').write_text(json.dumps(dict(passed=True,inventory_sha256=context.digest(root/'fleet.json'))))
            for name in ['pool-a-client','pool-a-client-slot']:
                p=root/'probes'/name;p.mkdir(parents=True)
                (p/'probe.json').write_text(json.dumps(dict(passed=True,boot_id='original-boot')))
                (p/'peer.json').write_text(json.dumps(dict(repo=repo)))
        (old/'state.sqlite').write_bytes(b'original state sentinel')
        return old,new

    def test_code_paths_change_without_copying_or_changing_original_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            old,new=self.fixture(Path(tmp));binding=context.validate(old,new)
            f=new/'binding.json';f.write_text(json.dumps(binding))
            self.assertEqual(context.select(old,context.ref(f)),new)
            self.assertEqual((old/'state.sqlite').read_bytes(),b'original state sentinel')
            self.assertFalse((new/'state.sqlite').exists())
            (new/'probes/pool-a-client/peer.json').write_text('{}')
            with self.assertRaisesRegex(ValueError,'changed or non-absolute'):
                context.select(old,context.ref(f))

    def test_changed_host_budget_reboot_or_second_database_are_rejected(self):
        for change in ('host','budget','boot','database'):
            with self.subTest(change=change),tempfile.TemporaryDirectory() as tmp:
                old,new=self.fixture(Path(tmp));f=new/'fleet.json';v=json.loads(f.read_text())
                if change=='host':v['nodes'][0]['host_id']='another-host';f.write_text(json.dumps(v))
                if change=='budget':v['nodes'][0]['slots'][0]['cgroup']='/different';f.write_text(json.dumps(v))
                if change=='boot':(new/'probes/pool-a-client/probe.json').write_text(json.dumps(dict(passed=True,boot_id='rebooted')))
                if change=='database':(new/'state.sqlite').write_bytes(b'forbidden duplicate')
                with self.assertRaises(ValueError):context.validate(old,new)
