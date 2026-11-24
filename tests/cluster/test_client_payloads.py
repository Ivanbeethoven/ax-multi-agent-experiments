import ast
from pathlib import Path
import unittest


class ClientPayloadTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        source = Path(__file__).resolve().parents[2] / 'bench/cluster/runtime/run_c1.py'
        tree = ast.parse(source.read_text(encoding='utf-8'))
        methods = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in ('diff_prom', 'enrich_amplification')]
        ns = {}
        exec(compile(ast.Module(body=methods, type_ignores=[]), 'metrics', 'exec'), ns)
        cls.diff = staticmethod(ns['diff_prom'])
        cls.enrich = staticmethod(ns['enrich_amplification'])

    def metric(self, counters):
        return self.enrich(dict(rustfs_s3_get_bytes=900, rustfs_s3_put_bytes=800,
                               backend_physical_read_bytes=1000, backend_physical_write_bytes=1000), 100, 100, counters)

    def test_read_with_zero_upload_keeps_client_zero(self):
        d = self.metric({'graftfs_s3_get_bytes_total': 100})
        self.assertEqual((d['s3_get_bytes'], d['s3_put_bytes'], d['s3_byte_source']), (100, 0, 'client'))

    def test_write_with_zero_download_keeps_client_zero(self):
        d = self.metric({'juicefs_object_request_data_bytes{method="PUT"}': 100})
        self.assertEqual((d['s3_get_bytes'], d['s3_put_bytes']), (0, 100))

    def test_cached_read_preserves_zero_transfer_counters(self):
        counters = {'graftfs_s3_get_bytes_total': 100, 'graftfs_s3_put_bytes_total': 0}
        d = self.metric(self.diff(counters, counters))
        self.assertEqual((d['s3_get_bytes'], d['s3_put_bytes'], d['s3_byte_source']), (0, 0, 'client'))

    def test_missing_client_uses_explicit_server_source(self):
        d = self.metric({})
        self.assertEqual((d['s3_get_bytes'], d['s3_put_bytes'], d['s3_byte_source']), (900, 800, 'rustfs-s3'))
        self.assertIsNone(d['client_reported_s3_get_bytes'])

    def test_general_and_packed_counters_are_not_added_together(self):
        d = self.metric({'graftfs_s3_get_bytes_total': 100, 'graftfs_packed_runtime_backend_received_bytes_total': 100})
        self.assertEqual(d['s3_get_bytes'], 100)

    def test_replay_keeps_zero_client_counts(self):
        source = Path(__file__).resolve().parents[2] / 'bench/cluster/runtime/run_c2.py'
        tree = ast.parse(source.read_text(encoding='utf-8'))
        method = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'extract_s3_and_amp')
        ns = {}
        exec(compile(ast.Module(body=[method], type_ignores=[]), 'replay-metrics', 'exec'), ns)
        for counters in ({'graftfs_s3_get_bytes_total': 0}, {'juicefs_object_request_data_bytes_GET': 0}):
            result = ns['extract_s3_and_amp']({'rustfs_s3_get_bytes':900, 'rustfs_s3_put_bytes':800,
                'backend_physical_read_bytes':1000, 'backend_physical_write_bytes':1000}, 100, 100, counters)
            self.assertEqual((result['s3_get_bytes'], result['s3_put_bytes'], result['s3_byte_source']), (0,0,'client'))


if __name__ == '__main__':
    unittest.main()
