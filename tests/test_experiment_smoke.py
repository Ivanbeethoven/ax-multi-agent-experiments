import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

class SmokeModeContract(unittest.TestCase):
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
