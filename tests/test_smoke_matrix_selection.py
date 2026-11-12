import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch


class MatrixSelection(unittest.TestCase):
    def test_selected_retry_keeps_other_cases_untouched_and_uses_candidate(self):
        from bench.experiment_smoke_matrix import main
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder); overrides=root/'overrides.json'
            overrides.write_text(json.dumps({'C4-fork-brewfs-full':['--brewfs','/candidate']}))
            calls=[]
            def run(command,**_):
                calls.append(command)
                output=Path(command[command.index('--out')+1]);output.mkdir()
                (output/'receipt.json').write_text('{"status":"passed"}')
                return subprocess.CompletedProcess(command,0)
            argv=['matrix','--out',str(root/'out'),'--case-overrides',str(overrides),
                  '--only','C4-fork-brewfs-full','--brewfs','/base']
            with patch.object(sys,'argv',argv),patch('bench.experiment_smoke_matrix.subprocess.run',side_effect=run):
                self.assertEqual(main(),0)
            self.assertEqual(len(calls),1)
            self.assertEqual([calls[0][i+1] for i,x in enumerate(calls[0]) if x=='--brewfs'],['/base','/candidate'])
            self.assertFalse((root/'out/C4-fork-juicefs').exists())
