import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

class SmokeModeContract(unittest.TestCase):
    def test_read_control_modes_reach_distinct_arms(self):
        from unittest.mock import patch
        from bench.experiment_smoke import main
        for mode,arm in [('brewfs-full','overlay-read'),('overlay-off','lower-read')]:
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as tmp:
                out=Path(tmp)/'out'
                argv=['experiment_smoke','--experiment','C2-runtime','--mode',mode,
                      '--brewfs','/bin/brewfs','--producer','/bin/producer',
                      '--smallfiles','/bin/smallfiles','--juicefs','/bin/juicefs',
                      '--brewfs-revision','test','--rustfs-image','image','--rustfs-revision','revision',
                      '--brewfs-provenance',str(Path(tmp)/'summary.json'),'--out',str(out)]
                def fake_run(cmd):
                    self.assertEqual(cmd[cmd.index('--arm')+1],arm)
                    self.assertEqual(cmd[cmd.index('--rustfs-revision')+1],'revision')
                    self.assertTrue(cmd[1].endswith('qualify-packed-read-local.py'))
                    folder=Path(cmd[cmd.index('--out')+1]); folder.mkdir()
                    (folder/'receipt.json').write_text('{"passed":true}')
                    return subprocess.CompletedProcess(cmd,0)
                with patch.object(sys,'argv',argv), \
                     patch('bench.runtime.packed_read_candidate.verify',return_value={'brewfs':'a'*64,'producer':'b'*64}), \
                     patch('bench.experiment_smoke.subprocess.run',side_effect=fake_run):
                    self.assertEqual(main(),0)
                self.assertIn('read-only',json.loads((out/'receipt.json').read_text())['runtime_scope'])

    def test_c4_candidate_receipt_reaches_native_runner(self):
        from unittest.mock import patch
        from bench.experiment_smoke import main
        with tempfile.TemporaryDirectory() as tmp:
            out=Path(tmp)/'out'
            provenance=Path(tmp)/'build.json'
            argv=['experiment_smoke','--experiment','C4-fork','--mode','brewfs-full',
                  '--brewfs','/bin/brewfs','--producer','/bin/producer',
                  '--smallfiles','/bin/smallfiles','--juicefs','/bin/juicefs',
                  '--brewfs-revision','test','--rustfs-image','image',
                  '--brewfs-provenance',str(provenance),'--out',str(out)]
            def fake_run(cmd):
                self.assertEqual(cmd[cmd.index('--brewfs-provenance')+1],str(provenance.resolve()))
                self.assertTrue(cmd[1].endswith('rbm-native-fork.py'))
                folder=Path(cmd[cmd.index('--out')+1])
                folder.mkdir()
                (folder/'receipt.json').write_text('{"passed": true}')
                return subprocess.CompletedProcess(cmd,0)
            with patch.object(sys,'argv',argv), patch('bench.experiment_smoke.subprocess.run',side_effect=fake_run):
                self.assertEqual(main(),0)

    def test_unimplemented_modes_write_blocked_receipt_without_mounting(self):
        for experiment, mode in [('C2-runtime','overlay-off'),('C3-smallfiles','packed-off'),
                                  ('C4-handoff','brewfs-full')]:
            with self.subTest(experiment=experiment, mode=mode), tempfile.TemporaryDirectory() as tmp:
                out=Path(tmp)/'out'
                argv=[sys.executable,'-m','bench.experiment_smoke','--experiment',experiment,
                      '--mode',mode,'--brewfs','/nonexistent','--producer','/nonexistent',
                      '--smallfiles','/nonexistent','--juicefs','/nonexistent',
                      '--brewfs-revision','test','--rustfs-image','missing','--out',str(out)]
                if experiment == 'C4-handoff':
                    argv+=['--placement','move']
                result=subprocess.run(argv,capture_output=True,text=True)
                self.assertEqual(result.returncode,2,result.stderr)
                receipt=json.loads((out/'receipt.json').read_text())
                self.assertEqual(receipt['status'],'blocked')
                self.assertNotIn('argv',receipt)
                self.assertFalse((out/'run').exists())

    def test_incompatible_c4_mode_fails_before_creating_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            out=Path(tmp)/'out'
            result=subprocess.run([sys.executable,'-m','bench.experiment_smoke',
                '--experiment','C4-fork','--mode','brewfs-basic',
                '--brewfs','/missing','--producer','/missing','--smallfiles','/missing',
                '--juicefs','/missing','--brewfs-revision','test','--rustfs-image','missing',
                '--out',str(out)],capture_output=True,text=True)
            self.assertEqual(result.returncode,2)
            self.assertFalse(out.exists())



    def test_dynamic_off_reaches_both_runtime_controls(self):
        from unittest.mock import patch
        from bench.experiment_smoke import main
        for experiment in ('C1-layout','C2-replay'):
            with self.subTest(experiment=experiment), tempfile.TemporaryDirectory() as tmp:
                out=Path(tmp)/'out'
                argv=['experiment_smoke','--experiment',experiment,'--mode','dynamic-off',
                      '--brewfs','/bin/brewfs','--producer','/bin/producer',
                      '--smallfiles','/bin/smallfiles','--juicefs','/bin/juicefs',
                      '--brewfs-revision','test','--rustfs-image','image','--out',str(out)]
                if experiment == 'C2-replay':
                    argv+=['--trace','/input/trace','--replay','/bin/replay']
                def fake_run(cmd):
                    self.assertEqual(cmd[cmd.index('--hint')+1],'off')
                    self.assertEqual(cmd[cmd.index('--layout')+1],'disabled')
                    self.assertEqual(cmd[cmd.index('--engine')+1],
                                     'flat-v1' if experiment == 'C1-layout' else 'workspace-v1')
                    folder=Path(cmd[cmd.index('--out')+1])
                    folder.mkdir()
                    (folder/'receipt.json').write_text('{"passed": true}')
                    return subprocess.CompletedProcess(cmd,0)
                with patch.object(sys,'argv',argv), patch('bench.experiment_smoke.subprocess.run',side_effect=fake_run):
                    self.assertEqual(main(),0)


    def test_control_streams_are_not_read_or_traversed(self):
        import os
        if not hasattr(os,'mkfifo'):
            self.skipTest('Linux FIFO regression')
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            os.mkfifo(root/'.accesslog')
            (root/'.trash').mkdir()
            os.mkfifo(root/'.trash'/'stream')
            (root/'.hidden').write_bytes(b'user data')
            (root/'sub').mkdir()
            (root/'sub'/'.accesslog').write_bytes(b'user data')
            code = "from pathlib import Path; from bench.runtime.smoke_tree import file_tree; import sys,json; print(json.dumps(file_tree(Path(sys.argv[1]))))"
            proc=subprocess.run([sys.executable,'-c',code,str(root)],
                                capture_output=True,text=True,timeout=3,check=True)
            tree=json.loads(proc.stdout)
            self.assertEqual(set(tree),{'.hidden','sub','sub/.accesslog'})
