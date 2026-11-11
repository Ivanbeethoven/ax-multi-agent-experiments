"""An evidence copy is reclaimed only after its whole byte manifest matches."""
import hashlib
import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from bench.archive_check import pack

FILE = (Path(__file__).resolve().parents[1]/
        'experiments/unified-suite/pilot-aliyun/scripts/reclaim-object-evidence-staging.py')
spec = importlib.util.spec_from_file_location('staging_reclaim', FILE)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class ObjectStagingReclaim(unittest.TestCase):
    def test_bad_second_copy_aborts_before_removing_verified_first(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            copies = []
            for tag in ('a', 'b'):
                stage = root/'evidence'/tag
                stage.mkdir(parents=True)
                (stage/'sample.json').write_text('original')
                archive = root/(tag+'.tar.gz')
                pack(stage, ['sample.json'], archive, tag, 'objects')
                copies.append(dict(stage='evidence/'+tag, worker_archive=dict(path=str(archive),
                                   sha256=hashlib.sha256(archive.read_bytes()).hexdigest())))
            s = dict(boot_id=Path('/proc/sys/kernel/random/boot_id').read_text().strip(),
                     device=root.stat().st_dev, copies=copies, apply=False, minimum_after_bytes=0)
            with patch.object(module, 'ROOT', root):
                self.assertTrue(module.node(s)['passed'])
                self.assertTrue((root/'evidence/a/sample.json').exists())
                s['apply'] = True
                (root/'evidence/b/sample.json').write_text('modified')
                with self.assertRaisesRegex(ValueError, 'do not match archive'):
                    module.node(s)
                self.assertTrue((root/'evidence/a/sample.json').exists())
                (root/'evidence/b/sample.json').write_text('original')
                self.assertTrue(module.node(s)['applied'])
                self.assertFalse((root/'evidence/a').exists())
                self.assertTrue((root/'a.tar.gz').exists())
