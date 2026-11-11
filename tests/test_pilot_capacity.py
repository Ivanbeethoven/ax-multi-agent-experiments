import copy
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest
from argparse import Namespace
from contextlib import redirect_stdout
if __package__:
    from .test_pilot_pools import cloud_inventory,SETUP
else:
    from test_pilot_pools import cloud_inventory,SETUP
from bench.suite_catalog import template,select_pilot_files
from bench.suite_plan import build

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

    def available_documents(self):
        before,after=self.documents(100)
        before.update(measurement_basis='available',pool='pool-b');after['pool']='pool-b'
        for role,node in before['nodes'].items():
            for index,disk in enumerate(node['disks']):
                current=after['nodes'][role]['disks'][index]
                for value in (disk,current):value.update(serial=role+'-'+str(index),device_bytes=100*GIB)
                disk['available_inodes']=disk['total_inodes']-disk['used_inodes']
                current['available_bytes']=current['total_bytes']-current['used_bytes']
                current['available_inodes']=current['total_inodes']-current['used_inodes']
                for key in ('total_bytes','used_bytes','total_inodes','used_inodes'):disk.pop(key)
        before['nodes']['metadata']['redis']['aof_current_size']=100
        after['nodes']['metadata']['redis']['aof_current_size']=1000
        return before,after

    def test_available_only_before_never_invents_totals_or_used_values(self):
        before,after=self.available_documents();original=copy.deepcopy(before)
        result=self.run_budget(before,after)
        self.assertTrue(result['passed']);self.assertEqual(before,original)
        rows=[r for r in result['rows'] if r.get('measurement_basis')=='available-decrease']
        self.assertEqual(len(rows),12)
        self.assertTrue(all(r['before_total_unknown'] for r in rows))
        self.assertEqual(next(r for r in rows if r['role']=='objects' and r['quantity']=='bytes')['measured_base_increment'],8192000000)
        self.assertEqual(next(r for r in result['rows'] if r['quantity']=='redis-aof-bytes')['measured_base_increment'],900)

    def test_available_only_extra_copies_reserve_and_serial_are_enforced(self):
        before,after=self.available_documents()
        after['nodes']['objects']['disks'][0]['available_bytes']=50*GIB
        self.assertFalse(self.run_budget(before,after)['passed'])
        before,after=self.available_documents();after['nodes']['objects']['disks'][0]['serial']='other-disk'
        with self.assertRaises(ValueError):self.run_budget(before,after)
        before,after=self.available_documents();after['pool']='pool-a'
        with self.assertRaises(ValueError):self.run_budget(before,after)

    def test_available_increase_or_aof_rewrite_cannot_hide_growth(self):
        before,after=self.available_documents();after['nodes']['objects']['disks'][0]['available_bytes']=before['nodes']['objects']['disks'][0]['available_bytes']+1
        self.assertFalse(self.run_budget(before,after)['passed'])

    def test_original_before_adapter_keeps_time_and_rejects_late_or_other_inventory(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);(root/'suite').mkdir();(root/'provenance').mkdir()
            cloud=cloud_inventory();inventory=root/'provenance/inventory.json';inventory.write_text(json.dumps(cloud))
            (root/'fleet.json').write_text(json.dumps(SETUP['actual_fleet'](cloud,'code-12')))
            config=select_pilot_files(template(),100000)
            for pool in config['clusters']:pool['inventory_sha256']=capacity.ref(inventory)['sha256']
            plan=build(config);(root/'suite/plan.json').write_text(json.dumps(plan))
            cell=next(c for c in plan['cells'] if c['workload']=='smallfiles' and c['arm_id']=='juicefs')
            started=root/'started.json';started.write_text(json.dumps({'at':'2026-11-11T05:00:00+00:00','cell_id':cell['id']}))
            raw=root/'raw'
            for role,node in cloud['pools'][1]['roles'].items():
                folder=raw/role;folder.mkdir(parents=True)
                source=dict(passed=True,at='2026-11-11T04:50:03+00:00',instance_id=node['instance_id'],boot_id=role,
                    disks=[dict(id=d,serial='actual-'+d,gib=60,mount='/srv/'+role+'-'+str(i),st_dev=100+i,
                        free_bytes=50*GIB,free_inodes=1000000) for i,d in enumerate(node['data_disk_ids'])])
                if role=='objects':source['containers']=[dict(running=True,id='object-service',image='image',pid=20,allowed_environment={'RUSTFS_ADDRESS':'10.0.0.1:9000'})]
                if role=='metadata':
                    source['redis_status']={'MainPID':'10'}
                    (folder/'redis-info').mkdir();(folder/'redis-info/stdout').write_text('used_memory:100\nused_memory_rss:200\nmaxmemory:1000000\naof_current_size:100\nprocess_id:10\n')
                (folder/'result.json').write_text(json.dumps(source))
            args=Namespace(control=root,pool='pool-b',runtime_control=None,runtime_control_sha256=None,
                source_directory=raw,preparation_started=started,out=root/'before.json')
            with redirect_stdout(io.StringIO()):capacity.import_before(args)
            result=json.loads(args.out.read_text())
            self.assertEqual(result['at'],'2026-11-11T04:50:03+00:00')
            self.assertNotIn('total_bytes',result['nodes']['objects']['disks'][0])
            metadata=raw/'metadata/result.json';value=json.loads(metadata.read_text());value['at']='2026-11-11T05:00:01+00:00';metadata.write_text(json.dumps(value))
            args.out=root/'late.json'
            with self.assertRaises(ValueError):capacity.import_before(args)
            inventory.write_text(inventory.read_text()+'\n')
            with self.assertRaises(ValueError):capacity.import_before(args)
        before,after=self.available_documents();after['nodes']['metadata']['redis']['aof_current_size']=0
        self.assertFalse(self.run_budget(before,after)['passed'])


if __name__=='__main__':unittest.main()
