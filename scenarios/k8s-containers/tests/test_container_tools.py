import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

SCRIPTS = Path(__file__).resolve().parents[1] / 'scripts'


def load(name):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f'{name}.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


observe, summary, campaign, case = (load(name) for name in ('observe', 'summarize', 'campaign', 'replay_case'))


class ScopeTests(unittest.TestCase):
    def test_nested_cgroup_mount(self):
        entries = observe.mounts('1 2 0:1 /kubepods/a /sys/fs/cgroup rw - cgroup2 cgroup rw')
        self.assertEqual(str(observe.cgroup_candidate('0::/kubepods/a/worker', entries)), '/sys/fs/cgroup/worker')
        self.assertIsNone(observe.cgroup_candidate('0::/other', entries))
        self.assertIsNone(observe.cgroup_candidate('0::/../../host', entries))
        self.assertIsNone(observe.cgroup_candidate('1:cpu:/x', entries))

    def test_cgroup_namespace_and_escaped_mount(self):
        entries = observe.mounts('1 2 0:1 / /sys/fs/cgroup rw - cgroup2 cgroup rw\n'
                                 '2 1 0:2 / /work\\040space rw - xfs disk rw')
        self.assertEqual(str(observe.cgroup_candidate('0::/', entries)), '/sys/fs/cgroup')
        self.assertEqual(observe.mount_for('/work space/task', entries)['fs_type'], 'xfs')

    def test_missing_and_reset_not_zero(self):
        def s(v):
            return {'cgroup': {'cpu.stat': {'value': v}}}
        self.assertEqual(summary.delta([s('usage_usec 10'), s('usage_usec 22')], 'cpu.stat', 'usage_usec')['value'], 12)
        self.assertIsNone(summary.delta([s('usage_usec 10'), s(None)], 'cpu.stat', 'usage_usec')['value'])
        self.assertEqual(summary.delta([s('usage_usec 10'), s('usage_usec 2')], 'cpu.stat', 'usage_usec')['reason'], 'counter_reset')


class CampaignTests(unittest.TestCase):
    def test_fixed_inputs_and_counts(self):
        plan = campaign.build(['juicefs', 'brewfs'], 'smoke')
        self.assertEqual(len(plan['batches']), 12)
        self.assertEqual(sum(len(b['tasks']) for b in plan['batches']), 48)
        main = campaign.build(['juicefs', 'brewfs'], 'main')
        self.assertEqual(len(main['batches']), 24)
        self.assertEqual(sum(len(b['tasks']) for b in main['batches']), 1536)
        for batch in main['batches']:
            for trace in campaign.CASES:
                self.assertEqual(sum(t['trace'] == trace for t in batch['tasks']), 16)
                for worker in range(4):
                    self.assertEqual(sum(t['trace'] == trace and t['worker_slot'] == worker for t in batch['tasks']), 4)
        self.assertEqual(plan, campaign.build(['juicefs', 'brewfs'], 'smoke'))
        with self.assertRaises(ValueError):
            campaign.build(['cephfs'], 'smoke')

    def test_scale_keeps_four_traces_per_worker(self):
        plan = campaign.build(['juicefs'], 'scale')
        self.assertEqual(len(plan['batches']), 12)
        self.assertEqual(sum(len(b['tasks']) for b in plan['batches']), 2544)
        self.assertIsNone(plan['tasks_per_batch'])
        self.assertEqual({b['worker_count'] for b in plan['batches']}, {4, 16, 64, 128})
        sizes = [b['worker_count'] for b in plan['batches']]
        self.assertEqual(sizes, sorted(sizes))
        for batch in plan['batches']:
            self.assertEqual(batch['concurrency'], 4 * batch['worker_count'])
            self.assertEqual(batch['concurrency'], len(batch['tasks']))
            for worker in range(batch['worker_count']):
                self.assertEqual(sorted(t['trace'] for t in batch['tasks'] if t['worker_slot'] == worker),
                                 sorted(campaign.CASES))


