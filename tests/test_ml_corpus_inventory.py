import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'harness'))
from ml_corpus_inventory import qualify


class Inventory(unittest.TestCase):
    def test_exact_inventory_and_duplicate_rejection(self):
        with tempfile.TemporaryDirectory() as d:
            base=Path(d); root=base/'corpus'; root.mkdir(); (root/'a').write_bytes(b'abc')
            row=hashlib.sha256(b'abc').hexdigest()+'\t3\ta\n'
            manifest=base/'files.tsv'; manifest.write_bytes(row.encode())
            self.assertTrue(qualify(root,manifest,base/'ok')['passed'])
            manifest.write_bytes((row+row).encode())
            with self.assertRaisesRegex(ValueError,'duplicate'):
                qualify(root,manifest,base/'duplicate')
            self.assertFalse(json.loads((base/'duplicate/result.json').read_text())['passed'])

    def test_unlisted_missing_and_wrong_size_rejected(self):
        for kind in ('unlisted','missing','wrong-size'):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as d:
                base=Path(d); root=base/'corpus'; root.mkdir()
                (root/'a').write_bytes(b'ab' if kind=='wrong-size' else b'abc')
                if kind=='unlisted': (root/'extra').write_bytes(b'')
                if kind=='missing': (root/'a').unlink()
                manifest=base/'files.tsv';manifest.write_bytes((hashlib.sha256(b'abc').hexdigest()+'\t3\ta\n').encode())
                with self.assertRaises(ValueError):qualify(root,manifest,base/'report')
