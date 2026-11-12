import importlib.util
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sys
import unittest

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO/'experiments/unified-suite/pilot-aliyun/scripts/c4-on1-preflight.py'


def load_module():
    spec = importlib.util.spec_from_file_location('c4_on1_preflight', SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


module = load_module()
NOW = datetime(2026, 11, 12, 1, 0, 0, tzinfo=timezone.utc)
SCRATCH = module.SCRATCH


def rows():
    return [
        dict(role='client', path='/srv/ax-client', quantity='bytes', data_growth=22, observer_raw=1653, archive_peak=165),
        dict(role='objects', path='/srv/ax-rustfs-data-0', quantity='bytes', data_growth=1562, observer_raw=48805, archive_peak=1084),
        dict(role='objects', path='/srv/ax-rustfs-data-0', quantity='inodes', data_growth=374989, observer_raw=315003, archive_peak=13),
        dict(role='objects', path='/srv/ax-rustfs-data-1', quantity='bytes', data_growth=1562, observer_raw=0, archive_peak=0),
        dict(role='metadata', path='/srv/ax-metadata', quantity='bytes', data_growth=11270, observer_raw=918, archive_peak=115),
    ]


class SplitRows(unittest.TestCase):
    def test_object_observer_moves_to_the_scratch_rows(self):
        kept, moved = module.split_rows(rows(), SCRATCH)
        self.assertEqual(len(moved), 2)
        self.assertEqual({row['quantity'] for row in moved}, {'bytes', 'inodes'})
        for row in moved:
            self.assertEqual(row['path'], SCRATCH)
            self.assertEqual(row['data_growth'], 0)
        by_key = {(row['role'], row['path'], row['quantity']): row for row in kept}
        observer_row = by_key[('objects', '/srv/ax-rustfs-data-0', 'bytes')]
        self.assertEqual(observer_row['observer_raw'], 0)
        self.assertEqual(observer_row['archive_peak'], 0)
        self.assertEqual(observer_row['data_growth'], 1562)
        self.assertEqual(by_key[('client', '/srv/ax-client', 'bytes')]['observer_raw'], 1653)

    def test_budget_total_is_preserved(self):
        merged, moved = module.split_rows(rows(), SCRATCH)
        before = sum(row['observer_raw'] for row in rows() if row['quantity'] == 'bytes')
        after = sum(row['observer_raw'] for row in merged if row['quantity'] == 'bytes')
        self.assertEqual(before, after)
        self.assertEqual(len(merged), len(rows())+2)

    def test_missing_object_rows_are_refused(self):
        with self.assertRaises(ValueError):
            module.split_rows(rows()[:1], SCRATCH)

    def test_incomplete_row_is_refused(self):
        broken = [dict(role='client', path='/srv/ax-client', quantity='bytes')]
        with self.assertRaises(ValueError):
            module.split_rows(broken, SCRATCH)


class Ordering(unittest.TestCase):
    def test_clean_sequence_passes(self):
        cleanup = NOW-timedelta(minutes=4)
        self.assertTrue(module.check_order(cleanup, cleanup+timedelta(minutes=1),
                                           cleanup+timedelta(minutes=2), cleanup+timedelta(minutes=3),
                                           now=NOW))

    def test_capacity_before_cleanup_is_refused(self):
        cleanup = NOW-timedelta(minutes=2)
        with self.assertRaises(ValueError):
            module.check_order(cleanup, cleanup, cleanup, cleanup-timedelta(minutes=1), now=NOW)

    def test_stale_evidence_is_refused(self):
        cleanup = NOW-timedelta(minutes=30)
        with self.assertRaises(ValueError):
            module.check_order(cleanup, cleanup+timedelta(minutes=1), cleanup+timedelta(minutes=2),
                               cleanup+timedelta(minutes=3), now=NOW)

    def test_future_evidence_is_refused(self):
        cleanup = NOW+timedelta(minutes=1)
        with self.assertRaises(ValueError):
            module.check_order(cleanup, cleanup, cleanup, cleanup, now=NOW)


class Coordinator(unittest.TestCase):
    def test_mixed_devices_are_refused(self):
        out = Path('/tmp/coord-'+str(abs(hash(str(NOW)))))/'continuation'
        out.parent.mkdir(parents=True, exist_ok=True)
        parent = str(out.parent.resolve())
        devices = {parent: 1, 'a': 1, 'b': 2}
        with self.assertRaises(ValueError):
            module.coordinator_block(out, ['a', 'b'], lambda path: devices[path])
        devices['b'] = 1
        block = module.coordinator_block(out, ['a', 'b'], lambda path: devices[path])
        self.assertEqual(block['device'], 1)

    def test_single_device_is_accepted(self):
        out = Path('/tmp/coord2-'+str(abs(hash(str(NOW)))))/'continuation'
        out.parent.mkdir(parents=True, exist_ok=True)
        block = module.coordinator_block(out, ['a', 'b'], lambda path: 7)
        self.assertEqual(block['path'], str(out.parent.resolve()))
        self.assertEqual(set(block['targets']), {'a', 'b'})
        self.assertEqual(block['targets']['a'], 7)


class Placement(unittest.TestCase):
    def snapshot(self):
        return dict(nodes={role: dict(boot_id='boot-'+role) for role in ('client', 'metadata', 'objects')})

    def scratch(self):
        return dict(root=SCRATCH, device=42, tag='c4-a-flat-on1')

    def probe(self, device):
        return lambda role, root: dict(boot_id='boot-'+role,
                                       trees={name: device for name in ('o', 'archives', 'evidence')})

    def test_placement_uses_the_scratch_root_for_objects(self):
        block = module.placement_block(self.snapshot(), self.scratch(), self.probe(42), dict(path='/srv/ax-pilot-control/fork-control-01/runtime-a-03/binding.json', sha256='0'*64), now=NOW)
        self.assertEqual(block['workers']['objects']['root'], SCRATCH)
        self.assertEqual(set(block['workers']['objects']['targets']), {SCRATCH+'/o', SCRATCH+'/archives', SCRATCH+'/evidence'})

    def test_boot_identity_change_is_refused(self):
        def probe(role, root):
            return dict(boot_id='other', trees={name: 42 for name in ('o', 'archives', 'evidence')})
        with self.assertRaises(ValueError):
            module.placement_block(self.snapshot(), self.scratch(), probe, dict(path='/srv/ax-pilot-control/fork-control-01/runtime-a-03/binding.json', sha256='0'*64), now=NOW)

    def test_wrong_scratch_device_is_refused(self):
        with self.assertRaises(ValueError):
            module.placement_block(self.snapshot(), self.scratch(), self.probe(99), dict(path='/srv/ax-pilot-control/fork-control-01/runtime-a-03/binding.json', sha256='0'*64), now=NOW)

    def test_foreign_scratch_root_is_refused(self):
        with self.assertRaises(ValueError):
            module.placement_block(self.snapshot(), dict(root='/srv/ax-objects-0', device=42, tag='t'),
                                   self.probe(42), dict(path='/srv/ax-pilot-control/fork-control-01/runtime-a-03/binding.json', sha256='0'*64), now=NOW)


class Documents(unittest.TestCase):
    def test_cleanup_requires_the_applied_reclaim(self):
        live = dict(left=[], newer=[], free_bytes=1, free_inodes=1)
        block = module.cleanup_block('pool-a', {'path': 'p', 'sha256': '0'*64}, 2, [], live,
                                     {'path': 'r', 'sha256': '1'*64}, now=NOW)
        self.assertEqual(block['outcome'], 'not-needed')
        self.assertEqual(block['actions'], [])
        with self.assertRaises(ValueError):
            module.cleanup_block('pool-a', {}, 2, [], dict(live, left=['x']), {}, now=NOW)

    def test_request_pins_capacity_and_overlay(self):
        draft = dict(original_request={'path': 'o', 'sha256': '0'*64})
        request_draft = dict(original_batch_tool={'path': 'b', 'sha256': '1'*64},
                             original_unit='u', original_invocation='i')
        overlay = dict(runner={'path': 'r', 'sha256': '2'*64}, helper={'path': 'h', 'sha256': '3'*64},
                       worker_code={'client': {}})
        doc = module.request_doc(draft, request_draft, {'path': 'c', 'sha256': '4'*64},
                                 {'path': 'cap', 'sha256': '5'*64}, '/srv/ax-pilot-control/fork-control-01/continuation-on1-01',
                                 overlay)
        self.assertEqual(doc['schema'], 'c4-after-directory-hold-v1')
        self.assertTrue(doc['reviewed'])
        self.assertEqual(doc['step'], 2)
        self.assertEqual(doc['prefix'], [])
        self.assertEqual(doc['capacity'], {'path': 'cap', 'sha256': '5'*64})
        self.assertEqual(doc['archive_overlay'], overlay)


if __name__ == '__main__':
    unittest.main()
