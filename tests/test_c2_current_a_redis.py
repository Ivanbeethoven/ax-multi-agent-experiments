"""Exercise the C2 Redis gate using its first live A-pool readback."""
import hashlib
import json
from pathlib import Path
import runpy
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch


EVIDENCE = (Path(__file__).resolve().parents[1] /
            'experiments/unified-suite/pilot-aliyun/evidence/preparation')
CONTROLLER = EVIDENCE / 'c2-current-trace-a-draft-01/run-c2-current-a.py'
LIVE = EVIDENCE / 'c2-current-trace-a-preflight-failed-01/redis.json'


class C2RedisTest(unittest.TestCase):
    def setUp(self):
        raw = LIVE.read_bytes()
        self.assertEqual(hashlib.sha256(raw).hexdigest(),
                         '24682576ece1e6b7233fcdece6bd8f97c6dca06273a101e2c44c68e660429536')
        self.live = json.loads(raw)
        self.gate = runpy.run_path(str(CONTROLLER))['preflight_redis']
        tmp = tempfile.TemporaryDirectory(prefix='ax-c2-redis-')
        self.addCleanup(tmp.cleanup)
        self.out = Path(tmp.name)
        self.elapsed = 0.0
        self.sleeps = []
        clock = SimpleNamespace(monotonic=lambda: self.elapsed, time=lambda: 1000 + self.elapsed,
                                sleep=self.advance)
        clock_patch = patch.dict(self.gate.__globals__, {'time': clock})
        clock_patch.start()
        self.addCleanup(clock_patch.stop)
        self.config = self.live['config'].copy()
        self.samples = [self.live['fields'].copy()]
        self.info_calls = []

    def advance(self, delay):
        self.sleeps.append(delay)
        self.elapsed += delay

    def ssh(self, role, argv, *, capture_output, text, timeout):
        self.assertEqual(role, 'metadata')
        self.assertEqual(argv[:4], ['redis-cli', '-h', '10.20.2.162', '--raw'])
        self.assertTrue(capture_output and text and 0 < timeout <= 8)
        if argv[4] == 'CONFIG':
            self.assertEqual(set(argv[6:]), set(self.live['config']))
            body = '\n'.join(item for pair in self.config.items() for item in pair)
        else:
            self.assertEqual(argv[4:], ['INFO'])
            self.assertTrue((self.out / 'redis-config.json').is_file())
            if self.info_calls:
                self.assertTrue((self.out / f'redis-info-{len(self.info_calls):02d}.json').is_file())
            self.info_calls.append(timeout)
            sample = self.samples[min(len(self.info_calls), len(self.samples)) - 1]
            body = '\n'.join(f'{key}:{value}' for key, value in sample.items())
            body += '\nnot_a_whitelisted_field:unused-test-marker\n'
        return SimpleNamespace(stdout=body)

    def receipt(self, name):
        return json.loads((self.out / name).read_bytes())

    def test_exact_observed_config_and_info_pass_without_wait(self):
        self.gate(self.ssh, self.out, 2000)
        self.assertEqual(self.receipt('redis.json'), self.live)
        self.assertEqual(self.receipt('redis-config.json')['config'], self.live['config'])
        self.assertEqual(self.receipt('redis-info-01.json')['fields'], self.live['fields'])
        self.assertEqual(self.sleeps, [])

    def test_only_rewrite_is_polled_and_every_sample_is_saved(self):
        self.samples = [dict(self.live['fields'], aof_rewrite_in_progress='1'), self.live['fields']]
        self.gate(self.ssh, self.out, 2000)
        self.assertEqual(self.sleeps, [3])
        self.assertEqual(self.receipt('redis-info-01.json')['fields'], self.samples[0])
        self.assertEqual(self.receipt('redis-info-02.json')['fields'], self.samples[1])
        self.assertEqual(self.receipt('redis.json'), self.live)

    def test_memory_headroom_failure_with_rewrite_is_immediate(self):
        memory = (4 * int(self.live['fields']['maxmemory']) + 4) // 5
        self.samples = [dict(self.live['fields'], used_memory=str(memory), aof_rewrite_in_progress='1')]
        with self.assertRaisesRegex(ValueError, 'memory headroom'):
            self.gate(self.ssh, self.out, 2000)
        self.assertEqual(self.receipt('redis-info-01.json')['fields'], self.samples[0])
        self.assertEqual(len(self.info_calls), 1)
        self.assertEqual(self.sleeps, [])
        self.assertFalse((self.out / 'redis.json').exists())

    def test_config_failure_is_preserved_before_info(self):
        self.config['maxmemory-policy'] = 'allkeys-lru'
        with self.assertRaisesRegex(ValueError, 'noeviction Redis'):
            self.gate(self.ssh, self.out, 2000)
        self.assertEqual(self.receipt('redis-config.json')['config'], self.config)
        self.assertEqual(self.info_calls, [])

    def test_wait_respects_sixty_seconds_and_shorter_window(self):
        self.samples = [dict(self.live['fields'], aof_rewrite_in_progress='1')]
        root = self.out
        for deadline, limit in [(2000, 60), (1005, 5)]:
            with self.subTest(limit=limit):
                self.out = root / str(limit)
                self.out.mkdir()
                self.elapsed, self.sleeps, self.info_calls = 0, [], []
                with self.assertRaisesRegex(ValueError, 'time exhausted'):
                    self.gate(self.ssh, self.out, deadline)
                self.assertEqual(sum(self.sleeps), limit)
                self.assertEqual(len(list(self.out.glob('redis-info-*.json'))), len(self.info_calls))
                self.assertFalse((self.out / 'redis.json').exists())
