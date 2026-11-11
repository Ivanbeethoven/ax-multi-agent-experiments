"""Offline guard rails for the C4 observer scratch setup helper.

No network, no host probe, no real block device: run/disks/host are replaced.
"""
from copy import deepcopy
import hashlib
import importlib.util
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

FILE = Path(__file__).resolve().parents[1]/'experiments/unified-suite/pilot-aliyun/scripts/prepare-c4-observer-scratch.py'
spec = importlib.util.spec_from_file_location('prepare_c4_observer_scratch', FILE)
module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)

GIB = 1024**3
SYSTEM = 'd-bp1h9kaoya0predx6kvc'
SERIAL = 'bp1h9kaoya0predx6kvh'
DISK = 'd-'+SERIAL
ORIGINAL = {'d-bp1h9kaoya0predx6kv'+c for c in 'defg'}
AT = '2026-11-12T00:00:00+00:00'


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def layout(scratch_serial=SERIAL, system_size=40*GIB):
    """The four original 60GiB ext4 data disks, the system disk, one blank scratch."""
    rows = [dict(name='/dev/vda', type='disk', size=system_size, serial=SYSTEM[2:], fstype='ext4',
                 uuid='system-uuid', mountpoints=[None], children=[dict(name='/dev/vda1', type='part')])]
    for index, letter in enumerate('defg'):
        rows.append(dict(name='/dev/vd'+'bcde'[index], type='disk', size=60*GIB,
                         serial='bp1h9kaoya0predx6kv'+letter, fstype='ext4', uuid='uuid-'+letter, mountpoints=[None]))
    if scratch_serial is not None:
        rows.append(dict(name='/dev/vdf', type='disk', size=100*GIB, serial=scratch_serial,
                         fstype=None, uuid=None, mountpoints=[None]))
    return rows


class DiskChoice(unittest.TestCase):
    def setUp(self):
        self.owner = dict(serial=SERIAL, disk_id=DISK)

    def test_selects_the_blank_100gib_whole_disk(self):
        self.assertEqual(module.choose_disk(self.owner, layout(), True)['name'], '/dev/vdf')

    def test_refuses_ambiguous_serial(self):
        rows = layout(); rows.append(dict(name='/dev/vdg', type='disk', size=100*GIB, serial=SERIAL))
        with self.assertRaises(ValueError): module.choose_disk(self.owner, rows, True)

    def test_refuses_prepared_signature(self):
        for field, value in [('children', [dict(name='/dev/vdf1', type='part')]), ('size', 99*GIB),
                             ('fstype', 'ext4'), ('uuid', 'old-uuid'), ('mountpoints', ['/srv/other'])]:
            rows = deepcopy(layout()); scratch = rows[-1]; scratch[field] = value
            with self.subTest(field=field), self.assertRaises(ValueError): module.choose_disk(self.owner, rows, True)

    def test_refuses_the_system_disk(self):
        rows = [row for row in layout() if row['serial'] != SERIAL]
        system = rows[0]; system.pop('children'); system['size'] = 100*GIB
        with self.assertRaises(ValueError):
            module.choose_disk(dict(serial=SYSTEM[2:], disk_id=SYSTEM), rows, True)

    def test_refuses_an_original_60gib_disk_that_changed(self):
        for field, value in [('size', 61*GIB), ('fstype', None)]:
            rows = deepcopy(layout()); rows[1][field] = value
            with self.subTest(field=field), self.assertRaises(ValueError): module.choose_disk(self.owner, rows, True)


