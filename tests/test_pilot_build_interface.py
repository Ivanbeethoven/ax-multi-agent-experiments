"""Live build receipts must describe the current shared experiment artifact."""
from pathlib import Path
import runpy
import unittest

CHECK = runpy.run_path(str(Path(__file__).resolve().parents[1] /
    'experiments/unified-suite/pilot-aliyun/scripts/attest-assets.py'))['unified_brewfs_build']
COMMAND = ['cargo','build','--locked','--release','--bin','brewfs',
           '--bin','packed_snapshot_fixture','--features',
           'workspace,observability,rustfs-ec-block-size-hint']


class UnifiedBuildInterface(unittest.TestCase):
    def test_current_default_feature_build_is_accepted(self):
        self.assertTrue(CHECK(COMMAND))
        self.assertTrue(CHECK(COMMAND[:-2] + ['--features=' + COMMAND[-1]]))

    def test_removed_probe_and_different_feature_sets_are_rejected(self):
        for suffix in (['--no-default-features'], ['--all-features'],
                       ['--example','entity_metadata_probe'], ['--example=entity_metadata_probe']):
            with self.subTest(suffix=suffix):
                self.assertFalse(CHECK(COMMAND + suffix))
        self.assertFalse(CHECK(COMMAND[:-1] + ['workspace,observability']))
        self.assertFalse(CHECK(COMMAND[:-1] + [COMMAND[-1] + ',native-packed-base']))
        self.assertFalse(CHECK(COMMAND[:6] + COMMAND[8:]))


if __name__ == '__main__':
    unittest.main()
