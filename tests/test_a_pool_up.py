"""Offline regression tests for a-pool-up.py.

No real disks are mounted, no /proc is read, and nothing here talks to ss,
systemctl, docker or redis. The whole machine is a recorded fake: subprocess,
the few Path predicates that touch /srv, os.kill and time.sleep are patched.
"""
import importlib.util
import io
import json
import subprocess
import sys
import tempfile
import time
import types
import unittest
from contextlib import ExitStack, redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch


SCRIPT = Path(__file__).resolve().parents[1]/'experiments/unified-suite/pilot-aliyun/scripts/a-pool-up.py'
SPEC = importlib.util.spec_from_file_location('a_pool_up', SCRIPT)
pool = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(pool)

REDIS_IP = pool.METADATA['redis_ip']
REDIS_CONF = pool.METADATA['redis_conf']
REDIS_UNIT = pool.METADATA['redis_unit']


def answer(stdout='', returncode=0, stderr=''):
    return subprocess.CompletedProcess([], returncode, stdout, stderr)


def disk_row(serial, name, fstype='ext4', uuid=None, type_='disk', children=None):
    row = dict(name=name, type=type_, serial=serial, size=1,
               fstype=fstype, uuid=(serial+'-uuid' if uuid is None else uuid))
    if children is not None:
        row['children'] = children
    return row


class FakeMachine:
    """Records every command and serves the state transitions each one causes."""

    def __init__(self):
        self.calls = []
        self.events = []
        self.mounted = set()
        self.blocks = []
        self.port_held = False
        self.ss_override = None
        self.systemctl = {}
        self.redis = {}
        self.redis_raw = None
        self.docker_running = True
        self.mount_takes = True
        self.sleeps = []
        self.kills = []
        self.bind_mkdirs = []
        self.release_on_kill = True

    def ss_text(self):
        if self.ss_override is not None:
            return self.ss_override
        if not self.port_held:
            return ''
        # The real ss line ends with users:(...), never with ':6379'.
        return ('LISTEN 0 511 %s:6379 0.0.0.0:* '
                'users:(("redis-server",pid=1,fd=6))\n' % REDIS_IP)

    def run(self, argv, **kwargs):
        argv = list(argv)
        self.calls.append(argv)
        self.events.append(('run', argv))
        name = argv[0]
        if name == 'lsblk':
            return answer(json.dumps({'blockdevices': self.blocks}))
        if name == 'ss':
            return answer(self.ss_text())
        if name == 'mount':
            if self.mount_takes:
                self.mounted.add(argv[-1])
            return answer()
        if name == 'docker':
            if argv[1] == 'start':
                return answer()
            if argv[1] == 'inspect':
                return answer('true' if self.docker_running else 'false')
            raise AssertionError('unexpected docker call: %r' % (argv,))
        if name == 'systemctl':
            action = argv[1]
            if action == 'is-active':
                returncode, stdout = self.systemctl.get(argv[2], (3, 'inactive'))
                return answer(stdout, returncode)
            if action in ('stop', 'start'):
                return answer()
            raise AssertionError('unexpected systemctl call: %r' % (argv,))
        if name == 'redis-cli':
            if self.redis_raw is not None:
                return answer(self.redis_raw(argv))
            key = argv[-1]
            return answer('%s\n%s\n' % (key, self.redis[key]))
        raise AssertionError('unexpected command: %r' % (argv,))

    def kill(self, pid, sig):
        self.events.append(('kill', pid))
        self.kills.append((pid, sig))
        if self.release_on_kill:
            self.port_held = False


