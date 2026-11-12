from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import runpy
import socket
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch


SCRIPT = (Path(__file__).resolve().parents[1]/
          'experiments/unified-suite/pilot-aliyun/scripts/apply-b-c2-observer-cleanup.py')
TOOL = runpy.run_path(str(SCRIPT))['execute'].__globals__


class ReviewBoundary(unittest.TestCase):
    def setUp(self):
        def pin(name):
            return dict(path='/approved/'+name+'.json',sha256=hashlib.sha256(name.encode()).hexdigest())

        draft = dict(schema='b-c2-observer-cleanup-draft-v1',reviewed=False,automatic_retry=False,
                     minimum_free_bytes=TOOL['MIN_FREE'],targets=['o/example'],**TOOL['IDENTITY'])
        self.pins = {}
        for name in ('draft','reviewed_request','approval','source_checker','archive_proof','readback','executor'):
            self.pins[name] = pin(name)['sha256']
            if name in ('source_checker','archive_proof','readback'):
                draft[name] = pin(name)
        self.draft = draft
        self.reviewed = dict(draft,reviewed=True)
        self.approval = dict(schema='b-c2-observer-cleanup-approval-v1',approved=True,action=TOOL['ACTION'],
                             reviewer='test operator',user_message_id='user-approved-test',
                             draft=pin('draft'),reviewed_request=pin('reviewed_request'),executor=pin('executor'))
        self.guard = dict(schema='b-c2-observer-cleanup-guard-v1',passed=True,locks_held=True,active_leases=0,
                          at=datetime.now(timezone.utc).isoformat(),reviewed_request=pin('reviewed_request'),
                          approval=pin('approval'),**TOOL['IDENTITY'])

    def validate(self):
        return TOOL['validate_request'](self.draft,self.reviewed,self.approval,self.guard,self.pins,self.pins['executor'])

    def test_exact_request_and_independent_approval_are_both_required(self):
        self.validate()
        self.reviewed['targets'] = ['o/another']
        with self.assertRaisesRegex(ValueError,'only the exact cleanup draft'):
            self.validate()
        self.reviewed['targets'] = self.draft['targets']
        self.approval['approved'] = False
        with self.assertRaisesRegex(ValueError,'Independent operator approval'):
            self.validate()
        self.approval['approved'] = True
        self.guard['active_leases'] = 1
        with self.assertRaisesRegex(ValueError,'zero active leases'):
            self.validate()

    def test_dispatch_guard_must_be_fresh_and_not_future(self):
        TOOL['current_guard'](self.guard)
        for offset in (-901,10):
            self.guard['at'] = (datetime.now(timezone.utc)+timedelta(seconds=offset)).isoformat()
            with self.assertRaisesRegex(ValueError,'900-second'):
                TOOL['current_guard'](self.guard)


@unittest.skipUnless(sys.platform.startswith('linux'),'fd-relative worker execution requires Linux')
class ExactRemoval(unittest.TestCase):
    def setUp(self):
        tmp = TemporaryDirectory(dir='/tmp')
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.paths = {'o/first':'first','o/second':'second'}
        self.maps = {}
        self.payload = b'original metric sample\n'
        for path, tag in self.paths.items():
            base = self.root/path
            (base/'sample-000000').mkdir(parents=True)
            files = {
                'ready.json':json.dumps(dict(passed=True,spec=dict(sha256='spec'),process=dict(boot_id='old'))).encode(),
                'finished.json':json.dumps(dict(passed=True,spec_sha256='spec')).encode(),
                'sample-000000/raw.txt':self.payload,
            }
            for name, raw in files.items():
                (base/name).write_bytes(raw)
            self.maps[tag] = {name:dict(bytes=len(raw),sha256=hashlib.sha256(raw).hexdigest()) for name,raw in files.items()}
        with socket.socket(socket.AF_UNIX) as conn:
            conn.bind(str(self.root/'o/first/observer.sock'))
        self.outside = self.root/'o/keep.txt'
        self.outside.write_text('unchanged')
        self.device = self.root.stat().st_dev
        with TOOL['directory'](self.root) as fd:
            self.groups = [TOOL['scan'](fd,path,self.maps[tag],self.device,set())[0] for path,tag in self.paths.items()]
        self.emitted = {}

    def execute(self):
        with TOOL['directory'](self.root) as fd, patch.dict(TOOL,MIN_FREE=1):
            return TOOL['execute'](fd,self.device,self.maps,self.paths,self.groups,lambda:None,
                                   lambda name,value:self.emitted.setdefault(name,value))

    def test_failure_on_last_tree_removes_nothing(self):
        (self.root/'o/second/sample-000000/raw.txt').write_bytes(b'x'*len(self.payload))
        with self.assertRaisesRegex(ValueError,'Archived file bytes differ'):
            self.execute()
        self.assertEqual((self.root/'o/first/sample-000000/raw.txt').read_bytes(),self.payload)
        self.assertFalse(self.emitted)

    def test_removes_only_manifest_files_and_keeps_unarchived_socket(self):
        result = self.execute()
        self.assertTrue(result['passed'])
        self.assertEqual([p.name for p in (self.root/'o/first').iterdir()],['observer.sock'])
        self.assertFalse((self.root/'o/second').exists())
        self.assertEqual(self.outside.read_text(),'unchanged')
        self.assertEqual(list(self.emitted),['all-verified.json','01-intent.json','01-finished.json',
                                             '02-intent.json','02-finished.json'])

    def test_rejects_an_unarchived_symlink_before_any_removal(self):
        (self.root/'o/second/unexpected').symlink_to(self.outside)
        with self.assertRaisesRegex(ValueError,'Unarchived'):
            self.execute()
        self.assertTrue((self.root/'o/first/ready.json').exists())
        self.assertEqual(self.outside.read_text(),'unchanged')


if __name__=='__main__':
    unittest.main()
