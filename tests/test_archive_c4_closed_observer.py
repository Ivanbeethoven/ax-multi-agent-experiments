"""Offline fidelity and closure checks for the C4 archive-only helper."""
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import runpy
import shutil
import socket
import tarfile
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from bench import archive_check
from bench.runtime import common, observe


REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO/'experiments/unified-suite/pilot-aliyun/scripts/archive-c4-closed-observer.py'
MODULE = runpy.run_path(str(SCRIPT))['archive'].__globals__
TAG = 'c4-example-on1'
CELL = 'c5e97774bcffa69a-r1-native'


def pin(path):
    return dict(path=str(path), sha256=hashlib.sha256(path.read_bytes()).hexdigest())


def write(path, body):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(body), encoding='utf-8')
    return pin(path)


def members(path):
    with tarfile.open(path, 'r:gz') as archive:
        return [(entry.name, archive.extractfile(entry).read()) for entry in archive]


class ClosedObserverArchive(unittest.TestCase):
    def setUp(self):
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.path = Path(tmp.name).resolve()
        self.root = self.path/'role'
        self.obs = self.root/'o'/TAG
        self.closure = self.root/'q'/(TAG+'-closure')/'result.json'
        self.q = self.root/'q'/(TAG+'-observer')
        self.log = self.obs/'collector.log'
        self.process = dict(common.identity(os.getpid()), pid=2147483647)
        self.exit_ref = write(self.closure, dict(passed=True, all_exited=True, all_unmounted=True,
                                               cell_id=CELL, processes=[self.process]))
        spec = write(self.q/'observer.json', dict(interval_seconds=5))
        ready = write(self.obs/'ready.json', dict(passed=True, spec=spec, process=self.process,
                                                metric_contract_sha256='metric'))
        history = []
        for i in range(2):
            sample = self.obs/f'sample-{i:06d}'
            sample.mkdir()
            raw = sample/'objects.txt'
            raw.write_bytes(b'rustfs_storage_snapshot_last_success_timestamp_seconds 123\n')
            digest = pin(raw)['sha256']
            history.append(dict(index=i, sample_sha256=write(sample/'sample.json', dict(
                passed=True, index=i, monotonic_start_ns=10+i*5, monotonic_end_ns=11+i*5,
                files={raw.name: dict(bytes=raw.stat().st_size, sha256=digest)}))['sha256']))
        finished = write(self.obs/'finished.json', dict(
            passed=True, samples=2, history=history, spec_sha256=spec['sha256'],
            metric_contract_sha256='metric',
            marks=[dict(label=m, sample_index=1, monotonic_ns=20+i) for i, m in enumerate(MODULE['MARKS'])]))
        self.log.write_bytes(b'collector log')
        write(self.obs/'cache'/'not-archive.json', {'skip': True})
        write(self.root/'q'/('different-collector')/'container.log', {'skip': True})
        self.directories = ['o/'+TAG, 'q/'+TAG+'-observer', 'q/'+TAG+'-closure']
        self.request = dict(schema='c4-closed-observer-archive-v1', reviewed=True, role='objects',
                            tag=TAG, cell_id=CELL, root=str(self.root), has_observer=True,
                            directories=self.directories, code=str(REPO),
                            code_pins=[pin(REPO/p) for p in sorted(MODULE['CODE_FILES'])],
                            observer_ready=ready, observer_finished=finished, role_closure=self.exit_ref)
        patcher = patch.dict(MODULE['ROOTS'], objects=str(self.root), client=str(self.root))
        patcher.start()
        self.addCleanup(patcher.stop)

    def original_archive(self):
        staging = self.path/'previous'
        staging.mkdir()
        names = []
        for directory in self.request['directories']:
            for p in sorted((self.root/directory).rglob('*')):
                if 'cache' in p.relative_to(self.root/directory).parts:
                    continue
                self.assertFalse(p.is_symlink())
                if p.is_file():
                    relative = p.relative_to(self.root)
                    target = staging/relative
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(p, target)
                    names.append(relative.as_posix())
        target = self.path/'previous.tar.gz'
        archive_check.pack(staging, names, target, CELL, self.request['role'])
        return target

    def test_payload_and_order_match_original_without_observer_snapshot(self):
        # Even an alias of an original inode is emitted as a regular tar member.
        os.link(self.log, self.obs/'other.log')
        with socket.socket(socket.AF_UNIX) as sock:
            sock.bind(str(self.obs/'observer.sock'))
            old = self.original_archive()
            result = MODULE['archive'](self.request)
        path = Path(result['archive']['archive'])
        expected = members(old)
        self.assertEqual(members(path), expected)
        self.assertEqual(archive_check.verify(path, result['archive']['sha256']), result['archive'])
        raw = self.path/'download'
        for name, data in members(path):
            if name.startswith('o/'+TAG+'/'):
                p = raw/name
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_bytes(data)
        self.assertTrue(observe.audit(raw/'o'/TAG, 30, MODULE['MARKS'])['passed'])
        staged = self.root/'evidence'/TAG
        self.assertFalse((staged/'o').exists())
        self.assertEqual((staged/'q'/self.q.name/'observer.json').read_bytes(), (self.q/'observer.json').read_bytes())
        self.assertNotIn('q/different-collector/container.log', dict(expected))
        self.assertNotIn(f'o/{TAG}/observer.sock', dict(expected))
        self.assertNotIn(f'o/{TAG}/cache/not-archive.json', dict(expected))

    def test_client_mount_and_worker_paths_remain_staged(self):
        extra = ['q/'+TAG, 'm/'+TAG, 'm/'+TAG+'-v', 'w/'+TAG, 'j/'+TAG]
        for d in extra:
            write(self.root/d/'test.json', {'path': d})
        self.request.update(role='client', directories=self.directories[:-1]+extra+self.directories[-1:])
        self.request['role_closure'] = write(self.closure, dict(
            passed=True, all_exited=True, all_unmounted=True, cell_id=CELL,
            processes=[dict(self.process, alive=False)]))
        expected = members(self.original_archive())
        result = MODULE['archive'](self.request)
        self.assertEqual(members(result['archive']['archive']), expected)
        for d in extra:
            self.assertTrue((self.root/'evidence'/TAG/d/'test.json').is_file())

    def test_rejects_path_expansion_links_and_bad_code_pin(self):
        s = deepcopy(self.request)
        s['directories'].insert(1, 'q/../elsewhere')
        with self.assertRaisesRegex(ValueError, 'directory allowlist'):
            MODULE['archive'](s)
        s = deepcopy(self.request)
        s['code_pins'][0]['sha256'] = '0'*64
        with self.assertRaisesRegex(ValueError, 'Changed pinned evidence'):
            MODULE['archive'](s)
        (self.obs/'indirect').symlink_to(self.path, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, 'Evidence symlink'):
            MODULE['archive'](self.request)
        self.assertFalse((self.root/'evidence').exists())

    def test_rejects_linked_output_before_writing_outside_worker_root(self):
        external = self.path/'external'
        external.mkdir()
        (self.root/'evidence').symlink_to(external, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, 'output path escapes'):
            MODULE['archive'](self.request)
        self.assertEqual(list(external.iterdir()), [])

    def test_non_observer_uses_original_snapshot_even_if_q_later_changes(self):
        source = self.q/'mutable-log.txt'
        source.write_bytes(b'initial')
        pack = MODULE['pack_mapped']

        def changed(*args):
            source.write_bytes(b'updated')
            return pack(*args)

        with patch.dict(MODULE, pack_mapped=changed):
            result = MODULE['archive'](self.request)
        files = dict(members(result['archive']['archive']))
        self.assertEqual(files[f'q/{TAG}-observer/mutable-log.txt'], b'initial')
        self.assertEqual(source.read_bytes(), b'updated')

    def test_rejects_live_observer_or_unbound_original_close(self):
        with patch.object(common, 'alive', return_value=True):
            with self.assertRaisesRegex(ValueError, 'observer has not closed'):
                MODULE['archive'](self.request)
        s = deepcopy(self.request)
        s['role_closure'] = write(self.closure, dict(passed=True, all_exited=True, all_unmounted=True,
                                                   cell_id=CELL, processes=[dict(self.process, pid=2)]))
        with self.assertRaisesRegex(ValueError, 'role closure does not bind'):
            MODULE['archive'](s)

    def test_rejects_changed_observer_payload_even_if_file_size_is_same(self):
        pack = MODULE['pack_mapped']

        def changed(*args):
            self.log.write_bytes(b'COLLECTOR LOG')
            return pack(*args)

        with patch.dict(MODULE, pack_mapped=changed):
            with self.assertRaisesRegex(ValueError, 'Evidence changed before reading'):
                MODULE['archive'](self.request)

    def test_rejects_member_added_during_archive(self):
        pack = MODULE['pack_mapped']

        def changed(*args):
            result = pack(*args)
            (self.obs/'late.json').write_text('{}', encoding='utf-8')
            return result

        with patch.dict(MODULE, pack_mapped=changed):
            with self.assertRaisesRegex(ValueError, 'membership or source changed'):
                MODULE['archive'](self.request)


if __name__ == '__main__':
    unittest.main()