class PoolTest(unittest.TestCase):
    def setUp(self):
        self.fake = FakeMachine()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

        fake = self.fake
        real_mkdir = Path.mkdir
        real_is_dir = Path.is_dir

        def mkdir(path, mode=0o777, parents=False, exist_ok=False):
            text = str(path)
            if text.startswith('/srv/ax-rustfs-data-'):
                fake.bind_mkdirs.append(text)
                return None
            return real_mkdir(path, mode=mode, parents=parents, exist_ok=exist_ok)

        def is_dir(path):
            if str(path) == '/srv/ax-metadata/redis':
                return True
            return real_is_dir(path)

        def is_mount(path):
            return str(path) in fake.mounted

        stack = ExitStack()
        self.addCleanup(stack.close)
        stack.enter_context(patch.object(pool.subprocess, 'run', side_effect=fake.run))
        stack.enter_context(patch.object(Path, 'is_mount', new=is_mount))
        stack.enter_context(patch.object(Path, 'mkdir', new=mkdir))
        stack.enter_context(patch.object(Path, 'is_dir', new=is_dir))
        stack.enter_context(patch.object(time, 'sleep',
                                         side_effect=lambda seconds: fake.sleeps.append(seconds)))
        stack.enter_context(patch.object(pool.os, 'kill', new=fake.kill))
        stack.enter_context(patch.object(pool, 'PROC', self.root/'proc'))


class MountDiskTest(PoolTest):
    def test_already_mounted_is_recorded_without_a_mount_call(self):
        self.fake.blocks = [disk_row('serial-0', '/dev/sdb')]
        self.fake.mounted.add('/srv/ax-objects-0')
        actions = []
        pool.mount_disk('serial-0', '/srv/ax-objects-0', actions)
        self.assertEqual(actions, [dict(serial='serial-0', target='/srv/ax-objects-0',
                                        already_mounted=True)])
        self.assertEqual([c for c in self.fake.calls if c[0] == 'mount'], [])

    def test_unmounted_ext4_is_mounted_read_only_atime_and_rechecked(self):
        self.fake.blocks = [disk_row('serial-0', '/dev/sdb')]
        target = self.root/'objects-0'
        actions = []
        row = pool.mount_disk('serial-0', str(target), actions)
        self.assertEqual(row['name'], '/dev/sdb')
        self.assertEqual([c for c in self.fake.calls if c[0] == 'mount'],
                         [['mount', '-t', 'ext4', '-o', 'rw,noatime', '/dev/sdb', str(target)]])
        self.assertIn(str(target), self.fake.mounted)
        self.assertTrue(target.is_dir())
        self.assertEqual(actions, [dict(serial='serial-0', target=str(target),
                                        device='/dev/sdb', uuid='serial-0-uuid')])

    def test_mount_that_does_not_take_is_refused(self):
        self.fake.blocks = [disk_row('serial-0', '/dev/sdb')]
        self.fake.mount_takes = False
        with self.assertRaisesRegex(ValueError, 'mount did not take'):
            pool.mount_disk('serial-0', str(self.root/'objects-0'), [])
        self.assertEqual(len([c for c in self.fake.calls if c[0] == 'mount']), 1)

    def test_non_ext4_disk_is_refused(self):
        self.fake.blocks = [disk_row('serial-0', '/dev/sdb', fstype='xfs')]
        with self.assertRaisesRegex(ValueError, 'original ext4 identity required: serial-0'):
            pool.mount_disk('serial-0', str(self.root/'objects-0'), [])
        self.assertEqual([c for c in self.fake.calls if c[0] == 'mount'], [])

    def test_disk_without_filesystem_uuid_is_refused(self):
        self.fake.blocks = [disk_row('serial-0', '/dev/sdb', uuid='')]
        with self.assertRaisesRegex(ValueError, 'original ext4 identity required: serial-0'):
            pool.mount_disk('serial-0', str(self.root/'objects-0'), [])