class Ownership(unittest.TestCase):
    def setUp(self):
        self.dir = TemporaryDirectory(); self.addCleanup(self.dir.cleanup)
        self.base = Path(self.dir.name)
        self.pins = []
        for name in ('plan.json', 'ownership.json'):
            path = self.base/name; path.write_text(name, encoding='utf-8')
            self.pins.append(dict(path=str(path), sha256=digest(path)))

    def receipt(self, **change):
        value = dict(schema='c4-observer-scratch-ownership-v1', passed=True, applied=True, pool='pool-a',
            role='objects', instance_id='i-bp1h9kaoya0predubdkn', disk_id=DISK, serial=SERIAL, size_gib=100,
            category='cloud_essd', performance_level='PL0', zone='cn-hangzhou-j', status='In_use',
            delete_with_instance=True, at=AT, original_disk_ids=sorted(ORIGINAL|{SYSTEM}), hourly_cny=0.1,
            source_pins=deepcopy(self.pins))
        value.update(change); return value

    def accept(self, value, not_before=None):
        path = self.base/'receipt.json'; path.write_text(json.dumps(value), encoding='utf-8')
        return module.ownership(path, digest(path), not_before)

    def test_accepts_producer_shape(self):
        self.assertEqual(self.accept(self.receipt())['disk_id'], DISK)

    def test_accepts_legacy_spelling(self):
        value = self.receipt(); value.pop('delete_with_instance'); value['DeleteWithInstance'] = True
        value.pop('original_disk_ids'); value['original_four_ids'] = sorted(ORIGINAL)
        self.assertEqual(self.accept(value)['serial'], SERIAL)

    def test_refuses_wrong_original_disk_sets(self):
        full = sorted(ORIGINAL|{SYSTEM})
        cases = dict(missing_one=[x for x in full if x != 'd-bp1h9kaoya0predx6kvg'],
                     unknown=full+['d-bp1h9kaoya0predx6kvz'],
                     no_system=sorted(ORIGINAL),
                     scratch_is_system=full)
        for name, owned in cases.items():
            value = self.receipt(original_disk_ids=owned)
            if name == 'scratch_is_system': value.update(disk_id=SYSTEM, serial=SYSTEM[2:])
            with self.subTest(name=name), self.assertRaises(ValueError): self.accept(value)

    def test_refuses_missing_delete_flag_or_disk_identity(self):
        for change in (dict(delete_with_instance=False), dict(disk_id='d-elsewhere'), dict(size_gib=60),
                       dict(performance_level='PL1'), dict(passed=False)):
            value = self.receipt(**change)
            with self.subTest(change=change), self.assertRaises(ValueError): self.accept(value)

    def test_refuses_changed_source_pin(self):
        value = self.receipt()
        value['source_pins'][0]['sha256'] = '0'*64
        with self.assertRaises(ValueError): self.accept(value)

    def test_refuses_missing_or_empty_source_pin(self):
        missing = self.receipt()
        missing['source_pins'][1]['path'] = str(self.base/'absent.json')
        for value in (missing, self.receipt(source_pins=[])):
            with self.subTest(value=value['source_pins']), self.assertRaises(ValueError): self.accept(value)

    def test_not_before_is_enforced(self):
        value = self.receipt()
        self.accept(value, module.instant(AT))
        with self.assertRaises(ValueError): self.accept(value, module.instant('2026-11-12T00:00:01+00:00'))
        self.accept(value, module.instant('2026-11-11T00:00:00Z'))

    def test_receipt_timestamp_must_parse(self):
        for at in ('', 'yesterday', '2026-13-01T00:00:00+00:00'):
            value = self.receipt(at=at)
            with self.subTest(at=at), self.assertRaises(ValueError): self.accept(value)
        value = self.receipt(); value.pop('at')
        with self.assertRaises(ValueError): self.accept(value)


