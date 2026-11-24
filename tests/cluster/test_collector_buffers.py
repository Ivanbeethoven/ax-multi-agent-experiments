import importlib.util
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from test_suite_runtime import fake_cluster_lib, load_ast_functions


ROOT = Path(__file__).resolve().parents[2]


class CollectorBufferTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location(
            'collector_buffer_test_module', ROOT / 'bench/cluster/runtime/host_collector.py')
        cls.module = importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules, {'runtime_context': SimpleNamespace(REDIS_PORT=17379)}):
            spec.loader.exec_module(cls.module)

    def test_byte_limit_flushes_before_sixty_samples(self):
        with tempfile.TemporaryDirectory() as temporary:
            compressor = self.module.SegmentCompressor(Path(temporary), max_buffer_bytes=12)
            with patch.object(self.module.subprocess, 'run') as run:
                compressor.append('{}\n', 'm 1\n')
                self.assertEqual(run.call_count, 0)
                compressor.append('{}\n', 'm 2\n')
                self.assertEqual(run.call_count, 2)
                self.assertEqual(run.call_args_list[0].kwargs['input'], b'{}\n{}\n')
                self.assertEqual(run.call_args_list[1].kwargs['input'], b'm 1\nm 2\n')
                self.assertEqual(compressor.buffer_bytes, 0)
                self.assertEqual(compressor.sample_buf, [])
                self.assertEqual(compressor.prom_buf, [])

    def test_unicode_counts_encoded_bytes(self):
        with tempfile.TemporaryDirectory() as temporary:
            compressor = self.module.SegmentCompressor(Path(temporary), max_buffer_bytes=5)
            with patch.object(self.module.subprocess, 'run') as run:
                compressor.append('{}', '字')
                self.assertEqual(run.call_count, 2)

    def test_disconnected_client_does_not_terminate_collector(self):
        for error in (BrokenPipeError(), ConnectionResetError()):
            connection = Mock()
            connection.sendall.side_effect = error
            self.assertFalse(self.module.send_reply(connection, {'ok': True}))

    def test_connected_client_gets_complete_json_line(self):
        connection = Mock()
        self.assertTrue(self.module.send_reply(connection, {'ok': True, 'samples': 7}))
        payload = connection.sendall.call_args.args[0]
        self.assertTrue(payload.endswith(b'\n'))
        self.assertEqual(json.loads(payload), {'ok': True, 'samples': 7})

    def test_collector_failure_still_stops_remote_mounts(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            cl = fake_cluster_lib(root, root / 'inputs')
            cl.collector_set_mounts = Mock(side_effect=RuntimeError('collector timeout'))
            cl.run_on_host = Mock(return_value=SimpleNamespace(
                returncode=0, stdout=b'{"stopped":[]}'))
            namespace = {'cl': cl, 'Path': Path, 'json': json,
                         'defaultdict': defaultdict, 'ThreadPoolExecutor': ThreadPoolExecutor}
            load_ast_functions(ROOT / 'bench/cluster/runtime/run_c2.py',
                               ['batch_stop_mounts'], namespace)
            with self.assertRaisesRegex(RuntimeError, 'collector timeout'):
                namespace['batch_stop_mounts']([{'host_name': 'node-store-2', 'id': 'reader'}], 'test')
            self.assertTrue(any('stop-batch' in call.args[1] for call in cl.run_on_host.call_args_list))

    def test_remote_collector_uses_requested_timeout(self):
        remote = Mock(return_value=SimpleNamespace(stdout=b'{"ok":true}'))
        namespace = {'json': json, 'run_on_host': remote}
        load_ast_functions(ROOT / 'bench/cluster/runtime/cluster_lib.py',
                           ['collector_rpc'], namespace)
        self.assertEqual(namespace['collector_rpc']('node-store-2', {'action': 'flush'}, timeout=27.5),
                         {'ok': True})
        self.assertIn('s.settimeout(27.5)', remote.call_args.args[1][-1])
        self.assertEqual(remote.call_args.kwargs['timeout'], 32.5)


if __name__ == '__main__':
    unittest.main()
