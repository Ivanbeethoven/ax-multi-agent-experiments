import copy
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest
from argparse import Namespace
from contextlib import redirect_stdout

SCRIPT=Path(__file__).resolve().parents[1]/'experiments/unified-suite/pilot-aliyun/scripts/capacity-check.py'
spec=importlib.util.spec_from_file_location('pilot_capacity',SCRIPT)
capacity=importlib.util.module_from_spec(spec);spec.loader.exec_module(capacity)
GIB=1024**3


class CapacityTests(unittest.TestCase):
    def documents(self,object_gib=100):
        def disk(name,total):return dict(path=name,device=1,total_bytes=total*GIB,used_bytes=GIB,
                                        available_bytes=(total-1)*GIB,total_inodes=total*GIB//4096,used_inodes=1000)
        before=dict(inventory=dict(path='/fleet.json',sha256='fixed'),nodes={
            'client':dict(boot_id='client',disks=[disk('/client',100)]),
            'metadata':dict(boot_id='meta',disks=[disk('/meta',60)],redis=dict(used_memory=GIB//5,maxmemory=10*GIB,process_id=10)),
            'objects':dict(boot_id='objects',container_id='fixed',image='fixed',pid=20,
                disks=[disk('/object-'+str(i),object_gib) for i in range(4)])})
        after=copy.deepcopy(before)
        for d in after['nodes']['objects']['disks']:
            d['used_bytes']+=8192000000;d['used_inodes']+=2000000
        after['nodes']['metadata']['disks'][0]['used_bytes']+=GIB//2
        after['nodes']['metadata']['redis']['used_memory']+=9*GIB//10
        return before,after

    def run_budget(self,before,after):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);a=root/'before.json';b=root/'after.json';out=root/'out.json'
            a.write_text(json.dumps(before));b.write_text(json.dumps(after))
            with redirect_stdout(io.StringIO()):
                try:capacity.budget(Namespace(before=a,after=b,out=out,reserve=.2,extra_full_copies=7))
                except SystemExit as error:self.assertEqual(error.code,2)
            return json.loads(out.read_text())

    def test_60gib_rejected_and_100gib_passes_for_same_growth(self):
        self.assertFalse(self.run_budget(*self.documents(60))['passed'])
        self.assertTrue(self.run_budget(*self.documents(100))['passed'])

    def test_redis_memory_is_a_separate_limit(self):
        before,after=self.documents();after['nodes']['metadata']['redis']['used_memory']+=GIB
        result=self.run_budget(before,after)
        self.assertFalse(result['passed'])
        self.assertFalse(next(r for r in result['rows'] if r['quantity']=='redis-memory-bytes')['passed'])

    def test_reboot_and_changed_disk_are_rejected(self):
        before,after=self.documents();after['nodes']['objects']['boot_id']='different'
        with self.assertRaises(ValueError):self.run_budget(before,after)
        before,after=self.documents();after['nodes']['objects']['disks'][0]['total_bytes']+=GIB
        with self.assertRaises(ValueError):self.run_budget(before,after)

    def test_allocation_decrease_cannot_hide_import_growth(self):
        before,after=self.documents();after['nodes']['metadata']['disks'][0]['used_bytes']=0
        self.assertFalse(self.run_budget(before,after)['passed'])


if __name__=='__main__':unittest.main()
