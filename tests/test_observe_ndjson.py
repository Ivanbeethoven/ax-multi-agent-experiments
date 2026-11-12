import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from bench.runtime import observe, observe_ndjson


def sha(data):
    return hashlib.sha256(data).hexdigest()


def record(index, start, end, text=b'host-stat'):
    name = 'host-stat.txt'
    return dict(index=index, monotonic_start_ns=start, monotonic_end_ns=end, passed=True, errors=[],
                capacity={'/srv/x': {'bytes_free': 10, 'inodes_free': 5}}, counters={},
                target_states={'t': {'state': 'live'}},
                files={name: {'sha256': sha(text), 'bytes': len(text),
                              'text': text.decode('utf-8', 'surrogateescape')}})


def write_legacy(root, count=3, step=5_000_000_000, text=b'host-stat'):
    root.mkdir(parents=True, exist_ok=True)
    history = []
    for index in range(count):
        start = 1_000_000_000+index*step
        directory = root/('sample-%06d' % index)
        directory.mkdir()
        (directory/'host-stat.txt').write_bytes(text)
        row = dict(record(index, start, start+1_000_000, text))
        row.pop('capacity'); row.pop('counters'); row.pop('target_states')
        row['files'] = {'host-stat.txt': {'sha256': sha(text), 'bytes': len(text)}}
        (directory/'sample.json').write_bytes(json.dumps(row).encode())
        history.append(dict(index=index, monotonic_start_ns=row['monotonic_start_ns'],
                            monotonic_end_ns=row['monotonic_end_ns'], passed=True, errors=[],
                            sample_sha256=sha((directory/'sample.json').read_bytes())))
    marks = [{'label': 'measure-start', 'sample_index': 0, 'monotonic_ns': 2_000_000_000},
             {'label': 'measure-end', 'sample_index': count-1, 'monotonic_ns': count*step+2_000_000_000}]
    finished = dict(passed=True, samples=count, history=history, marks=marks,
                    spec_sha256='0'*64, metric_contract_sha256='1'*64, target_states={})
    (root/'finished.json').write_text(json.dumps(finished))


def write_stream(root, count=3, step=5_000_000_000, text=b'host-stat'):
    root.mkdir(parents=True, exist_ok=True)
    history = []
    with (root/'samples.ndjson').open('wb') as stream:
        for index in range(count):
            start = 1_000_000_000+index*step
            line = json.dumps(record(index, start, start+1_000_000, text), sort_keys=True,
                              separators=(',', ':')).encode()
            stream.write(line+b'\n')
            history.append(dict(index=index, monotonic_start_ns=start, monotonic_end_ns=start+1_000_000,
                                passed=True, errors=[], line_sha256=sha(line), line_bytes=len(line)))
    data = (root/'samples.ndjson').read_bytes()
    marks = [{'label': 'measure-start', 'sample_index': 0, 'monotonic_ns': 2_000_000_000},
             {'label': 'measure-end', 'sample_index': count-1, 'monotonic_ns': count*step+2_000_000_000}]
    finished = dict(passed=True, samples=count, history=history, marks=marks,
                    spec_sha256='0'*64, metric_contract_sha256='1'*64, target_states={},
                    stream={'name': observe_ndjson.STREAM, 'sha256': sha(data), 'bytes': len(data)})
    (root/'finished.json').write_text(json.dumps(finished))


MARKS = ['measure-start', 'measure-end']


class Equivalence(unittest.TestCase):
    def test_both_formats_reach_the_same_verdict(self):
        with TemporaryDirectory() as tmp:
            legacy, stream = Path(tmp)/'legacy', Path(tmp)/'stream'
            write_legacy(legacy)
            write_stream(stream)
            old = observe.audit(legacy, 30, MARKS)
            new = observe_ndjson.audit(stream, 30, MARKS)
            self.assertEqual(old['samples'], new['samples'])
            self.assertEqual(old['passed'], new['passed'])
            self.assertEqual(old['max_gap_seconds'], new['max_gap_seconds'])
            self.assertEqual(old['metric_contract_sha256'], new['metric_contract_sha256'])

    def test_member_count_drops(self):
        with TemporaryDirectory() as tmp:
            legacy, stream = Path(tmp)/'legacy', Path(tmp)/'stream'
            write_legacy(legacy, count=50)
            write_stream(stream, count=50)
            self.assertGreater(len(list(legacy.rglob('*'))), 100)
            self.assertEqual(len([p for p in stream.rglob('*') if p.is_file()]), 2)


class DropIn(unittest.TestCase):
    def test_legacy_tree_is_audited_identically_through_the_new_module(self):
        with TemporaryDirectory() as tmp:
            legacy = Path(tmp)/'legacy'
            write_legacy(legacy)
            old = observe.audit(legacy, 30, MARKS)
            new = observe_ndjson.audit(legacy, 30, MARKS)
            self.assertEqual(old, new)

    def test_stream_tree_is_audited_through_the_same_entry_point(self):
        with TemporaryDirectory() as tmp:
            stream = Path(tmp)/'stream'
            write_stream(stream)
            result = observe_ndjson.audit(stream, 30, MARKS)
            self.assertEqual(result['samples'], 3)
            self.assertTrue(result['passed'])

    def test_broken_legacy_tree_still_fails_through_the_new_module(self):
        with TemporaryDirectory() as tmp:
            legacy = Path(tmp)/'legacy'
            write_legacy(legacy)
            (legacy/'sample-000001'/'host-stat.txt').write_bytes(b'changed!')
            with self.assertRaises(ValueError):
                observe_ndjson.audit(legacy, 30, MARKS)


