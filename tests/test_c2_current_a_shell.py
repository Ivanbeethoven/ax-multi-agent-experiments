"""Execute the C2 controller's actual slot and closure commands in a local shell."""
import json
import os
from pathlib import Path
import runpy
import shutil
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
CONTROLLER = ROOT / (
    'experiments/unified-suite/pilot-aliyun/evidence/preparation/'
    'c2-current-trace-a-draft-01/run-c2-current-a.py'
)


@unittest.skipUnless(os.name == 'posix' and shutil.which('python3'), 'requires a POSIX shell and python3')
class C2ShellTest(unittest.TestCase):
    def setUp(self):
        self.controller = runpy.run_path(str(CONTROLLER))
        tmp = tempfile.TemporaryDirectory(prefix='ax-c2-shell-')
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)

    def shell(self, command, *, check=True):
        return subprocess.run(['/bin/sh', '-c', command], cwd=self.root, check=check,
                              capture_output=True, text=True, timeout=5)

    def test_slot_command_passes_paths_and_only_creates_missing_slot(self):
        remote = self.root / "client's receipts"
        remote.mkdir()
        spec = remote / 'slot.json'
        spec.write_text('exact spec', encoding='utf-8')
        configure = self.root / "fixture's configure-node.py"
        configure.write_text(
            'import json, sys\nfrom pathlib import Path\n'
            'out = Path(sys.argv[5]); out.mkdir()\n'
            'result = {"argv": sys.argv[1:], "spec": Path(sys.argv[3]).read_text()}\n'
            '(out / "observed.json").write_text(json.dumps(result))\n',
            encoding='utf-8',
        )
        slot = self.root / 'fresh slot'
        out = remote / 'slot-setup'
        command = self.controller['client_slot_command'](remote, slot=slot, configure=configure)
        self.shell(command)
        self.assertEqual(json.loads((out / 'observed.json').read_bytes()),
                         dict(argv=['slot', '--spec', str(spec), '--out', str(out)], spec='exact spec'))
        slot.mkdir()
        self.shell(command)  # A second configure call would fail at out.mkdir().

    def test_closure_command_checks_both_mounts_and_original_slot(self):
        slot = self.root / "client's slot"
        slot.mkdir()
        procs = slot / 'cgroup.procs'
        procs.touch()
        mountinfo = self.root / 'simulated mountinfo'
        output = self.root / "client's-output"
        command = self.controller['client_closure_command'](output, slot=slot, mountinfo=mountinfo)
        mountinfo.write_text('24 1 0:1 / /unrelated rw - ext4 /dev/vda rw\n', encoding='utf-8')
        self.assertEqual(self.shell(command).stdout.strip(), 'False')
        for leaf in ('writer-mnt', 'cold-mnt'):
            with self.subTest(mount=leaf):
                mountinfo.write_text(f'24 1 0:1 / {output / leaf} rw - fuse test rw\n', encoding='utf-8')
                self.assertEqual(self.shell(command).stdout.strip(), 'True')
        mountinfo.write_text('', encoding='utf-8')
        procs.write_text('12345\n', encoding='utf-8')
        self.assertEqual(self.shell(command).stdout.strip(), '12345\nFalse')
        procs.unlink()
        self.assertNotEqual(self.shell(command, check=False).returncode, 0)

    def test_failure_location_omits_messages_and_paths(self):
        def fail():
            raise ValueError('unarchived diagnostic text')

        try:
            fail()
        except ValueError as error:
            receipt = self.controller['failure_location'](error)
        self.assertEqual(receipt['error_class'], 'ValueError')
        self.assertEqual(set(receipt), {'error_class', 'traceback'})
        self.assertEqual(receipt['traceback'][-1]['function'], 'fail')
        for frame in receipt['traceback']:
            self.assertEqual(set(frame), {'file', 'line', 'function'})
            self.assertEqual(frame['file'], Path(__file__).name)
            self.assertGreater(frame['line'], 0)
        self.assertNotIn('unarchived diagnostic text', json.dumps(receipt))