class Snapshot(unittest.TestCase):
    def test_exposes_top_level_identity(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()/'scratch'; root.mkdir()
            for name in ('o','q','archives','evidence'): (root/name).mkdir()
            owner_sha = 'a'*64
            stored = dict(schema='c4-observer-scratch-storage-v1', passed=True, applied=True, root=str(root),
                ownership_sha256=owner_sha, disk_id=DISK, serial=SERIAL, uuid='fs-uuid',
                device=root.stat().st_dev, boot_id=module.BOOT, source='/dev/vdf', automatic_retry=False,
                original_four_ids=sorted(ORIGINAL), at=AT)
            (root/'storage.json').write_text(json.dumps(stored), encoding='utf-8')
            owner = dict(serial=SERIAL, disk_id=DISK)
            row = dict(name='/dev/vdf', uuid='fs-uuid', size=100*GIB)
            receipt = root/'storage.json'
            with patch.object(module, 'ROOT', root), patch.object(module, 'host'), \
                    patch.object(module, 'device_uuid', return_value='fs-uuid'), \
                    patch.object(module, 'mounted', return_value=(row, {'uuid': 'fs-uuid'})):
                result = module.snapshot(owner, owner_sha, 'c4-a-flat-on1')
            expected = dict(path=str(receipt), sha256=digest(receipt))
        for key in ('root','disk_id','serial','uuid','device','boot_id','ownership_sha256','worker_receipt'):
            self.assertIn(key, result)
        self.assertEqual((result['disk_id'], result['serial'], result['uuid'], result['device']),
                         (DISK, SERIAL, 'fs-uuid', stored['device']))
        self.assertEqual(result['worker_receipt'], expected)
        self.assertEqual(result['disk']['disk_id'], DISK)


class Apply(unittest.TestCase):
    """The apply sequence must never leave a formatted or mounted disk without a receipt."""

    def test_failure_writes_false_receipt_and_unmounts(self):
        calls = []

        def fake_run(argv, codes=(0,)):
            calls.append(list(argv))
            if argv[0] == 'mount': raise module.CommandError(argv, 32, 'mount: /srv/ax-c4-observer-scratch: failed')
            return ''

        row = dict(name='/dev/vdf', type='disk', size=100*GIB, serial=SERIAL, uuid='fs-uuid',
                   fstype='ext4', mountpoints=[None])
        with TemporaryDirectory() as tmp, \
                patch.object(module, 'ROOT', Path(tmp).resolve()/'scratch'), \
                patch.object(module, 'run', fake_run), \
                patch.object(module, 'host'), \
                patch.object(module, 'inspect', return_value=(dict(row), [])), \
                patch.object(module, 'mounted', return_value=(row, {})), \
                patch.object(module, 'wait_identity', side_effect=lambda row, seconds=15: row), \
                patch.object(module.os, 'geteuid', return_value=0):
            out = Path(tmp)/'receipts'; out.mkdir()
            with self.assertRaises(module.CommandError):
                module.install(dict(serial=SERIAL, disk_id=DISK), out, 'a'*64)
            record = json.loads((out/'result.json').read_bytes())
            root = module.ROOT
            self.assertFalse((root/'storage.json').exists())

        self.assertFalse(record['passed'])
        self.assertTrue(record['applied'])
        self.assertIn('mount', record['error'])
        self.assertIn('failed', record['stderr'])
        self.assertTrue(record['unmounted'])
        self.assertIn('mkfs.ext4', [c[0] for c in record['commands']])
        self.assertIn('mount', [c[0] for c in record['commands']])
        self.assertIn('umount', [c[0] for c in calls])
        self.assertEqual(record['disk_id'], DISK)

    def test_non_root_never_formats(self):
        calls = []

        def fake_run(argv, codes=(0,)):
            calls.append(list(argv)); return ''

        with TemporaryDirectory() as tmp, \
                patch.object(module, 'ROOT', Path(tmp).resolve()/'scratch'), \
                patch.object(module, 'run', fake_run), \
                patch.object(module.os, 'geteuid', return_value=1000):
            out = Path(tmp)/'receipts'; out.mkdir()
            with self.assertRaises(ValueError):
                module.install(dict(serial=SERIAL, disk_id=DISK), out, 'a'*64)
            record = json.loads((out/'result.json').read_bytes())
        self.assertEqual(calls, [])
        self.assertFalse(record['applied'])
        self.assertIn('Root required', record['error'])

    def test_refusal_before_mount_never_unmounts(self):
        calls = []

        def fake_run(argv, codes=(0,)):
            calls.append(list(argv)); return ''

        with TemporaryDirectory() as tmp, \
                patch.object(module, 'ROOT', Path(tmp).resolve()/'scratch'), \
                patch.object(module, 'run', fake_run), \
                patch.object(module, 'host'), \
                patch.object(module, 'inspect', side_effect=ValueError('Fresh scratch mount path required')), \
                patch.object(module.os, 'geteuid', return_value=0):
            out = Path(tmp)/'receipts'; out.mkdir()
            with self.assertRaises(ValueError):
                module.install(dict(serial=SERIAL, disk_id=DISK), out, 'a'*64)
            record = json.loads((out/'result.json').read_bytes())
        self.assertEqual(calls, [])
        self.assertFalse(record['applied'])
        self.assertIsNone(record['unmounted'])

    def test_mkfs_arguments_match_provision_node(self):
        self.assertEqual(module.MKFS, ['mkfs.ext4','-q','-m','0','-i','4096','-L','ax-c4-observer'])


class UdevRace(unittest.TestCase):
    """mkfs returns before udev/lsblk expose the new identity; never trust the stale row."""

    def test_waits_for_the_new_ext4_uuid(self):
        state = {'blkid': 0, 'disks': 0}

        def fake_run(argv, codes=(0,)):
            if argv[0] == 'udevadm':
                return ''
            if argv[0] == 'blkid':
                state['blkid'] += 1
                return '' if state['blkid'] == 1 else 'TYPE=ext4\nUUID=fs-uuid\n'
            raise AssertionError(argv)

        stale = dict(name='/dev/vdf', type='disk', size=100*GIB, serial=SERIAL, fstype=None, uuid=None)
        settled = dict(stale, fstype='ext4', uuid='fs-uuid')

        def fake_disks():
            state['disks'] += 1
            return [stale if state['disks'] == 1 else settled]

        with patch.object(module, 'run', fake_run), patch.object(module, 'disks', fake_disks), \
                patch.object(module.time, 'sleep', lambda seconds: None):
            row = module.wait_identity(dict(stale))
        self.assertEqual((row['fstype'], row['uuid']), ('ext4', 'fs-uuid'))
        self.assertGreaterEqual(state['blkid'], 2)

    def test_missing_identity_never_returns_a_stale_row(self):
        def fake_run(argv, codes=(0,)):
            if argv[0] == 'udevadm':
                return ''
            if argv[0] == 'blkid':
                return ''
            raise AssertionError(argv)

        stale = dict(name='/dev/vdf', type='disk', size=100*GIB, serial=SERIAL, fstype=None, uuid=None)
        with patch.object(module, 'run', fake_run), patch.object(module, 'disks', lambda: [stale]), \
                patch.object(module.time, 'sleep', lambda seconds: None), \
                patch.object(module.time, 'monotonic', side_effect=[0, 0, 1, 30]):
            with self.assertRaises(ValueError):
                module.wait_identity(dict(stale))


if __name__ == '__main__':
    unittest.main()
