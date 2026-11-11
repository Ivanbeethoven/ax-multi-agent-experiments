"""Version lookup failures must leave a receipt and block a new batch."""
import hashlib
import json
from pathlib import Path
import runpy
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

SCRIPT = Path(__file__).resolve().parents[1]/'experiments/unified-suite/pilot-aliyun/scripts/verify-latest.py'


class LatestReceipt(unittest.TestCase):
    def test_timeout_keeps_both_component_results_and_rejects_batch(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary); manifest=root/'manifest.json'; out=root/'check.json'
            manifest.write_text(json.dumps(dict(sources=[
                dict(component='brewfs',repository='https://github.com/brewfs/brewfs.git',branch='main',source_commit='a'*40),
                dict(component='rustfs',repository='https://github.com/Ivanbeethoven/rustfs.git',
                    branch='feat/dynamic-block-size-rebased',source_commit='b'*40)])))
            answers=[subprocess.TimeoutExpired('git',45),subprocess.CompletedProcess([],0,'b'*40+'\trefs/heads/test\n','')]
            with patch.object(sys,'argv',[str(SCRIPT),'--manifest',str(manifest),'--out',str(out),'--git','/custom/git']), \
                    patch('subprocess.run',side_effect=answers) as lookup, patch('builtins.print'):
                with self.assertRaises(ValueError):runpy.run_path(str(SCRIPT),run_name='__main__')
            receipt=json.loads(out.read_text())
            self.assertFalse(receipt['passed'])
            self.assertEqual(receipt['build_manifest_sha256'],hashlib.sha256(manifest.read_bytes()).hexdigest())
            self.assertEqual([x['lookup_exit_code'] for x in receipt['sources']],[124,0])
            self.assertEqual([x['matches'] for x in receipt['sources']],[False,True])
            self.assertIsNone(receipt['sources'][0]['remote_commit'])
            self.assertEqual(lookup.call_count,2)
            self.assertTrue(all(x.args[0][0]=='/custom/git' for x in lookup.call_args_list))


if __name__=='__main__':unittest.main()
