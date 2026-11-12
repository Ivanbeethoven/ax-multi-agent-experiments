import importlib.util
import json
import os
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO/'experiments/unified-suite/pilot-aliyun/scripts/pack-c4-observer-split.py'


def load_module():
    spec = importlib.util.spec_from_file_location('pack_c4_observer_split', SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


module = load_module()


class PackObserverSplit(unittest.TestCase):
    def tree(self, root, files):
        for name, data in files.items():
            path = root/name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)

    def test_packs_two_tars_and_verifies_both(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()/'scratch'
            observer = root/'o'/'c4-a-flat-on1'
            self.tree(observer, {'sample-000000/sample.json': b'{"a":1}\n',
                                 'sample-000001/sample.json': b'{"a":2}\n',
                                 'finished.json': b'{"passed":true}\n'})
            self.tree(root, {'q/c4-a-flat-on1-closure/result.json': b'{"passed":true}\n'})
            out = Path(tmp).resolve()/'archives'
            code = str(REPO)
            argv = ['--root', str(root), '--tag', 'c4-a-flat-on1', '--role', 'objects',
                    '--cell-id', 'c5e97774bcffa69a-r1-native', '--include', 'q/c4-a-flat-on1-closure',
                    '--out-dir', str(out), '--code', code]
            with patch.object(sys, 'argv', ['pack-c4-observer-split.py', *argv]):
                module.main()
            receipt = json.loads((out/'c4-a-flat-on1-objects-split.json').read_bytes())
            self.assertTrue(receipt['passed'])
            self.assertEqual(receipt['observer']['members'], 3)
            self.assertEqual(receipt['evidence']['members'], 1)
            for row in (receipt['observer'], receipt['evidence']):
                self.assertEqual(module.digest(row['path']), row['sha256'])
                self.assertEqual(Path(row['path']).stat().st_size, row['bytes'])

    def test_index_is_streamed_and_complete(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()/'scratch'
            observer = root/'o'/'c4-a-flat-on1'
            files = {'sample-%06d/sample.json' % i: ('{"i":%d}\n' % i).encode() for i in range(50)}
            self.tree(observer, files)
            tar = Path(tmp).resolve()/'observer.tar.gz'
            header = dict(schema=module.INDEX_SCHEMA, tag='c4-a-flat-on1', role='objects',
                          cell_id='cell', members=len(files))
            names = sorted(files)
            entries = module.write_observer(observer, names, tar, header)
            module.verify_observer(tar, names)
            self.assertEqual(len(entries), 50)
            import tarfile
            with tarfile.open(tar, 'r:gz') as archive:
                first = archive.next()
                self.assertEqual(first.name, module.INDEX)
                lines = archive.extractfile(first).read().decode().splitlines()
            self.assertEqual(len(lines), 51)
            self.assertEqual(json.loads(lines[0])['members'], 50)

    def test_tampered_member_is_refused(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()/'scratch'
            observer = root/'o'/'c4-a-flat-on1'
            self.tree(observer, {'a/sample.json': b'{"a":1}\n'})
            tar = Path(tmp).resolve()/'observer.tar.gz'
            names = ['a/sample.json']
            module.write_observer(observer, names, tar,
                                  dict(schema=module.INDEX_SCHEMA, tag='t', role='objects',
                                       cell_id='c', members=1))
            bad = Path(tmp).resolve()/'bad.tar.gz'
            import tarfile, io
            with tarfile.open(tar, 'r:gz') as source, tarfile.open(bad, 'w:gz') as sink:
                for member in source:
                    data = source.extractfile(member).read()
                    if member.name != module.INDEX:
                        data = b'{"a":2}\n'
                    info = tarfile.TarInfo(member.name)
                    info.size = len(data)
                    sink.addfile(info, io.BytesIO(data))
            with self.assertRaises(ValueError):
                module.verify_observer(bad, names)

    def test_symlink_and_credential_names_are_refused(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()/'scratch'
            observer = root/'o'/'c4-a-flat-on1'
            observer.mkdir(parents=True)
            (observer/'real.json').write_bytes(b'x')
            os.symlink(observer/'real.json', observer/'link.json')
            with self.assertRaises(ValueError):
                module.members(observer)
        with TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()/'scratch'
            observer = root/'o'/'c4-a-flat-on1'
            self.tree(observer, {'id_ed25519': b'secret'})
            with self.assertRaises(ValueError):
                module.members(observer)

    def test_output_inside_observer_tree_is_refused(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()/'scratch'
            observer = root/'o'/'c4-a-flat-on1'
            self.tree(observer, {'a.json': b'x'})
            argv = ['--root', str(root), '--tag', 'c4-a-flat-on1', '--role', 'objects',
                    '--cell-id', 'cell', '--out-dir', str(observer/'archives'), '--code', str(REPO)]
            with patch.object(sys, 'argv', ['pack-c4-observer-split.py', *argv]):
                with self.assertRaises(ValueError):
                    module.main()

    def test_existing_output_is_never_overwritten(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()/'scratch'
            observer = root/'o'/'c4-a-flat-on1'
            self.tree(observer, {'a.json': b'x'})
            out = Path(tmp).resolve()/'archives'
            out.mkdir()
            (out/'c4-a-flat-on1-objects-observer.tar.gz').write_bytes(b'old')
            argv = ['--root', str(root), '--tag', 'c4-a-flat-on1', '--role', 'objects',
                    '--cell-id', 'cell', '--out-dir', str(out), '--code', str(REPO)]
            with patch.object(sys, 'argv', ['pack-c4-observer-split.py', *argv]):
                with self.assertRaises(ValueError):
                    module.main()


if __name__ == '__main__':
    unittest.main()
