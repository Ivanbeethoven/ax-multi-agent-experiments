import hashlib
import importlib.util
import io
import json
from pathlib import Path
import tarfile
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from bench import archive_check


SCRIPT = Path(__file__).resolve().parents[1]/'experiments/unified-suite/pilot-aliyun/scripts/recover-c4-on1-objects.py'
MODULE = importlib.util.spec_from_file_location('recover_c4_on1_objects', SCRIPT)
RECOVER = importlib.util.module_from_spec(MODULE)
MODULE.loader.exec_module(RECOVER)


def put(p, data):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data if isinstance(data, bytes) else (json.dumps(data)+'\n').encode())
    return p


class RecoverObjects(unittest.TestCase):
    def setUp(self):
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.base = Path(tmp.name).resolve()
        r = RECOVER
        self.root = self.base/'retained'
        self.out = self.base/'recovered'
        self.raw_observer = self.root/r.Q/'observer.json'
        for name in r.LEGACY_FILES:
            put(self.root/name, b'original field\n')
        spec_sha = r.digest(self.raw_observer)
        identity = dict(pid=19,start_ticks=25,boot_id='old-boot')
        put(self.root/r.OBS/'ready.json',dict(passed=True,spec=dict(sha256=spec_sha),process=identity,metric_contract_sha256='metric'))
        put(self.root/r.CLOSE,dict(cell_id=r.CELL,passed=True,all_exited=True,all_unmounted=True,processes=[identity]))
        self.stream = put(self.root/r.OBS/'samples.ndjson', b'original sample stream\n')
        fmt = dict(name=self.stream.name, **r.entry(self.stream))
        self.old = put(self.base/'finished.original.json', dict(passed=True,samples=1))
        self.folded = put(self.root/r.OBS/'finished.json',
                          dict(passed=True,samples=1,stream=fmt,spec_sha256=spec_sha,metric_contract_sha256='metric'))
        self.fold = put(self.base/'fold.json',dict(passed=True,samples=1,source_members=5,stream=fmt))
        put(self.root/r.OBS/'fold-receipt.json',self.fold.read_bytes())
        put(self.root/r.OBS/'fold-receipt.ndjson',b'detailed audit\n')
        members = {name:r.entry(self.root/name) for name in r.LEGACY_FILES}
        members[f'{r.OBS}/finished.json'] = r.entry(self.old)
        manifest = json.dumps(dict(schema='suite-archive-manifest-v1',cell_id=r.CELL,kind='objects',files=members,deferred=[])).encode()
        self.legacy = self.root/r.ARCHIVE
        self.legacy.parent.mkdir()
        with tarfile.open(self.legacy,'w:gz') as ar:
            head = tarfile.TarInfo(archive_check.MANIFEST)
            head.size = len(manifest)
            ar.addfile(head,io.BytesIO(manifest))
        pin = patch.multiple(r,MAX_REJECTED_ARCHIVE_BYTES=self.legacy.stat().st_size,MIN_MANIFEST_BYTES=len(manifest)-1,MAX_MANIFEST_BYTES=len(manifest),
            FINISHED_SHA=r.digest(self.old),FOLDED_SHA=r.digest(self.folded),FOLD_RECEIPT_SHA=r.digest(self.fold),
            STREAM=fmt,SAMPLES=1,SOURCE_MEMBERS=5)
        pin.start()
        self.addCleanup(pin.stop)

    def recover(self):
        return RECOVER.build(self.root,self.old,self.fold,self.out)

    def test_retains_original_receipt_and_exact_stream_without_writing_input(self):
        before = {p:hashlib.sha256(p.read_bytes()).hexdigest() for p in self.root.rglob('*') if p.is_file()}
        receipt = self.recover()
        result = archive_check.verify(receipt['archive']['path'], receipt['archive']['sha256'])
        self.assertTrue(result['passed'])
        self.assertEqual(result['kind'],'objects')
        self.assertFalse(receipt['formal_acceptance'])
        with tarfile.open(receipt['archive']['path'],'r:gz') as ar:
            self.assertEqual(ar.extractfile(f'{RECOVER.OBS}/legacy-finished.json').read(), self.old.read_bytes())
            self.assertEqual(ar.extractfile(f'{RECOVER.OBS}/samples.ndjson').read(), self.stream.read_bytes())
            self.assertEqual(ar.extractfile(f'{RECOVER.Q}/observer.json').read(), self.raw_observer.read_bytes())
        self.assertEqual(before,{p:hashlib.sha256(p.read_bytes()).hexdigest() for p in self.root.rglob('*') if p.is_file()})
        with self.assertRaisesRegex(ValueError,'New absolute output'):
            self.recover()

    def test_rejects_substituted_original_probe_before_creating_output(self):
        put(self.root/RECOVER.Q/'rustfs-probe.txt',b'modified probe\n')
        with self.assertRaisesRegex(ValueError,'does not bind'):
            self.recover()
        self.assertFalse(self.out.exists())

    def test_standard_verifier_rejects_stream_change_of_same_size(self):
        self.stream.write_bytes(b'x'*self.stream.stat().st_size)
        with self.assertRaisesRegex(ValueError,'member list/size/checksum differs'):
            self.recover()
        self.assertTrue((self.out/'objects.tar.gz').is_file())
        self.assertFalse((self.out/'objects-pack.json').exists())


if __name__ == '__main__':
    unittest.main()
