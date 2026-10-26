import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import download_archives
spec = importlib.util.spec_from_file_location('check_archives', ROOT / 'scripts/check_archives.py')
checker = importlib.util.module_from_spec(spec)
spec.loader.exec_module(checker)


class ArchiveTests(unittest.TestCase):
    def download_fixture(self, data, corrupt=False):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'scripts').mkdir()
            manifest = {'endpoint': 'https://oss-cn-hangzhou.aliyuncs.com', 'archives': [
                {'case': 'fixture', 'file': 'fixture.tar.gz', 'uri': 'oss://private/fixture.tar.gz',
                 'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest()}]}
            (root / 'archives.json').write_text(json.dumps(manifest))
            def transfer(command, **kwargs):
                Path(command[4]).write_bytes(b'x' * len(data) if corrupt else data)
            with patch.object(download_archives, '__file__', str(root / 'scripts/download_archives.py')), \
                    patch.object(sys, 'argv', ['download', '--out', str(root / 'out')]), \
                    patch.object(download_archives.shutil, 'which', return_value='/fake/aliyun'), \
                    patch.object(download_archives.subprocess, 'run', side_effect=transfer) as transport:
                if corrupt:
                    with self.assertRaisesRegex(ValueError, 'SHA256'):
                        download_archives.main()
                    self.assertFalse((root / 'out/fixture.tar.gz').exists())
                    self.assertTrue((root / 'out/fixture.tar.gz.partial').exists())
                else:
                    download_archives.main()
                    self.assertEqual((root / 'out/fixture.tar.gz').read_bytes(), data)
                    self.assertFalse((root / 'out/fixture.tar.gz.partial').exists())
                    download_archives.main()
                    self.assertEqual(transport.call_count, 1)

    def test_download_verifies_before_rename_and_skips_existing(self):
        self.download_fixture(b'valid archive content')

    def test_download_rejects_corruption_and_keeps_partial(self):
        self.download_fixture(b'valid archive content', corrupt=True)

    def test_checks_actual_bytes_and_rejects_corruption(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'small.tar.gz'
            path.write_bytes(b'archive fixture')
            entry = {'case': 'fixture', 'bytes': path.stat().st_size,
                     'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}
            self.assertTrue(checker.verify_archive(path, entry)['verified'])
            path.write_bytes(b'corrupt fixture')
            with self.assertRaisesRegex(ValueError, 'SHA256'):
                checker.verify_archive(path, entry)
            path.write_bytes(b'x')
            with self.assertRaisesRegex(ValueError, 'size'):
                checker.verify_archive(path, entry)

    def test_download_listing_has_all_seven_without_network(self):
        command = [sys.executable, str(ROOT / 'scripts/download_archives.py'), '--list']
        result = subprocess.run(command, check=True, capture_output=True, text=True)
        rows = json.loads(result.stdout)
        self.assertEqual(len(rows), 7)
        self.assertEqual(len({row['case'] for row in rows}), 7)
        new = {r['case']: r for r in rows if r['batch'] == 'deepseek-opencode-20261026'}
        self.assertEqual(set(new), {'pytest-20261026', 'ripgrep-20261026', 'archive-io-20261026'})
        self.assertEqual(new['pytest-20261026']['root_dir'], 'pytest')
        self.assertTrue(all('/deepseek-opencode-20261026/' in r['uri'] for r in new.values()))

    def test_metadata_is_separate_from_workloads(self):
        result = subprocess.run([sys.executable, str(ROOT / 'scripts/download_archives.py'),
            '--list', '--cases', 'pytest-20261026', '--include-metadata'],
            check=True, capture_output=True, text=True)
        rows = json.loads(result.stdout)
        self.assertEqual([row['file'] for row in rows], ['pytest.tar.gz', 'metadata.tar.gz'])


if __name__ == '__main__':
    unittest.main()
