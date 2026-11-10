"""Checkpoint bytes, symlink boundaries and empty HOME for C4 preparation."""
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
FILE = ROOT/'experiments/unified-suite/pilot-aliyun/scripts/prepare-session-input.py'
spec = importlib.util.spec_from_file_location('pilot_session_input', FILE)
module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
from bench import experiment_step

phase_spec = importlib.util.spec_from_file_location('pilot_session_phase', FILE.parent/'draft-session-phase.py')
phase_module = importlib.util.module_from_spec(phase_spec); phase_spec.loader.exec_module(phase_module)
worker_spec = importlib.util.spec_from_file_location('pilot_native_session', FILE.parent/'session-worker.py')
worker_module = importlib.util.module_from_spec(worker_spec); worker_spec.loader.exec_module(worker_module)


class PrivateMountEnvironment(unittest.TestCase):
    def test_secret_values_reach_only_child_environment_and_not_retained_logs(self):
        with tempfile.TemporaryDirectory() as tmp:
            r = Path(tmp); private = r/'private.json'
            private.write_text(json.dumps({'AWS_ACCESS_KEY_ID':'fake-key-for-local-test',
                'AWS_SECRET_ACCESS_KEY':'fake-secret-for-local-test'})); private.chmod(0o600)
            ref = dict(path=str(private), sha256=module.digest(private))
            python = str(Path(sys.executable).resolve())
            step = dict(schema='experiment-step-v1', reviewed=True, cell_id='c4-test',stage='mount',
                argv=[python,'-c',"import os; assert os.environ.get('AWS_SECRET_ACCESS_KEY'); print('ok')"],
                cwd=str(r), executable_sha256=module.digest(Path(python)), inputs=[ref],
                inherit_environment=['AWS_ACCESS_KEY_ID','AWS_SECRET_ACCESS_KEY'], protected_environment=ref)
            file = r/'step.json'; file.write_text(json.dumps(step))
            _, env = experiment_step.prepare(file, module.digest(file))
            self.assertEqual(env['AWS_SECRET_ACCESS_KEY'],'fake-secret-for-local-test')
            self.assertNotIn('protected_environment',env)
            out=r/'execution'
            self.assertEqual(experiment_step.execute(file,module.digest(file),out),0)
            self.assertEqual((out/'stdout.log').read_text().strip(),'ok')
            for retained in out.rglob('*'):
                if retained.is_file():
                    self.assertNotIn(b'fake-secret-for-local-test',retained.read_bytes())
            private.write_text('{}')
            with self.assertRaisesRegex(ValueError,'input changed'):
                experiment_step.prepare(file, module.digest(file))

    @unittest.skipUnless(sys.platform == 'linux', 'POSIX private mode')
    def test_public_credentials_and_layout_controls_are_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            r=Path(tmp); private=r/'private.json'; python=Path(sys.executable).resolve()
            def prepare(values,mode):
                private.write_text(json.dumps(values));private.chmod(mode)
                ref=dict(path=str(private),sha256=module.digest(private))
                s=dict(schema='experiment-step-v1',reviewed=True,cell_id='c4',stage='mount',
                    argv=[str(python),'-V'],cwd=str(r),executable_sha256=module.digest(python),
                    inputs=[ref],inherit_environment=list(values),protected_environment=ref)
                f=r/'step.json';f.write_text(json.dumps(s))
                return experiment_step.prepare(f,module.digest(f))
            with self.assertRaisesRegex(ValueError,'0600'):
                prepare({'AWS_SECRET_ACCESS_KEY':'fake'},0o644)
            with self.assertRaisesRegex(ValueError,'only explicitly inherited AWS'):
                prepare({'RUSTFS_EC_BLOCK_SIZE':'unexpected-control'},0o600)


