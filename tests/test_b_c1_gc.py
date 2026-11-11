"""Offline guards for the Pool B C1 JuiceFS gc node script and its controller.

No network and no cloud call: Aliyun, ssh and the node scripts are all replaced
by recording fakes.
"""
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

REPO = Path(__file__).resolve().parents[1]
SCRIPTS = REPO/'experiments/unified-suite/pilot-aliyun/scripts'
META = 'redis://10.20.2.169:6379/62'


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


node = load('b_c1_gc_node', SCRIPTS/'b-c1-gc-node.py')
apply_mod = load('b_c1_gc_apply', SCRIPTS/'b-c1-gc-apply.py')

DISKS = {
    'objects': ['d-bp1bhdngko7nq3c2i33z', 'd-bp1bhdngko7nq3c2i340', 'd-bp1bhdngko7nq3c2i341',
                'd-bp1bhdngko7nq3c2i342', 'd-bp1bhdngko7nq3c2i343'],
    'metadata': ['d-bp14nenf7osz9v1rkk5k', 'd-bp14nenf7osz9v1rkk5l'],
    'client': ['d-bp1fmmfkcpr8w99pwgc4', 'd-bp1fmmfkcpr8w99pwgc5'],
}


def make_aof(root):
    subdir = root/'appendonlydir'
    subdir.mkdir(parents=True)
    (subdir/'appendonly.aof.1.base.rdb').write_bytes(b'base')
    (subdir/'appendonly.aof.1.incr.aof').write_bytes(b'incr')
    (subdir/'appendonly.aof.manifest').write_text(
        'file appendonly.aof.1.base.rdb seq 1 type b\n'
        'file appendonly.aof.1.incr.aof seq 1 type i\n')
    return root


def example_allowlist():
    roles = {}
    for role, instance in apply_mod.INSTANCES.items():
        roles[role] = {'instance': {'id': instance, 'type': 'ecs.u1-c1m4.xlarge', 'zone': 'cn-hangzhou-j'},
                       'disks': [{'id': disk} for disk in DISKS[role]]}
    return dict(schema='ax-pilot-pool-b-release-freeze-public-audit-v1', passed=True,
                campaign='ax-pilot-20261111-b', owned_instance_count=3, owned_disk_count=9, roles=roles)


def example_quote(path):
    disks = [dict(role=role, disk_id=disk, gib=60, category='cloud_essd', performance_level='PL1',
                  quoted_hourly_cny=0.126)
             for role in apply_mod.ALL_ROLES for disk in DISKS[role]]
    document = dict(at='2026-11-11T13:08:59.717385+00:00', currency='CNY', cloud_changed=False,
                    disks=disks,
                    instances=[dict(role=role, instance_id=apply_mod.INSTANCES[role],
                                    instance_type='ecs.u1-c1m4.xlarge', hourly_trade_price=0.9)
                               for role in apply_mod.ALL_ROLES],
                    total_disk_hourly_cny=0.126*len(disks))
    path.write_text(json.dumps(document, indent=2)+'\n')
    return document


def example_request(tmp):
    quote = tmp/'quote.json'
    example_quote(quote)
    import hashlib
    return {
        'schema': apply_mod.SCHEMA, 'reviewed': True, 'automatic_retry': False,
        'attempt': 'b-c1-gc-01', 'pool': 'pool-b',
        'instances': dict(apply_mod.INSTANCES), 'meta_url': META,
        'juicefs': {'path': '/usr/local/bin/juicefs', 'sha256': apply_mod.JUICEFS_SHA256},
        'node_script': {'path': str(SCRIPTS/'b-c1-gc-node.py'),
                        'sha256': hashlib.sha256((SCRIPTS/'b-c1-gc-node.py').read_bytes()).hexdigest()},
        'quote': {'path': str(quote), 'sha256': hashlib.sha256(quote.read_bytes()).hexdigest()},
        'allowlist': {'path': str(tmp/'allowlist.json'), 'sha256': apply_mod.ALLOWLIST_SHA256},
        'start_deadline_seconds': 5, 'ready_deadline_seconds': 60,
        'gc_timeout_seconds': 60, 'eof_grace_seconds': 0,
    }