class StreamIsTamperEvident(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.root = Path(self.tmp.name)/'stream'
        write_stream(self.root)
        self.stream = self.root/'samples.ndjson'

    def tearDown(self):
        self.tmp.cleanup()

    def rewrite(self, lines):
        self.stream.write_bytes(b''.join(line+b'\n' for line in lines))

    def finished(self):
        return json.loads((self.root/'finished.json').read_text())

    def repin(self):
        data = self.stream.read_bytes()
        finished = self.finished()
        finished['stream'] = {'name': observe_ndjson.STREAM, 'sha256': sha(data), 'bytes': len(data)}
        (self.root/'finished.json').write_text(json.dumps(finished))

    def test_modified_line_is_refused(self):
        lines = self.stream.read_bytes().splitlines()
        row = json.loads(lines[1])
        row['counters'] = {'tampered': 1}
        lines[1] = json.dumps(row, sort_keys=True, separators=(',', ':')).encode()
        self.rewrite(lines)
        self.repin()
        with self.assertRaises(ValueError) as caught:
            observe_ndjson.audit(self.root, 30, MARKS)
        self.assertIn('sample record changed', str(caught.exception))

    def test_altered_payload_text_is_refused(self):
        lines = self.stream.read_bytes().splitlines()
        row = json.loads(lines[0])
        row['files']['host-stat.txt']['text'] = 'host-staT'
        lines[0] = json.dumps(row, sort_keys=True, separators=(',', ':')).encode()
        self.rewrite(lines)
        finished = self.finished()
        finished['history'][0]['line_sha256'] = sha(lines[0])
        (self.root/'finished.json').write_text(json.dumps(finished))
        self.repin()
        with self.assertRaises(ValueError) as caught:
            observe_ndjson.audit(self.root, 30, MARKS)
        self.assertIn('telemetry bytes changed', str(caught.exception))

    def test_payload_length_mismatch_is_refused(self):
        lines = self.stream.read_bytes().splitlines()
        row = json.loads(lines[0])
        row['files']['host-stat.txt']['text'] = 'tampered'
        lines[0] = json.dumps(row, sort_keys=True, separators=(',', ':')).encode()
        self.rewrite(lines)
        finished = self.finished()
        finished['history'][0]['line_sha256'] = sha(lines[0])
        (self.root/'finished.json').write_text(json.dumps(finished))
        self.repin()
        with self.assertRaises(ValueError) as caught:
            observe_ndjson.audit(self.root, 30, MARKS)
        self.assertIn('unexpected telemetry record', str(caught.exception))

    def test_stream_hash_mismatch_is_refused(self):
        lines = self.stream.read_bytes().splitlines()
        self.rewrite(lines[:-1])
        with self.assertRaises(ValueError) as caught:
            observe_ndjson.audit(self.root, 30, MARKS)
        self.assertIn('stream', str(caught.exception))

    def test_missing_mark_is_refused(self):
        finished = self.finished()
        finished['marks'] = [m for m in finished['marks'] if m['label'] != 'measure-end']
        (self.root/'finished.json').write_text(json.dumps(finished))
        with self.assertRaises(ValueError) as caught:
            observe_ndjson.audit(self.root, 30, MARKS)
        self.assertIn('phase marker missing', str(caught.exception))

    def test_gap_is_refused(self):
        self.tmp2 = TemporaryDirectory()
        root = Path(self.tmp2.name)/'stream'
        write_stream(root, count=2, step=60_000_000_000)
        with self.assertRaises(ValueError) as caught:
            observe_ndjson.audit(root, 30, MARKS)
        self.assertIn('telemetry gap', str(caught.exception))
        self.tmp2.cleanup()

    def test_non_monotonic_clock_is_refused(self):
        self.tmp3 = TemporaryDirectory()
        root = Path(self.tmp3.name)/'stream'
        write_stream(root)
        lines = (root/'samples.ndjson').read_bytes().splitlines()
        row = json.loads(lines[2])
        row['monotonic_start_ns'] = 1
        lines[2] = json.dumps(row, sort_keys=True, separators=(',', ':')).encode()
        (root/'samples.ndjson').write_bytes(b''.join(line+b'\n' for line in lines))
        finished = json.loads((root/'finished.json').read_text())
        finished['history'][2]['line_sha256'] = sha(lines[2])
        data = (root/'samples.ndjson').read_bytes()
        finished['stream'] = {'name': observe_ndjson.STREAM, 'sha256': sha(data), 'bytes': len(data)}
        (root/'finished.json').write_text(json.dumps(finished))
        with self.assertRaises(ValueError) as caught:
            observe_ndjson.audit(root, 30, MARKS)
        self.assertIn('non-monotonic', str(caught.exception))
        self.tmp3.cleanup()


class LineShape(unittest.TestCase):
    def test_one_line_per_sample(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)/'stream'
            write_stream(root, count=7)
            lines = (root/'samples.ndjson').read_bytes().splitlines()
            self.assertEqual(len(lines), 7)
            self.assertEqual(json.loads(lines[0])['index'], 0)
            self.assertEqual(json.loads(lines[-1])['index'], 6)


if __name__ == '__main__':
    unittest.main()
