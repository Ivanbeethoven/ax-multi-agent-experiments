"""Checkpoint bytes, symlink boundaries and empty HOME for C4 preparation."""
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
FILE = ROOT/'experiments/unified-suite/pilot-aliyun/scripts/prepare-session-input.py'
spec = importlib.util.spec_from_file_location('pilot_session_input', FILE)
module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)


@unittest.skipUnless(sys.platform == 'linux', 'Linux filesystem metadata and fsync')
class SessionInput(unittest.TestCase):
    def test_real_tree_roundtrip_and_fsync_preserve_binary_payloads_and_internal_links(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)/'workspace'; root.mkdir()
            (root/'data').mkdir(); (root/'data/db').write_bytes(bytes(range(256))*1024)
            (root/'link').symlink_to('data/db')
            before = module.tree(root)
            receipt = module.sync_tree(root)
            self.assertEqual(receipt['files'], 1)
            self.assertEqual(receipt['file_bytes'], 262144)
            self.assertEqual(module.tree(root), before)
            (root/'data/db').write_bytes(b'corrupt')
            self.assertNotEqual(module.tree(root), before)

    def test_external_runtime_symlink_is_rejected_before_any_copy(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)/'workspace'; root.mkdir()
            outside = Path(tmp)/'private'; outside.write_text('must not travel')
            (root/'escape').symlink_to(outside)
            with self.assertRaisesRegex(ValueError, 'leaves workspace'):
                module.tree(root)

    def test_hot_checkpoint_is_rejected_before_mount_or_workspace_access(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp)/'checkpoint.json'
            p.write_text(json.dumps(dict(source_process_exited=False)))
            request = dict(checkpoint_directory_receipt=dict(path=str(p), sha256=module.digest(p)))
            with patch.object(module.node, 'control') as control:
                with self.assertRaisesRegex(ValueError, 'Closed, verified'):
                    module.run(request, Path(tmp)/'output')
                control.assert_not_called()


if __name__ == '__main__':
    unittest.main()