class SessionPhaseDraft(unittest.TestCase):
    def test_stopped_check_cannot_acquire_an_unmounted_supervisor(self):
        with tempfile.TemporaryDirectory() as tmp:
            r=Path(tmp);f=r/'mount.json';f.write_text(json.dumps(dict(purpose='experiment',
                mount_point='/srv/ax-pilot-session',anchor=dict(workspace='w'))))
            ref=dict(path=str(f),sha256=module.digest(f));mount=dict(spec=ref,directory='/source-supervisor')
            request=dict(phase=dict(action='verify-stopped',mount=mount),purpose='experiment',
                worker_id='a01',mount=mount,session_worker=dict(path=str(FILE),sha256=module.digest(FILE)),
                phase_output=str(r/'result'),cell_id='c4',workload_id='handoff-1')
            result=phase_module.draft(request,r/'draft');worker=json.loads(Path(result['path']).read_text())
            self.assertFalse(worker['requires_mount']);self.assertNotIn('mount',worker)
            self.assertTrue(worker['jobs'][0]['result']['equals']['all_exited'])

    def test_phase_cannot_switch_the_canonical_project(self):
        with tempfile.TemporaryDirectory() as tmp:
            r=Path(tmp);f=r/'mount.json';f.write_text(json.dumps(dict(purpose='experiment',mount_point='/shared')))
            ref=dict(path=str(f),sha256=module.digest(f))
            request=dict(phase=dict(action='export',binary=dict(path=str(FILE),sha256=module.digest(FILE)),
                workspace='/outside/workspace',project='/outside/workspace/repo'),purpose='experiment',
                worker_id='a01',mount=dict(spec=ref,directory='/supervisor'),
                session_worker=dict(path=str(FILE),sha256=module.digest(FILE)))
            with self.assertRaisesRegex(ValueError,'canonical project'):
                phase_module.draft(request,r/'draft')

    def test_normal_stop_check_rejects_a_live_original_supervisor(self):
        with tempfile.TemporaryDirectory() as tmp:
            r=Path(tmp);f=r/'mount.json';anchor=dict(workspace='original')
            f.write_text(json.dumps(dict(anchor=anchor,mount_point='/shared')))
            ref=dict(path=str(f),sha256=module.digest(f));directory=r/'supervisor';directory.mkdir()
            original=dict(mount_id=123,point='/shared')
            for name,value in [('ready.json',dict(spec=ref,anchor=anchor,mount=original)),
                ('process.json',dict(spec=ref,process=dict(pid=100),supervisor=dict(pid=101))),
                ('stopped.json',dict(anchor=anchor,original_mount=original,passed=True,exit_code=0,
                    mount_absent=True,active_workers=[]))]:
                (directory/name).write_text(json.dumps(value))
            request=dict(action='verify-stopped',mount=dict(spec=ref,directory=str(directory)))
            with patch.object(worker_module,'mounts',return_value=[]),patch.object(worker_module,'alive',return_value=True):
                with self.assertRaisesRegex(ValueError,'not stopped normally'):
                    worker_module.run(request,r/'failed')
            self.assertFalse((r/'failed/result.json').exists())
            with patch.object(worker_module,'mounts',return_value=[]),patch.object(worker_module,'alive',return_value=False):
                self.assertTrue(worker_module.run(request,r/'closed')['all_exited'])


@unittest.skipUnless(sys.platform == 'linux', 'Linux filesystem metadata and fsync')
class SessionInput(unittest.TestCase):
    def test_real_tree_roundtrip_and_fsync_preserve_binary_payloads_and_internal_links(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)/'workspace'; root.mkdir()
            (root/'data').mkdir(); (root/'data/db').write_bytes(bytes(range(256))*1024)
            (root/'link').symlink_to('data/db')
            before = module.tree(root)
            receipt = module.sync_tree(root)
            self.assertEqual(receipt['files'], 1)
            self.assertEqual(receipt['file_bytes'], 262144)
            self.assertEqual(module.tree(root), before)
            (root/'data/db').write_bytes(b'corrupt')
            self.assertNotEqual(module.tree(root), before)

    def test_external_runtime_symlink_is_rejected_before_any_copy(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)/'workspace'; root.mkdir()
            outside = Path(tmp)/'private'; outside.write_text('must not travel')
            (root/'escape').symlink_to(outside)
            with self.assertRaisesRegex(ValueError, 'leaves workspace'):
                module.tree(root)

    def test_hot_checkpoint_is_rejected_before_mount_or_workspace_access(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp)/'checkpoint.json'
            p.write_text(json.dumps(dict(source_process_exited=False)))
            request = dict(checkpoint_directory_receipt=dict(path=str(p), sha256=module.digest(p)))
            with patch.object(module.node, 'control') as control:
                with self.assertRaisesRegex(ValueError, 'Closed, verified'):
                    module.run(request, Path(tmp)/'output')
                control.assert_not_called()


if __name__ == '__main__':
    unittest.main()
