import hashlib
from pathlib import Path
import runpy
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
M = runpy.run_path(str(ROOT / 'scripts/check_repository_archive.py'))


class RepositoryHistoryTests(unittest.TestCase):
    def test_recover_deleted_bytes_and_reject_tampering_or_restoration(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            def git(*args):
                return subprocess.check_output(['git', '-C', str(root), *args], stderr=subprocess.PIPE).decode().strip()
            git('init')
            git('config', 'user.name', 'Archive Test')
            git('config', 'user.email', 'archive@example.invalid')
            git('config', 'core.autocrlf', 'false')
            data = b'old command\n'
            (root / 'old.py').write_bytes(data)
            git('add', 'old.py')
            git('-c', 'commit.gpgsign=false', 'commit', '-m', 'fixture')
            manifest = {'source_commit': git('rev-parse', 'HEAD'), 'removed_files': 1,
                        'removed_bytes': len(data), 'files': [{'path': 'old.py', 'bytes': len(data),
                        'sha256': hashlib.sha256(data).hexdigest(), 'disposition': 'removed'}]}
            (root / 'old.py').unlink()
            self.assertEqual(M['verify_retired'](root, manifest)['files_verified'], 1)
            manifest['files'][0]['sha256'] = '0' * 64
            with self.assertRaisesRegex(ValueError, 'mismatch'):
                M['verify_retired'](root, manifest)
            manifest['files'][0]['sha256'] = hashlib.sha256(data).hexdigest()
            (root / 'old.py').write_bytes(data)
            with self.assertRaisesRegex(ValueError, 'returned'):
                M['verify_retired'](root, manifest)

    def test_reject_unsafe_paths(self):
        for path in ('../outside', '/absolute', 'C:/outside', 'x\ny', 'x\\y', '', '.'):
            with self.subTest(path=path), self.assertRaises(ValueError):
                M['safe_path'](path)
