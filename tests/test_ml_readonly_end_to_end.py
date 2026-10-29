import hashlib
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'harness'))
from ml_readonly_end_to_end import native_config, native_environment, import_files


class WholePathQualification(unittest.TestCase):
    def test_native_uses_real_flat_tikv_path(self):
        spec={'endpoint':'http://127.0.0.1:9000','tikv_pd':'127.0.0.1:2379','metadata_cache_bytes':67108864}
        config=native_config(spec,Path('/owned/run'),'owned-bucket','unique-ns')
        self.assertEqual(config['volume_format'],'flat-v1')
        self.assertNotIn('packed_manifest_key',config)
        self.assertEqual(config['meta']['backend'],'tikv')
        self.assertFalse(any(k.startswith('BREWFS_PACKED_') for k in native_environment(spec)))

    def test_import_rejects_wrong_bytes_and_never_overwrites(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);source=root/'source';source.mkdir();(source/'a').write_bytes(b'abc')
            target=root/'target';target.mkdir();manifest=root/'manifest.tsv'
            expected=hashlib.sha256(b'abc').hexdigest()
            manifest.write_text(f'{expected}\t3\ta\n')
            report=import_files(source,manifest,target,1,3)
            self.assertTrue(report['file_fsync']);self.assertEqual((target/'a').read_bytes(),b'abc')
            with self.assertRaises(FileExistsError):import_files(source,manifest,target,1,3)
            other=root/'other';other.mkdir();(source/'a').write_bytes(b'bad')
            with self.assertRaisesRegex(ValueError,'source changed'):import_files(source,manifest,other,1,3)

    def test_import_rejects_traversal(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);manifest=root/'manifest.tsv'
            source=root/'source';source.mkdir();target=root/'target';target.mkdir()
            manifest.write_text('0'*64+'\t3\t../outside\n')
            with self.assertRaisesRegex(ValueError,'unsafe manifest'):
                import_files(source,manifest,target,1,3)
