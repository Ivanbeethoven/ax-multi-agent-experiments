import importlib.util
import io
from pathlib import Path
import stat
import tarfile
import tempfile
import unittest


SCRIPT=Path(__file__).resolve().parents[1]/'experiments/unified-suite/pilot-aliyun/scripts/prepare-cloud-trace.py'
spec=importlib.util.spec_from_file_location('pilot_trace_extract',SCRIPT)
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)


class TraceExtract(unittest.TestCase):
    def archive(self,entries):
        raw=io.BytesIO()
        with tarfile.open(fileobj=raw,mode='w') as stream:
            for member,data in entries:stream.addfile(member,io.BytesIO(data) if data else None)
        raw.seek(0)
        return raw

    def test_original_writable_modes_survive_safe_extraction(self):
        directory=tarfile.TarInfo('case/initial');directory.type=tarfile.DIRTYPE;directory.mode=0o777
        regular=tarfile.TarInfo('case/initial/data');regular.mode=0o666;regular.size=4
        with tempfile.TemporaryDirectory() as folder:
            with tarfile.open(fileobj=self.archive([(directory,b''),(regular,b'data')])) as archive:
                archive.extractall(folder,filter=module.preserve_permissions)
            self.assertEqual(stat.S_IMODE((Path(folder)/'case/initial').stat().st_mode),0o777)
            self.assertEqual(stat.S_IMODE((Path(folder)/'case/initial/data').stat().st_mode),0o666)
            self.assertEqual((Path(folder)/'case/initial/data').read_bytes(),b'data')

    def test_external_symlink_is_rejected(self):
        link=tarfile.TarInfo('case/escape');link.type=tarfile.SYMTYPE;link.linkname='../../outside'
        with tempfile.TemporaryDirectory() as folder:
            with tarfile.open(fileobj=self.archive([(link,b'')])) as archive:
                with self.assertRaises(tarfile.LinkOutsideDestinationError):
                    archive.extractall(folder,filter=module.preserve_permissions)

    def test_special_permission_bits_are_rejected(self):
        regular=tarfile.TarInfo('case/special');regular.mode=0o4755;regular.size=1
        with tempfile.TemporaryDirectory() as folder:
            with tarfile.open(fileobj=self.archive([(regular,b'x')])) as archive:
                with self.assertRaises(ValueError):archive.extractall(folder,filter=module.preserve_permissions)


if __name__=='__main__':unittest.main()
