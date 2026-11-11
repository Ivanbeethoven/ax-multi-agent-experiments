"""C4 expected results agree with the independently executed filesystem challenge."""
import copy
import importlib.util
from pathlib import Path
import sys
import tempfile
import unittest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO/'bench'))
sys.path.insert(0, str(REPO/'scripts'))
import fs_step
from full_trace_common import tree

FILE = REPO/'experiments/unified-suite/pilot-aliyun/scripts/fork-oracle.py'
spec = importlib.util.spec_from_file_location('fork_oracle', FILE)
module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)


class ForkOracle(unittest.TestCase):
    def test_expected_full_view_matches_real_mutations_without_changing_source(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)/'fixture'; proof = fs_step.fixture(root, 16, 4096)
            base = tree(root); unchanged = copy.deepcopy(base)
            expected = module.derive(base, proof, 'pilot-child-01', required_files=16)
            self.assertEqual(base, unchanged)
            self.assertEqual(tree(root), unchanged)
            fs_step.mutate(root, 'pilot-child-01')
            self.assertEqual(tree(root), expected)

    def test_other_child_tag_cannot_pass_the_expected_view(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)/'fixture'; proof = fs_step.fixture(root, 16, 4096)
            expected = module.derive(tree(root), proof, 'child-one', required_files=16)
            fs_step.mutate(root, 'child-two')
            self.assertNotEqual(tree(root), expected)

    def test_incomplete_or_changed_original_input_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)/'fixture'; proof = fs_step.fixture(root, 16, 4096); base = tree(root)
            with self.assertRaisesRegex(ValueError, 'Full original'):
                module.derive({k:v for k,v in base.items() if k!='d000000/f00000015'}, proof, 'child', 16)
            bad = copy.deepcopy(base); bad['d000000/f00000000']['sha256']='0'*64
            with self.assertRaisesRegex(ValueError, 'fixture formula'):
                module.derive(bad, proof, 'child', 16)
            with self.assertRaises(ValueError):module.derive(base, proof, 'child')

    @unittest.skipUnless(sys.platform != 'win32', 'POSIX umask semantics')
    def test_fixture_and_mutate_modes_are_canonical_across_umasks(self):
        import os
        for mask in (0o002, 0o022, 0o077):
            with self.subTest(umask=oct(mask)), tempfile.TemporaryDirectory() as temporary:
                previous = os.umask(mask)
                try:
                    root = Path(temporary)/'fixture'
                    proof = fs_step.fixture(root, 16, 4096)
                    base = tree(root)
                    expected = module.derive(base, proof, 'umask-child', required_files=16)
                    fs_step.mutate(root, 'umask-child')
                    self.assertEqual(tree(root), expected)
                finally:
                    os.umask(previous)


if __name__ == '__main__':
    unittest.main()
