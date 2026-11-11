"""Never hide the canonical workspace without an explicit private namespace."""
import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

FILE=Path(__file__).resolve().parents[1]/'experiments/unified-suite/pilot-aliyun/scripts/qualify-session-shared-local.py'
spec=importlib.util.spec_from_file_location('pilot_session_shared',FILE)
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)


class NamespacePreflight(unittest.TestCase):
    def test_empty_or_current_namespace_fails_before_any_input_or_output_change(self):
        for original in ('','mnt:[77]'):
            with self.subTest(original=original),tempfile.TemporaryDirectory() as tmp:
                root=Path(tmp);out=root/'out'
                argv=['qualify-session-shared-local.py']
                for name in ('build-root','checkpoint-directory','services','provider-config'):
                    argv+=['--'+name,str(root/'missing')]
                argv+=['--out',str(out),'--parent-mount-namespace',original,'--database','15']
                with patch.object(module.sys,'argv',argv),patch.object(module.os,'geteuid',return_value=0),\
                     patch.object(module.os,'readlink',return_value='mnt:[77]'),\
                     self.assertRaisesRegex(ValueError,'private mount namespace'):
                    module.main()
                self.assertFalse(out.exists())


if __name__=='__main__':unittest.main()
