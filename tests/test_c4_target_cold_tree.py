import copy
import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from bench.runtime.common import digest, write_new

path = Path(__file__).resolve().parents[1]/'experiments/unified-suite/pilot-aliyun/scripts/check-c4-target-cold-tree.py'
loader = importlib.util.spec_from_file_location('c4_target_cold_leaf', path)
leaf = importlib.util.module_from_spec(loader)
loader.loader.exec_module(leaf)


class TargetColdTree(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name).resolve()
        self.point = root/'mount'
        self.workspace = self.point/'workspace'
        self.workspace.mkdir(parents=True)
        self.db = self.workspace/'native.db'
        self.db.write_bytes(b'checkpoint')
        self.evidence = root/'mount-evidence'
        self.evidence.mkdir()
        self.output = root/'proof.json'
        anchor = dict(filesystem='juicefs',workspace='c4-fixture')
        mount = self.put(root/'mount.json',dict(schema='experiment-mount-v1',reviewed=True,
                                               anchor=anchor,mount_point=str(self.point)))
        self.snapshot = dict(passed=True,anchor=anchor,mount=dict(mount_id=40,point=str(self.point),filesystem='fuse.juicefs'),
                             process=dict(pid=88,boot_id='abc',start_ticks=123,state='S'))
        self.put(self.evidence/'ready.json',dict(self.snapshot,spec=mount))
        self.request_path = root/'request.json'
        self.manifest_path = root/'manifest.json'
        self.request = dict(schema='c4-target-cold-tree-request-v1',reviewed=True,mount_spec=mount,
                            mount_directory=str(self.evidence),workspace=str(self.workspace))
        self.status = self.enterContext(patch.object(leaf.node,'control',return_value=self.snapshot))
        self.kernel = self.enterContext(patch.object(leaf,'mounts',return_value=[self.snapshot['mount']]))

    def put(self,path,value):
        write_new(path,value)
        return dict(path=str(path),sha256=digest(path))

    def freeze(self):
        tree = leaf.session_input.tree(self.workspace)
        return self.put(self.request_path,dict(self.request,expected_tree=self.put(self.manifest_path,tree),
                                               expected_tree_sha256=leaf.fingerprint(tree)))

    def test_hashes_actual_tree_and_returns_fuse_identity(self):
        ref = self.freeze()
        result = leaf.check(ref,self.output)
        self.assertEqual(result['files_hashed'],1)
        self.assertEqual(result['bytes_hashed'],10)
        self.assertEqual(result['mount'],self.snapshot['mount'])
        self.assertEqual(result['observed_tree_sha256'],leaf.fingerprint(leaf.session_input.tree(self.workspace)))
        self.assertTrue(result['read_only'])
        self.assertFalse(result['native_app_opened'])
        self.assertEqual(self.status.call_count,2)

    def test_changed_file_cannot_produce_receipt(self):
        ref = self.freeze()
        self.db.write_bytes(b'checkp0int')
        with self.assertRaisesRegex(ValueError,'tree differs'):
            leaf.check(ref,self.output)
        self.assertFalse(self.output.exists())

    def test_restart_cannot_produce_receipt(self):
        ref = self.freeze()
        restarted = copy.deepcopy(self.snapshot)
        restarted['process']['start_ticks'] += 1
        self.status.side_effect = [self.snapshot,restarted]
        with self.assertRaisesRegex(ValueError,'process changed'):
            leaf.check(ref,self.output)
        self.assertFalse(self.output.exists())

    def test_open_wal_is_not_a_checkpoint(self):
        self.db.with_name('native.db-wal').write_bytes(b'journal')
        with self.assertRaisesRegex(ValueError,'unclosed SQLite'):
            leaf.check(self.freeze(),self.output)
        self.assertFalse(self.output.exists())


if __name__ == '__main__':
    unittest.main()
