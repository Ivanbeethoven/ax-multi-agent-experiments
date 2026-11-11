"""Offline checks for the real C4 trial-2 boundary and archive-only overlay."""
import ast
from datetime import datetime, timedelta, timezone
import errno
import hashlib
import importlib.util
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest

from bench.archive_check import pack
from unittest.mock import patch

SCRIPTS = Path(__file__).resolve().parents[1]/'experiments/unified-suite/pilot-aliyun/scripts'


def module(name):
    s = importlib.util.spec_from_file_location(name, SCRIPTS/(name+'.py'))
    m = importlib.util.module_from_spec(s)
    s.loader.exec_module(m)
    return m


resume = module('resume-c4-after-directory-hold')
overlay = module('calibrate-c4-closed-observer')
original = module('run-c4-batch')
continuation = module('continue-c4-formal')


def doc(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding='utf-8')
    return resume.ref(path)


class EmptyStop(unittest.TestCase):
    def test_requires_original_reap_empty_fence_and_all_roles(self):
        with TemporaryDirectory() as td:
            root = Path(td)
            out = root/'original'
            trial = root/'flat-off1'
            entry = {'trials':[{'out':str(trial)}], 'tools':{'calibrate':resume.ref(SCRIPTS/'calibrate-c4.py')}}
            p = doc(root/'batch-request.json', entry)
            doc(out/'locks-held.json', {'request':p})
            (out/'trial-2').mkdir()
            stop = out/'trial-2'
            doc(out/'failed.json', dict(passed=False, automatic_retry=False,
                error=str(FileExistsError(errno.EEXIST,os.strerror(errno.EEXIST),str(stop)))))
            first = doc(out/'trial-1-request.json', {'attempt':'c4-a-flat-off1'})
            doc(out/'trial-1/intent.json', dict(argv=['python3', entry['tools']['calibrate']['path'],
                '--request', first['path'], '--sha256', first['sha256']], automatic_retry=False))
            doc(out/'trial-2-request.json', {'attempt':'c4-a-flat-on1'})
            done = doc(trial/'finished.json', {'passed':True})
            for role in continuation.ROLES:
                d = out/'failure-release-probe-2'
                doc(d/(role+'-stdout'),dict(safe_for_release=True, live=[],
                    unknown_startup_directories=[], slot='',mounts=[]))
                doc(d/(role+'-exit.json'),dict(exit_code=0))
            with self.assertRaises(FileNotFoundError):
                resume.directory_stop(out,entry,p,original,continuation)
            doc(out/'trial-1/exit.json',dict(exit_code=0))
            self.assertEqual(resume.directory_stop(out,entry,p,original,continuation)['first'],done)
            doc(out/'trial-2/intent.json',dict(fake='would have dispatched'))
            with self.assertRaisesRegex(ValueError,'pre-spawn'):
                resume.directory_stop(out,entry,p,original,continuation)
            (out/'trial-2/intent.json').unlink()
            doc(out/'failure-release-probe-2/objects-exit.json',dict(exit_code=2))
            with self.assertRaisesRegex(ValueError,'objects'):
                resume.directory_stop(out,entry,p,original,continuation)

    def test_frozen_body_does_not_change_collector_or_attempt(self):
        old = {'calibration_verification':'touched-cold-v1', 'trials':[{},
            dict(collector_name='stopped',collector_out='/stopped',new_collector_name='on1',
                 new_collector_out='/on1',mode='on',attempt='exact-on1',out='/original/on1')]}
        core = {'asset':'/original/asset'}
        full = {'path':'/original/full', 'sha256':'0'*64}
        current = resume.current(old['trials'][1])
        self.assertEqual(resume.trial_body(old,core,2,current,full,original),dict(
            **core,**current,new_collector_name='on1',new_collector_out='/on1',
            attempt='exact-on1',out='/original/on1',purpose='observer-calibration-on',
            reviewed=True,verification_policy='touched-cold-v1',full_reference=full))
        with self.assertRaisesRegex(ValueError,'collector'):
            resume.trial_body(old,core,2,dict(collector_name='other',collector_out='/other'),full,original)


