import hashlib
import importlib.util
import json
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
from types import SimpleNamespace
import unittest
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import artifacts
import resource_exporter

spec = importlib.util.spec_from_file_location('render', ROOT / 'deploy/render.py')
render = importlib.util.module_from_spec(spec)
spec.loader.exec_module(render)


def digest(data):
    return hashlib.sha256(data).hexdigest()


def free_port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


def wait_http(process, url, expected=200):
    deadline = time.monotonic() + 8
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise AssertionError('Service exited before readiness')
        try:
            with urllib.request.urlopen(url, timeout=.5) as response:
                if response.status == expected:
                    return response.read()
        except urllib.error.HTTPError as exc:
            if exc.code == expected:
                return exc.read()
        except OSError:
            pass
        time.sleep(.05)
    raise AssertionError('Service did not become ready')


def stop(process):
    process.terminate()
    try:
        process.wait(timeout=3)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=3)


class ConfigTests(unittest.TestCase):
    def test_static_addresses_and_otel_pipeline(self):
        hosts = json.loads((ROOT / 'deploy/hosts.example.json').read_text())
        prom, otel = render.render(hosts)
        jobs = {j['job_name']: j for j in prom['scrape_configs']}
        self.assertEqual(len(jobs['resources']['static_configs']), 5)
        self.assertEqual(len(jobs['juicefs']['static_configs']), 2)
        self.assertEqual(jobs['redis']['scrape_interval'], '5s')
        self.assertEqual(jobs['rustfs']['static_configs'][0]['targets'], ['127.0.0.1:8889'])
        self.assertEqual(otel['receivers']['otlp']['protocols']['http']['endpoint'],
                         hosts['monitor'] + ':4318')
        self.assertNotIn('kubernetes', json.dumps([prom, otel]))
        self.assertEqual(set(otel['service']['pipelines']), {'metrics'})
        hosts['workers'][0]['ip'] = hosts['redis']
        with self.assertRaises(ValueError):
            render.render(hosts)
        hosts['workers'][0]['ip'] = '0.0.0.0'
        with self.assertRaises(ValueError):
            render.render(hosts)


