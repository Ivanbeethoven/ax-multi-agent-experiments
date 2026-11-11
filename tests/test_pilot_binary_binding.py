"""A new asset must keep its selected executable, including after old-path changes."""
import copy
from pathlib import Path
import runpy
import tempfile
import unittest
from unittest.mock import patch

REPO=Path(__file__).resolve().parents[1]
MODULE=runpy.run_path(str(REPO/'experiments/unified-suite/pilot-aliyun/scripts/pilot-node.py'))
select=MODULE['filesystem_binary']


class BinaryBinding(unittest.TestCase):
    def test_new_asset_keeps_its_binary_and_rejects_later_replacement(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'brewfs';path.write_bytes(b'new build')
            ref=dict(path=str(path),sha256=MODULE['digest'](path))
            asset=dict(product='brewfs',filesystem_binary=ref);original=copy.deepcopy(asset)
            with patch.dict(select.__globals__,ref=lambda p:(_ for _ in ()).throw(AssertionError('legacy binary accessed'))):
                self.assertEqual(select(asset),ref)
            path.write_bytes(b'different build')
            with self.assertRaises(ValueError):select(asset)
            self.assertEqual(asset,original)

    def test_legacy_asset_is_read_without_rewriting_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'original';path.write_bytes(b'original build')
            ref=dict(path=str(path),sha256=MODULE['digest'](path));asset=dict(product='brewfs')
            with patch.dict(select.__globals__,ref=lambda p:ref):self.assertEqual(select(asset),ref)
            self.assertEqual(asset,dict(product='brewfs'))