class WholeTest(PoolTest):
    def test_serial_matching_several_rows_is_refused(self):
        self.fake.blocks = [disk_row('serial-0', '/dev/sdb'),
                            disk_row('serial-0', '/dev/sdc')]
        with self.assertRaisesRegex(ValueError, 'original whole disk not unique: serial-0'):
            pool.whole('serial-0')

    def test_serial_matching_a_partition_is_refused(self):
        self.fake.blocks = [disk_row('serial-0', '/dev/sdb1', type_='part')]
        with self.assertRaisesRegex(ValueError, 'wrong topology: serial-0'):
            pool.whole('serial-0')

    def test_whole_disk_with_children_is_refused(self):
        self.fake.blocks = [disk_row('serial-0', '/dev/sdb',
                                     children=[dict(name='/dev/sdb1', type='part')])]
        with self.assertRaisesRegex(ValueError, 'wrong topology: serial-0'):
            pool.whole('serial-0')

    def test_unique_whole_ext4_disk_is_returned(self):
        row = disk_row('serial-0', '/dev/sdb')
        self.fake.blocks = [row]
        self.assertEqual(pool.whole('serial-0'), row)


class BringObjectsTest(PoolTest):
    def objects(self, docker_running=True):
        env = dict(pool.OBJECTS)
        base = self.root/'objects'
        base.mkdir(exist_ok=True)
        disks = []
        rows = []
        for index, (serial, _target) in enumerate(pool.OBJECTS['disks']):
            target = base/('disk-%d' % index)
            (target/'rustfs-data').mkdir(parents=True, exist_ok=True)
            disks.append((serial, str(target)))
            rows.append(disk_row(serial, '/dev/sd%s' % chr(ord('b')+index)))
        scratch_serial = pool.OBJECTS['scratch'][0]
        scratch_target = base/'scratch'
        scratch_target.mkdir()
        rows.append(disk_row(scratch_serial, '/dev/sdg'))
        env['disks'] = disks
        env['scratch'] = (scratch_serial, str(scratch_target))
        self.fake.blocks = rows
        self.fake.docker_running = docker_running
        self.enter(patch.object(pool, 'OBJECTS', env))
        return disks, scratch_target

    def enter(self, patcher):
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_missing_binds_are_created_and_mounted_in_order(self):
        disks, scratch_target = self.objects()
        actions = []
        pool.bring_objects(actions)
        binds = [c for c in self.fake.calls if c[:2] == ['mount', '--bind']]
        self.assertEqual([c[2] for c in binds],
                         [str(Path(target)/'rustfs-data') for _serial, target in disks])
        self.assertEqual([c[3] for c in binds],
                         ['/srv/ax-rustfs-data-%d' % index for index in range(4)])
        self.assertEqual(self.fake.bind_mkdirs,
                         ['/srv/ax-rustfs-data-%d' % index for index in range(4)])
        object_targets = {target for _serial, target in disks}
        disk_mounts = [index for index, c in enumerate(self.fake.calls)
                       if c[:3] == ['mount', '-t', 'ext4']
                       and c[-1] in object_targets]
        bind_mounts = [index for index, c in enumerate(self.fake.calls)
                       if c[:2] == ['mount', '--bind']]
        scratch_mount = [index for index, c in enumerate(self.fake.calls)
                         if c[0] == 'mount' and c[-1] == str(scratch_target)]
        self.assertEqual(len(disk_mounts), 4)
        self.assertLess(max(disk_mounts), min(bind_mounts))
        self.assertLess(max(bind_mounts), min(scratch_mount))
        self.assertIn(['docker', 'start', pool.OBJECTS['container']], self.fake.calls)
        self.assertEqual(actions[-1], dict(container=pool.OBJECTS['container'], started=True))

    def test_opt_in_brings_four_data_disks_up_with_observer_disk_absent(self):
        disks, scratch = self.objects()
        self.fake.blocks.pop()  # The fifth, legacy-only observer disk is detached.
        actions = []
        pool.bring_objects(actions, without_legacy_observer_scratch=True)
        disk_mounts = [c[-1] for c in self.fake.calls if c[:3] == ['mount', '-t', 'ext4']]
        self.assertEqual(disk_mounts, [target for _serial, target in disks])
        self.assertFalse(any(str(scratch) in c for c in self.fake.calls))
        self.assertEqual(len([c for c in self.fake.calls if c[:2] == ['mount', '--bind']]), 4)
        self.assertIn(dict(legacy_observer_scratch='skipped'), actions)
        self.assertEqual(actions[-1], dict(container=pool.OBJECTS['container'], started=True))

    def test_default_still_requires_the_historical_observer_disk(self):
        self.objects()
        self.fake.blocks.pop()
        with self.assertRaisesRegex(ValueError, 'original whole disk not unique: '+pool.OBJECTS['scratch'][0]):
            pool.bring_objects([])
        self.assertNotIn(['docker', 'start', pool.OBJECTS['container']], self.fake.calls)

    def test_already_mounted_bind_is_not_recreated_nor_remounted(self):
        self.objects()
        self.fake.mounted.add('/srv/ax-rustfs-data-0')
        actions = []
        pool.bring_objects(actions)
        binds = [c for c in self.fake.calls if c[:2] == ['mount', '--bind']]
        self.assertEqual([c[3] for c in binds],
                         ['/srv/ax-rustfs-data-%d' % index for index in (1, 2, 3)])
        self.assertEqual(self.fake.bind_mkdirs,
                         ['/srv/ax-rustfs-data-%d' % index for index in (1, 2, 3)])

    def test_container_not_running_after_sixty_polls_is_refused(self):
        self.objects(docker_running=False)
        with self.assertRaisesRegex(ValueError, 'rustfs container did not start'):
            pool.bring_objects([])
        self.assertEqual(len([c for c in self.fake.calls if c[1:2] == ['inspect']]), 60)
        self.assertEqual(self.fake.sleeps, [1]*60)


