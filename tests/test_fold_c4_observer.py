import hashlib
import importlib.util
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from bench.runtime import observe_ndjson

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO/'experiments/unified-suite/pilot-aliyun/scripts/fold-c4-observer.py'
MARKS = ['measure-start', 'measure-end']


def load_module():
    spec = importlib.util.spec_from_file_location('fold_c4_observer', SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


module = load_module()


def sha(data):
    return hashlib.sha256(data).hexdigest()


def write_tree(root, count=3, step=5_000_000_000, payload=b'host-stat payload'):
    root.mkdir(parents=True, exist_ok=True)
    history = []
    for index in range(count):
        start = 1_000_000_000+index*step
        directory = root/('sample-%06d' % index)
        directory.mkdir()
        bodies = {'host-stat.txt': payload, 'cgroup-cpu.stat.txt': b'usage %d\n' % index,
                  'sample.json': b''}
        record = dict(index=index, monotonic_start_ns=start, monotonic_end_ns=start+1_000_000,
                      passed=True, errors=[], capacity={'/srv/x': {'bytes_free': 1, 'inodes_free': 1}},
                      counters={'t.requests': index}, target_states={'t': {'state': 'live'}},
                      files={})
        for name in ('host-stat.txt', 'cgroup-cpu.stat.txt'):
            record['files'][name] = {'sha256': sha(bodies[name]), 'bytes': len(bodies[name])}
        bodies['sample.json'] = json.dumps(record).encode()
        for name, body in bodies.items():
            (directory/name).write_bytes(body)
        history.append(dict(index=index, monotonic_start_ns=start, monotonic_end_ns=start+1_000_000,
                            passed=True, errors=[], sample_sha256=sha(bodies['sample.json'])))
    finished = dict(passed=True, samples=count, history=history,
                    marks=[{'label': 'measure-start', 'sample_index': 0, 'monotonic_ns': 2_000_000_000},
                           {'label': 'measure-end', 'sample_index': count-1,
                            'monotonic_ns': count*step+2_000_000_000}],
                    spec_sha256='0'*64, metric_contract_sha256='1'*64, target_states={})
    (root/'finished.json').write_text(json.dumps(finished))
    return root


class Fold(unittest.TestCase):
    def test_members_collapse_and_the_audit_still_passes(self):
        with TemporaryDirectory() as tmp:
            root = write_tree(Path(tmp)/'o'/'tag', count=4)
            before = len([p for p in root.rglob('*') if p.is_file()])
            self.assertEqual(before, 4*3+1)
            receipt = module.fold(root)
            after = len([p for p in root.rglob('*') if p.is_file()])
            self.assertEqual(after, 3)
            self.assertEqual(receipt['samples'], 4)
            self.assertEqual(receipt['source_members'], 12)
            result = observe_ndjson.audit(root, 30, MARKS)
            self.assertTrue(result['passed'])
            self.assertEqual(result['samples'], 4)

    def test_every_source_byte_survives(self):
        with TemporaryDirectory() as tmp:
            root = write_tree(Path(tmp)/'o'/'tag', count=2)
            originals = {}
            for sample in sorted(root.glob('sample-*')):
                for path in sample.iterdir():
                    originals[(sample.name, path.name)] = path.read_bytes()
            module.fold(root)
            lines = (root/'samples.ndjson').read_bytes().splitlines()
            seen = {}
            for line in lines:
                row = json.loads(line)
                for name, entry in row['files'].items():
                    seen[(row['index'], name)] = entry['text'].encode('utf-8', 'surrogateescape')
            for (sample, name), body in originals.items():
                index = int(sample.split('-')[1])
                self.assertEqual(seen[(index, name)], body)

    def test_sample_directories_are_removed(self):
        with TemporaryDirectory() as tmp:
            root = write_tree(Path(tmp)/'o'/'tag', count=3)
            module.fold(root)
            self.assertEqual(list(root.glob('sample-*')), [])
            self.assertTrue((root/'samples.ndjson').is_file())
            self.assertTrue((root/'fold-receipt.json').is_file())

    def test_finished_json_points_at_the_stream(self):
        with TemporaryDirectory() as tmp:
            root = write_tree(Path(tmp)/'o'/'tag', count=3)
            module.fold(root)
            finished = json.loads((root/'finished.json').read_text())
            data = (root/'samples.ndjson').read_bytes()
            self.assertEqual(finished['stream']['sha256'], sha(data))
            self.assertEqual(finished['stream']['bytes'], len(data))
            self.assertEqual(len(finished['history']), 3)
            for row in finished['history']:
                self.assertIn('line_sha256', row)
                self.assertNotIn('sample_sha256', row)


class DryRun(unittest.TestCase):
    def test_dry_run_verifies_without_touching_the_tree(self):
        with TemporaryDirectory() as tmp:
            root = write_tree(Path(tmp)/'o'/'tag', count=3)
            before = sorted(p.name for p in root.rglob('*'))
            receipt = module.fold(root, dry_run=True)
            self.assertTrue(receipt['dry_run'])
            self.assertEqual(receipt['samples'], 3)
            self.assertEqual(receipt['source_members'], 6)
            self.assertEqual(sorted(p.name for p in root.rglob('*')), before)
            self.assertFalse((root/'samples.ndjson').exists())

    def test_dry_run_still_refuses_a_changed_payload(self):
        with TemporaryDirectory() as tmp:
            root = write_tree(Path(tmp)/'o'/'tag', count=2)
            (root/'sample-000000'/'host-stat.txt').write_bytes(b'changed')
            with self.assertRaises(ValueError):
                module.fold(root, dry_run=True)


class Refusals(unittest.TestCase):
    def test_already_folded_is_refused(self):
        with TemporaryDirectory() as tmp:
            root = write_tree(Path(tmp)/'o'/'tag', count=2)
            module.fold(root)
            with self.assertRaises(ValueError):
                module.fold(root)

    def test_tampered_payload_is_refused_before_any_write(self):
        with TemporaryDirectory() as tmp:
            root = write_tree(Path(tmp)/'o'/'tag', count=2)
            (root/'sample-000001'/'host-stat.txt').write_bytes(b'tampered')
            with self.assertRaises(ValueError) as caught:
                module.fold(root)
            self.assertIn('payload bytes changed', str(caught.exception))
            self.assertFalse((root/'samples.ndjson').exists())
            self.assertEqual(len(list(root.glob('sample-*'))), 2)

    def test_non_contiguous_sequence_is_refused(self):
        with TemporaryDirectory() as tmp:
            root = write_tree(Path(tmp)/'o'/'tag', count=2)
            (root/'sample-000001').rename(root/'sample-000002')
            with self.assertRaises(ValueError) as caught:
                module.fold(root)
            self.assertIn('non-contiguous', str(caught.exception))

    def test_sample_count_mismatch_is_refused(self):
        with TemporaryDirectory() as tmp:
            root = write_tree(Path(tmp)/'o'/'tag', count=2)
            finished = json.loads((root/'finished.json').read_text())
            finished['samples'] = 5
            (root/'finished.json').write_text(json.dumps(finished))
            with self.assertRaises(ValueError) as caught:
                module.fold(root)
            self.assertIn('sample count', str(caught.exception))

    def test_missing_payload_is_refused(self):
        with TemporaryDirectory() as tmp:
            root = write_tree(Path(tmp)/'o'/'tag', count=2)
            (root/'sample-000000'/'cgroup-cpu.stat.txt').unlink()
            with self.assertRaises(ValueError) as caught:
                module.fold(root)
            self.assertIn('recorded payload missing', str(caught.exception))


if __name__ == '__main__':
    unittest.main()
