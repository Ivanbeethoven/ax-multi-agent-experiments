"""Small available-only capacity fixtures; no SSH or source dataset creation."""
import copy
from datetime import datetime,timezone
import json
from pathlib import Path
import runpy
import tempfile
import types
import unittest

REPO=Path(__file__).resolve().parents[1]
CAPACITY=runpy.run_path(str(REPO/'experiments/unified-suite/pilot-aliyun/scripts/packed-c3-capacity.py'))
CONTROLLER=runpy.run_path(str(REPO/'experiments/unified-suite/pilot-aliyun/scripts/packed-c3-controller.py'))


class PackedCapacity(unittest.TestCase):
    def test_raw_before_is_earlier_hashed_available_only_and_same_boot(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);raw=root/'host-meminfo.txt';raw.write_text('MemAvailable: 1000 kB\n')
            path=root/'sample.json';value=dict(passed=True,errors=[],utc_ns=1_000_000_000,processes=[dict(boot_id='same')],
                capacity={'/data':dict(bytes_free=100,inodes_free=100)},files={raw.name:dict(bytes=raw.stat().st_size,sha256=CAPACITY['digest'](raw))})
            path.write_text(json.dumps(value));boundary=datetime.fromtimestamp(2,timezone.utc)
            result=CAPACITY['before_sample'](CAPACITY['ref'](path),boundary,'same',['/data'])
            self.assertTrue(result['before_total_unknown']);self.assertNotIn('total_bytes',result['capacity']['/data'])
            self.assertEqual(result['capacity']['/data']['available_bytes'],100)
            with self.assertRaises(ValueError):CAPACITY['before_sample'](CAPACITY['ref'](path),boundary,'another',['/data'])
            with self.assertRaises(ValueError):CAPACITY['before_sample'](CAPACITY['ref'](path),datetime.fromtimestamp(1,timezone.utc),'same',['/data'])
            raw.write_text('changed')
            with self.assertRaises(ValueError):CAPACITY['before_sample'](CAPACITY['ref'](path),boundary,'same',['/data'])

    def documents(self):
        before={};after={}
        for role in ('client','metadata','objects'):
            before[role]=dict(boot_id=role,capacity={'/'+role:dict(available_bytes=900,available_inodes=900)})
            after[role]=dict(boot_id=role,disks=[dict(path='/'+role,available_bytes=850,available_inodes=850,total_bytes=1000,total_inodes=1000)])
        before['metadata']['redis']=dict(process_id=5,maxmemory=1000,used_memory=100,aof_current_size=10)
        after['metadata']['redis']=dict(process_id=5,maxmemory=1000,used_memory=150,aof_current_size=20)
        return before,after

    def test_all_roles_budget_seven_copies_and_native_decreases_cannot_hide_growth(self):
        before,after=self.documents();rows=CAPACITY['forecast'](before,after,7,.2)
        self.assertTrue(all(row['passed'] for row in rows));self.assertEqual(len(rows),8)
        self.assertEqual(next(row for row in rows if row['role']=='client' and row['quantity']=='bytes')['projected_available'],500)
        after['client']['disks'][0]['available_bytes']=950
        self.assertFalse(all(row['passed'] for row in CAPACITY['forecast'](before,after,7,.2)))
        before,after=self.documents();after['metadata']['redis']['aof_current_size']=0
        self.assertFalse(all(row['passed'] for row in CAPACITY['forecast'](before,after,7,.2)))
        for copies,reserve in ((6,.2),(7,.19)):
            with self.assertRaises(ValueError):CAPACITY['forecast'](before,after,copies,reserve)
        before,after=self.documents();after['objects']['boot_id']='rebooted'
        with self.assertRaises(ValueError):CAPACITY['forecast'](before,after,7,.2)
        before,after=self.documents();after['metadata']['redis']['process_id']=6
        with self.assertRaises(ValueError):CAPACITY['forecast'](before,after,7,.2)

    def test_admission_budget_is_bound_to_exact_packed_parent_child_and_runtime(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);runtime=root/'runtime';runtime.mkdir();(runtime/'fleet.json').write_text('{}')
            q=root/'q';q.mkdir();(q/'filesystem.json').write_text(json.dumps(dict(original_cloud_base=dict(sha256='a'*64),original_cloud_route=dict(sha256='b'*64))))
            raw=root/'before.json';raw.write_text('{}');pin=CAPACITY['ref'](raw)
            value=dict(schema='packed-c3-capacity-v1',passed=True,cell_id='packed',pool='pool-a',plan_sha256='c'*64,
                inventory_sha256=CAPACITY['digest'](runtime/'fleet.json'),extra_full_copies=7,reserve_fraction=.2,rows=[dict(passed=True)],
                base_preparation=dict(sha256='a'*64),qualification=dict(sha256='b'*64),before=pin,after=pin,source_pins=[pin])
            path=root/'budget.json';path.write_text(json.dumps(value));c=types.SimpleNamespace(qualification=q,id='packed',pool='pool-a',plan={'plan_sha256':'c'*64},runtime=runtime)
            self.assertTrue(CONTROLLER['capacity_gate'](path,c)['passed'])
            value['qualification']['sha256']='d'*64;path.write_text(json.dumps(value))
            with self.assertRaises(ValueError):CONTROLLER['capacity_gate'](path,c)
            value['qualification']['sha256']='b'*64;path.write_text(json.dumps(value));raw.write_text('changed')
            with self.assertRaises(ValueError):CONTROLLER['capacity_gate'](path,c)


if __name__=='__main__':unittest.main()