class ForeignRedisPidsTest(PoolTest):
    def test_only_non_original_redis_server_processes_are_selected(self):
        proc = self.root/'proc'
        argv_by_pid = {
            # The original unit: exact configuration path, must be ignored.
            '10': b'/usr/bin/redis-server\0'+REDIS_CONF.encode()+b'\0',
            # Someone else's configuration.
            '11': b'redis-server\0/etc/redis/redis.conf\0',
            # A redis-server started with no configuration argument at all.
            '12': b'/usr/bin/redis-server\0',
            # Not redis-server.
            '13': b'/usr/bin/python3\0worker.py\0',
            # Empty cmdline.
            '14': b'\0',
        }
        for pid, raw in argv_by_pid.items():
            (proc/pid).mkdir(parents=True)
            (proc/pid/'cmdline').write_bytes(raw)
        (proc/'self').mkdir()          # non-numeric: not a process
        (proc/'15').mkdir()            # vanished before cmdline could be read
        found = pool.foreign_redis_pids(REDIS_CONF)
        self.assertEqual(sorted(row['pid'] for row in found), [11, 12])
        by_pid = {row['pid']: row['argv'] for row in found}
        self.assertEqual(by_pid[11], ['redis-server', '/etc/redis/redis.conf'])
        self.assertEqual(by_pid[12], ['/usr/bin/redis-server'])


class ListeningTest(PoolTest):
    def test_line_with_users_suffix_still_counts_as_listening(self):
        self.fake.port_held = True
        line = self.fake.ss_text().strip()
        self.assertTrue(line.endswith('users:(("redis-server",pid=1,fd=6))'))
        self.assertTrue(pool.listening())

    def test_closed_port_is_not_listening(self):
        self.fake.port_held = False
        self.assertFalse(pool.listening())

    def test_a_different_port_is_not_mistaken_for_6379(self):
        self.fake.ss_override = ('LISTEN 0 511 10.20.2.162:16379 0.0.0.0:* '
                                 'users:(("redis-server",pid=1,fd=6))\n')
        self.assertFalse(pool.listening())


