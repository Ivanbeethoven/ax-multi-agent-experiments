import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest

FILE=Path(__file__).resolve().parents[1]/'experiments/unified-suite/pilot-aliyun/scripts/run-preparation-step.py'
spec=importlib.util.spec_from_file_location('pilot_preparation_step',FILE)
step=importlib.util.module_from_spec(spec);spec.loader.exec_module(step)


class PreparationStep(unittest.TestCase):
    def test_raw_outputs_and_real_nonzero_exit_are_retained_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            argv=[sys.executable,'-c','import sys;print("original stdout");print("original stderr",file=sys.stderr);sys.exit(23)']
            result=step.run_step(root,'original',argv)
            self.assertTrue(result['started']);self.assertEqual(result['exit_code'],23)
            self.assertEqual((root/'original/stdout').read_text(),'original stdout\n')
            self.assertEqual((root/'original/stderr').read_text(),'original stderr\n')
            self.assertEqual(json.loads((root/'original/command.json').read_text())['argv'],argv)
            with self.assertRaises(FileExistsError):step.run_step(root,'original',argv)
            self.assertEqual(json.loads((root/'original/exit.json').read_text())['exit_code'],23)

    def test_success_and_failed_launch_are_distinct(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            self.assertEqual(step.run_step(root,'success',[sys.executable,'-c','pass'])['exit_code'],0)
            failed=step.run_step(root,'missing',[str(root/'missing-program')])
            self.assertFalse(failed['started']);self.assertIsNone(failed['exit_code'])
            self.assertFalse((root/'missing/started.json').exists())
            with self.assertRaises(ValueError):step.run_step(root,'../escape',[sys.executable])


if __name__=='__main__':unittest.main()
