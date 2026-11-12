import datetime
import json
import os
from pathlib import Path
import runpy
import sys
import tempfile
import unittest
from unittest.mock import patch


SCRIPTS = Path(__file__).resolve().parents[1] / 'experiments/unified-suite/pilot-aliyun/scripts'
CAPTURE = runpy.run_path(str(SCRIPTS / 'capture-c3-packed-capacity-before.py'))['capture']
CAPACITY = runpy.run_path(str(SCRIPTS / 'packed-c3-capacity.py'))
OBSERVE = CAPTURE.__globals__['observe']


@unittest.skipUnless(sys.platform.startswith('linux') and Path('/sys/fs/cgroup/cgroup.controllers').exists(),
                     'Linux procfs and cgroup v2 required')
class CapacityCaptureTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.path = Path(tmp.name)
        self.pid = os.getpid()
        self.identity = CAPTURE.__globals__['identity'](self.pid)
        self.boot = self.identity['boot_id']
        self.exe = Path(sys.executable).resolve()
        cgroup = next(line[3:] for line in Path('/proc/self/cgroup').read_text().splitlines()
                      if line.startswith('0::'))
        cgroup = Path('/sys/fs/cgroup') / cgroup.lstrip('/')
        if not all((cgroup / name).is_file() for name in OBSERVE.CGROUP_FILES):
            self.skipTest('current test cgroup does not expose the frozen observer file set')
        self.step = self.path / 'step.json'
        self.step.write_text(json.dumps(dict(argv=[str(self.exe), 'INFO', 'ALL'], inherit_environment=[])))
        self.spec = dict(schema='experiment-observer-v1', role='metadata', pool='pool-a',
                         observer_id='pool-a-metadata', interval_seconds=5, metric_contract_sha256='a' * 64,
                         data_paths=[str(self.path)], processes=[self.identity],
                         cgroups=[dict(id='redis-process', path=str(cgroup))], targets=[
                             dict(id='redis', kind='command', format='redis-info',
                                  step=CAPACITY['ref'](self.step), required_names=['process_id']),
                         ])
        sandbox = patch.dict(CAPTURE.__globals__, PATHS={'metadata': [str(self.path)]}, REDIS_CLI=self.exe)
        sandbox.start()
        self.addCleanup(sandbox.stop)

    def capture(self, redis_pid=None):
        spec_path = self.path / 'observer.json'
        spec_path.write_text(json.dumps(self.spec))
        text = f'# Server\nprocess_id:{redis_pid or self.pid}\nused_memory:123\n'
        with patch.object(OBSERVE, 'collect_target', return_value=(text, {})):
            return CAPTURE('metadata', CAPACITY['ref'](spec_path), self.path / 'before')['sample']

    def test_live_procfs_and_disk_sample_consumed_by_frozen_capacity_reader(self):
        pin = self.capture()
        boundary = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(seconds=1)
        before = CAPACITY['before_sample'](pin, boundary, self.boot, [str(self.path)])
        self.assertGreater(before['capacity'][str(self.path)]['available_bytes'], 0)
        self.assertTrue(before['before_total_unknown'])
        self.assertIn(self.identity, before['original_processes'])
        self.assertTrue(any(p['path'].endswith('/redis.txt') for p in before['source_pins']))
        with self.assertRaisesRegex(ValueError, 'runtime boot differ'):
            CAPACITY['before_sample'](pin, boundary, 'other-boot', [str(self.path)])
        Path(pin['path']).with_name('redis.txt').write_text('changed')
        with self.assertRaisesRegex(ValueError, 'raw sample changed'):
            CAPACITY['before_sample'](pin, boundary, self.boot, [str(self.path)])

    def test_redis_native_pid_mismatch_retains_original_sample(self):
        with self.assertRaisesRegex(ValueError, 'Redis INFO PID differs'):
            self.capture(self.pid + 1)
        self.assertTrue((self.path / 'before/sample-000000/redis.txt').is_file())

    def test_refuses_old_boot_and_redis_command_with_extra_arguments(self):
        self.spec['processes'][0]['boot_id'] = 'old-boot'
        with self.assertRaisesRegex(ValueError, 'different boot'):
            self.capture()
        self.spec['processes'][0]['boot_id'] = self.boot
        self.step.write_text(json.dumps(dict(argv=[str(self.exe), '-h', 'different', 'INFO', 'ALL'],
                                             inherit_environment=[])))
        self.spec['targets'][0]['step'] = CAPACITY['ref'](self.step)
        with self.assertRaisesRegex(ValueError, 'Only native local'):
            self.capture()
        self.assertFalse((self.path / 'before').exists())


if __name__ == '__main__':
    unittest.main()