class EffectiveTest(PoolTest):
    def test_matching_two_line_answer_returns_the_value(self):
        self.fake.redis = {'appendonly': 'yes'}
        self.assertEqual(pool.effective('appendonly'), 'yes')
        self.assertEqual(self.fake.calls[-1],
                         ['redis-cli', '-h', REDIS_IP, 'CONFIG', 'GET', 'appendonly'])

    def test_malformed_answers_are_refused(self):
        for output in ('appendonly\n',
                       'appendonly\nyes\nextra\n',
                       'appendonlyx\nyes\n'):
            with self.subTest(output=output):
                self.fake.redis_raw = lambda argv, output=output: output
                with self.assertRaisesRegex(ValueError,
                                            'unexpected CONFIG GET answer: appendonly'):
                    pool.effective('appendonly')


class BringMetadataTest(PoolTest):
    def metadata(self):
        self.fake.blocks = [disk_row(METADATA_SERIAL, '/dev/vdb')]
        self.fake.mounted.add('/srv/ax-metadata')
        self.fake.redis = dict(appendonly='yes', databases='256',
                               dir='/srv/ax-metadata/redis',
                               bind='127.0.0.1 %s' % REDIS_IP)
        self.fake.systemctl = {REDIS_UNIT: (3, 'inactive')}
        proc = self.root/'proc'
        (proc/'777').mkdir(parents=True)
        (proc/'777'/'cmdline').write_bytes(b'/usr/bin/redis-server\0/etc/redis/redis.conf\0')

    def test_port_taken_by_image_unit_stops_then_kills_then_starts_original(self):
        self.metadata()
        self.fake.port_held = True
        self.fake.systemctl['redis-server.service'] = (0, 'active')
        self.fake.systemctl['redis.service'] = (3, 'inactive')
        actions = []
        pool.bring_metadata(actions)
        self.assertEqual([row for row in actions if 'stopped_unit' in row],
                         [dict(stopped_unit='redis-server.service')])
        self.assertEqual(self.fake.kills, [(777, 15)])
        self.assertFalse(self.fake.port_held)
        stops = [index for index, event in enumerate(self.fake.events)
                 if event[0] == 'run' and event[1][:2] == ['systemctl', 'stop']]
        kills = [index for index, event in enumerate(self.fake.events) if event[0] == 'kill']
        starts = [index for index, event in enumerate(self.fake.events)
                  if event[0] == 'run' and event[1][:2] == ['systemctl', 'start']]
        self.assertTrue(stops)
        self.assertTrue(kills)
        self.assertTrue(starts)
        self.assertLess(max(stops), min(kills))
        self.assertLess(max(kills), min(starts))
        self.assertEqual([event[1] for event in self.fake.events
                          if event[0] == 'run' and event[1][:2] == ['systemctl', 'start']],
                         [['systemctl', 'start', REDIS_UNIT]])
        self.assertIn(dict(stopped_unit='redis-server.service'), actions)
        self.assertTrue(any('terminated' in row for row in actions))
        self.assertIn(dict(started_unit=REDIS_UNIT), actions)
        self.assertEqual(actions[0],
                         dict(serial=METADATA_SERIAL, target='/srv/ax-metadata',
                              already_mounted=True))
        self.assertEqual(actions[-1],
                         dict(effective=dict(appendonly='yes', databases='256',
                                             dir='/srv/ax-metadata/redis',
                                             bind='127.0.0.1 %s' % REDIS_IP)))

    def test_port_that_stays_held_fails_without_starting_the_original_unit(self):
        self.metadata()
        self.fake.port_held = True
        self.fake.release_on_kill = False
        self.fake.systemctl['redis-server.service'] = (0, 'active')
        actions = []
        with self.assertRaisesRegex(ValueError, 'port 6379 still held'):
            pool.bring_metadata(actions)
        self.assertEqual(self.fake.sleeps, [1]*30)
        self.assertFalse([event for event in self.fake.events
                          if event[0] == 'run' and event[1][:2] == ['systemctl', 'start']])
        self.assertIn(dict(stopped_unit='redis-server.service'), actions)
        self.assertTrue(any('terminated' in row for row in actions))
        self.assertFalse(any('started_unit' in row for row in actions))

    def test_effective_configuration_mismatch_times_out_with_last_value(self):
        self.metadata()
        self.fake.systemctl[REDIS_UNIT] = (0, 'active')
        self.fake.redis['databases'] = '16'
        actions = []
        with self.assertRaises(ValueError) as caught:
            pool.bring_metadata(actions)
        message = str(caught.exception)
        self.assertIn('original Redis did not serve', message)
        self.assertIn('databases', message)
        self.assertIn('16', message)
        self.assertEqual(self.fake.sleeps, [1]*60)
        self.assertEqual(len([c for c in self.fake.calls if c[0] == 'redis-cli']), 60*4)
        self.assertFalse([event for event in self.fake.events
                          if event[0] == 'run' and event[1][:2] == ['systemctl', 'start']])
        self.assertFalse(any('effective' in row for row in actions))


