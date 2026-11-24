import unittest

from bench.cluster.runtime.metric_totals import rustfs_payload_metrics


class MetricTotalsTests(unittest.TestCase):
    def test_s3_payload_excludes_internal_http_responses(self):
        metrics={
            'rustfs_s3_get_object_size_bytes_sum{node="a"}':4096,
            'rustfs_s3_get_object_size_bytes_sum{node="b"}':8192,
            'rustfs_s3_put_object_size_bytes_sum{node="a"}':1024,
            'rustfs_http_server_response_body_bytes_total{node="a"}':1000000,
            'rustfs_http_server_response_body_bytes_total{node="b"}':2000000,
            'rustfs_http_server_requests_total{method="POST"}':800,
            'rustfs_s3_http_requests_total{node="a",method="GET"}':1,
            'rustfs_s3_http_requests_total{method="GET",node="b"}':2,
            'rustfs_s3_http_requests_total{method="PUT",node="a"}':1,
            'rustfs_io_get_object_shard_read_observed_bytes_total{node="a"}':65536,
        }
        totals=rustfs_payload_metrics(metrics)
        self.assertEqual(totals['rustfs_s3_get_bytes'],12288)
        self.assertEqual(totals['rustfs_s3_put_bytes'],1024)
        self.assertEqual(totals['rustfs_http_response_bytes'],3000000)
        self.assertEqual(totals['rustfs_s3_requests_by_method'],{'GET':3,'PUT':1})
        self.assertEqual(totals['rustfs_shard_read_bytes'],65536)

    def test_empty_s3_payload_does_not_use_http_as_a_substitute(self):
        totals=rustfs_payload_metrics({'rustfs_http_server_response_body_bytes_total':2048})
        self.assertEqual(totals['rustfs_s3_get_bytes'],0)
        self.assertEqual(totals['rustfs_s3_put_bytes'],0)
        self.assertEqual(totals['rustfs_s3_requests_by_method'],{})


if __name__=='__main__':unittest.main()
