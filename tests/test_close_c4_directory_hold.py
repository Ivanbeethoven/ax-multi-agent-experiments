"""Offline safety checks: no test accesses real processes or sends a signal."""
import copy
import importlib.util
import json
from pathlib import Path
import signal
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch


SCRIPT = Path(__file__).resolve().parents[1]/'experiments/unified-suite/pilot-aliyun/scripts/close-c4-directory-hold.py'
spec = importlib.util.spec_from_file_location('close_c4_directory_hold', SCRIPT)
close = importlib.util.module_from_spec(spec)
spec.loader.exec_module(close)


class ParentClose(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.control = self.root/'control'
        self.batch = self.control/'batch'
        self.off = self.control/'calibration/off1'
        self.tool = self.root/'code/parent.py'
        self.request = self.root/'requests/batch.json'
        self.reuse = self.root/'requests/reuse.json'
        self.calibrator = self.root/'code/calibrate.py'
        self.doc(self.tool, {})
        self.doc(self.calibrator, {})
        self.doc(self.reuse, {})
        self.body = dict(purpose='observer-calibration-off', attempt=close.ATTEMPT,
                         verification_policy='full-reference-v1', collector_name='old', collector_out='/old',
                         fork=dict(preparation=dict(path='/original/preparation.json', sha256='1'*64)))
        trials = [dict(mode='off' if i == 1 else 'on', attempt=close.ATTEMPT if i == 1 else 'on'+str(i),
                       out=str(self.off if i == 1 else self.control/('on'+str(i)))) for i in range(1, 7)]
        self.old = dict(schema='c4-fork-batch-v1', reviewed=True, out=str(self.batch), control=str(self.control),
                        cell_id=close.CELL, calibration_verification='touched-cold-v1', trials=trials,
                        tools=dict(calibrate=close.ref(self.calibrator)), asset='/original/asset', formal_attempt='formal')
        request_pin = self.doc(self.request, self.old)
        self.doc(self.batch/'request.json', self.old)
        self.doc(self.batch/'locks-held.json', dict(request=request_pin, pool=close.POOL))
        trial_pin = self.doc(self.batch/'trial-1-request.json', self.body)
        self.doc(self.batch/'trial-1/intent.json', dict(argv=['python3', str(self.calibrator), '--request',
                    trial_pin['path'], '--sha256', trial_pin['sha256']], automatic_retry=False))
        (self.batch/'trial-2').mkdir()
        self.doc(self.off/'request.json', self.body)
        self.doc(self.off/'context.json', self.body)
        q = self.off/'raw/client/q'/close.ATTEMPT
        parent_identity = dict(schema='c4-parent-stat-v1', entries=100101, sha256='2'*64)
        source = dict(path='/source.json', sha256='3'*64)
        expected = dict(path='/expected.json', sha256='4'*64)
        full = dict(passed=True, verification_policy='full-reference-v1', cell_id=close.CELL,
            child_name=close.ATTEMPT, files=100000, work_tag='child-01', parent_identity=parent_identity,
            expected_child=expected, full_cold_child=True, parent_unchanged=True, parent_matches=True,
            child_matches=True, full_reference=None, touched_paths=None, namespace_entries=dict(parent=100100, child=100100),
            content_read=dict(parent=dict(files=100000, bytes=409600000), child=dict(files=100000, bytes=409591841)),
            binding=dict(cell_id=close.CELL, files=100000, work_tag='child-01', arm='native',
                         preparation=self.body['fork']['preparation'], asset=dict(path='/original/asset/asset.json',sha256='5'*64),
                         source_tree=source, expected_child=expected))
        proofs = dict(client_verify=self.doc(q/'verify/result.json', full),
            parent_verify=self.doc(q/'verify/parent.json', dict(passed=True, verification_policy='full-reference-v1',
                cell_id=close.CELL, expected=source, full_cold_parent=True, parent_identity=parent_identity)),
            native_exit=self.doc(q/'exit.json', dict(passed=True, exit_code=0, mount_absent=True)),
            verify_exit=self.doc(q/'verify-exit.json', dict(passed=True, exit_code=0, mount_absent=True)),
            cohort=self.doc(self.off/'cohort/result.json', dict(passed=True, workload_passed=True, purpose=self.body['purpose'])),
            work=self.doc(self.off/'work-check.json', dict(passed=True)),
            closure=self.doc(self.off/'closure.json', dict(passed=True, all_exited=True, all_unmounted=True,
                                                         pending_upload_bytes=0, pending_upload_operations=0)))
        archive = self.off/'archives/client.tar.gz'
        archive.parent.mkdir()
        archive.write_bytes(b'archive bytes never rehashed by the tool')
        archives = self.doc(self.off/'archives.json', [dict(path=str(archive), sha256='6'*64)])
        self.doc(archive.parent/'client-check.json', dict(schema='suite-archive-check-v1', passed=True,
            archive=str(archive), sha256='6'*64, bytes=archive.stat().st_size, kind='client', cell_id=close.CELL, deferred=[]))
        self.finished = dict(passed=True, automatic_retry=False, pool=close.POOL, purpose=self.body['purpose'],
                             collector_name='old', collector_out='/old', archives=archives, **proofs)
        self.doc(self.off/'finished.json', self.finished)
        self.patches = patch.multiple(close, CONTROL=self.control, BATCH=self.batch, OFF1=self.off,
            TOOL=self.tool, TOOL_SHA=close.ref(self.tool)['sha256'], REQUEST=self.request, REQUEST_SHA=request_pin['sha256'],
            REUSE=self.reuse, REUSE_SHA=close.ref(self.reuse)['sha256'], TRIAL_SHA=trial_pin['sha256'])
        self.patches.start()
        self.addCleanup(self.patches.stop)
        group = ['0::/system.slice/'+close.UNIT]
        self.parent = dict(pid=close.PARENT, starttime=close.PARENT_START, state='T', ppid=1, raw_exit_status=0,
            cgroup=group, argv=['/usr/bin/python3', str(self.tool), '--request', str(self.request), '--sha256', close.REQUEST_SHA,
                              '--reuse-request', str(self.reuse), '--reuse-sha256', close.REUSE_SHA])
        self.child = dict(pid=close.CHILD, starttime=close.CHILD_START, state='Z', ppid=close.PARENT, raw_exit_status=0, cgroup=group)
        process = patch.object(close, 'process', side_effect=lambda pid: copy.deepcopy(self.parent if pid == close.PARENT else self.child))
        process.start()
        self.addCleanup(process.stop)
        unit = patch.object(close.subprocess, 'check_output', return_value='MainPID='+str(close.PARENT)+
                            '\nActiveState=active\nSubState=running\nInvocationID='+close.INVOCATION+'\n')
        unit.start()
        self.addCleanup(unit.stop)
        self.send = Mock()
        self.pidfd_closed = []
        real_close = close.os.close
        def release(fd):
            if fd == 77:
                self.pidfd_closed.append(fd)
            else:
                real_close(fd)
        for p in (patch.object(close.os, 'pidfd_open', return_value=77, create=True),
                  patch.object(close.os, 'close', side_effect=release), patch.object(close.signal, 'SIGCONT', 18, create=True),
                  patch.object(close.signal, 'pidfd_send_signal', self.send, create=True)):
            p.start()
            self.addCleanup(p.stop)
        self.receipt = self.root/'receipt'

    def doc(self, path, body):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(body), encoding='utf-8')
        return close.ref(path)

    def reject(self, message):
        with self.assertRaisesRegex(ValueError, message):
            close.run(self.receipt, True)
        self.send.assert_not_called()
        self.assertTrue((self.receipt/'intent.json').is_file())
        self.assertFalse(close.load(self.receipt/'failed.json')['signal_sent'])
        self.assertFalse((self.batch/'trial-1/exit.json').exists())

    def test_default_checks_without_pidfd_or_signal_or_original_exit(self):
        self.assertFalse(close.run(self.receipt)['signal_sent'])
        close.os.pidfd_open.assert_not_called()
        self.send.assert_not_called()
        self.assertFalse((self.batch/'trial-1/exit.json').exists())
        self.assertEqual(list((self.batch/'trial-2').iterdir()), [])

    def test_explicit_resume_sends_only_one_pidfd_sigcont(self):
        self.assertTrue(close.run(self.receipt, True)['signal_sent'])
        close.os.pidfd_open.assert_called_once_with(close.PARENT, 0)
        self.send.assert_called_once_with(77, signal.SIGCONT, None, 0)
        self.assertEqual(self.pidfd_closed, [77])
        self.assertFalse((self.batch/'trial-1/exit.json').exists())

    def test_live_child_rejected_before_evidence_inspection(self):
        self.child['state'] = 'S'
        with patch.object(close, 'evidence') as evidence:
            self.reject('must be a zombie')
        evidence.assert_not_called()

    def test_wrong_parent_pid_or_starttime(self):
        self.parent['pid'] += 1
        self.reject('parent identity changed')

    def test_failed_child_raw_status(self):
        self.child['raw_exit_status'] = 256
        self.reject('raw exit status 0')

    def test_child_reparented(self):
        self.child['ppid'] = 1
        self.reject('must be a zombie')

    def test_nonempty_fence(self):
        self.doc(self.batch/'trial-2/intent.json', {})
        self.reject('empty directory')

    def test_existing_on1_output(self):
        Path(self.old['trials'][1]['out']).mkdir()
        self.reject('next stage')

    def test_missing_finished_blocks_early_resume(self):
        (self.off/'finished.json').unlink()
        with self.assertRaises(FileNotFoundError):
            close.run(self.receipt, True)
        self.send.assert_not_called()

    def test_bad_pin(self):
        (self.off/'closure.json').write_text('{}')
        self.reject('SHA changed')

    def test_reduced_cold_proof_rejected_even_when_pin_matches(self):
        path = Path(self.finished['client_verify']['path'])
        proof = close.load(path)
        proof['content_read']['child']['files'] = 5
        self.finished['client_verify'] = self.doc(path, proof)
        self.doc(self.off/'finished.json', self.finished)
        self.reject('complete 100k')

    def test_identity_changes_after_pidfd_open_no_signal(self):
        def open_pidfd(*unused):
            self.parent['starttime'] += 1
            return 77
        close.os.pidfd_open.side_effect = open_pidfd
        self.reject('parent identity changed')
        self.assertEqual(self.pidfd_closed, [77])

    def test_stat_field_52_is_raw_wait_status_with_tricky_comm(self):
        fields = ['0'] * 50
        fields[0], fields[1], fields[19], fields[49] = 'Z', str(close.PARENT), str(close.CHILD_START), '256'
        value = close.parse_stat(str(close.CHILD)+' (python ) child) '+' '.join(fields), close.CHILD)
        self.assertEqual(value, dict(pid=close.CHILD, state='Z', ppid=close.PARENT,
                                    starttime=close.CHILD_START, raw_exit_status=256))
        with self.assertRaisesRegex(ValueError, 'stat identity'):
            close.parse_stat(str(close.CHILD+1)+' (python) '+' '.join(fields), close.CHILD)


if __name__ == '__main__':
    unittest.main()