class MainTest(PoolTest):
    def client(self):
        env = dict(pool.CLIENT)
        target = self.root/'client-disk'
        env['disks'] = [(pool.CLIENT['disks'][0][0], str(target))]
        self.enter(patch.object(pool, 'CLIENT', env))
        self.fake.blocks = [disk_row(env['disks'][0][0], '/dev/vdc')]
        return target

    def enter(self, patcher):
        patcher.start()
        self.addCleanup(patcher.stop)

    def args(self, role='client', out=None, *extra):
        out = out or (self.root/'out')
        self.enter(patch.object(sys, 'argv',
                                ['a-pool-up.py', role, '--out', str(out), *extra]))
        return out

    def identity(self, euid, nodename):
        self.enter(patch.object(pool.os, 'geteuid', return_value=euid))
        self.enter(patch.object(pool.os, 'uname',
                                return_value=types.SimpleNamespace(nodename=nodename)))

    def test_non_root_is_refused_before_touching_anything(self):
        self.identity(1, pool.CLIENT['hostname'])
        out = self.args()
        with self.assertRaisesRegex(ValueError, 'wrong A host'):
            pool.main()
        self.assertEqual(self.fake.calls, [])
        self.assertFalse(out.exists())

    def test_wrong_hostname_is_refused(self):
        self.identity(0, pool.OBJECTS['hostname'])
        self.args()
        with self.assertRaisesRegex(ValueError, 'wrong A host'):
            pool.main()
        self.assertEqual(self.fake.calls, [])

    def test_existing_output_directory_is_refused(self):
        self.identity(0, pool.CLIENT['hostname'])
        self.client()
        out = self.args()
        out.mkdir()
        with self.assertRaisesRegex(ValueError, 'fresh receipt directory required'):
            pool.main()
        self.assertEqual([c for c in self.fake.calls if c[0] == 'mount'], [])

    def test_skip_option_is_refused_for_client_before_any_actions(self):
        out = self.args('client', None, '--without-legacy-observer-scratch')
        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as error:
            pool.main()
        self.assertEqual(error.exception.code, 2)
        self.assertEqual(self.fake.calls, [])
        self.assertFalse(out.exists())

    def test_successful_client_bringup_writes_the_receipt(self):
        self.identity(0, pool.CLIENT['hostname'])
        target = self.client()
        out = self.args()
        with redirect_stdout(io.StringIO()):
            pool.main()
        result = json.loads((out/'result.json').read_text())
        self.assertEqual(result['schema'], 'a-pool-bringup-v1')
        self.assertIs(result['passed'], True)
        self.assertIs(result['automatic_retry'], False)
        self.assertEqual(result['role'], 'client')
        self.assertEqual(result['instance'], pool.CLIENT['instance'])
        self.assertEqual(result['actions'],
                         [dict(serial=pool.CLIENT['disks'][0][0], target=str(target),
                               device='/dev/vdc',
                               uuid=pool.CLIENT['disks'][0][0]+'-uuid')])
        self.assertIn('at', result)


METADATA_SERIAL = pool.METADATA['disks'][0][0]


if __name__ == '__main__':
    unittest.main()
