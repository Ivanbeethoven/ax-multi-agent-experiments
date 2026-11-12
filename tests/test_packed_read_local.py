import contextlib
import hashlib
import importlib.util
import io
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch


class PackedReadPreflight(unittest.TestCase):
    def test_wrong_artifact_is_rejected_before_output_or_services(self):
        root=Path(__file__).resolve().parents[1]
        source=root/'experiments/unified-suite/pilot-aliyun/scripts/qualify-packed-read-local.py'
        spec=importlib.util.spec_from_file_location('packed_read_local_test',source)
        module=importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory() as folder:
            binary=Path(folder)/'binary'; binary.write_bytes(b'known bytes')
            actual=hashlib.sha256(binary.read_bytes()).hexdigest()
            for which in ('brewfs','producer'):
                with self.subTest(which=which):
                    out=Path(folder)/('out-'+which)
                    argv=['qualify-packed-read-local','--brewfs',str(binary),'--producer',str(binary),
                          '--brewfs-sha256','0'*64 if which=='brewfs' else actual,
                          '--producer-sha256','0'*64 if which=='producer' else actual,
                          '--rustfs-image','image','--rustfs-revision','revision','--out',str(out)]
                    with patch.object(sys,'argv',argv), patch.object(module.os,'geteuid',return_value=0), \
                         patch.object(module.sp,'run') as run, contextlib.redirect_stderr(io.StringIO()):
                        with self.assertRaises(SystemExit) as raised:
                            module.main()
                        self.assertEqual(raised.exception.code,2)
                        run.assert_not_called()
                        self.assertFalse(out.exists())
