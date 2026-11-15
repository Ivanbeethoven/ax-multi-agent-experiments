"""Harness self-checks on a temporary ordinary filesystem, not benchmark data."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

spec = importlib.util.spec_from_file_location('fix_workload', Path(__file__).with_name('run_correctness.py'))
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class Workloads(unittest.TestCase):
    def test_replace_preserves_all_generation_checks(self):
        with tempfile.TemporaryDirectory() as directory:
            root, expected = Path(directory), {}
            module.replacement(root, expected, root / 'receipt.json', phases=4)
            receipt = json.loads((root / 'receipt.json').read_text())
            self.assertTrue(receipt['passed'])
            self.assertEqual(receipt['completed_writes'], [1, 2, 3, 4])
            self.assertEqual(len(receipt['reads']), 64)
            self.assertEqual((root / '.ax-replace').read_bytes(), expected['.ax-replace'])

    def test_mixed_reads_and_remount_expectation(self):
        with tempfile.TemporaryDirectory() as directory:
            root, expected = Path(directory), {}
            module.mixed(root, expected, root / 'receipt.json', seconds=0, blocks=16)
            receipt = json.loads((root / 'receipt.json').read_text())
            self.assertTrue(receipt['passed'])
            self.assertEqual(receipt['reads'] + receipt['writes'], 256)
            self.assertGreater(receipt['reads'], 0)
            self.assertGreater(receipt['writes'], 0)
            self.assertEqual((root / '.ax-mixed').read_bytes(), expected['.ax-mixed'])


if __name__ == '__main__':
    unittest.main()
