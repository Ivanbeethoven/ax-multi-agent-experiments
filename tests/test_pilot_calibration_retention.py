"""Raw calibration evidence must survive worker deletion without silent omissions."""
import importlib.util
from pathlib import Path
import socket
import sqlite3
import tarfile
import tempfile
import unittest
from unittest.mock import patch

FILE = Path(__file__).resolve().parents[1]/'experiments/unified-suite/pilot-aliyun/scripts/retain-calibrations.py'
spec = importlib.util.spec_from_file_location('calibration_retention', FILE)
module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)


class CalibrationRetention(unittest.TestCase):
    def test_all_raw_bytes_retained_and_cache_and_socket_excluded(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)/'worker'; observer = root/'o/cal-check-on1'
            sample = observer/'sample-000000'; sample.mkdir(parents=True)
            (sample/'host-diskstats.txt').write_bytes(b'253 16 vdb 1 2 3\n')
            (sample/'sample.json').write_bytes(b'{"passed":true}\n')
            (observer/'finished.json').write_bytes(b'{"passed":true}\n')
            mount = root/'m/cal-check-on1'; (mount/'cache').mkdir(parents=True)
            (mount/'cache/sensitive-non-evidence').write_bytes(b'excluded')
            (mount/'stopped.json').write_bytes(b'{"exit_code":0}\n')
            ipc = socket.socket(socket.AF_UNIX); ipc.bind(str(observer/'observer.sock')); ipc.close()
            dirs = ['o/cal-check-on1','m/cal-check-on1']
            files, skipped = module.file_list(root, dirs)
            before = module.stable_snapshot(root, files)
            out = root/'retained-calibrations/check'; out.mkdir(parents=True)
            archive = out/'raw.tar.gz'
            checked = module.pack_node_files(root, files, out, 'cell-1', 'calibration')
            self.assertTrue(module.archive_check.verify(archive, checked['sha256'])['passed'])
            with tarfile.open(archive) as tar:
                self.assertEqual(set(tar.getnames()), set(files)|{module.archive_check.MANIFEST})
                self.assertEqual(tar.extractfile('o/cal-check-on1/sample-000000/host-diskstats.txt').read(), b'253 16 vdb 1 2 3\n')
            self.assertEqual(checked['file_members'], 4)
            self.assertEqual(set(skipped), {'m/cal-check-on1/cache','o/cal-check-on1/observer.sock'})
            module.assert_unchanged(root, dirs, files, skipped, before)

    def test_added_deleted_and_rewritten_raw_data_are_detected(self):
        for change in ('add','delete','rewrite'):
            with self.subTest(change=change), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary); (root/'o').mkdir(); source = root/'o/raw'
                source.write_bytes(b'raw-a')
                (root/'o/unchanged').write_bytes(b'kept')
                files, skipped = module.file_list(root, ['o'])
                before = module.stable_snapshot(root, files)
                if change == 'add': (root/'o/new-sample').write_bytes(b'new')
                elif change == 'delete': source.unlink()
                else: source.write_bytes(b'raw-b')
                with self.assertRaisesRegex(ValueError, 'mutated'):
                    module.assert_unchanged(root, ['o'], files, skipped, before)

    def test_links_cannot_pull_unrelated_files_into_public_evidence(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); (root/'o').mkdir(); outside = root/'private'
            outside.write_text('private'); (root/'o/link').symlink_to(outside)
            with self.assertRaisesRegex(ValueError, 'Linked evidence'):
                module.file_list(root, ['o'])

    def test_missing_required_subtree_is_not_an_empty_success(self):
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaisesRegex(ValueError, 'Missing'):
                module.file_list(Path(temporary), ['o/missing'])

    def test_live_or_unclosed_mount_cannot_be_retained_as_closed(self):
        with patch.object(module, 'mount_lifecycle', return_value=None):
            with self.assertRaisesRegex(ValueError, 'not normally closed'):
                module.assert_closed_mount(Path('/unused'))
        with patch.object(module, 'mount_lifecycle', return_value={'state':'cleanly-stopped'}), \
                patch.object(module, 'load', return_value={'supervisor':{'pid':123}}), \
                patch.object(module, 'alive', return_value=True):
            with self.assertRaisesRegex(ValueError, 'supervisor remains live'):
                module.assert_closed_mount(Path('/unused'))

    def test_running_failed_and_reserved_cells_block_retention(self):
        for raw in ('LoadState=loaded\nActiveState=active\nSubState=running\nExecMainStatus=0\nExecMainCode=0\n',
                    'LoadState=loaded\nActiveState=failed\nSubState=failed\nExecMainStatus=1\nExecMainCode=1\n'):
            with patch.object(module.subprocess, 'check_output', return_value=raw):
                with self.assertRaisesRegex(ValueError, 'not exited normally'):
                    module.idle(Path('/unused'), 'ax-pilot-check')
        with tempfile.TemporaryDirectory() as temporary:
            control = Path(temporary)
            with sqlite3.connect(control/'state.sqlite') as db:
                db.execute('CREATE TABLE cell_leases (cell_id TEXT)')
            raw = 'LoadState=not-found\nActiveState=inactive\nSubState=dead\nExecMainStatus=0\nExecMainCode=0\n'
            with patch.object(module.subprocess, 'check_output', return_value=raw):
                self.assertEqual(module.idle(control, 'ax-pilot-check')['LoadState'], 'not-found')
                run = control/'one-cell-runs/other'; run.mkdir(parents=True)
                (run/'started.json').write_text('{}')
                with self.assertRaisesRegex(ValueError, 'remains unfinished'):
                    module.idle(control, 'ax-pilot-check')
                (run/'result.json').write_text('{}')
                with sqlite3.connect(control/'state.sqlite') as db:
                    db.execute("INSERT INTO cell_leases VALUES ('active')")
                with self.assertRaisesRegex(ValueError, 'remains reserved'):
                    module.idle(control, 'ax-pilot-check')


if __name__ == '__main__':
    unittest.main()
