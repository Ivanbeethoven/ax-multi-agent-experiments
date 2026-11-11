"""The storage-only path fences the old grant and rejects networking."""
import os
from pathlib import Path
import runpy
import subprocess
import sys
import tempfile
import unittest

SCRIPTS = Path(__file__).resolve().parents[1]/'experiments/unified-suite/pilot-aliyun/scripts'
GRANT = runpy.run_path(str(SCRIPTS/'run-c4-state-reopen.py'))['require_grant']
NETWORK = runpy.run_path(str(SCRIPTS/'c4-state-reopen-node.py'))['NETWORK_WRAPPER']


class StateReopen(unittest.TestCase):
    def test_source_grant_cannot_admit_mount_after_actual_rotation(self):
        source = dict(side='source', generation=1, attempt='state-reopen-test')
        target = dict(side='target', generation=2, attempt='state-reopen-test')
        GRANT(source, source, 'source')
        GRANT(target, target, 'target')
        for grant, side in ((source,'source'),(source,'target'),(target,'source')):
            with self.subTest(grant=grant,side=side), self.assertRaises(PermissionError):
                GRANT(grant,target,side)

    @unittest.skipUnless(sys.platform.startswith('linux'), 'Linux network namespace required')
    def test_native_export_never_runs_in_parent_network(self):
        with tempfile.TemporaryDirectory() as tmp:
            proof = Path(tmp)/'network.json'
            result = subprocess.run([sys.executable,'-c',NETWORK,os.readlink('/proc/self/ns/net'),
                str(proof),'/usr/bin/true','fake'],capture_output=True,text=True)
            self.assertNotEqual(result.returncode,0)
            self.assertIn('isolated, loopback-only', result.stderr)
            self.assertFalse(proof.exists())


if __name__ == '__main__':
    unittest.main()
