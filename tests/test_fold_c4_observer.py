import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest import mock

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


def write_tree(root, count=3, step=5_000_000_000, payload=b'host-stat payload', extra=None):
    root.mkdir(parents=True, exist_ok=True)
    history = []
    for index in range(count):
        start = 1_000_000_000+index*step
        directory = root/('sample-%06d' % index)
        directory.mkdir()
        bodies = {'host-stat.txt': payload, 'cgroup-cpu.stat.txt': b'usage %d\n' % index}
        if extra:
            bodies.update(extra)
        record = dict(index=index, monotonic_start_ns=start, monotonic_end_ns=start+1_000_000,
                      passed=True, errors=[], capacity={'/srv/x': {'bytes_free': 1, 'inodes_free': 1}},
                      counters={'t.requests': index}, target_states={'t': {'state': 'live'}},
                      files={})
        for name, body in bodies.items():
            record['files'][name] = {'sha256': sha(body), 'bytes': len(body)}
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
            after = sorted(p.name for p in root.iterdir() if p.is_file())
            self.assertEqual(after, ['finished.json', 'fold-receipt.json', 'fold-receipt.ndjson',
                                     'samples.ndjson'])
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


BLOBS = {'blob-a.bin': b'a'*4096, 'blob-b.bin': b'b'*4096, 'blob-c.bin': b'c'*4096}
MEMORY_CHILD = '''\
import importlib.util, json, resource, sys
from pathlib import Path
spec = importlib.util.spec_from_file_location("fold_c4_observer", sys.argv[1])
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
receipt = module.fold(Path(sys.argv[2]))
peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
print(json.dumps({"peak_kb": peak, "samples": receipt["samples"]}))
'''


class Streaming(unittest.TestCase):
    def test_peak_memory_does_not_grow_with_samples(self):
        def peak_kb(count):
            with TemporaryDirectory() as tmp:
                root = write_tree(Path(tmp)/'o'/'tag', count=count, extra=BLOBS)
                done = subprocess.run([sys.executable, '-c', MEMORY_CHILD, str(SCRIPT), str(root)],
                                      capture_output=True, text=True)
                self.assertEqual(done.returncode, 0, done.stderr)
                result = json.loads(done.stdout)
                self.assertEqual(result['samples'], count)
                return result['peak_kb']

        small = peak_kb(200)
        large = peak_kb(2_000)
        detail = 'peak 200 samples=%d KB, peak 2000 samples=%d KB' % (small, large)
        # Same process shape, same per-sample payloads: a streaming fold keeps
        # the high-water mark flat instead of growing with the series length.
        self.assertLess(large, 200*1024, detail)
        self.assertLess(large, small*2, detail)
        self.assertLess(large-small, 8*1024, detail)

    def test_receipt_is_streamed_and_matches_the_stream(self):
        with TemporaryDirectory() as tmp:
            root = write_tree(Path(tmp)/'o'/'tag', count=4)
            module.fold(root)
            receipt = json.loads((root/'fold-receipt.json').read_text())
            self.assertNotIn('records', receipt)
            self.assertEqual(receipt['samples'], 4)
            self.assertEqual(receipt['detail'], {'name': 'fold-receipt.ndjson', 'lines': 4})
            self.assertEqual(receipt['stream']['name'], 'samples.ndjson')
            self.assertEqual(receipt['stream']['bytes'], (root/'samples.ndjson').stat().st_size)
            self.assertEqual(receipt['stream']['sha256'], sha((root/'samples.ndjson').read_bytes()))
            lines = (root/'samples.ndjson').read_bytes().splitlines()
            detail = [json.loads(line) for line in
                      (root/'fold-receipt.ndjson').read_text().splitlines()]
            self.assertEqual(len(detail), 4)
            finished = json.loads((root/'finished.json').read_text())
            for index, (line, entry) in enumerate(zip(lines, detail)):
                self.assertEqual(entry['index'], index)
                self.assertEqual(entry['directory'], 'sample-%06d' % index)
                self.assertEqual(entry['line_sha256'], sha(line))
                self.assertEqual(entry['line_bytes'], len(line))
                row = json.loads(line)
                self.assertEqual(entry['members'],
                                 {name: value['sha256'] for name, value in row['files'].items()})
                self.assertEqual(finished['history'][index]['line_sha256'], entry['line_sha256'])
                self.assertEqual(finished['history'][index]['line_bytes'], entry['line_bytes'])
                self.assertNotIn('target_states', finished['history'][index])
                self.assertNotIn('counters', finished['history'][index])

    def test_stale_staging_directory_is_removed(self):
        with TemporaryDirectory() as tmp:
            root = write_tree(Path(tmp)/'o'/'tag', count=2)
            staging = root/'.fold-staging'
            staging.mkdir()
            (staging/'leftover.ndjson').write_bytes(b'stale'*256)
            receipt = module.fold(root)
            self.assertTrue(receipt['stale_staging_removed'])
            self.assertFalse(staging.exists())
            self.assertFalse((root/'.fold-staging').exists())
            self.assertEqual([p.name for p in root.iterdir() if p.is_dir()], [])


class FailureAtomicity(unittest.TestCase):
    def test_read_failure_keeps_tree_and_writes_no_receipt(self):
        with TemporaryDirectory() as tmp:
            root = write_tree(Path(tmp)/'o'/'tag', count=4)
            finished_before = (root/'finished.json').read_bytes()
            samples_before = sorted(p.name for p in root.glob('sample-*'))
            original = Path.read_bytes
            observed = {}

            def explode(path):
                if path.parent.name == 'sample-000002' and path.name == 'host-stat.txt':
                    with (root/'samples.ndjson').open('rb') as sink:
                        observed['partial'] = sink.read()
                    with (root/'fold-receipt.ndjson').open('rb') as sink:
                        observed['detail'] = sink.read()
                    raise OSError('injected read failure at the third sample')
                return original(path)

            with mock.patch.object(Path, 'read_bytes', explode):
                with self.assertRaises(OSError) as caught:
                    module.fold(root)
            self.assertIn('injected read failure', str(caught.exception))
            # The failure really happened mid-write: two complete samples were
            # already appended before the read of the third one blew up.
            self.assertEqual(len(observed['partial'].splitlines()), 2)
            self.assertTrue(observed['partial'].endswith(b'\n'))
            self.assertEqual(len(observed['detail'].splitlines()), 2)
            self.assertEqual((root/'finished.json').read_bytes(), finished_before)
            self.assertEqual(sorted(p.name for p in root.glob('sample-*')), samples_before)
            self.assertFalse((root/'samples.ndjson').exists())
            self.assertFalse((root/'fold-receipt.ndjson').exists())
            self.assertFalse((root/'fold-receipt.json').exists())
            self.assertFalse((root/'.fold-staging').exists())


if __name__ == '__main__':
    unittest.main()
