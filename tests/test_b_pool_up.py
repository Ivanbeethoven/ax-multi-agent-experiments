"""Offline regression tests for b-pool-up.py.

Same shape as test_a_pool_up.py: the B machine is a recorded fake. Nothing here
mounts a real disk, reads the real /proc, or talks to lsblk, ss, systemctl,
docker or redis. Only subprocess.run, a few Path predicates, os.kill and
time.sleep are patched; the bringup functions themselves are never mocked.
The fake /proc is reached by patching the module constant PROC.
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
from contextlib import ExitStack, redirect_stdout
from pathlib import Path
from unittest.mock import patch


SCRIPT = Path(__file__).resolve().parents[1]/'experiments/unified-suite/pilot-aliyun/scripts/b-pool-up.py'
SPEC = importlib.util.spec_from_file_location('b_pool_up', SCRIPT)
pool = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(pool)

REDIS_IP = pool.METADATA['redis_ip']
REDIS_CONF = pool.METADATA['redis_conf']
REDIS_UNIT = pool.METADATA['redis_unit']
METADATA_SERIAL = pool.METADATA['disks'][0][0]
CLIENT_SERIAL = pool.CLIENT['disks'][0][0]
B_CONTAINER = 'ax-pilot-20261111-b-pool-b-objects-01-rustfs-1'

EXPECTED_OBJECT_DISKS = [
    ('bp1bhdngko7nq3c2i340', '/srv/ax-objects-0'),
    ('bp1bhdngko7nq3c2i341', '/srv/ax-objects-1'),
    ('bp1bhdngko7nq3c2i342', '/srv/ax-objects-2'),
    ('bp1bhdngko7nq3c2i343', '/srv/ax-objects-3'),
]


def answer(stdout='', returncode=0, stderr=''):
    return subprocess.CompletedProcess([], returncode, stdout, stderr)


def disk_row(serial, name, fstype='ext4', uuid=None, type_='disk', children=None):
    row = dict(name=name, type=type_, serial=serial, size=1,
               fstype=fstype, uuid=(serial+'-uuid' if uuid is None else uuid))
    if children is not None:
        row['children'] = children
    return row


class FakeMachine:
    """Records every command and serves the state transition each one causes."""

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

    def enter(self, patcher):
        patcher.start()
        self.addCleanup(patcher.stop)

    def mount_calls(self):
        return [call for call in self.fake.calls if call[0] == 'mount']

    def starts(self):
        return [call for call in self.fake.calls if call[:2] == ['systemctl', 'start']]

    def stops(self):
        return [call for call in self.fake.calls if call[:2] == ['systemctl', 'stop']]


class ObjectsEnvMixin:
    """Points OBJECTS at throwaway directories and serves matching lsblk rows."""

    def install_objects(self, docker_running=True):
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
        env['disks'] = disks
        self.fake.blocks = rows
        self.fake.docker_running = docker_running
        self.enter(patch.object(pool, 'OBJECTS', env))
        return disks, env


class TableTest(PoolTest):
    def test_four_object_disks_map_serials_one_to_one_onto_mountpoints(self):
        disks = pool.OBJECTS['disks']
        self.assertEqual(len(disks), 4)
        self.assertEqual(disks, EXPECTED_OBJECT_DISKS)
        self.assertEqual(len({serial for serial, _target in disks}), 4)
        self.assertEqual(len({target for _serial, target in disks}), 4)

    def test_metadata_table_carries_the_b_redis_configuration(self):
        self.assertEqual(pool.METADATA['redis_ip'], '10.20.2.169')
        self.assertEqual(pool.METADATA['redis_conf'], '/etc/redis/ax-pilot-20261111-b.conf')
        self.assertTrue(pool.METADATA['redis_conf'].endswith('-b.conf'))
        self.assertEqual(pool.METADATA['redis_unit'], 'ax-pilot-redis.service')
        self.assertEqual(pool.METADATA['disks'], [(METADATA_SERIAL, '/srv/ax-metadata')])

    def test_client_table_holds_exactly_one_hundred_gib_data_disk(self):
        self.assertEqual(len(pool.CLIENT['disks']), 1)
        self.assertEqual(pool.CLIENT['disks'], [(CLIENT_SERIAL, '/srv/ax-client')])

    def test_object_container_name_is_the_b_pool_container(self):
        self.assertEqual(pool.OBJECTS['container'], B_CONTAINER)
        self.assertNotEqual(pool.OBJECTS['container'],
                            'ax-pilot-20261111-objects-01-rustfs-1')

    def test_the_three_roles_are_distinct_b_hosts(self):
        hosts = [pool.OBJECTS['hostname'], pool.METADATA['hostname'], pool.CLIENT['hostname']]
        instances = [pool.OBJECTS['instance'], pool.METADATA['instance'], pool.CLIENT['instance']]
        self.assertEqual(len(set(hosts)), 3)
        self.assertEqual(len(set(instances)), 3)
        for host in hosts:
            self.assertTrue(host.startswith('ax-pilot-20261111-b-'), host)
        self.assertEqual([host.rsplit('-', 1)[-1] for host in hosts],
                         ['objects', 'metadata', 'client'])


class MountDiskTest(PoolTest):
    def test_already_mounted_is_recorded_without_a_mount_call(self):
        self.fake.blocks = [disk_row('serial-0', '/dev/sdb')]
        self.fake.mounted.add('/srv/ax-objects-0')
        actions = []
        pool.mount_disk('serial-0', '/srv/ax-objects-0', actions)
        self.assertEqual(actions, [dict(serial='serial-0', target='/srv/ax-objects-0',
                                        already_mounted=True)])
        self.assertEqual(self.mount_calls(), [])
        self.assertEqual([event for event in self.fake.events if event[0] == 'run'
                          and event[1][0] == 'mount'], [])

    def test_unmounted_ext4_is_mounted_rw_noatime_and_rechecked(self):
        self.fake.blocks = [disk_row('serial-0', '/dev/sdb')]
        target = self.root/'objects-0'
        actions = []
        row = pool.mount_disk('serial-0', str(target), actions)
        self.assertEqual(row['name'], '/dev/sdb')
        self.assertTrue(target.is_dir())                 # created before mount
        self.assertEqual(self.mount_calls(),
                         [['mount', '-t', 'ext4', '-o', 'rw,noatime', '/dev/sdb', str(target)]])
        self.assertEqual(self.fake.events[-1][1],
                         ['mount', '-t', 'ext4', '-o', 'rw,noatime', '/dev/sdb', str(target)])
        self.assertIn(str(target), self.fake.mounted)
        self.assertEqual(actions, [dict(serial='serial-0', target=str(target),
                                        device='/dev/sdb', uuid='serial-0-uuid')])
        self.assertEqual(self.fake.sleeps, [])

    def test_mount_that_does_not_take_is_refused_after_the_recheck(self):
        self.fake.blocks = [disk_row('serial-0', '/dev/sdb')]
        self.fake.mount_takes = False
        actions = []
        with self.assertRaisesRegex(ValueError, 'mount did not take'):
            pool.mount_disk('serial-0', str(self.root/'objects-0'), actions)
        self.assertEqual(len(self.mount_calls()), 1)
        self.assertEqual(actions, [])                    # nothing recorded as done

    def test_non_ext4_disk_is_refused(self):
        self.fake.blocks = [disk_row('serial-0', '/dev/sdb', fstype='xfs')]
        with self.assertRaisesRegex(ValueError, 'original ext4 identity required: serial-0'):
            pool.mount_disk('serial-0', str(self.root/'objects-0'), [])
        self.assertEqual(self.mount_calls(), [])

    def test_disk_without_filesystem_uuid_is_refused(self):
        self.fake.blocks = [disk_row('serial-0', '/dev/sdb', uuid='')]
        actions = []
        with self.assertRaisesRegex(ValueError, 'original ext4 identity required: serial-0'):
            pool.mount_disk('serial-0', str(self.root/'objects-0'), actions)
        self.assertEqual(self.mount_calls(), [])
        self.assertEqual(actions, [])


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
        self.assertEqual(self.fake.calls[0][0], 'lsblk')


class BringObjectsTest(ObjectsEnvMixin, PoolTest):
    def test_four_ext4_mounts_then_four_binds_then_the_b_container(self):
        disks, _env = self.install_objects()
        actions = []
        pool.bring_objects(actions)

        ext4 = [index for index, call in enumerate(self.fake.calls)
                if call[:3] == ['mount', '-t', 'ext4']]
        binds = [index for index, call in enumerate(self.fake.calls)
                 if call[:2] == ['mount', '--bind']]
        starts = [index for index, call in enumerate(self.fake.calls)
                  if call[:2] == ['docker', 'start']]
        inspects = [index for index, call in enumerate(self.fake.calls)
                    if call[1:2] == ['inspect']]
        self.assertEqual(len(ext4), 4)
        self.assertEqual(len(binds), 4)
        self.assertEqual(len(starts), 1)
        self.assertTrue(inspects)
        self.assertLess(max(ext4), min(binds))
        self.assertLess(max(binds), min(starts))
        self.assertLess(min(starts), min(inspects))

        self.assertEqual([self.fake.calls[index][-1] for index in ext4],
                         [str(target) for _serial, target in disks])
        self.assertEqual([self.fake.calls[index][1:4] for index in ext4],
                         [['-t', 'ext4', '-o'] for _index in range(4)])
        self.assertEqual([self.fake.calls[index][2:] for index in binds],
                         [[str(Path(target)/'rustfs-data'),
                           '/srv/ax-rustfs-data-%d' % position]
                          for position, (_serial, target) in enumerate(disks)])
        self.assertEqual(self.fake.bind_mkdirs,
                         ['/srv/ax-rustfs-data-%d' % position for position in range(4)])
        self.assertEqual(self.fake.calls[starts[0]], ['docker', 'start', B_CONTAINER])
        self.assertEqual(actions[-1], dict(container=B_CONTAINER, started=True))
        self.assertEqual(self.fake.sleeps, [])           # first inspect already Running
        disk_actions = [row for row in actions if 'serial' in row]
        bind_actions = [row for row in actions if 'bind' in row]
        self.assertEqual(len(disk_actions), 4)
        self.assertEqual(len(bind_actions), 4)
        self.assertLess(max(actions.index(row) for row in disk_actions),
                        min(actions.index(row) for row in bind_actions))

    def test_already_mounted_bind_is_not_recreated_nor_remounted(self):
        self.install_objects()
        self.fake.mounted.add('/srv/ax-rustfs-data-0')
        actions = []
        pool.bring_objects(actions)
        binds = [call for call in self.fake.calls if call[:2] == ['mount', '--bind']]
        self.assertEqual([call[3] for call in binds],
                         ['/srv/ax-rustfs-data-%d' % index for index in (1, 2, 3)])
        self.assertEqual(self.fake.bind_mkdirs,
                         ['/srv/ax-rustfs-data-%d' % index for index in (1, 2, 3)])

    def test_missing_rustfs_data_dir_is_refused_before_any_bind_or_docker(self):
        env = dict(pool.OBJECTS)
        base = self.root/'objects'
        base.mkdir(exist_ok=True)
        disks = []
        rows = []
        for index, (serial, _target) in enumerate(pool.OBJECTS['disks']):
            target = base/('bare-%d' % index)          # no rustfs-data inside
            target.mkdir()
            disks.append((serial, str(target)))
            rows.append(disk_row(serial, '/dev/sd%s' % chr(ord('b')+index)))
        env['disks'] = disks
        self.fake.blocks = rows
        self.enter(patch.object(pool, 'OBJECTS', env))
        with self.assertRaisesRegex(ValueError, 'original RustFS data dir missing'):
            pool.bring_objects([])
        self.assertEqual([call for call in self.fake.calls if call[:2] == ['mount', '--bind']], [])
        self.assertEqual([call for call in self.fake.calls if call[0] == 'docker'], [])

    def test_container_not_running_after_sixty_polls_is_refused(self):
        self.install_objects(docker_running=False)
        actions = []
        with self.assertRaisesRegex(ValueError, 'rustfs container did not start'):
            pool.bring_objects(actions)
        self.assertEqual(len([call for call in self.fake.calls if call[1:2] == ['inspect']]), 60)
        self.assertEqual(self.fake.sleeps, [1]*60)
        self.assertEqual(len([call for call in self.fake.calls
                              if call[:2] == ['docker', 'start']]), 1)
        self.assertEqual([row['bind'] for row in actions if 'bind' in row],
                         ['/srv/ax-rustfs-data-%d' % index for index in range(4)])
        self.assertFalse(any('started' in row for row in actions))


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
            # Not redis-server at all.
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
        self.assertNotIn(10, by_pid)

    def test_original_config_with_extra_arguments_still_counts_as_foreign(self):
        proc = self.root/'proc'
        (proc/'20').mkdir(parents=True)
        (proc/'20'/'cmdline').write_bytes(
            b'/usr/bin/redis-server\0' + REDIS_CONF.encode() + b'\0--port\0' + b'6379\0')
        found = pool.foreign_redis_pids(REDIS_CONF)
        self.assertEqual([row['pid'] for row in found], [20])
        self.assertEqual(found[0]['argv'][1], REDIS_CONF)


class ListeningTest(PoolTest):
    def test_line_ending_in_users_suffix_still_counts_as_listening(self):
        self.fake.port_held = True
        line = self.fake.ss_text().strip()
        self.assertTrue(line.endswith('users:(("redis-server",pid=1,fd=6))'))
        self.assertTrue(pool.listening())
        self.assertEqual(self.fake.calls, [['ss', '-H', '-ltn']])

    def test_closed_port_is_not_listening(self):
        self.fake.port_held = False
        self.assertFalse(pool.listening())

    def test_a_different_port_is_not_mistaken_for_6379(self):
        self.fake.ss_override = ('LISTEN 0 511 10.20.2.169:16379 0.0.0.0:* '
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
                       'appendonlyx\nyes\n',
                       ''):
            with self.subTest(output=output):
                self.fake.redis_raw = lambda argv, output=output: output
                with self.assertRaisesRegex(ValueError,
                                            'unexpected CONFIG GET answer: appendonly'):
                    pool.effective('appendonly')


class BringMetadataTest(PoolTest):
    def metadata(self, port_held=False, unit='inactive'):
        self.fake.blocks = [disk_row(METADATA_SERIAL, '/dev/vdb')]
        self.fake.mounted.add('/srv/ax-metadata')
        self.fake.redis = dict(appendonly='yes', databases='256',
                               dir='/srv/ax-metadata/redis',
                               bind='127.0.0.1 %s' % REDIS_IP)
        self.fake.systemctl = {REDIS_UNIT: (0, 'active') if unit == 'active' else (3, 'inactive')}
        self.fake.port_held = port_held
        proc = self.root/'proc'
        (proc/'777').mkdir(parents=True)
        (proc/'777'/'cmdline').write_bytes(b'/usr/bin/redis-server\0/etc/redis/redis.conf\0')

    def test_port_taken_by_image_unit_stops_then_kills_then_waits_then_starts(self):
        self.metadata(port_held=True)
        self.fake.systemctl['redis-server.service'] = (0, 'active')
        self.fake.systemctl['redis.service'] = (3, 'inactive')
        actions = []
        pool.bring_metadata(actions)

        self.assertEqual(self.stops(), [['systemctl', 'stop', 'redis-server.service']])
        self.assertEqual(self.fake.kills, [(777, 15)])
        self.assertFalse(self.fake.port_held)
        self.assertEqual(self.starts(), [['systemctl', 'start', REDIS_UNIT]])
        self.assertEqual(self.fake.sleeps, [])

        stop_at = [index for index, event in enumerate(self.fake.events)
                   if event[0] == 'run' and event[1][:2] == ['systemctl', 'stop']]
        kill_at = [index for index, event in enumerate(self.fake.events) if event[0] == 'kill']
        start_at = [index for index, event in enumerate(self.fake.events)
                    if event[0] == 'run' and event[1][:2] == ['systemctl', 'start']]
        self.assertTrue(stop_at and kill_at and start_at)
        self.assertLess(max(stop_at), min(kill_at))
        self.assertLess(max(kill_at), min(start_at))

        self.assertEqual(actions[0], dict(serial=METADATA_SERIAL, target='/srv/ax-metadata',
                                          already_mounted=True))
        self.assertEqual(actions[1], dict(stopped_unit='redis-server.service'))
        self.assertTrue(any('terminated' in row for row in actions))
        self.assertIn(dict(started_unit=REDIS_UNIT), actions)
        self.assertEqual(actions[-1],
                         dict(effective=dict(appendonly='yes', databases='256',
                                             dir='/srv/ax-metadata/redis',
                                             bind='127.0.0.1 %s' % REDIS_IP)))
        # The effective check runs only after the original unit is started.
        first_redis_cli = self.fake.calls.index(['redis-cli', '-h', REDIS_IP,
                                                 'CONFIG', 'GET', 'appendonly'])
        first_start = self.fake.calls.index(['systemctl', 'start', REDIS_UNIT])
        self.assertLess(first_start, first_redis_cli)

    def test_port_that_stays_held_fails_without_starting_the_original_unit(self):
        self.metadata(port_held=True)
        self.fake.release_on_kill = False
        self.fake.systemctl['redis-server.service'] = (0, 'active')
        actions = []
        with self.assertRaisesRegex(ValueError, 'port 6379 still held'):
            pool.bring_metadata(actions)
        self.assertEqual(self.fake.sleeps, [1]*30)
        self.assertEqual(self.starts(), [])
        self.assertEqual(self.fake.kills, [(777, 15)])
        self.assertIn(dict(stopped_unit='redis-server.service'), actions)
        self.assertTrue(any('terminated' in row for row in actions))
        self.assertFalse(any('started_unit' in row for row in actions))
        self.assertFalse(any('effective' in row for row in actions))
        self.assertEqual([call for call in self.fake.calls if call[0] == 'redis-cli'], [])

    def test_effective_configuration_mismatch_times_out_with_last_value(self):
        self.metadata(unit='active')
        self.fake.redis['databases'] = '16'
        actions = []
        with self.assertRaises(ValueError) as caught:
            pool.bring_metadata(actions)
        message = str(caught.exception)
        self.assertIn('original Redis did not serve', message)
        self.assertIn('databases', message)
        self.assertIn('16', message)
        self.assertEqual(self.fake.sleeps, [1]*60)
        self.assertEqual(len([call for call in self.fake.calls if call[0] == 'redis-cli']), 60*4)
        self.assertEqual(self.starts(), [])
        self.assertEqual(self.stops(), [])
        self.assertEqual(self.fake.kills, [])
        self.assertFalse(any('effective' in row for row in actions))

    def test_free_port_starts_the_original_unit_without_killing_anything(self):
        self.metadata(port_held=False, unit='inactive')
        actions = []
        pool.bring_metadata(actions)
        self.assertEqual(self.starts(), [['systemctl', 'start', REDIS_UNIT]])
        self.assertEqual(self.stops(), [])
        self.assertEqual(self.fake.kills, [])
        self.assertEqual(self.fake.sleeps, [])
        self.assertEqual(actions[0], dict(serial=METADATA_SERIAL, target='/srv/ax-metadata',
                                          already_mounted=True))
        self.assertEqual(actions[1], dict(started_unit=REDIS_UNIT))

    def test_active_original_unit_is_left_alone(self):
        self.metadata(port_held=True, unit='active')
        actions = []
        pool.bring_metadata(actions)
        self.assertEqual(self.starts(), [])
        self.assertEqual(self.stops(), [])
        self.assertEqual(self.fake.kills, [])
        self.assertTrue(any('effective' in row for row in actions))


class MainTest(ObjectsEnvMixin, PoolTest):
    def identity(self, euid, nodename):
        self.enter(patch.object(pool.os, 'geteuid', return_value=euid))
        self.enter(patch.object(pool.os, 'uname',
                                return_value=types.SimpleNamespace(nodename=nodename)))

    def args(self, role='client', out=None):
        out = out or (self.root/'out')
        self.enter(patch.object(sys, 'argv', ['b-pool-up.py', role, '--out', str(out)]))
        return out

    def client(self):
        env = dict(pool.CLIENT)
        target = self.root/'client-disk'
        env['disks'] = [(CLIENT_SERIAL, str(target))]
        self.enter(patch.object(pool, 'CLIENT', env))
        self.fake.blocks = [disk_row(CLIENT_SERIAL, '/dev/vdc')]
        return target

    def test_non_root_is_refused_before_touching_anything(self):
        self.identity(1, pool.CLIENT['hostname'])
        out = self.args()
        with self.assertRaisesRegex(ValueError, 'wrong B host'):
            pool.main()
        self.assertEqual(self.fake.calls, [])
        self.assertFalse(out.exists())

    def test_wrong_hostname_is_refused_for_every_role(self):
        cases = (('objects', pool.METADATA['hostname']),
                 ('metadata', pool.CLIENT['hostname']),
                 ('client', pool.OBJECTS['hostname']))
        for role, nodename in cases:
            with self.subTest(role=role):
                self.identity(0, nodename)
                out = self.args(role=role, out=self.root/('out-'+role))
                with self.assertRaisesRegex(ValueError, 'wrong B host'):
                    pool.main()
                self.assertFalse(out.exists())
        self.assertEqual(self.fake.calls, [])

    def test_existing_output_directory_is_refused(self):
        self.identity(0, pool.CLIENT['hostname'])
        self.client()
        out = self.args()
        out.mkdir()
        with self.assertRaisesRegex(ValueError, 'fresh receipt directory required'):
            pool.main()
        self.assertEqual(self.mount_calls(), [])
        self.assertEqual(list(out.iterdir()), [])

    def test_successful_objects_bringup_writes_the_receipt(self):
        self.identity(0, pool.OBJECTS['hostname'])
        disks, env = self.install_objects()
        out = self.args(role='objects')
        with redirect_stdout(io.StringIO()):
            pool.main()
        result = json.loads((out/'result.json').read_text())
        self.assertEqual(result['schema'], 'b-pool-bringup-v1')
        self.assertIs(result['passed'], True)
        self.assertIs(result['automatic_retry'], False)
        self.assertEqual(result['role'], 'objects')
        self.assertEqual(result['instance'], env['instance'])
        self.assertIn('at', result)
        self.assertEqual(result['actions'][-1],
                         dict(container=env['container'], started=True))
        self.assertEqual([row for row in result['actions'] if 'serial' in row],
                         [dict(serial=serial, target=target,
                               device='/dev/sd%s' % chr(ord('b')+index),
                               uuid=serial+'-uuid')
                          for index, (serial, target) in enumerate(disks)])
        self.assertEqual([row['bind'] for row in result['actions'] if 'bind' in row],
                         ['/srv/ax-rustfs-data-%d' % index for index in range(4)])
        self.assertIn(['docker', 'start', B_CONTAINER], self.fake.calls)

    def test_successful_client_bringup_mounts_only_the_single_client_disk(self):
        self.identity(0, pool.CLIENT['hostname'])
        target = self.client()
        out = self.args()
        with redirect_stdout(io.StringIO()):
            pool.main()
        result = json.loads((out/'result.json').read_text())
        self.assertEqual(result['schema'], 'b-pool-bringup-v1')
        self.assertIs(result['passed'], True)
        self.assertIs(result['automatic_retry'], False)
        self.assertEqual(result['role'], 'client')
        self.assertEqual(result['instance'], pool.CLIENT['instance'])
        self.assertEqual(result['actions'],
                         [dict(serial=CLIENT_SERIAL, target=str(target),
                               device='/dev/vdc', uuid=CLIENT_SERIAL+'-uuid')])
        self.assertEqual(self.mount_calls(),
                         [['mount', '-t', 'ext4', '-o', 'rw,noatime', '/dev/vdc', str(target)]])
        self.assertEqual([call for call in self.fake.calls if call[0] == 'docker'], [])
        self.assertEqual([call for call in self.fake.calls if call[:2] == ['mount', '--bind']], [])


if __name__ == '__main__':
    unittest.main()
