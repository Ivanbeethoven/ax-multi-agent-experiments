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
        self.cell = {'pool':'pool-a','id':'original-c4'}
        self.request = doc(self.root/'original.json', {})
        self.prefix = [doc(self.root/'first-finished.json', {})]
        self.clock = datetime.now(timezone.utc)+timedelta(seconds=1)
        self.inventory = doc(self.runtime/'fleet.json', {})
        self.old = dict(runtime_control=doc(self.root/'runtime-pin.json',{}),
                        tools={'capacity':resume.ref(SCRIPTS/'capacity-check.py')})
        self.nodes = {}
        self.placements = {}
        for role, path in continuation.ROLES.items():
            first = dict(path=path if role!='objects' else path+'/rustfs-0',device=10,
                         serial=role,device_bytes=20000,total_bytes=10000,total_inodes=10000,
                         used_bytes=1000,used_inodes=1000,available_bytes=9000,available_inodes=9000)
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
        self.rows = []
        for role,path,quantity in original.capacity_rows(self.snapshot):
            archive = 35 if role in self.placements and path==self.placements[role]['path'] else 0
            observer = 40 if archive else 0
            self.rows.append(dict(role=role,path=path,quantity=quantity,data_growth=12,
                                  observer_raw=observer,archive_peak=archive,included_in_metadata_disk_forecast=True))
        base = [dict(role=r['role'],path=r['path'],quantity=r['quantity'],measured_base_increment=10) for r in self.rows]
        self.legacy = doc(self.batch/'capacity.json',dict(after=self.previous,rows=base))
        self.rows.extend(dict(role='coordinator',path=str(self.root),quantity=q,data_growth=0,
                              observer_raw=20,archive_peak=30) for q in ('bytes','inodes'))
        self.budget = dict(schema='c4-resume-stage-capacity-v1',passed=True,reviewed=True,
            original_request=self.request,legacy_capacity=self.legacy,runtime=self.old['runtime_control'],
            pool='pool-a',cell_id=self.cell['id'],step=2,prefix=self.prefix,archive_mode='closed-observer-direct',
            reserve_fraction=.2,at=self.clock.isoformat(),coordinator=dict(path=str(self.root),device=self.root.stat().st_dev),
            rows=self.rows)

    def gate(self, mode='closed-observer-direct'):
        snap = doc(self.root/'fresh.json',self.snapshot)
        p = doc(self.root/'placement.json',dict(schema='c4-archive-device-placement-v1',at=self.clock.isoformat(),
            runtime=self.old['runtime_control'],pool='pool-a',workers=self.placements))
        review = doc(self.root/'growth-evidence.json',dict(profile='capacity values under test'))
        cap = doc(self.root/'budget.json',dict(self.budget,snapshot=snap,placement=p,source_pins=[snap,p,review]))
        free = SimpleNamespace(f_blocks=10000,f_frsize=1,f_bfree=9000,f_bavail=9000,
                               f_files=10000,f_ffree=9000,f_favail=9000)
        with patch.object(resume.os,'statvfs',return_value=free):
            return resume.stage_capacity(cap,self.request,self.old,self.prefix,self.cell,2,'on',self.batch,
                self.runtime,self.out,mode,original,self.clock+timedelta(seconds=1))

    def test_direct_and_original_charge_different_peak(self):
        self.assertTrue(self.gate()['passed'])
        self.budget['archive_mode']='original'
        with self.assertRaisesRegex(ValueError,'duplicates observer'):
            self.gate('original')
        for row in self.rows:
            row['archive_peak']=max(row['observer_raw'],row['archive_peak'])
        self.assertTrue(self.gate('original')['passed'])

    def test_rejects_oversubscription_or_wrong_observer_device(self):
        self.placements['objects']['targets']['/srv/ax-objects-0/o']=13
        with self.assertRaisesRegex(ValueError,'target placement'):
            self.gate()
        self.placements['objects']['targets']['/srv/ax-objects-0/o']=10
        row = next(r for r in self.rows if r['role']=='objects' and r['archive_peak'] and r['quantity']=='bytes')
        row['observer_raw']=8000
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