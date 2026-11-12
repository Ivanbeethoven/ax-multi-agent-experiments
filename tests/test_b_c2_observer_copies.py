import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest


PATH = (Path(__file__).resolve().parents[1] /
        'experiments/unified-suite/pilot-aliyun/scripts/check-b-c2-observer-copies.py')
SPEC = importlib.util.spec_from_file_location('b_c2_observer_copies', PATH)
CHECK = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CHECK)


class ObserverCopiesTest(unittest.TestCase):
    def test_matches_bytes_and_rejects_changes_foreign_members_and_links(self):
        content = {
            'ready.json': json.dumps(dict(passed=True, spec={'sha256': 'same'},
                                          process={'boot_id': 'historical'})).encode(),
            'finished.json': b'{"passed":true,"spec_sha256":"same"}',
            'sample-000000/sample.json': b'first payload',
        }
        expected = {name: dict(bytes=len(data), sha256=hashlib.sha256(data).hexdigest())
                    for name, data in content.items()}
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            rel = CHECK.candidate_paths(CHECK.TAGS[0])[0]
            tree = root/rel
            for name, data in content.items():
                f = tree/name
                f.parent.mkdir(parents=True, exist_ok=True)
                f.write_bytes(data)
            def inspect():
                return CHECK.check_copy(root, rel, expected, root.stat().st_dev, set())

            self.assertEqual(inspect()['files'], 3)
            sample = tree/'sample-000000/sample.json'
            sample.write_bytes(b'third payload')
            with self.assertRaisesRegex(ValueError, 'Archive bytes differ'):
                inspect()
            sample.write_bytes(content['sample-000000/sample.json'])
            link = tree/'foreign'
            link.hardlink_to(sample)
            with self.assertRaises(ValueError):
                inspect()
            link.unlink()
            (tree/'not-in-archive').mkdir()
            with self.assertRaisesRegex(ValueError, 'foreign directory'):
                inspect()

    def test_archive_relative_path_cannot_escape(self):
        for path in ('../a', '/absolute', 'a//b', 'a\\b', '.'):
            with self.subTest(path=path), self.assertRaises(ValueError):
                CHECK.safe_relative(path)
