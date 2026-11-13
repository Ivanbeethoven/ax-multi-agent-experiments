import importlib.util
from pathlib import Path
import unittest

script = Path(__file__).parent.parent / 'hot-lower-20261112/run_local.py'
if not script.is_file():
    script = Path(__file__).with_name('run_local.py')
spec = importlib.util.spec_from_file_location('runner', script)
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)


class TotalOverlayParity(unittest.TestCase):
    def test_direct_mount_has_no_workspace_and_cache_budgets_match(self):
        original = dict(volume_format='workspace', volume={'format': 'workspace'},
                        workspace='123', workspace_namespace='private',
                        cache=dict(cache_root='/temporary', read_memory_bytes=512 << 20),
                        fuse=dict(workers=4, cache_ttl_ms=0), data={'endpoint': 'same-object-store'})
        overlay = runner.total_overlay_config(original, 'production', 'snapshots/sha')
        direct = runner.total_overlay_config(original, 'packed-direct', 'snapshots/sha')
        self.assertEqual(overlay['cache'], direct['cache'])
        self.assertEqual(overlay['fuse'], direct['fuse'])
        self.assertEqual(overlay['data'], direct['data'])
        self.assertEqual(overlay['workspace'], '123')
        self.assertNotIn('workspace', direct)
        self.assertNotIn('workspace_namespace', direct)
        self.assertEqual(direct['volume_format'], 'packed-snapshot')
        self.assertEqual(direct['packed_manifest_key'], 'snapshots/sha')
        self.assertEqual(direct['cache']['read_memory_bytes'], 0)
        self.assertEqual(direct['cache']['read_ssd_bytes'], 0)
        self.assertEqual(direct['cache']['packed_cache'], 'off')
        self.assertFalse(direct['cache']['prefetch_enabled'])
        self.assertFalse(direct['cache']['range_background_prefetch'])
        self.assertEqual(original['cache']['read_memory_bytes'], 512 << 20)


if __name__ == '__main__':
    unittest.main()
