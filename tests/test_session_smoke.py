import hashlib
import json
import os
from pathlib import Path
import runpy
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch


class SessionSmoke(unittest.TestCase):
    def test_binary_mismatch_fails_before_export(self):
        from bench.runtime.session_export import export
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); binary=root/'opencode'; binary.write_bytes(b'wrong')
            with self.assertRaisesRegex(ValueError,'binary differs'):
                export(root,{'opencode_binary_sha256':'0'*64},binary,root/'out')
            self.assertFalse((root/'out').exists())

    def test_stay_routes_to_session_reopen_and_move_cannot_fall_back(self):
        from bench.experiment_smoke import main
        for placement in ('stay','move'):
            with self.subTest(placement=placement), tempfile.TemporaryDirectory() as tmp:
                root=Path(tmp); out=root/'out'
                argv=['smoke','--experiment','C4-handoff','--mode','brewfs-full','--placement',placement,
                      '--brewfs','/bin/brewfs','--producer','/bin/producer','--smallfiles','/bin/smallfiles',
                      '--juicefs','/bin/juicefs','--brewfs-revision','test','--rustfs-image','test',
                      '--checkpoint',str(root/'checkpoint.json'),'--opencode',str(root/'opencode'),'--out',str(out)]
                def run(command):
                    self.assertEqual(command[command.index('--workload')+1],'c4-stay')
                    self.assertEqual(command[command.index('--checkpoint')+1],str(root/'checkpoint.json'))
                    folder=Path(command[command.index('--out')+1]); folder.mkdir()
                    (folder/'receipt.json').write_text('{"passed":true}')
                    return subprocess.CompletedProcess(command,0)
                with patch.object(sys,'argv',argv), patch('bench.experiment_smoke.subprocess.run',side_effect=run) as child:
                    self.assertEqual(main(),0 if placement=='stay' else 2)
                    self.assertEqual(child.call_count,1 if placement=='stay' else 0)
                if placement=='move':
                    self.assertEqual(json.loads((out/'receipt.json').read_text())['status'],'blocked')


@unittest.skipUnless(sys.platform=='linux','requires real Linux process groups and /proc')
class NativeExportClosure(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from bench.runtime.node import process_group_members
        from bench.runtime.session_export import SCRIPTS, _run_with_natural_closure
        cls.members=staticmethod(process_group_members)
        cls.run_export=staticmethod(_run_with_natural_closure)
        cls.wait=staticmethod(runpy.run_path(str(SCRIPTS/'c4-state-reopen-node.py'))['wait_export_group'])

    def test_runner_waits_for_background_write_after_parent_exit(self):
        with tempfile.TemporaryDirectory() as tmp:
            out=Path(tmp)
            command=[sys.executable,'-c',"""
import os, time
if os.fork()==0:
    time.sleep(.35)
    with open('workspace-marker','x') as stream:
        stream.write('background write completed')
    os._exit(0)
os._exit(0)
"""]
            proof=self.run_export(command,out,os.environ,out,self.members,self.wait,export_timeout=2)
            self.assertEqual(proof['status'],'natural')
            self.assertTrue(proof['group_members_after_parent_exit'])
            self.assertEqual(proof['group_members_after'],[])
            self.assertLess(proof['parent_exited_monotonic_ns'],proof['closure_observed_monotonic_ns'])
            self.assertEqual((out/'workspace-marker').read_text(),'background write completed')
            self.assertFalse((out/'cleanup.json').exists())

    def test_group_deadline_consumes_parent_time_and_cleanup_cannot_pass(self):
        with tempfile.TemporaryDirectory() as tmp:
            out=Path(tmp)
            command=[sys.executable,'-c',"""
import os, signal, time
r,w=os.pipe()
if os.fork()==0:
    os.close(r)
    def term(sig, frame):
        with open('received-term','x') as stream:
            stream.write('terminated')
        os._exit(0)
    signal.signal(signal.SIGTERM,term)
    os.write(w,b'x')
    time.sleep(5)
    os._exit(0)
os.close(w)
os.read(r,1)
time.sleep(.25)
os._exit(0)
"""]
            unrelated=subprocess.Popen([sys.executable,'-c','import time; time.sleep(5)'],
                start_new_session=True)
            try:
                with self.assertRaisesRegex(RuntimeError,'did not exit naturally'):
                    self.run_export(command,out,os.environ,out,self.members,self.wait,export_timeout=.65)
                proof=json.loads((out/'process-closure.json').read_text())
                cleanup=json.loads((out/'cleanup.json').read_text())
                self.assertEqual(proof['status'],'group-timeout')
                self.assertEqual(proof['exit_code'],0)
                self.assertLess(proof['group_closure_timeout_seconds'],.45)
                self.assertTrue(proof['group_members_after'])
                self.assertEqual(cleanup['group_members_after'],[])
                self.assertEqual([(item['signal'],item['sent']) for item in cleanup['attempts']],
                    [('SIGTERM',True)])
                self.assertEqual((out/'received-term').read_text(),'terminated')
                self.assertIsNone(unrelated.poll())
                self.assertFalse((out/'result.json').exists())
            finally:
                unrelated.terminate()
                unrelated.wait(timeout=2)

    def test_parent_deadline_remains_failed_after_cleanup(self):
        with tempfile.TemporaryDirectory() as tmp:
            out=Path(tmp)
            command=[sys.executable,'-c','import time; time.sleep(5)']
            with self.assertRaisesRegex(RuntimeError,'parent did not exit'):
                self.run_export(command,out,os.environ,out,self.members,self.wait,export_timeout=.2)
            proof=json.loads((out/'process-closure.json').read_text())
            cleanup=json.loads((out/'cleanup.json').read_text())
            self.assertEqual(proof['status'],'parent-timeout')
            self.assertIsNone(proof['parent_exited_monotonic_ns'])
            self.assertEqual(cleanup['group_members_after'],[])
            self.assertLess(cleanup['parent_exit_code'],0)
