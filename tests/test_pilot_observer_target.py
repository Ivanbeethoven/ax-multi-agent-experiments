"""The destination observer must bind its own host and original mount lifecycle."""
import contextlib
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

FILE=Path(__file__).resolve().parents[1]/'experiments/unified-suite/pilot-aliyun/scripts/make-observer.py'
spec=importlib.util.spec_from_file_location('pilot_observer',FILE)
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)


class DestinationObserver(unittest.TestCase):
    def test_new_target_keeps_mount_lifecycle_without_probing_an_absent_endpoint(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);out=root/'observer'
            argv=['make-observer.py','--role','target','--product','juicefs',
                '--metric-contract-sha256','a'*64,'--mount-directory','/srv/ax-target/m/target',
                '--extra-mount-directory','/srv/ax-target/m/cold','--out',str(out)]
            with patch.object(module.sys,'argv',argv),\
                 patch.object(module.socket,'gethostname',return_value='ax-pilot-20261111-target'),\
                 patch.object(module,'identity',return_value=dict(pid=1,boot_id='target-boot',start_ticks=1)),\
                 patch.object(module,'collect_target',side_effect=AssertionError('Target not mounted yet')),\
                 contextlib.redirect_stdout(io.StringIO()):
                module.main()
            value=json.loads((out/'observer.json').read_text())
            self.assertEqual(value['data_paths'],['/srv/ax-target'])
            self.assertEqual(value['role'],'target')
            self.assertEqual(value['cgroups'],[dict(id='agent',path='/sys/fs/cgroup/ax-slot-1')])
            self.assertEqual([t['mount_directory'] for t in value['targets']],
                ['/srv/ax-target/m/target','/srv/ax-target/m/cold'])
            self.assertEqual(value['targets'][0]['url'],'http://127.0.0.1:9567/metrics')
            self.assertEqual(value['processes'][0]['boot_id'],'target-boot')

    def test_source_cannot_be_mislabeled_as_destination(self):
        with tempfile.TemporaryDirectory() as tmp:
            out=Path(tmp)/'observer'
            argv=['make-observer.py','--role','target','--product','brewfs',
                '--metric-contract-sha256','b'*64,'--mount-directory','/srv/ax-target/m/target','--out',str(out)]
            with patch.object(module.sys,'argv',argv),\
                 patch.object(module.socket,'gethostname',return_value='ax-pilot-20261111-client'),\
                 self.assertRaisesRegex(ValueError,'Wrong observer host'):
                module.main()
            self.assertFalse(out.exists())


if __name__=='__main__':unittest.main()