class ResourceTests(unittest.TestCase):
    def test_missing_fields_are_not_zero(self):
        row = {'cgroup': {key: {'value': None} for key in
                         ('cpu.stat', 'memory.current', 'memory.max', 'memory.events', 'io.stat')},
               'network_dev': {'value': None}, 'utc_ns': 10**9, 'collector_read_ns': 1000}
        output = resource_exporter.prometheus(row)
        self.assertIn('ax_source_available{source="cpu.stat"} 0', output)
        self.assertNotIn('ax_cpu_seconds_total', output)
        self.assertNotIn('ax_memory_current_bytes', output)
        self.assertNotIn('ax_netns_bytes_total', output)
        row['cgroup']['cpu.stat']['value'] = 'usage_usec 2000000\nnr_periods 10\nnr_throttled 3'
        row['cgroup']['memory.max']['value'] = 'max'
        row['cgroup']['io.stat']['value'] = '8:0 rbytes=12 wbytes=34 rios=1 wios=2'
        row['network_dev']['value'] = 'eth0: 11 0 0 0 0 0 0 0 22 0 0 0 0 0 0 0'
        output = resource_exporter.prometheus(row)
        self.assertIn('ax_cpu_seconds_total 2.0', output)
        self.assertNotIn('ax_memory_limit_bytes', output)
        self.assertIn('ax_cgroup_block_bytes_total{device="8:0",direction="wbytes"} 34', output)
        self.assertIn('ax_netns_bytes_total{interface="eth0",direction="tx"} 22', output)

    def test_http_endpoint_and_raw_sample(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / 'resources.jsonl'
            port = free_port()
            process = subprocess.Popen([sys.executable, str(ROOT / 'scripts/resource_exporter.py'),
                '--bind', '127.0.0.1', '--port', str(port), '--worker-id', 'w-test', '--out', str(out)],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            try:
                body = wait_http(process, f'http://127.0.0.1:{port}/metrics').decode()
                self.assertIn('ax_sample_timestamp_seconds', body)
                sample = json.loads(out.read_text().splitlines()[0])
                self.assertEqual(sample['worker_id'], 'w-test')
                self.assertEqual(sample['index'], 0)
                self.assertIn(sample['scope'], ('unavailable', 'own_cgroup_container_boundary_unconfirmed'))
                self.assertIn('reason', sample['cgroup']['cpu.stat'])
            finally:
                stop(process)


class ArtifactTests(unittest.TestCase):
    def test_retry_after_growth_preserves_prefix_and_empty_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            artifacts.append_chunk(root, 'w0/stdout.log', 0, b'abc', digest(b'abc'))
            # Restarted sender reads a larger chunk than it did before its crash.
            artifacts.append_chunk(root, 'w0/stdout.log', 0, b'abcdef', digest(b'abcdef'))
            artifacts.append_chunk(root, 'w0/stdout.log', 3, b'def', digest(b'def'))
            self.assertEqual((root / 'w0/stdout.log').read_bytes(), b'abcdef')
            artifacts.append_chunk(root, 'w0/stderr.log', 0, b'', digest(b''))
            self.assertEqual((root / 'w0/stderr.log').stat().st_size, 0)
            for offset, data, sha in [(0, b'bad', digest(b'bad')), (9, b'x', digest(b'x')),
                                       (6, b'x', 'incorrect')]:
                with self.assertRaises(ValueError):
                    artifacts.append_chunk(root, 'w0/stdout.log', offset, data, sha)
            self.assertEqual((root / 'w0/stdout.log').read_bytes(), b'abcdef')

    def test_path_and_symlink_escape_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / 'received'
            root.mkdir()
            for relative in ('../stdout.log', '/tmp/stdout.log', 'w0/secret.env'):
                with self.assertRaises(ValueError):
                    artifacts.append_chunk(root, relative, 0, b'x', digest(b'x'))
            (root / 'w0').symlink_to(Path(tmp), target_is_directory=True)
            with self.assertRaises(ValueError):
                artifacts.append_chunk(root, 'w0/stdout.log', 0, b'x', digest(b'x'))

    def test_http_roundtrip_restart_and_auth(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source, destination = root / 'source', root / 'received'
            source.mkdir()
            token = 'local-test-token-' + 'x' * 32
            tokenfile = root / 'token'
            tokenfile.write_text(token)
            (source / 'stdout.log').write_bytes(b'a' * (artifacts.LIMIT + 17))
            (source / 'stderr.log').write_bytes(b'')
            (source / 'secret.env').write_text('do-not-send')
            port = free_port()
            process = subprocess.Popen([sys.executable, str(ROOT / 'scripts/artifacts.py'), 'serve',
                '--bind', '127.0.0.1', '--port', str(port), '--root', str(destination),
                '--token-file', str(tokenfile)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            args = SimpleNamespace(root=source, url=f'http://127.0.0.1:{port}', worker_id='w0')
            try:
                # GET isn't supported; listening is sufficient for this readiness check.
                wait_http(process, args.url, expected=501)
                request = urllib.request.Request(args.url + '/append', data=b'x', method='POST')
                with self.assertRaises(urllib.error.HTTPError) as error:
                    urllib.request.urlopen(request, timeout=2)
                self.assertEqual(error.exception.code, 403)
                offsets = {}
                artifacts.ship_once(args, token, offsets)
                self.assertEqual(artifacts.ship_once(args, token, offsets), 0)
                with (source / 'stdout.log').open('ab') as stream:
                    stream.write(b'grown-after-restart')
                artifacts.ship_once(args, token, {})
                self.assertEqual((source / 'stdout.log').read_bytes(),
                                 (destination / 'w0/stdout.log').read_bytes())
                self.assertTrue((destination / 'w0/stderr.log').exists())
                self.assertFalse((destination / 'w0/secret.env').exists())
            finally:
                stop(process)


if __name__ == '__main__':
    unittest.main()