class FakeCloud:
    """Records every API call; a real StartInstance for the client would show up."""

    def __init__(self):
        self.calls = []
        self.status = {instance: 'Stopped' for instance in apply_mod.INSTANCES.values()}
        self.stopped = {instance: 'StopCharging' for instance in apply_mod.INSTANCES.values()}

    def api(self, action, **params):
        self.calls.append((action, dict(params)))
        if action == 'DescribeInstances':
            rows = []
            for instance in json.loads(params['InstanceIds']):
                rows.append(dict(InstanceId=instance, Status=self.status[instance],
                                 StoppedMode=self.stopped[instance], InstanceType='ecs.u1-c1m4.xlarge',
                                 ZoneId='cn-hangzhou-j', InstanceChargeType='PostPaid',
                                 InternetMaxBandwidthOut=0))
            return dict(RequestId='req-describe', Instances={'Instance': rows})
        if action == 'DescribeDisks':
            instance = params['InstanceId']
            role = next(role for role, value in apply_mod.INSTANCES.items() if value == instance)
            return dict(RequestId='req-disks', Disks={'Disk': [
                dict(DiskId=disk, InstanceId=instance, Status='In_use', DeleteWithInstance=True)
                for disk in DISKS[role]]})
        if action == 'StartInstance':
            instance = params['InstanceId']
            assert instance != apply_mod.CLIENT, 'the client instance was started'
            self.status[instance] = 'Running'
            self.stopped[instance] = None
            return dict(RequestId='req-start-'+instance)
        if action == 'StopInstance':
            assert params['ForceStop'] is False and params['StoppedMode'] == 'StopCharging'
            instance = params['InstanceId']
            self.status[instance] = 'Stopped'
            self.stopped[instance] = 'StopCharging'
            return dict(RequestId='req-stop-'+instance)
        raise AssertionError('unexpected action '+action)

    @property
    def started(self):
        return [params['InstanceId'] for action, params in self.calls if action == 'StartInstance']


class GcArgv(unittest.TestCase):
    def test_argv_is_exactly_pinned(self):
        argv = node.gc_argv(META)
        self.assertEqual(argv, ['/usr/local/bin/juicefs', 'gc', '--threads', '1', META])
        self.assertEqual(argv[1:], ['gc', '--threads', '1', META])

    def test_custom_binary_keeps_the_same_tail(self):
        argv = node.gc_argv(META, '/opt/ax/bin/juicefs')
        self.assertEqual(Path(argv[0]).name, 'juicefs')
        self.assertEqual(argv[1:], ['gc', '--threads', '1', META])

    def test_destructive_flags_are_rejected(self):
        for flag in ('--delete', '--compact', '--force'):
            with self.assertRaisesRegex(ValueError, 'Forbidden'):
                node.require_gc_argv(['juicefs', 'gc', '--threads', '1', flag, META])
            with self.assertRaisesRegex(ValueError, 'Forbidden'):
                node.require_gc_argv(['juicefs', 'gc', '--threads', '1', flag+'=true', META])

    def test_other_shapes_are_rejected(self):
        for argv in (['juicefs', 'gc', '--threads', '2', META],
                     ['juicefs', 'gc', META],
                     ['juicefs', 'gc', '--threads', '1'],
                     ['/usr/bin/other', 'gc', '--threads', '1', META]):
            with self.assertRaises(ValueError):
                node.require_gc_argv(argv)

    def test_only_the_original_metadata_url_is_accepted(self):
        for value in ('redis://10.20.2.162:6379/62', 'redis://10.20.2.169:6380/62',
                      'redis://10.20.2.169:6379/9', 'redis://10.20.2.169:6379/x', None):
            with self.assertRaises(ValueError):
                node.gc_argv(value)


