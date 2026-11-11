"""Offline gates for exact historical object observer reclamation."""

import importlib.util
import json
import os
from pathlib import Path
import socket
import tempfile
import unittest
from unittest.mock import patch

from bench.archive_check import pack


FILE = (Path(__file__).resolve().parents[1]
        / 'experiments/unified-suite/pilot-aliyun/scripts/reclaim-closed-object-observer.py')
SPEC = importlib.util.spec_from_file_location('closed_observer_reclaim', FILE)
TOOL = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(TOOL)
BOOT = Path('/proc/sys/kernel/random/boot_id').read_text().strip()
TAG, CELL = 'cal-n-rw-on1', '7084af367e0bcfad-r1-native'


def save(path, value):
    path.write_text(json.dumps(value))
    return ref(path)


def ref(path):
    return {'path': str(path), 'sha256': TOOL.file_sha(path)}


class HistoricalObserverReclaim(unittest.TestCase):
    def setUp(self):
        scratch = tempfile.TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        self.root = Path(scratch.name).resolve()
        self.original = self.root/'o'/TAG
        self.sample = self.original/'sample-000000/rustfs.txt'
        self.sample.parent.mkdir(parents=True)
        self.sample.write_text('the archived original')
        self.spec_sha = 'a'*64
        # Existing process ID with a deliberately different generation.
        self.ident = {'boot_id': BOOT, 'pid': os.getpid(), 'start_ticks': 1}
        save(self.original/'ready.json', {'passed': True, 'process': self.ident, 'spec': {'sha256': self.spec_sha}})
        save(self.original/'finished.json', {'passed': True, 'spec_sha256': self.spec_sha})
        self.files = {p.relative_to(self.original).as_posix(): {'bytes': p.stat().st_size, 'sha256': TOOL.file_sha(p)}
                      for p in self.original.rglob('*') if p.is_file()}
        self.request = {'identity': {'boot_id': BOOT, 'hostname': socket.gethostname(), 'data_device': self.root.stat().st_dev},
                        'targets': [{'tag': TAG, 'files': self.files}], 'mode': 'dry-run', 'minimum_after_bytes': 1}

    def test_dry_run_accepts_stale_socket_without_removing(self):
        address = str(self.original/'observer.sock')
        with socket.socket(socket.AF_UNIX) as sock:
            sock.bind(address)
        with patch.object(TOOL, 'ROOT', self.root), patch.object(TOOL.shutil, 'rmtree') as remove:
            result = TOOL.node(self.request)
            self.assertGreater(result['observed_allocated_bytes'], 0)
            self.assertFalse(result['applied'])
            self.assertTrue(self.sample.exists())
            self.assertTrue(Path(address).exists())
            remove.assert_not_called()

    def test_bound_socket_and_original_process_block(self):
        address = str(self.original/'observer.sock')
        with patch.object(TOOL, 'ROOT', self.root), socket.socket(socket.AF_UNIX) as sock:
            sock.bind(address)
            sock.listen()
            with self.assertRaisesRegex(ValueError, 'Live observer socket'):
                TOOL.node(self.request)
        ticks = int(Path('/proc/self/stat').read_text().rsplit(')', 1)[1].split()[19])
        with self.assertRaisesRegex(ValueError, 'Original observer process still exists'):
            TOOL.old_observer_exited({'passed': True, 'process': dict(self.ident, start_ticks=ticks),
                                      'spec': {'sha256': self.spec_sha}},
                                     {'passed': True, 'spec_sha256': self.spec_sha}, BOOT)

    def test_changed_bytes_and_extra_symlink_block(self):
        with patch.object(TOOL, 'ROOT', self.root):
            self.sample.write_text('x'*self.sample.stat().st_size)
            with self.assertRaisesRegex(ValueError, 'Observer member bytes differ'):
                TOOL.node(self.request)
            self.sample.write_text('the archived original')
            (self.original/'extra').symlink_to(self.root)
            with self.assertRaisesRegex(ValueError, 'Unexpected observer file'):
                TOOL.node(self.request)

    def test_apply_must_match_dry_run_fingerprint(self):
        with patch.object(TOOL, 'ROOT', self.root), patch.object(TOOL.shutil, 'rmtree') as remove:
            receipt = TOOL.node(self.request)
            self.request['mode'] = 'apply'
            self.request['dry_run_groups'] = receipt['groups']
            self.sample.touch()
            with self.assertRaisesRegex(ValueError, 'identical verified dry-run groups'):
                TOOL.node(self.request, apply=True)
            self.assertTrue(self.sample.exists())
            remove.assert_not_called()

    def test_nested_public_source_is_exact_tag_role_and_checksum(self):
        stage, outer_stage = self.root/'role-copy', self.root/'outer-copy'
        stage.mkdir()
        package = outer_stage/TAG
        package.mkdir(parents=True)
        role = package/'objects.tar.gz'
        names = []
        for name in self.files:
            relative = f'o/{TAG}/{name}'
            dest = stage/relative
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes((self.original/name).read_bytes())
            names.append(relative)
        check = save(self.root/'objects-check.json', pack(stage, names, role, CELL, f'calibration-{TAG}-objects'))
        outer = self.root/'calibrations.tar.gz'
        nested = f'{TAG}/objects.tar.gz'
        outer_check = save(self.root/'outer-check.json', pack(outer_stage, [nested], outer, CELL, 'six-calibration-trials'))
        proof = {'passed': True, 'source': str(outer), 'sha256': TOOL.file_sha(outer), 'bytes': outer.stat().st_size}
        key = f'pilot-results/20261111/c1-{CELL}/calibrations.tar.gz'
        pub = {'schema': 'pilot-cloud-oss-archive-v1', 'bucket': TOOL.BUCKET, 'key': key,
               'public_url': f'https://{TOOL.BUCKET}.oss-cn-hangzhou.aliyuncs.com/{key}',
               'anonymous_public_head': True, 'prepared': proof,
               'upload': dict(proof, readback_sha256=proof['sha256'], full_internal_readback=True, overwrite=False)}
        item = {'tag': TAG, 'cell_id': CELL, 'role_archive': ref(role), 'role_check': check,
                'public_archive': ref(outer), 'public_member': nested, 'public_check': outer_check,
                'public_receipt': save(self.root/'oss.json', pub)}
        original_pin = TOOL.pin
        with patch.object(TOOL, 'pin', side_effect=lambda value: original_pin(value, below=self.root)):
            self.assertEqual(TOOL.source(item, {CELL})['files'], self.files)
            with self.assertRaisesRegex(ValueError, 'Wrong published nested objects member'):
                TOOL.source(dict(item, public_member='cal-n-rw-on2/objects.tar.gz'), {CELL})
            pub['upload']['readback_sha256'] = 'f'*64
            item['public_receipt'] = save(self.root/'oss.json', pub)
            with self.assertRaisesRegex(ValueError, 'Exact public full readback required'):
                TOOL.source(item, {CELL})


if __name__ == '__main__':
    unittest.main()
