"""Outer checksum success alone must not admit corrupt nested node evidence."""
import importlib.util
from pathlib import Path
import tempfile
import tarfile
import unittest
from bench import archive_check

SCRIPT=Path(__file__).resolve().parents[1]/'experiments/unified-suite/pilot-aliyun/scripts/verify-retained-bundle.py'
spec=importlib.util.spec_from_file_location('verify_retained_bundle',SCRIPT)
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)


class NestedRetention(unittest.TestCase):
    def test_all_inner_members_verified_and_corrupt_inner_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);raw=root/'raw';raw.mkdir();(raw/'sample.json').write_text('{"counter":3}')
            bundle=root/'bundle';bundle.mkdir()
            archive_check.pack(raw,['sample.json'],bundle/'client.tar.gz','one-cell','node-raw')
            outer=archive_check.pack(bundle,['client.tar.gz'],root/'outer.tar.gz','one-cell','retention')
            result=module.verify_bundle(root/'outer.tar.gz',outer['sha256'],root/'checked')
            self.assertTrue(result['passed']);self.assertEqual(result['nested_archives'],1)
            # Repacking records a VALID outer digest around an INVALID inner tar.
            (bundle/'client.tar.gz').write_bytes(b'not a tar archive')
            bad=archive_check.pack(bundle,['client.tar.gz'],root/'bad-outer.tar.gz','one-cell','retention')
            archive_check.verify(root/'bad-outer.tar.gz',bad['sha256'])
            with self.assertRaises(tarfile.ReadError):
                module.verify_bundle(root/'bad-outer.tar.gz',bad['sha256'],root/'bad-check')
            self.assertFalse((root/'bad-check/result.json').exists())


if __name__=='__main__':unittest.main()
