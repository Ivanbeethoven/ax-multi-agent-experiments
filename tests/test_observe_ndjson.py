import hashlib
import json
import os
from pathlib import Path
import runpy
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from bench.runtime import fleet, observe, observe_ndjson, transport


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


class DropInSurface(unittest.TestCase):
    def test_everything_the_frozen_callers_import_is_available(self):
        for name in ('validate', 'request', 'overhead', 'start', 'serve', 'audit', 'snapshot',
                     'HOST_FILES', 'CGROUP_FILES', 'prometheus', 'required_values',
                     'collect_target', 'mount_lifecycle'):
            with self.subTest(name=name):
                self.assertTrue(hasattr(observe_ndjson, name), name)

    def test_start_spawns_the_stream_entry_point(self):
        from unittest.mock import patch
        with TemporaryDirectory() as tmp:
            out = Path(tmp)/'collector'
            recorded = {}

            class Child:
                def poll(self):
                    return None
                def wait(self):
                    return 0

            def fake_popen(argv, **kwargs):
                recorded['argv'] = argv
                recorded['kwargs'] = kwargs
                out.mkdir(parents=True, exist_ok=True)
                (out/'ready.json').write_text('{}')
                return Child()

            ref = dict(path=str(Path(tmp)/'spec.json'), sha256='0'*64)
            Path(ref['path']).write_text('{"schema":"experiment-observer-v1"}')
            with patch.object(observe_ndjson, 'spec', return_value={
                    'interval_seconds': 5, 'cgroups': [], 'processes': [], 'targets': [],
                    'data_paths': [], 'metric_contract_sha256': '1'*64}), \
                 patch.object(observe_ndjson, 'validate'), \
                 patch.object(observe_ndjson.subprocess, 'Popen', side_effect=fake_popen), \
                 patch.object(observe_ndjson, 'request', return_value={'passed': True}), \
                 patch.object(observe_ndjson, 'private_dir', side_effect=lambda p: (Path(p).mkdir(parents=True, exist_ok=True), Path(p))[1]), \
                 patch.object(observe_ndjson.threading, 'Thread'):
                result = observe_ndjson.start(ref, out)
            self.assertEqual(result, {'passed': True})
            self.assertIn('bench.runtime.observe_ndjson', recorded['argv'])
            self.assertEqual(recorded['argv'][-2:], ['--output', str(out)])


@unittest.skipUnless(sys.platform.startswith('linux'), 'observer runtime requires Linux')
class PinnedProducer(unittest.TestCase):
    def test_real_detached_writer_uses_the_legacy_socket_and_keeps_raw_samples(self):
        repo = Path(__file__).resolve().parents[1]
        wrapper = repo/'experiments/unified-suite/pilot-aliyun/scripts/calibrate-c4-closed-observer.py'
        start = runpy.run_path(str(wrapper))['start_ndjson']
        python = str(Path(sys.executable).resolve())
        source_path = repo/'bench/runtime/observe_ndjson.py'
        payload = dict(source=source_path.read_bytes().decode('utf-8'), sha256=sha(source_path.read_bytes()))
        with TemporaryDirectory(dir='/tmp') as tmp:
            root = Path(tmp).resolve()
            out = root/'observer'
            spec = root/'spec.json'
            spec.write_text(json.dumps(dict(schema='experiment-observer-v1', reviewed=True,
                role='host-only', interval_seconds=5, data_paths=[str(root)], targets=[],
                cgroups=[], processes=[], metric_contract_sha256='1'*64)))
            peer = root/'peer.json'
            peer.write_text(json.dumps(dict(transport='ssh', repo=str(repo), python=python,
                                           pins=transport.runtime_pins(repo, python))))
            protocol = root/'protocol'
            try:
                # Only SSH is stubbed: run the real bootstrap, detached child, and socket.
                with patch.object(transport, 'ssh_command', side_effect=lambda _, argv: argv):
                    self.assertTrue(start(fleet.reference(peer), str(out), protocol,
                                          fleet.reference(spec), payload)['passed'])
                ready = json.loads((out/'ready.json').read_text())
                self.assertEqual(ready['observer_format_sha256'], payload['sha256'])
                for mark in MARKS:
                    observe.request(out, 'mark', mark)
                done = observe.request(out, 'stop')
            finally:
                if (out/'ready.json').is_file() and not (out/'finished.json').exists():
                    # A started fixture must never leave a background observer running.
                    try:
                        observe.request(out, 'stop')
                    except (OSError, ValueError):
                        pid = json.loads((out/'ready.json').read_text())['process']['pid']
                        os.kill(pid, 15)
            self.assertEqual(done['observer_format_sha256'], payload['sha256'])
            self.assertFalse(list(out.glob('sample-*')))
            with (out/'samples.ndjson').open() as stream:
                first = json.loads(stream.readline())
            raw = json.loads(first['files']['sample.json']['text'])
            self.assertTrue({'utc_ns', 'processes', 'collector_cpu_seconds'} <= raw.keys())
            self.assertEqual(raw['files'].keys(), first['files'].keys()-{'sample.json'})
            self.assertEqual(observe_ndjson.audit(out, 30, MARKS)['samples'], done['samples'])


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
        finished['history'][0]['line_bytes'] = len(lines[0])
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
        finished['history'][2]['line_bytes'] = len(lines[2])
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
