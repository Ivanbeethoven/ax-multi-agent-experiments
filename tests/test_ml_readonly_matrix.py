import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'harness'))
from ml_readonly_matrix import mount_config, environment, run


class PairedControls(unittest.TestCase):
    def test_only_metadata_placement_differs(self):
        spec={'endpoint':'http://127.0.0.1:9000','tikv_pd':'127.0.0.1:2379'}
        packed=mount_config(spec,'packed',Path('/owned/run'),'same-key','same-bucket','ns')
        native=mount_config(spec,'native-tikv',Path('/owned/run'),'same-key','same-bucket','ns')
        self.assertEqual(native.pop('meta')['backend'],'tikv');self.assertEqual(native,packed)
        self.assertEqual(packed['cache']['read_memory_bytes'],0)
        with self.assertRaises(ValueError):mount_config(spec,'juicefs',Path('/tmp'),'key','bucket','ns')

    def test_inherited_ttl_and_import_flags_cannot_leak(self):
        with patch.dict(os.environ,{'BREWFS_CACHE_TTL_MS':'0','BREWFS_PACKED_NATIVE_IMPORT':'1'}):
            env=environment({'metadata_cache_bytes':67108864},'native-tikv')
        self.assertNotIn('BREWFS_CACHE_TTL_MS',env)
        self.assertEqual(env['BREWFS_PACKED_NATIVE_IMPORT'],'0')
        self.assertEqual(env['BREWFS_PACKED_METADATA_ARM'],'native-tikv')
        self.assertEqual(env['BREWFS_PACKED_V3_DECODE_WORKSPACE_BUDGET_BYTES'],'8388608')
        explicit=environment({'metadata_cache_bytes':67108864,'decode_workspace_bytes':33554432},'packed')
        self.assertEqual(explicit['BREWFS_PACKED_V3_DECODE_WORKSPACE_BUDGET_BYTES'],'33554432')

    def test_qualification_cannot_be_relabelled_formal_perf(self):
        with self.assertRaisesRegex(ValueError,'formal performance'):
            run({'scope':'cloud-performance'},Path('/unused'))