class GcBinary(unittest.TestCase):
    def test_wrong_binary_sha_aborts_before_any_gc(self):
        with tempfile.TemporaryDirectory() as tmp:
            binary = Path(tmp)/'juicefs'
            binary.write_bytes(b'not the pinned build')
            out = Path(tmp)/'gc'
            with self.assertRaisesRegex(ValueError, 'SHA256'):
                node.run_gc(out, META, str(binary))
            self.assertFalse(out.exists())

    def test_data_disk_output_is_rejected(self):
        with self.assertRaisesRegex(ValueError, 'data disk'):
            node.run_gc(Path('/srv/ax-objects-0/gc'), META, '/usr/local/bin/juicefs')

    def test_relative_binary_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(ValueError, 'binary path'):
                node.run_gc(Path(tmp)/'gc', META, 'juicefs')


class AttemptMarkers(unittest.TestCase):
    def test_second_attempt_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            node.begin_attempt('b-c1-gc-01', 'objects', directory)
            with self.assertRaisesRegex(ValueError, 'already started'):
                node.begin_attempt('b-c1-gc-01', 'objects', directory)

    def test_done_marker_alone_blocks_a_retry(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            _, done = node.marker_paths('b-c1-gc-02', directory)
            done.write_text('{}\n')
            with self.assertRaisesRegex(ValueError, 'already started'):
                node.begin_attempt('b-c1-gc-02', 'metadata', directory)


class AofIdentity(unittest.TestCase):
    def setUp(self):
        node.AOF_BASELINE.clear()
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = make_aof(Path(tmp.name)/'redis')

    def test_baseline_records_size_mtime_and_sha256(self):
        value = node.verify_aof(self.root)
        self.assertTrue(value['unchanged'])
        part = value['after']['parts'][0]
        self.assertEqual(set(part), {'name', 'type', 'size', 'mtime_ns', 'sha256'})
        self.assertEqual(part['size'], len(b'base'))

    def test_newer_aof_mtime_before_gc_aborts(self):
        node.verify_aof(self.root)
        part = self.root/'appendonlydir'/'appendonly.aof.1.incr.aof'
        stamp = part.stat().st_mtime_ns
        os.utime(part, ns=(stamp+10**9, stamp+10**9))
        with self.assertRaisesRegex(ValueError, 'AOF changed'):
            node.verify_aof(self.root)

    def test_appended_bytes_before_gc_abort(self):
        node.verify_aof(self.root)
        part = self.root/'appendonlydir'/'appendonly.aof.1.incr.aof'
        with part.open('ab') as stream:
            stream.write(b'more')
        with self.assertRaisesRegex(ValueError, 'AOF changed'):
            node.verify_aof(self.root)

    def test_rewrite_after_gc_is_rejected_even_though_append_is_tolerated(self):
        node.verify_aof(self.root)
        part = self.root/'appendonlydir'/'appendonly.aof.1.incr.aof'
        with part.open('ab') as stream:
            stream.write(b'more')
        grown = node.verify_aof(self.root, 'after')
        self.assertTrue(grown['delta']['changed'])
        self.assertGreater(grown['after']['parts'][1]['size'], grown['before']['parts'][1]['size'])
        node.AOF_BASELINE.clear()
        node.verify_aof(self.root)
        (self.root/'appendonlydir'/'appendonly.aof.1.base.rdb').write_bytes(b'othr')
        with self.assertRaisesRegex(ValueError, 'rewritten'):
            node.verify_aof(self.root, 'after')


class MountIdentity(unittest.TestCase):
    def test_bind_inode_mismatch_aborts(self):
        with tempfile.TemporaryDirectory() as tmp:
            first, second = Path(tmp)/'first', Path(tmp)/'second'
            first.mkdir()
            second.mkdir()
            with self.assertRaisesRegex(ValueError, 'bind inode'):
                node.check_bind(first.stat(), second.stat())
            node.check_bind(first.stat(), first.stat())


class DiskUsage(unittest.TestCase):
    def test_missing_du_reading_aborts(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with patch.object(node.sp, 'check_output', return_value=''):
                with self.assertRaisesRegex(ValueError, 'du reading missing'):
                    node.disk_usage(root, 5)

    def test_bytes_and_file_count_are_kept(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            calls = []

            def fake(argv, **kwargs):
                calls.append(list(argv))
                return '4096\t'+str(root) if argv[0] == 'du' else b'a\0b\0c\0'

            with patch.object(node.sp, 'check_output', side_effect=fake):
                value = node.disk_usage(root, 5)
            self.assertEqual(value, {'path': str(root), 'du_bytes': 4096, 'files': 3})
            self.assertEqual(calls[0][:4], ['du', '-sx', '-B1', str(root)])
            self.assertIn('-xdev', calls[1])


class GcExecution(unittest.TestCase):
    def test_run_gc_executes_only_the_pinned_argv(self):
        seen = []

        class FakeProcess:
            def __init__(self, argv, **kwargs):
                seen.append(list(argv))
                self.returncode = 0

            def wait(self, timeout=None):
                return 0

            def kill(self):
                raise AssertionError('a completed gc must not be killed')

        with tempfile.TemporaryDirectory() as tmp:
            binary = Path(tmp)/'juicefs'
            binary.write_bytes(b'pinned-build-stand-in')
            out = Path(tmp)/'gc'
            with patch.object(node, 'sha256_file', return_value=node.JUICEFS_SHA256), \
                    patch.object(node.sp, 'Popen', FakeProcess), \
                    patch.object(node, 'juicefs_processes', return_value=[]), \
                    patch.object(node, 'established_redis_sockets', return_value=[]):
                node.run_gc(out, META, str(binary))
            self.assertEqual(seen, [[str(binary), 'gc', '--threads', '1', META]])
            exit_doc = json.loads((out/'gc-exit.json').read_text())
            self.assertEqual(exit_doc['argv'], [str(binary), 'gc', '--threads', '1', META])
            self.assertEqual(exit_doc['threads'], 1)
            self.assertEqual(exit_doc['exit_code'], 0)
            self.assertIs(exit_doc['timed_out'], False)
            self.assertEqual(exit_doc['forbidden_flags'], [])
            self.assertTrue((out/'gc.stdout').is_file())
            self.assertTrue((out/'gc.stderr').is_file())
            self.assertTrue(json.loads((out/'gc-post.json').read_text())['passed'])


class LedgerInput(unittest.TestCase):
    def test_original_ledger_is_pinned_and_left_byte_identical(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger = Path(tmp)
            for name in ('client.created.json', 'metadata.created.json', 'objects.created.json'):
                (ledger/name).write_text('{"role":"x"}\n')
            before = {path.name: path.read_bytes() for path in ledger.iterdir()}
            pins = apply_mod.pin_ledger(ledger)
            self.assertEqual(set(pins), {'client.created.json', 'metadata.created.json',
                                         'objects.created.json'})
            self.assertEqual({path.name: path.read_bytes() for path in ledger.iterdir()}, before)
            self.assertEqual(pins['objects.created.json']['path'], str(ledger/'objects.created.json'))

    def test_incomplete_ledger_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(ValueError, 'creation receipt'):
                apply_mod.pin_ledger(Path(tmp))


class ControllerLock(unittest.TestCase):
    def test_second_holder_raises_and_releases(self):
        with tempfile.TemporaryDirectory() as tmp:
            control = Path(tmp)/'control'
            control.mkdir()
            ids = apply_mod.resource_ids(example_allowlist())
            with apply_mod.locked(REPO, control, ids):
                with self.assertRaisesRegex(ValueError, 'owns physical resource'):
                    with apply_mod.locked(REPO, control, ids):
                        self.fail('the second lock must not be granted')
            with apply_mod.locked(REPO, control, ids):
                pass

    def test_lock_contention_performs_no_cloud_call(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            control = root/'control'
            control.mkdir()
            request = example_request(root)
            # The frozen allowlist is not on this test machine; inject the same shape.
            allowlist = example_allowlist()
            options = dict(repo=REPO, control=control, ssh={'binary': 'ssh', 'config': str(root/'ssh.conf')},
                           start_deadline_seconds=5, ready_deadline_seconds=60,
                           gc_timeout_seconds=60, eof_grace_seconds=0, allowlist=allowlist)
            fake = FakeCloud()
            ids = apply_mod.resource_ids(allowlist)
            with apply_mod.locked(REPO, control, ids):
                with self.assertRaisesRegex(ValueError, 'owns physical resource'):
                    apply_mod.apply(request, {'path': str(root/'request.json'), 'sha256': '0'*64},
                                    root/'out', fake, options['ssh'], options)
            self.assertEqual(fake.calls, [])


class ControllerClientSafety(unittest.TestCase):
    def test_only_objects_and_metadata_are_started(self):
        fake = FakeCloud()
        starts = apply_mod.start_guarded(fake)
        self.assertEqual(set(starts), {'objects', 'metadata'})
        self.assertEqual(fake.started, [apply_mod.INSTANCES['objects'], apply_mod.INSTANCES['metadata']])
        self.assertNotIn(apply_mod.CLIENT, fake.started)
        with self.assertRaisesRegex(ValueError, 'Only objects and metadata'):
            apply_mod.start_instance(fake, 'client')

    def test_start_aborts_if_client_is_running(self):
        fake = FakeCloud()
        fake.status[apply_mod.CLIENT] = 'Running'
        fake.stopped[apply_mod.CLIENT] = None
        with self.assertRaisesRegex(ValueError, 'must stay Stopped/StopCharging'):
            apply_mod.start_guarded(fake)
        self.assertEqual(fake.started, [])

    def test_client_leaving_stopcharging_after_a_start_aborts(self):
        class Leaky(FakeCloud):
            def api(self, action, **params):
                value = super().api(action, **params)
                if action == 'StartInstance':
                    self.status[apply_mod.CLIENT] = 'Running'
                    self.stopped[apply_mod.CLIENT] = None
                return value

        fake = Leaky()
        with self.assertRaisesRegex(ValueError, 'must stay Stopped/StopCharging'):
            apply_mod.start_guarded(fake)
        self.assertEqual(fake.started, [apply_mod.INSTANCES['objects']])

    def test_request_must_be_one_shot_and_bound_to_the_original_pool(self):
        with tempfile.TemporaryDirectory() as tmp:
            request = example_request(Path(tmp))
            with self.assertRaisesRegex(ValueError, 'automatic_retry'):
                apply_mod.validate_request(dict(request, automatic_retry=True))
            tampered = json.loads(json.dumps(request))
            tampered['instances']['client'] += '-other'
            with self.assertRaisesRegex(ValueError, 'Exact Pool B'):
                apply_mod.validate_request(tampered)
            tampered = json.loads(json.dumps(request))
            tampered['meta_url'] = 'redis://10.20.2.162:6379/62'
            with self.assertRaisesRegex(ValueError, 'metadata URL'):
                apply_mod.validate_request(tampered)
            tampered = json.loads(json.dumps(request))
            tampered['juicefs']['sha256'] = '0'*64
            with self.assertRaisesRegex(ValueError, 'JuiceFS'):
                apply_mod.validate_request(tampered)

    def test_full_apply_returns_both_instances_to_stopcharging(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            request = example_request(root)
            allowlist = example_allowlist()
            ledger = root/'ledger'
            ledger.mkdir()
            for name in ('client.created.json', 'metadata.created.json', 'objects.created.json'):
                (ledger/name).write_text('{"preexisting": true}\n')
            options = dict(repo=REPO, control=root/'control', ssh={'binary': 'ssh', 'config': str(root/'ssh.conf')},
                           start_deadline_seconds=5, ready_deadline_seconds=60,
                           gc_timeout_seconds=60, eof_grace_seconds=0, allowlist=allowlist,
                           ledger=apply_mod.pin_ledger(ledger))
            (root/'control').mkdir()
            fake = FakeCloud()
            with patch.object(apply_mod, 'drive_nodes', return_value={'faked': True}):
                result = apply_mod.apply(request, {'path': str(root/'request.json'), 'sha256': '0'*64},
                                         root/'out', fake, options['ssh'], options)
            self.assertTrue(result['passed'])
            self.assertEqual(fake.started, [apply_mod.INSTANCES['objects'], apply_mod.INSTANCES['metadata']])
            for instance in (apply_mod.INSTANCES['objects'], apply_mod.INSTANCES['metadata'],
                             apply_mod.CLIENT):
                self.assertEqual(fake.status[instance], 'Stopped')
                self.assertEqual(fake.stopped[instance], 'StopCharging')
            self.assertTrue((root/'out'/'intent.json').is_file())
            self.assertTrue((root/'out'/'result.json').is_file())
            intent = json.loads((root/'out'/'intent.json').read_text())
            self.assertIs(intent['automatic_retry'], False)
            self.assertEqual(intent['never_start'], apply_mod.CLIENT)
            self.assertEqual(set(intent['inputs']['ledger']),
                             {'client.created.json', 'metadata.created.json', 'objects.created.json'})
            events = json.loads((root/'out'/'cost'/'events.json').read_text())
            self.assertEqual(events['schema'], 'ax-pilot-cost-events-v1')
            self.assertIs(events['reviewed'], True)
            self.assertEqual(len(events['events']), 4)
            self.assertEqual({row['kind'] for row in events['events']}, {'rate-change'})
            self.assertEqual({row['resource_id'] for row in events['events']},
                             {apply_mod.INSTANCES['objects'], apply_mod.INSTANCES['metadata']})
            for name in ('running-rate.json', 'stopped-rate.json'):
                value = json.loads((root/'out'/'cost'/name).read_text())
                self.assertTrue(value['passed'])
                self.assertEqual(set(value['roles']), {'objects', 'metadata'})
                for role in ('objects', 'metadata'):
                    proof = value['roles'][role]
                    self.assertEqual(proof['schema'], 'ax-pilot-stopcharging-cost-rate-evidence-v1')
                    self.assertEqual(proof['resource_id'], apply_mod.INSTANCES[role])
                    self.assertEqual(proof['quote'], request['quote'])
                    self.assertIs(proof['configuration_verified'], True)

    def test_failed_attempt_is_recorded_and_still_stops(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            request = example_request(root)
            allowlist = example_allowlist()
            (root/'control').mkdir()
            options = dict(repo=REPO, control=root/'control', ssh={'binary': 'ssh', 'config': str(root/'ssh.conf')},
                           start_deadline_seconds=5, ready_deadline_seconds=60,
                           gc_timeout_seconds=60, eof_grace_seconds=0, allowlist=allowlist)
            fake = FakeCloud()
            with patch.object(apply_mod, 'drive_nodes', side_effect=ValueError('gc failed')):
                with self.assertRaisesRegex(ValueError, 'gc failed'):
                    apply_mod.apply(request, {'path': str(root/'request.json'), 'sha256': '0'*64},
                                    root/'out', fake, options['ssh'], options)
            self.assertTrue((root/'out'/'failed.json').is_file())
            self.assertFalse((root/'out'/'result.json').exists())
            self.assertEqual(fake.started, [apply_mod.INSTANCES['objects'], apply_mod.INSTANCES['metadata']])
            for instance in (apply_mod.INSTANCES['objects'], apply_mod.INSTANCES['metadata']):
                self.assertEqual(fake.status[instance], 'Stopped')
                self.assertEqual(fake.stopped[instance], 'StopCharging')


if __name__ == '__main__':
    unittest.main()