class RunnerTests(unittest.TestCase):
    def test_case_timeout_is_shared_between_phases(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / 'case'
            (source / 'initial').mkdir(parents=True)
            (source / 'capture/blobs').mkdir(parents=True)
            # Python stands in for a slow replay binary; no executable on /tmp is needed.
            (source / 'capture/events.jsonl').write_text('import time\ntime.sleep(5)\n')
            for name in ('initial-tree.json', 'final-tree.json'):
                (source / name).write_text('{}')
            (source / 'metrics.json').write_text('{}')
            (root / 'work').mkdir()
            binary = Path(sys.executable)
            out = root / 'out'
            result = subprocess.run([sys.executable, str(SCRIPTS / 'run_case.py'),
                '--case', str(source), '--workspace-parent', str(root / 'work'), '--out', str(out),
                '--binary', str(binary), '--run-id', 'budget', '--worker-id', 'w0', '--timeout', '.5'],
                capture_output=True, text=True, timeout=8)
            self.assertNotEqual(result.returncode, 0)
            report = json.loads((out / 'case-result.json').read_text())
            self.assertFalse(report['valid'])
            self.assertLess(report['wall_seconds'], 3)
            # Preparation consumes some of the same budget passed to replay.
            if (out / 'replay/environment.json').exists():
                env = json.loads((out / 'replay/environment.json').read_text())
                self.assertLess(env['timeout_s'], .5)
                self.assertTrue(json.loads((out / 'replay/observer-result.json').read_text())['timed_out'])
            self.assertFalse((out / 'verify').exists())

    def run_command(self, root, name, command, timeout='5'):
        out = root / name
        result = subprocess.run([sys.executable, str(SCRIPTS / 'observe.py'), 'run',
                                 '--out', str(out), '--work-root', str(root / 'work'),
                                 '--run-id', 'test', '--worker-id', 'w0', '--phase', 'replay',
                                 '--interval', '.05', '--timeout', timeout, '--', *command],
                                capture_output=True, text=True)
        return result, out

    def test_success_failure_timeout_and_truncated_tail(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'work').mkdir()
            result, out = self.run_command(root, 'ok', [sys.executable, '-c', 'import time; time.sleep(.15)'])
            self.assertEqual(result.returncode, 0, result.stderr)
            report = summary.summarize(out)
            self.assertTrue(report['complete'])
            self.assertEqual(report['observer_result']['correctness'], 'not_checked_by_observer')
            rows = [json.loads(s) for s in (out / 'samples.jsonl').read_text().splitlines()]
            rows[0]['index'] = 99
            (out / 'samples.jsonl').write_text(''.join(json.dumps(s) + '\n' for s in rows))
            self.assertFalse(summary.summarize(out)['complete'])
            failed, failout = self.run_command(root, 'bad', [sys.executable, '-c', 'raise SystemExit(7)'])
            self.assertNotEqual(failed.returncode, 0)
            self.assertEqual(summary.summarize(failout)['observer_result']['exit_code'], 7)
            timed, timeout = self.run_command(root, 'timeout', [sys.executable, '-c', 'import time; time.sleep(10)'], '.1')
            self.assertNotEqual(timed.returncode, 0)
            self.assertTrue(summary.summarize(timeout)['observer_result']['timed_out'])
            (out / 'observer-result.json').unlink()
            with (out / 'samples.jsonl').open('a') as f:
                f.write('{broken')
            incomplete = summary.summarize(out)
            self.assertFalse(incomplete['complete'])
            self.assertEqual(incomplete['damaged_lines'], 1)
            self.assertIsNone(incomplete['observer_result'])

    def test_timeout_kills_descendant_even_after_parent_exits(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'work').mkdir()
            pidfile = root / 'child-pid'
            program = ('import os,signal,time,pathlib\n'
                       'pid=os.fork()\n'
                       'if pid == 0:\n'
                       ' signal.signal(signal.SIGTERM,signal.SIG_IGN)\n'
                       f' pathlib.Path({str(pidfile)!r}).write_text(str(os.getpid()))\n'
                       ' time.sleep(30)\n'
                       'else:\n time.sleep(30)\n')
            result, out = self.run_command(root, 'descendant', [sys.executable, '-c', program], '.5')
            self.assertNotEqual(result.returncode, 0)
            cleanup = summary.summarize(out)['observer_result']['process_group_cleanup']
            self.assertTrue(cleanup['sigkill_sent'])
            pid = int(pidfile.read_text())
            status = Path(f'/proc/{pid}/stat')
            # A killed orphan may remain a zombie until init reaps it.
            if status.exists():
                self.assertEqual(status.read_text().split(') ', 1)[1][0], 'Z')

    def test_observer_cannot_write_into_tested_root(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'work').mkdir()
            result, out = self.run_command(root, 'work/logs', [sys.executable, '-c', 'pass'])
            self.assertNotEqual(result.returncode, 0)
            self.assertFalse(out.exists())

    def test_prepare_never_overwrites_and_verify_rejects_bad_tree(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / 'case'
            (source / 'initial').mkdir(parents=True)
            (source / 'capture' / 'blobs').mkdir(parents=True)
            (source / 'capture/events.jsonl').write_text('')
            (source / 'initial/file').write_text('original')
            expected = case.tree(source / 'initial')
            for f in ('initial-tree.json', 'final-tree.json'):
                (source / f).write_text(json.dumps(expected))
            (source / 'metrics.json').write_text(json.dumps({'callbacks': 2, 'actual_read_bytes': 0,
                                                           'actual_write_bytes': 0, 'repeated_read_ratio': 0}))
            target = root / 'target'
            case.prepare(source, target)
            with self.assertRaises(ValueError):
                case.prepare(source, target)
            replay = root / 'replay.json'
            replay.write_text(json.dumps({'valid': True, 'remaining_handles': 0, 'operations': 2,
                                          'actual_read_bytes': 0, 'actual_write_bytes': 0}))
            self.assertTrue(case.verify(source, target, replay)['valid'])
            (target / 'file').write_text('modified')
            with self.assertRaises(ValueError):
                case.verify(source, target, replay)


if __name__ == '__main__':
    unittest.main()
