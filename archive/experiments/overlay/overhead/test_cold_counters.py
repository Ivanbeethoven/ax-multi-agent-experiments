import importlib.util
from pathlib import Path
import unittest

script = Path(__file__).parent.parent / 'hot-lower-20261112/run_local.py'
if not script.is_file():
    script = Path(__file__).with_name('run_local.py')
spec = importlib.util.spec_from_file_location('hot_runner', script)
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)


class RedisPhaseCounters(unittest.TestCase):
    def test_client_object_counts_do_not_sum_multiple_observation_layers(self):
        raw = '''brewfs_object_read_http_body_bytes_observed 1
brewfs_object_read_started_total{layer="backend_body",engine="packed"} 8
brewfs_object_read_started_total{layer="http_attempt",engine="packed"} 16
brewfs_object_read_received_body_bytes_total{layer="backend_body",engine="packed"} 35873
brewfs_object_read_received_body_bytes_total{layer="http_attempt",engine="packed"} 35873
brewfs_s3_get_ops_total 2
brewfs_s3_get_bytes_total 85
'''
        self.assertEqual(runner.client_objects(raw, 'production'), dict(read_requests=16, body_bytes=35873))
        self.assertEqual(runner.client_objects(raw, 'native-overlay'), dict(read_requests=2, body_bytes=85))
        with self.assertRaises(AssertionError):
            runner.client_objects(raw.replace('layer="http_attempt"', 'layer="other"'), 'production')

    def test_nested_command_counters_and_new_command(self):
        before = {'get': {'calls': 10.0, 'usec': 42.0}, 'info': {'calls': 1.0, 'usec': 7.0}}
        after = {'get': {'calls': 13.0, 'usec': 50.0, 'usec_per_call': 3.8}, 'info': {'calls': 2.0, 'usec': 12.0},
                 'evalsha': {'calls': 2.0, 'usec': 70.0}}
        self.assertEqual(runner.redis_delta(after, before),
                         {'get': {'calls': 3.0, 'usec': 8.0}, 'info': {'calls': 1.0, 'usec': 5.0},
                          'evalsha': {'calls': 2.0, 'usec': 70.0}})


if __name__ == '__main__':
    unittest.main()