class StageCapacity(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.batch = self.root/'batch'
        self.runtime = self.root/'runtime'
        self.out = self.root/'resume'
        self.trial = self.root/'calibration/on1'
        self.cell = {'pool':'pool-a','id':'original-c4'}
        self.request = doc(self.root/'original.json', {})
        self.off1 = self.root/'calibration/off1'
        self.tag = 'c4-a-flat-off1'
        self.off1_timing(2,1,2,1)
        self.clock = datetime.now(timezone.utc)+timedelta(seconds=1)
        self.cleanup = dict(schema='pilot-prestep-cleanup-v1',pool='pool-a',original_request=self.request,
            step=2,prefix=self.prefix,finished_at=self.clock.isoformat(),outcome='not-needed',actions=[])
        self.inventory = doc(self.runtime/'fleet.json', {})
        self.old = dict(runtime_control=doc(self.root/'runtime-pin.json',{}),
                        tools={'capacity':resume.ref(SCRIPTS/'capacity-check.py')},
                        control=str(self.root/'formal-control'),
                        trials=[dict(out=str(self.off1),attempt=self.tag),dict(out=str(self.trial))])
        self.nodes = {}
        self.placements = {}
        for role, path in continuation.ROLES.items():
            first = dict(path=path if role!='objects' else path+'/rustfs-0',device=10,
                         serial=role,device_bytes=2_000_000,total_bytes=1_000_000,total_inodes=10000,
                         used_bytes=1000,used_inodes=1000,available_bytes=999000,available_inodes=9000)
            self.nodes[role] = dict(boot_id=role,sampled_at=self.clock.isoformat(),disks=[first])
            self.placements[role] = dict(path=first['path'],boot_id=role,targets={
                path+'/'+name:10 for name in ('o','archives','evidence')})
        self.nodes['objects'].update(container_id='rustfs',image='rustfs-image',pid=123)
        for i in range(1,4):
            self.nodes['objects']['disks'].append(dict(first,path='/objects/extra-'+str(i),device=10+i))
        self.nodes['metadata']['redis'] = dict(process_id=99,maxmemory=10000,used_memory=1000,aof_current_size=300)
        self.snapshot = dict(at=self.clock.isoformat(),pool='pool-a',inventory=self.inventory,nodes=self.nodes,
                             source_pins=[self.inventory,self.old['tools']['capacity']])
        self.previous = doc(self.root/'previous.json',self.snapshot)
        baseline = json.loads(json.dumps(self.snapshot))
        baseline['nodes']['objects']['disks'][0]['used_bytes']-=10
        self.baseline = doc(self.root/'baseline.json',baseline)
        self.rows = []
        for role,path,quantity in original.capacity_rows(self.snapshot):
            active = role in self.placements and path==self.placements[role]['path']
            archive = (25_000 if quantity=='bytes' else 20) if active else 0
            observer = (80_000 if quantity=='bytes' else 30) if active else 0
            self.rows.append(dict(role=role,path=path,quantity=quantity,data_growth=12,
                                  observer_raw=observer,archive_peak=archive,included_in_metadata_disk_forecast=True))
        self.original_growth = [dict(role=r['role'],path=r['path'],quantity=r['quantity'],measured_base_increment=10)
                                for r in self.rows]
        self.legacy = doc(self.batch/'capacity.json',dict(passed=True,before=self.baseline,
                          after=self.previous,rows=self.original_growth))
        self.rows.extend(dict(role='coordinator',path=str(self.root),quantity=q,data_growth=0,
                              observer_raw=3*raw,archive_peak=3*arc)
                         for q,raw,arc in (('bytes',80000,25000),('inodes',30,20)))
        self.raw, self.archive = {}, {}
        for role in continuation.ROLES:
            self.raw[role] = [self.observe(role)]
            self.archive[role] = self.pack(role)
        self.budget = dict(schema='c4-resume-stage-capacity-v1',passed=True,reviewed=True,
            original_request=self.request,legacy_capacity=self.legacy,runtime=self.old['runtime_control'],
            pool='pool-a',cell_id=self.cell['id'],step=2,prefix=self.prefix,archive_mode='closed-observer-direct',
            reserve_fraction=.2,at=self.clock.isoformat(),
            coordinator=dict(path=str(self.root),device=self.root.stat().st_dev,
                targets={str(p):self.root.stat().st_dev for p in
                    (self.out,self.trial,self.trial/'archives',self.trial/'raw')}),
            observer_basis=self.raw,archive_basis=self.archive,observer_seconds=10,rows=self.rows)

    def off1_timing(self,measure,drain,cold,after_ack):
        purpose = 'observer-calibration-off'
        ack = 100_000_000_000
        start = ack + round(after_ack*1e9)
        end = start + round(drain*1e9)
        drain_pin = doc(self.off1/'drain-timing.json',dict(passed=True,cell_id=self.cell['id'],
            start_ns=start,end_ns=end,seconds=(end-start)/1e9))
        cohort = doc(self.off1/'cohort/result.json',dict(passed=True,workload_passed=True,purpose=purpose,
            coordinator_makespan_seconds=measure,worker_ack_times={'a01':{'finished_ack':ack}}))
        t0 = 33_000_000_000
        verify = doc(self.off1/'raw/client/q'/self.tag/'verify/result.json',dict(passed=True,cell_id=self.cell['id'],
            child_name=self.tag,verification_policy=resume.c4_verify.REFERENCE,full_cold_child=True,
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
        a,b = 'o/actual/rustfs.prom','q/actual/closure.json'
        for name,value in ((a,b'a'*262144),(b,b'{}')):
            p=staging/name
            p.parent.mkdir(parents=True,exist_ok=True)
            p.write_bytes(value)
        parent = self.root/'historical-archive'/'archives'
        parent.mkdir(parents=True,exist_ok=True)
        return doc(parent/(role+'-check.json'),pack(staging,[a,b],parent/(role+'.tar.gz'),'historical',role))

    def row(self, role, quantity):
        return next(r for r in self.rows if r['role']==role and r['quantity']==quantity and r['archive_peak'])

    def gate(self, mode='closed-observer-direct', omit=(), step=2, phase='on'):
        snap = doc(self.root/'fresh.json',self.snapshot)
        p = doc(self.root/'placement.json',dict(schema='c4-archive-device-placement-v1',at=self.clock.isoformat(),
            runtime=self.old['runtime_control'],pool='pool-a',workers=self.placements))
        cleanup = doc(self.root/'cleanup.json',self.cleanup)
        leaves = [cleanup,self.baseline,self.previous,*self.timing_pins,*self.archive.values(),
                  *(v[0] for v in self.raw.values())]
        cap = doc(self.root/'budget.json',dict(self.budget,snapshot=snap,placement=p,cleanup=cleanup,
                  source_pins=[k for k in [snap,p,*leaves] if k not in omit]))
        free = SimpleNamespace(f_blocks=10000000,f_frsize=1,f_bfree=9000000,f_bavail=9000000,
                               f_files=10000,f_ffree=9000,f_favail=9000)
        with patch.object(resume.os,'statvfs',return_value=free):
            return resume.stage_capacity(cap,self.request,self.old,self.prefix,self.cell,step,phase,self.batch,
                self.runtime,self.out,mode,original,self.clock+timedelta(seconds=1))

    def test_direct_and_original_charge_different_peak(self):
        self.assertTrue(self.gate()['passed'])
        self.budget['archive_mode']='original'
        with self.assertRaisesRegex(ValueError,'measured staging/tar peak'):
            self.gate('original')
        for row in self.rows:
            if row['role']!='coordinator':
                row['archive_peak']+=row['observer_raw']
        self.assertTrue(self.gate('original')['passed'])

    def test_rejects_oversubscription_or_wrong_observer_device(self):
        self.placements['objects']['targets']['/srv/ax-objects-0/o']=13
        with self.assertRaisesRegex(ValueError,'target placement'):
            self.gate()
        self.placements['objects']['targets']['/srv/ax-objects-0/o']=10
        row = next(r for r in self.rows if r['role']=='objects' and r['archive_peak'] and r['quantity']=='bytes')
        row['data_growth']=850_000
        with self.assertRaisesRegex(ValueError,'20% reserve'):
            self.gate()

    def test_rejects_legacy_only_or_wrong_prefix(self):
        self.budget['prefix']=[]
        with self.assertRaisesRegex(ValueError,'exact next step'):
            self.gate()
        self.budget['prefix']=self.prefix
        del self.rows[-1]
        with self.assertRaisesRegex(ValueError,'all native rows'):
            self.gate()


    def test_declines_pin_only_or_understated_growth_and_peaks(self):
        with self.assertRaisesRegex(ValueError,'distinct pinned sources'):
            self.gate(omit=self.raw['objects'])
        with self.assertRaisesRegex(ValueError,'distinct pinned sources'):
            self.gate(omit=(self.archive['client'],))
        self.row('objects','bytes')['observer_raw']=4096
        with self.assertRaisesRegex(ValueError,'measured raw rate/peak'):
            self.gate()
        self.row('objects','bytes')['observer_raw']=80000
        self.row('objects','bytes')['archive_peak']=1
        with self.assertRaisesRegex(ValueError,'measured staging/tar peak'):
            self.gate()
        self.row('objects','bytes')['archive_peak']=25000
        for row in self.original_growth:
            if row['role']=='objects' and row['quantity']=='bytes':
                row['measured_base_increment']=0
        self.legacy = doc(self.batch/'capacity.json',dict(passed=True,before=self.baseline,
                          after=self.previous,rows=self.original_growth))
        self.budget['legacy_capacity']=self.legacy
        with self.assertRaisesRegex(ValueError,'below its native snapshots'):
            self.gate()

    def test_declines_actual_output_on_another_device(self):
        self.assertTrue(self.gate()['passed'])
        targets=self.budget['coordinator']['targets']
        del targets[str(self.trial/'raw')]
        with self.assertRaisesRegex(ValueError,'actual trial/formal'):
            self.gate()
        targets[str(self.trial/'raw')]=self.root.stat().st_dev
        self.trial.parent.mkdir(exist_ok=True)
        stat = Path.stat
        def different_device(path,*args,**kw):
            value = stat(path,*args,**kw)
            if path==self.trial.parent:
                fields=list(value)
                fields[2]+=1
                return os.stat_result(fields)
            return value
        with patch.object(Path,'stat',different_device):
            with self.assertRaisesRegex(ValueError,'actual trial/formal'):
                self.gate()


    def test_cleanup_must_be_real_or_not_needed_and_before_every_sample(self):
        self.cleanup['outcome']='cleaned'
        with self.assertRaisesRegex(ValueError,'real apply actions'):
            self.gate()
        request = doc(self.root/'reclaim.request.json',
                      dict(schema='c4-closed-object-staging-reclaim-v1',reviewed=True))
        action = dict(request=request,at=self.clock.isoformat(),applied=False,
                      passed=True,groups=[dict(allocated_bytes=100)])
        self.cleanup['actions']=[doc(self.root/'reclaimer.json',action)]
        with self.assertRaisesRegex(ValueError,'real applied'):
            self.gate()
        action['applied']=True
        self.cleanup['actions']=[doc(self.root/'reclaimer.json',action)]
        self.assertTrue(self.gate()['passed'])
        self.nodes['objects']['sampled_at']=(self.clock-timedelta(milliseconds=5)).isoformat()
        with self.assertRaisesRegex(ValueError,'Every native capacity sample'):
            self.gate()
        self.nodes['objects']['sampled_at']=self.clock.isoformat()
        self.cleanup['finished_at']=(self.clock+timedelta(milliseconds=5)).isoformat()
        with self.assertRaisesRegex(ValueError,'Every native capacity sample'):
            self.gate()

    def test_tampered_measurement_or_tar_is_not_a_basis(self):
        raw = Path(self.raw['objects'][0]['path']).parent/'sample-000000'/'rustfs.prom'
        raw.write_bytes(b'wrong-length')
        with self.assertRaisesRegex(ValueError,'member size differs'):
            self.gate()
        raw.write_bytes(b'a'*20)
        tar = Path(self.archive['objects']['path']).parent/'objects.tar.gz'
        changed = bytearray(tar.read_bytes())
        changed[-1] ^= 1
        tar.write_bytes(changed)
        with self.assertRaisesRegex(ValueError,'changed or non-absolute pinned file'):
            self.gate()


    def test_short_history_cannot_shrink_actual_off1_window(self):
        self.off1_timing(32,2.5,26.4,4)
        self.budget['prefix']=self.cleanup['prefix']=self.prefix
        for phase,step in (('on',2),('formal',7)):
            with self.subTest(phase=phase):
                self.budget['step']=self.cleanup['step']=step
                self.budget['observer_seconds']=10  # Covers the 5.5s source, but not actual A Off1.
                with self.assertRaisesRegex(ValueError,'65 seconds minimum'):
                    self.gate(step=step,phase=phase)
                self.budget['observer_seconds']=61  # Covers the three durations, but not the recorded post-ACK gap.
                with self.assertRaisesRegex(ValueError,'65 seconds minimum'):
                    self.gate(step=step,phase=phase)
                self.budget['observer_seconds']=65
                output = self.trial if phase=='on' else Path(self.old['control'])/'cells'/self.cell['id']
                self.budget['coordinator']['targets']={str(p):self.root.stat().st_dev for p in
                    (self.out,output,output/'archives',output/'raw')}
                for role in (*continuation.ROLES,'coordinator'):
                    roles = 3 if role=='coordinator' else 1
                    self.row(role,'bytes')['observer_raw']=240000*roles
                    self.row(role,'inodes')['observer_raw']=160*roles
                with self.assertRaisesRegex(ValueError,'distinct pinned sources'):
                    self.gate(omit=(self.timing_pins[2],),step=step,phase=phase)
                self.assertTrue(self.gate(step=step,phase=phase)['passed'])


class ArchiveOnly(unittest.TestCase):
    def setUp(self):
        tree = ast.parse((SCRIPTS/'run-c4.py').read_bytes())
        self.archive = next(n.value for n in ast.walk(tree) if isinstance(n,ast.Constant)
            and isinstance(n.value,str) and overlay.MARKER in n.value)

    def test_uses_exact_original_archive_prefix(self):
        raw = (SCRIPTS/'archive-c4-closed-observer.py').read_bytes()
        updated = overlay.rewrite(self.archive,raw.decode(),hashlib.sha256(raw).hexdigest(),{})
        self.assertEqual(updated.split('\nHELPER_SOURCE=',1)[0],self.archive.split(overlay.MARKER,1)[0])
        self.assertNotIn('shutil.copyfile(p,target)',updated)
        self.assertIn('scope[\'archive\'](spec)',updated)
        compile(updated,'archive','exec')
        with self.assertRaisesRegex(ValueError,'boundary'):
            overlay.rewrite(self.archive.replace(overlay.MARKER,'\nunknown pack'),raw.decode(), '0'*64,{})

    def test_off_request_cannot_use_overlay_and_unchanged_original_is_importable(self):
        runner = SCRIPTS/'run-c4.py'
        calibration = SCRIPTS/'calibrate-c4.py'
        helper = SCRIPTS/'archive-c4-closed-observer.py'
        pins = {r:{f:resume.ref(SCRIPTS.parents[3]/f)['sha256'] for f in overlay.CODE_FILES}
                for r in overlay.ROLES}
        with TemporaryDirectory() as td:
            root = Path(td)
            req = doc(root/'request.json',dict(schema='c4-calibration-archive-overlay-v1',reviewed=True,
                calibrate=resume.ref(calibration),runner=resume.ref(runner),helper=resume.ref(helper),worker_code=pins,
                trial=doc(root/'trial.json',dict(reviewed=True,purpose='observer-calibration-off'))))
            with self.assertRaisesRegex(ValueError,'observer-on'):
                overlay.run(req)
        imported = overlay.import_original(calibration)
        self.assertEqual(Path(imported.runner.__file__),runner)


if __name__=='__main__':
    unittest.main()