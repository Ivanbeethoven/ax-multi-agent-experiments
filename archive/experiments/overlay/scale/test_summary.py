import unittest
import json
import os
from pathlib import Path
import tempfile
from summarize import overhead


class PercentageTests(unittest.TestCase):
    def test_monitor_distinguishes_client_and_lua_commands(self):
        from redis_audit import parse
        self.assertEqual(parse(b'+1794600000.1 [0 127.0.0.1:123] "MGET" "key"\r\n')['source'], 'client')
        self.assertEqual(parse(b'+1794600000.2 [0 lua] "get" "key"\r\n')['source'], 'lua')
        with self.assertRaises(AssertionError):
            parse(b'+unknown line\r\n')

    @unittest.skipUnless(os.name == 'posix', 'Linux rename-over-existing semantics')
    def test_concurrent_fixture_keeps_complete_final_generation(self):
        from run_mutations import concurrent_replace
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            expected = {}
            receipt = root / 'proof.json'
            concurrent_replace(root, expected, receipt)
            result = json.loads(receipt.read_text())
            self.assertTrue(result['passed'])
            self.assertEqual(len(result['reads']), 96)
            self.assertEqual(result['completed_writes'], list(range(1, 13)))
            self.assertEqual(expected['.ax-concurrent.bin'], bytes([12]) * 4096)
            self.assertEqual((root / '.ax-concurrent.bin').read_bytes(), expected['.ax-concurrent.bin'])

    def test_positive_is_slower_and_uses_displayed_times(self):
        self.assertAlmostEqual(overhead(120, 100), 20)
        self.assertAlmostEqual(overhead(90, 100), -10)
        # Ratio of displayed summary times is deliberately not a median of ratios.
        self.assertAlmostEqual(overhead(102, 100), 2)
        with self.assertRaises(AssertionError):
            overhead(1, 0)

    @unittest.skipUnless(os.name == 'posix', 'Linux permissions')
    def test_concurrent_chmod_retains_data_and_final_mode(self):
        from run_mutations import concurrent_chmod
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            expected = {}
            receipt = root / 'proof.json'
            concurrent_chmod(root, expected, receipt)
            result = json.loads(receipt.read_text())
            self.assertTrue(result['passed'])
            self.assertEqual(len(result['reads']), 96)
            self.assertEqual(result['completed_chmods'], list(range(1, 13)))
            self.assertEqual((root / '.ax-concurrent.bin').stat().st_mode & 0o777, 0o600)


if __name__ == '__main__':
    unittest.main()
