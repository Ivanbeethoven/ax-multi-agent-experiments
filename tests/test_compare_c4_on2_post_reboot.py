"""Offline checks for C4 On2 reboot evidence comparison."""
import copy
from datetime import datetime, timedelta, timezone
import importlib.util
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from uuid import UUID

SCRIPTS = Path(__file__).resolve().parents[1]/'experiments/unified-suite/pilot-aliyun/scripts'
spec = importlib.util.spec_from_file_location('compare_on2',SCRIPTS/'compare-c4-on2-post-reboot.py')
compare = importlib.util.module_from_spec(spec)
spec.loader.exec_module(compare)


class On2Reboot(unittest.TestCase):
    def setUp(self):
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.now = datetime(2026,11,13,12,tzinfo=timezone.utc)
        self.capacity = self.doc('tools/capacity-check.py',{})
        nodes, placements, self.rows, self.probes = {}, [], [], {}
        for i,(r,paths) in enumerate(compare.MOUNTS.items(),1):
            physical = sorted(paths)
            disks = [dict(path=p.replace('ax-objects','ax-rustfs-data'),serial=f'volume-{i}-{j}',
                device=i*10+j,device_bytes=100,used_bytes=20,available_bytes=80,total_bytes=100,
                used_inodes=10,available_inodes=90,total_inodes=100) for j,p in enumerate(physical)]
            nodes[r] = dict(boot_id=str(UUID(int=i)),disks=disks)
            placements.append(dict(id='pool-a-'+r,cluster_id='pool-a',host_id=f'i-{i}',hostname=r,
                                   data_disk_ids=['d-'+d['serial'] for d in disks]))
            self.probes[r] = dict(read_only=True,instance_id=f'i-{i}',hostname=r,boot_id=str(UUID(int=i+100)),
                data_mounts=[dict(path=p,is_mount=True,device=d['device']+100) for p,d in zip(physical,disks)])
            self.rows.extend(dict(role=r,path=d['path'],quantity=q,measured_base_increment=5)
                             for d in disks for q in ('bytes','inodes'))
        nodes['objects'].update(container_id='container',image='sha256:old',pid=111)
        nodes['metadata']['redis'] = dict(maxmemory=100,used_memory=10,aof_current_size=1,process_id=22)
        self.probes['objects']['rustfs'] = dict(running=True,id='container',image_id='sha256:old')
        self.probes['metadata']['redis'] = dict(active='active',systemd_pid='22',process_id='22',
                                               settings=dict(appendonly='yes',maxmemory='100'))
        self.rows.extend(dict(role='metadata',quantity=q,measured_base_increment=0)
                         for q in ('redis-memory-bytes','redis-aof-bytes'))
        orig_fleet = self.doc('historic/fleet.json',dict(nodes=placements))
        live_fleet = self.doc('live/fleet.json',dict(nodes=placements))
        old_stamp = self.now-timedelta(days=1)
        common = dict(pool='pool-a',inventory=orig_fleet,source_pins=[orig_fleet,self.capacity])
        self.before = self.doc('historic/before.json',dict(common,at=old_stamp.isoformat(),nodes=nodes))
        for n in nodes.values():
            for d in n['disks']:
                d['used_bytes'] += 5
                d['available_bytes'] -= 5
        self.after = self.doc('historic/after.json',dict(common,at=(old_stamp+timedelta(hours=1)).isoformat(),
                                                       nodes=nodes,source_pins=[*common['source_pins'],self.before]))
        self.original_cap = self.doc('historic/original-capacity.json',dict(before=self.before,after=self.after))
        self.old = self.doc('historic/request.json',dict(schema='c4-fork-batch-v1',reviewed=True,cell_id=compare.CELL,
            out=str(self.root/'historic'),runtime_control=self.doc('historic/binding.json',dict(fleet=orig_fleet)),
            tools=dict(capacity=self.capacity),trials=[{}, {}, dict(mode='on',attempt='c4-a-flat-on2',
                collector_name='original-on1',collector_out='/original-on1')]))
        live = copy.deepcopy(nodes)
        for r,n in live.items():
            n.update(boot_id=self.probes[r]['boot_id'],sampled_at=(self.now-timedelta(seconds=5)).isoformat())
            for d in n['disks']:
                d['device'] += 100
        self.current = dict(pool='pool-a',at=self.now.isoformat(),nodes=live,inventory=live_fleet,
                            source_pins=[live_fleet,self.capacity])

    def doc(self, name, value):
        p = self.root/name
        p.parent.mkdir(parents=True,exist_ok=True)
        p.write_text(json.dumps(value),encoding='utf-8')
        return dict(path=str(p),sha256=compare.digest(p))

    def run_compare(self):
        growth = self.doc('historic/capacity.json',dict(schema='c4-aof-reconciled-capacity-v1',passed=True,
            pool='pool-a',cell_id=compare.CELL,before=self.before,after=self.after,original_capacity=self.original_cap,
            source_pins=[self.before,self.after,self.original_cap],rows=self.rows,reserve_fraction=.2,extra_full_copies=7))
        current = self.doc('live/current.json',self.current)
        probes = {r:self.doc('live/'+r+'.json',p) for r,p in self.probes.items()}
        return compare.run(self.doc('compare.json',dict(schema='c4-on2-post-reboot-comparison-request-v1',
            original_request=self.old,historic_growth=growth,current_snapshot=current,current_probes=probes)),self.now)

    def test_distinguishes_new_boot_from_reused_pid_and_device_numbers(self):
        out = self.run_compare()
        self.assertTrue(out['comparison_valid'])
        self.assertFalse(out['dispatch_eligible'])
        self.assertNotIn('reviewed',out)
        self.assertNotIn('passed',out)
        rustfs = out['nodes']['objects']['service']
        self.assertEqual(rustfs['historic_pid'],rustfs['current_pid'])
        self.assertEqual(out['collector_predecessor']['state'],'not-probed')
        self.assertFalse(out['collector_predecessor']['has_declared_replacement'])

    def test_rejects_fleet_claim_if_raw_probe_names_other_host_or_boot(self):
        raw = self.probes['objects']
        good = raw['instance_id']
        raw['instance_id'] = 'i-other'
        with self.assertRaisesRegex(ValueError,'Wrong current host'):
            self.run_compare()
        raw['instance_id'] = good
        raw['boot_id'] = str(UUID(int=3))
        with self.assertRaisesRegex(ValueError,'unchanged boot'):
            self.run_compare()

    def test_rejects_replaced_disk_and_rustfs_image(self):
        disk = self.current['nodes']['objects']['disks'][0]
        old = disk['serial']
        disk['serial'] = 'replacement'
        with self.assertRaisesRegex(ValueError,'serials changed'):
            self.run_compare()
        disk['serial'] = old
        self.current['nodes']['objects']['image'] = 'sha256:new'
        self.probes['objects']['rustfs']['image_id'] = 'sha256:new'
        with self.assertRaisesRegex(ValueError,'RustFS image'):
            self.run_compare()

    def test_rejects_old_before_input_and_understated_historical_delta(self):
        self.current['source_pins'].append(self.before)
        with self.assertRaisesRegex(ValueError,'no historical before'):
            self.run_compare()
        self.current['source_pins'].pop()
        self.rows[0]['measured_base_increment'] = 4
        with self.assertRaisesRegex(ValueError,'same-boot allocation deltas'):
            self.run_compare()

    def test_rejects_stale_native_readback(self):
        self.current['nodes']['client']['sampled_at'] = (self.now-timedelta(seconds=901)).isoformat()
        with self.assertRaisesRegex(ValueError,'stale probe'):
            self.run_compare()


if __name__=='__main__':
    unittest.main()
