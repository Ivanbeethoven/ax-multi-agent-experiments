"""Offline gates that route the A C4 object observer onto the dedicated scratch.

The native capacity tool never sees the new 100 GiB disk, so the reviewed
capacity file pins one ``observer_scratch`` snapshot and this module has to
charge that disk with the same (used,total) rows and 20% reserve gates. No
network and no real /srv path is touched here; only temporary local documents.
"""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import importlib.util
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from bench.archive_check import pack

SCRIPTS = Path(__file__).resolve().parents[1]/'experiments/unified-suite/pilot-aliyun/scripts'


def module(name):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS/(name+'.py'))
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


resume = module('resume-c4-after-directory-hold')
original = module('run-c4-batch')
continuation = module('continue-c4-formal')
overlay = module('calibrate-c4-closed-observer')

SCRATCH = '/srv/ax-c4-observer-scratch'
TAG = 'c4-a-flat-on1'


def doc(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding='utf-8')
    return resume.ref(path)


class ScratchStageCapacity(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.batch = self.root/'batch'
        self.runtime = self.root/'runtime'
        self.out = self.root/'resume'
        self.trial = self.root/'calibration/on1'
        self.cell = {'pool':'pool-a','id':'original-c4'}
        self.request = doc(self.root/'original.json', {})
        self.off1 = self.root/'calibration/off1'
        self.clock = datetime.now(timezone.utc)+timedelta(seconds=1)
        self.off1_timing(2,1,2,1)
        self.cleanup = dict(schema='pilot-prestep-cleanup-v1',pool='pool-a',original_request=self.request,
            step=2,prefix=self.prefix,finished_at=self.clock.isoformat(),outcome='not-needed',actions=[])
        self.inventory = doc(self.runtime/'fleet.json', {})
        self.old = dict(runtime_control=doc(self.root/'runtime-pin.json',{}),
            tools={'capacity':resume.ref(SCRIPTS/'capacity-check.py')},
            control=str(self.root/'formal-control'), formal_attempt='c4-a-flat-formal-01',
            trials=[dict(out=str(self.off1),attempt='c4-a-flat-off1',mode='off'),
                    dict(out=str(self.trial),attempt=TAG,mode='on')])
        for suffix,mode in (('on2','on'),('off2','off'),('off3','off'),('on3','on')):
            self.old['trials'].append(dict(out=str(self.root/'calibration'/suffix),
                                           attempt='c4-a-flat-'+suffix,mode=mode))
        self.nodes, self.placements = {}, {}
        for role, path in continuation.ROLES.items():
            disk = dict(path=path if role!='objects' else path+'/rustfs-0',device=10,serial=role,
                device_bytes=2_000_000,total_bytes=1_000_000,total_inodes=10000,
                used_bytes=1000,used_inodes=1000,available_bytes=999000,available_inodes=9000)
            self.nodes[role] = dict(boot_id=role,sampled_at=self.clock.isoformat(),disks=[disk])
            self.placements[role] = dict(path=disk['path'],boot_id=role,targets={
                path+'/'+name:10 for name in ('o','archives','evidence')})
        self.nodes['objects'].update(container_id='rustfs',image='rustfs-image',pid=123)
        for i in range(1,4):
            self.nodes['objects']['disks'].append(dict(self.nodes['objects']['disks'][0],
                path='/objects/extra-'+str(i),device=10+i))
        self.nodes['metadata']['redis'] = dict(process_id=99,maxmemory=10000,used_memory=1000,aof_current_size=300)
        self.snapshot = dict(at=self.clock.isoformat(),pool='pool-a',inventory=self.inventory,
                             nodes=self.nodes,source_pins=[self.inventory,self.old['tools']['capacity']])
        self.previous = doc(self.root/'previous.json',self.snapshot)
        baseline = deepcopy(self.snapshot)
        baseline['nodes']['objects']['disks'][0]['used_bytes'] -= 10
        self.baseline = doc(self.root/'baseline.json',baseline)
        self.rows = []
        charged = {'client':'/srv/ax-client','metadata':'/srv/ax-metadata'}
        for role,path,quantity in original.capacity_rows(self.snapshot):
            active = path==charged.get(role)
            self.rows.append(dict(role=role,path=path,quantity=quantity,data_growth=12,
                observer_raw=(80_000 if quantity=='bytes' else 30) if active else 0,
                archive_peak=(25_000 if quantity=='bytes' else 20) if active else 0,
                included_in_metadata_disk_forecast=True))
        growth = [dict(role=r['role'],path=r['path'],quantity=r['quantity'],measured_base_increment=10)
                  for r in self.rows]
        self.legacy = doc(self.batch/'capacity.json',
                          dict(passed=True,before=self.baseline,after=self.previous,rows=growth))
        self.rows.extend(dict(role='coordinator',path=str(self.root),quantity=q,data_growth=0,
                              observer_raw=3*raw,archive_peak=3*arc)
                         for q,raw,arc in (('bytes',80_000,25_000),('inodes',30,20)))
        self.device = 20
        self.scratch = dict(schema='c4-observer-scratch-snapshot-v1',passed=True,at=self.clock.isoformat(),
            pool='pool-a',role='objects',tag=TAG,root=SCRATCH,boot_id='objects',ownership_sha256='a'*64,
            worker_receipt={'path':SCRATCH+'/storage.json','sha256':'b'*64},
            disk_id='d-scratch',serial='serial',uuid='uuid',device=self.device,
            disk=dict(path=SCRATCH,device=self.device,serial='serial',disk_id='d-scratch',uuid='uuid',
                device_bytes=100*1024**3,source='/dev/vdf',fstype='ext4',total_bytes=2_000_000,
                used_bytes=1000,available_bytes=1_599_000,total_inodes=10000,used_inodes=1000,
                available_inodes=9000),
            targets={SCRATCH+'/'+name:self.device for name in resume.SCRATCH_TREES})
        self.rows.extend(dict(role='objects',path=SCRATCH,quantity=quantity,data_growth=0,
                              observer_raw=80_000 if quantity=='bytes' else 30,
                              archive_peak=25_000 if quantity=='bytes' else 20,
                              included_in_metadata_disk_forecast=True)
                         for quantity in ('bytes','inodes'))
        self.placements['objects'] = dict(path=SCRATCH,root=SCRATCH,boot_id='objects',
            targets={SCRATCH+'/'+name:self.device for name in ('o','archives','evidence')})
        self.raw, self.archive = {}, {}
        for role in continuation.ROLES:
            self.raw[role] = [self.observe(role)]
            self.archive[role] = self.pack(role)
        self.budget = dict(schema='c4-resume-stage-capacity-v1',passed=True,reviewed=True,
            original_request=self.request,legacy_capacity=self.legacy,runtime=self.old['runtime_control'],
            pool='pool-a',cell_id=self.cell['id'],step=2,prefix=self.prefix,
            archive_mode='closed-observer-direct',reserve_fraction=.2,at=self.clock.isoformat(),
            coordinator=dict(path=str(self.root),device=self.root.stat().st_dev,
                targets={str(p):self.root.stat().st_dev for p in
                         (self.out,self.trial,self.trial/'archives',self.trial/'raw')}),
            observer_basis=self.raw,archive_basis=self.archive,observer_seconds=10,
            rows=self.rows,observer_scratch=None)

    def off1_timing(self, measure, drain, cold, after_ack):
        purpose = 'observer-calibration-off'
        ack = 100_000_000_000
        start = ack + round(after_ack*1e9)
        end = start + round(drain*1e9)
        drain_pin = doc(self.off1/'drain-timing.json',dict(passed=True,cell_id=self.cell['id'],
            start_ns=start,end_ns=end,seconds=(end-start)/1e9))
        cohort = doc(self.off1/'cohort/result.json',dict(passed=True,workload_passed=True,purpose=purpose,
            coordinator_makespan_seconds=measure,worker_ack_times={'a01':{'finished_ack':ack}}))
        t0 = 33_000_000_000
        verify = doc(self.off1/'raw/client/q'/'c4-a-flat-off1'/'verify/result.json',
            dict(passed=True,cell_id=self.cell['id'],child_name='c4-a-flat-off1',
                 verification_policy=resume.c4_verify.REFERENCE,full_cold_child=True,
                 parent_unchanged=True,start_ns=t0,end_ns=t0+round(cold*1e9),cold_verification_seconds=cold))
        first = doc(self.off1/'finished.json',dict(passed=True,purpose=purpose,pool=self.cell['pool'],
            cohort=cohort,client_verify=verify))
        self.prefix = [first]
        self.timing_pins = [first,cohort,drain_pin,verify]

    def observe(self, role):
        root = self.root/'historical-raw'/'raw'/role/'o'/'actual'
        history = []
        for i in range(2):
            sample = root/f'sample-{i:06}'
            sample.mkdir(parents=True)
            (sample/'rustfs.prom').write_bytes(b'a'*20)
            times = dict(index=i,passed=True,monotonic_start_ns=i*5_000_000_000,
                         monotonic_end_ns=i*5_000_000_000+500_000_000)
            manifest = doc(sample/'sample.json',dict(times,files={'rustfs.prom':{'bytes':20}}))
            history.append(dict(times,sample_sha256=manifest['sha256']))
        return doc(root/'finished.json',dict(passed=True,samples=2,history=history))

    def pack(self, role):
        staging = self.root/'staging'/role
        names = ('o/actual/rustfs.prom','q/actual/closure.json')
        for name,value in zip(names,(b'a'*262144,b'{}')):
            p = staging/name
            p.parent.mkdir(parents=True,exist_ok=True)
            p.write_bytes(value)
        parent = self.root/'historical-archive'/'archives'
        parent.mkdir(parents=True,exist_ok=True)
        return doc(parent/(role+'-check.json'),pack(staging,list(names),parent/(role+'.tar.gz'),'historical',role))

    def scratch_row(self, quantity):
        return next(r for r in self.rows if r['role']=='objects' and r['path']==SCRATCH and r['quantity']==quantity)

    def gate(self, mode='closed-observer-direct', step=2, phase='on', omit=()):
        snap = doc(self.root/'fresh.json',self.snapshot)
        placement = doc(self.root/'placement.json',dict(schema='c4-archive-device-placement-v1',
            at=self.clock.isoformat(),runtime=self.old['runtime_control'],pool='pool-a',
            workers=self.placements))
        cleanup = doc(self.root/'cleanup.json',self.cleanup)
        scratch_pin = doc(self.root/'scratch-snapshot.json',self.scratch)
        self.budget['observer_scratch'] = scratch_pin
        leaves = [cleanup,self.baseline,self.previous,*self.timing_pins,*self.archive.values(),
                  *(v[0] for v in self.raw.values()),scratch_pin]
        cap = doc(self.root/'budget.json',dict(self.budget,snapshot=snap,placement=placement,cleanup=cleanup,
                  source_pins=[k for k in [snap,placement,*leaves] if k not in omit]))
        free = SimpleNamespace(f_blocks=10_000_000,f_frsize=1,f_bfree=9_000_000,f_bavail=9_000_000,
                               f_files=10_000,f_ffree=9_000,f_favail=9_000)
        with patch.object(resume.os,'statvfs',return_value=free):
            return resume.stage_capacity(cap,self.request,self.old,self.prefix,self.cell,step,phase,
                self.batch,self.runtime,self.out,mode,original,self.clock+timedelta(seconds=1))

    def test_scratch_rows_must_cover_the_dedicated_disk(self):
        self.rows[:] = [r for r in self.rows if not (r['role']=='objects' and r['path']==SCRATCH)]
        with self.assertRaisesRegex(ValueError,'observer scratch rows'):
            self.gate()

    def test_scratch_is_charged_with_the_unchanged_reserve_gate(self):
        self.assertTrue(self.gate()['passed'] is True)
        row = self.scratch_row('bytes')
        extra = sum(row[k] for k in resume.GROWTH)
        disk = self.scratch['disk']
        disk['available_bytes'] = extra + disk['total_bytes']//5 - 1
        with self.assertRaisesRegex(ValueError,'20% reserve'):
            self.gate()
        disk['available_bytes'] = extra + disk['total_bytes']//5
        self.assertTrue(self.gate()['passed'] is True)

    def test_placement_must_declare_the_scratch_object_root(self):
        self.placements['objects']['root'] = resume.ROOTS['objects']
        with self.assertRaisesRegex(ValueError,'root changed'):
            self.gate()
        self.placements['objects'].pop('root')
        with self.assertRaisesRegex(ValueError,'root changed'):
            self.gate()
        self.placements['objects'].update(root=SCRATCH,path=resume.ROOTS['objects']+'/rustfs-0')
        with self.assertRaisesRegex(ValueError,'scratch disk'):
            self.gate()

    def test_client_root_cannot_move_to_the_scratch_disk(self):
        self.placements['client']['root'] = SCRATCH
        with self.assertRaisesRegex(ValueError,'root changed'):
            self.gate()

    def test_scratch_boot_id_must_match_the_objects_sample(self):
        self.scratch['boot_id'] = 'another-boot'
        with self.assertRaisesRegex(ValueError,'scratch snapshot'):
            self.gate()

    def test_scratch_sample_must_follow_the_cleanup_check(self):
        self.scratch['at'] = (self.clock-timedelta(seconds=1)).isoformat()
        with self.assertRaisesRegex(ValueError,'scratch sample'):
            self.gate()

    def test_off_step_cannot_use_the_scratch_disk(self):
        with self.assertRaisesRegex(ValueError,'observed on/formal'):
            self.gate(phase='off')

    def test_scratch_requires_the_closed_observer_overlay(self):
        self.budget['archive_mode'] = 'original'
        with self.assertRaisesRegex(ValueError,'closed-observer overlay'):
            self.gate(mode='original')

    def test_formal_step_is_tagged_with_the_formal_attempt(self):
        # Step 7 has no old['trials'][6]; its scratch tag is the formal attempt.
        self.budget['step'] = self.cleanup['step'] = 7
        self.scratch['tag'] = self.old['formal_attempt']
        formal = Path(self.old['control'])/'cells'/self.cell['id']
        self.budget['coordinator'] = dict(path=str(self.root),device=self.root.stat().st_dev,
            targets={str(p):self.root.stat().st_dev for p in (self.out,formal,formal/'archives',formal/'raw')})
        self.assertTrue(self.gate(step=7,phase='formal')['passed'] is True)
        self.scratch['tag'] = TAG
        with self.assertRaisesRegex(ValueError,'attempt'):
            self.gate(step=7,phase='formal')


class ScratchOverlayRequest(unittest.TestCase):
    def setUp(self):
        self.overlay = dict(helper={'path':'/helper','sha256':'0'*64},worker_code={'client':{}})
        self.old = {'tools':{'calibrate':{'path':'/calibrate','sha256':'1'*64},
                             'run':{'path':'/run','sha256':'2'*64}}}
        self.trial = {'path':'/trial','sha256':'3'*64}
        self.scratch = dict(schema='c4-observer-scratch-snapshot-v1',root=SCRATCH,device=20,
                            targets={SCRATCH+'/'+n:20 for n in resume.SCRATCH_TREES})

    def test_calibration_overlay_carries_the_scratch_snapshot(self):
        body = resume.calibration_overlay(self.overlay,self.old,self.trial,self.scratch)
        self.assertEqual(body['schema'],'c4-calibration-archive-overlay-v1')
        self.assertEqual(body['observer_scratch'],self.scratch)
        self.assertEqual(body['trial'],self.trial)
        self.assertNotIn('observer_scratch',
                         resume.calibration_overlay(self.overlay,self.old,self.trial,None))

    def test_calibration_overlay_only_routes_the_canonical_scratch_root(self):
        class FakeFork:
            def archive(self):
                return '''
staging=root/'evidence'/TAG;staging.mkdir(parents=True);names=[]
'''

        roots = {'objects':'/srv/ax-objects-0'}
        with self.assertRaises(ValueError):
            overlay.archive_fork(FakeFork,'source','0'*64,{},dict(self.scratch,root='/srv/wrong'),roots)
        self.assertTrue(issubclass(overlay.archive_fork(FakeFork,'source','0'*64,{},self.scratch,roots),FakeFork))


if __name__=='__main__':
    unittest.main()
