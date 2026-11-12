"""Offline Linux checks for cold A-pool evidence preservation."""

import json
import os
from pathlib import Path
import runpy
import socket
import stat
import sys
import tarfile
from tempfile import TemporaryDirectory
import unittest


SCRIPTS = Path(__file__).resolve().parents[1] / 'experiments/unified-suite/pilot-aliyun/scripts'


@unittest.skipUnless(sys.platform.startswith('linux'), 'Uses Linux openat and /proc file descriptors')
class PreserveA(unittest.TestCase):
    def setUp(self):
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.data, self.system, self.home = [self.root/name for name in ('disk', 'srv', 'home')]
        for directory in (self.data, self.system, self.home):
            directory.mkdir()
        self.script = runpy.run_path(str(SCRIPTS/'preserve-a-backend.py'))
        self.core = self.script['common'](SCRIPTS/'preserve-b-backend.py')

    def scan(self, include=(), exclude=()):
        return self.script['selection'](self.core, {'serial': self.data}, set(include),
                                        dict(exclude), system=self.system, home=self.home)

    def put(self, path, value=b'owned data'):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(value)

    def test_archive_contains_link_metadata_not_target_content(self):
        self.put(self.root/'outside'/'must-not-read', b'outside source disk')
        oracle = self.data/'qualification'/'parallel-juicefs-regular-01'/'oracle'
        self.put(oracle/'regular')
        link = oracle/'symbolic'
        target = '../../../../outside//./'
        link.symlink_to(target)
        self.assertTrue((link/'must-not-read').is_file())
        dangling = oracle/'dangling'
        dangling.symlink_to('absent')
        files, unknown, skipped, links = self.scan()
        self.assertEqual(unknown, [])
        self.assertEqual(skipped, [])
        self.assertEqual([x[0] for x in files], [
            'serial/qualification/parallel-juicefs-regular-01/oracle/regular'])
        self.assertEqual(links, [
            {'path': 'serial/qualification/parallel-juicefs-regular-01/oracle/'+p.name,
             'target': expected, 'mode': stat.S_IMODE(p.lstat().st_mode),
             'mtime_ns': p.lstat().st_mtime_ns}
            for p, expected in ((dangling, 'absent'), (link, target))
        ])
        archive = self.root/'evidence.tar.gz'
        self.core.pack(archive, self.script['safe_pack_files'](files), {'symlinks': links}, b'')
        self.assertEqual(self.core.check_pack(archive), 3)
        with tarfile.open(archive, 'r:gz') as tar:
            self.assertTrue(all(member.isfile() for member in tar.getmembers()))
            self.assertEqual(json.load(tar.extractfile('retirement-inventory.json')), {'symlinks': links})
            self.assertFalse(any('symbolic' in n or 'must-not-read' in n for n in tar.getnames()))

        # The link target is part of inventory equality even if timestamps match.
        before = link.lstat()
        link.unlink()
        link.symlink_to('another')
        os.utime(link, ns=(before.st_atime_ns, before.st_mtime_ns), follow_symlinks=False)
        self.assertEqual(self.scan()[3][1]['mtime_ns'], links[1]['mtime_ns'])
        self.assertNotEqual(self.scan()[3], links)

    def test_nested_exact_paths_and_symlink_ancestor_refusal(self):
        self.put(self.data/'evidence'/'deep'/'keep')
        self.put(self.data/'evidence'/'deep'/'discard')
        self.put(self.data/'work'/'nested'/'oracle')
        self.put(self.data/'work'/'nested'/'unclassified')
        (self.data/'work'/'link').symlink_to('nested')
        self.put(self.data/'inputs'/'run'/'keep-receipt')
        self.put(self.data/'inputs'/'run'/'generated'/'discard')
        discard = {'serial/evidence/deep/discard': 'a'*64,
                   'serial/inputs/run/generated': 'b'*64}
        include = {'serial/work/nested/oracle', 'serial/inputs'}
        files, unknown, _, links = self.scan(include, discard)
        self.assertEqual([x[0] for x in files], [
            'serial/evidence/deep/keep', 'serial/inputs/run/keep-receipt', 'serial/work/nested/oracle'])
        self.assertEqual(unknown, ['serial/work/link', 'serial/work/nested/unclassified'])
        self.assertEqual(links[0]['path'], 'serial/work/link')
        with self.assertRaisesRegex(ValueError, 'Explicit archive/exclusion path not found'):
            self.scan({'serial/work/link/oracle'}, discard)

    def test_socket_exception_does_not_allow_other_special_files(self):
        evidence = self.data/'evidence'
        evidence.mkdir()
        os.mkfifo(evidence/'pipe')
        with socket.socket(socket.AF_UNIX) as known, socket.socket(socket.AF_UNIX) as other:
            known.bind(str(evidence/'observer.sock'))
            other.bind(str(evidence/'rogue.sock'))
            files, unknown, skipped, links = self.scan()
        self.assertEqual((files, links), ([], []))
        self.assertEqual(unknown, ['serial/evidence/pipe', 'serial/evidence/rogue.sock'])
        self.assertEqual(skipped, ['serial/evidence/observer.sock'])
        with self.assertRaisesRegex(ValueError, 'Explicit archive/exclusion path not found'):
            self.scan(exclude={'serial/evidence/pipe': 'b'*64})

    def test_regular_file_parent_replaced_with_symlink_before_pack_is_refused(self):
        evidence = self.data/'evidence'
        self.put(evidence/'ordinary')
        files, unknown, _, _ = self.scan()
        self.assertEqual(unknown, [])
        moved = self.root/'moved'
        evidence.rename(moved)
        evidence.symlink_to(moved, target_is_directory=True)
        with self.assertRaises(OSError):
            self.core.pack(self.root/'changed.tar.gz', self.script['safe_pack_files'](files), {}, b'')


if __name__ == '__main__':
    unittest.main()
