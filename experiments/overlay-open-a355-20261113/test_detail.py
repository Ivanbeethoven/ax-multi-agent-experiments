import os
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from run_detail import measured_round, assert_zero_ttl, directory_page_probe
from summarize_detail import summarize


class SummaryTests(unittest.TestCase):
    def test_ttl_guard_uses_absolute_values_and_requires_both_gauges(self):
        assert_zero_ttl('brewfs_fuse_attr_valid_ns 0\nbrewfs_fuse_entry_valid_ns 0\n')
        for raw in ('brewfs_fuse_attr_valid_ns 1000000000\nbrewfs_fuse_entry_valid_ns 1000000000\n',
                    'brewfs_fuse_attr_valid_ns 0\n', ''):
            with self.assertRaises(AssertionError):
                assert_zero_ttl(raw)

    def test_additive_means_keep_between_rounds_and_raw_runs(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for pair, extra in ((1, 100), (2, 500)):
                cell = root / str(pair)
                cell.mkdir()
                sample = dict(round_elapsed_ns=300, breakdown_ns={'file_open': 200, 'caller_other': 100})
                data = dict(scenario='two-files', arm='production', cache_temperature='warm',
                            pair=pair, label=str(pair), steady_rounds=2,
                            steady_elapsed_ns=600 + extra, samples=[sample, sample, sample])
                (cell / 'result.json').write_text(json.dumps(data))
            group = summarize(root)['groups'][0]
            self.assertEqual(group['pairs'], 2)
            self.assertAlmostEqual(sum(group['mean_components_ms'].values()), group['mean_round_ms'])
            self.assertAlmostEqual(group['mean_components_ms']['between_rounds'], 150 / 1e6)
            self.assertEqual([r['pair'] for r in group['runs']], [1, 2])

    def test_reject_incomplete_sample_partition(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'one').mkdir()
            sample = dict(round_elapsed_ns=300, breakdown_ns={'file_open': 200})
            data = dict(scenario='two-files', arm='production', cache_temperature='warm',
                        pair=1, label='one', steady_rounds=1, steady_elapsed_ns=300,
                        samples=[sample, sample])
            (root / 'one/result.json').write_text(json.dumps(data))
            with self.assertRaises(AssertionError):
                summarize(root)


@unittest.skipUnless(hasattr(os, 'pread') and hasattr(os, 'O_DIRECTORY'), 'Linux syscall probe')
class DetailTests(unittest.TestCase):
    def test_real_directory_pagination_keeps_all_names(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'sub').mkdir()
            expected = {f'sub/file-{i:04}': b'x' for i in range(100)}
            for name, data in expected.items():
                (root / name).write_bytes(data)
            proof = directory_page_probe(root, expected, 512)
            self.assertEqual(proof['entries'], 100)
            self.assertGreater(proof['user_getdents64_calls'], 2)
            self.assertFalse(proof['timed'])

    def test_complete_partition_and_content(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'sub').mkdir()
            expected = {'a.txt': b'hello', 'sub/b.txt': b'world'}
            for name, data in expected.items():
                (root / name).write_bytes(data)
            result = measured_round(root, expected)
            self.assertEqual(sum(result['breakdown_ns'].values()), result['round_elapsed_ns'])
            self.assertTrue(all(value >= 0 for value in result['breakdown_ns'].values()))
            self.assertEqual(sum(r['bytes'] for r in result['pread']), 10)
            self.assertEqual({r['path'] for r in result['pread']}, set(expected))
            with patch('run_detail.os.pread', return_value=b'wrong'):
                with self.assertRaises(AssertionError):
                    measured_round(root, expected)
            # A content mismatch must not retain an open descriptor.
            before = len(list(Path('/proc/self/fd').iterdir()))
            for _ in range(5):
                with patch('run_detail.os.pread', return_value=b'wrong'):
                    with self.assertRaises(AssertionError):
                        measured_round(root, expected)
            self.assertEqual(len(list(Path('/proc/self/fd').iterdir())), before)


if __name__ == '__main__':
    unittest.main()
