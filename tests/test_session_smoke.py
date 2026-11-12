import hashlib
import json
from pathlib import Path
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
