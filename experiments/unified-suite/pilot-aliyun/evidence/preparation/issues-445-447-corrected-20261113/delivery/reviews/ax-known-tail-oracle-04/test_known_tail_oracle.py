import json
import ctypes
import errno
from contextlib import contextmanager
import os
from pathlib import Path
import tempfile
import subprocess
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import known_tail_oracle as oracle


class FakeMount:
    def __init__(self, root, config):
        self.root, self.config, self.pid = root, config, 10
        self.bad_unmount = False
        self.on_start = None
        self.starts = 0

    def current(self, root):
        if root != self.root:
            raise ValueError('wrong actual cache')
        return {'pid': self.pid, 'starttime': str(self.pid * 7), 'state': 'S',
                'cache_root': str(root), 'argv': ['brewfs', 'mount', '--config', str(self.config)],
                'executable_sha256': 'a' * 64}

    def unmount(self, old):
        return {'method': 'fusermount3 -u', 'forced': self.bad_unmount,
                'old_process_terminated': not self.bad_unmount}

    def start(self, root):
        self.root, self.pid = root, self.pid + 1
        self.starts += 1
        if self.on_start:
            self.on_start(self.starts)
        return self.current(root)


class OracleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.cache = self.root / 'cache-initial'
        self.cache.mkdir()
        self.config = self.root / 'config.yaml'
        self.config.write_text('value: unchanged\ncache:\n  root: ' + str(self.cache) + '\n')
        self.dataset, self.output = self.root / 'data', self.root / 'evidence'
        self.log, self.op_log = self.root / 'log', self.root / 'ops'
        self.log.write_text(''); self.op_log.write_text('')
        self.driver = FakeMount(self.cache, self.config)
        self.env = {'RUST_LOG': 'warn,asyncfuse::raw::logfs=debug',
                    'BREWFS_FUSE_WRITEBACK': '0', 'BREWFS_FUSE_OP_LOG': '1',
                    'BREWFS_FUSE_LOG_FILE': str(self.op_log),
                    'BREWFS_FUSE_DIRECT_IO': '', 'BREWFS_FUSE_WRITE_DIRECT_IO': '',
                    'BREWFS_FUSE_READ_DIRECT_IO': '1', 'BREWFS_CONFIG_PATH': str(self.config)}
        def env_reader(pid):
            return dict(self.env, BREWFS_CACHE_ROOT=str(self.driver.root))
        self.subject = oracle.Oracle(self.dataset, self.config, self.log, self.op_log,
                                     self.output, self.driver, self.root, env_reader)
        for name, value in [('TAIL_OFFSET', 48), ('TAIL_BYTES', 16), ('FILE_BYTES', 64)]:
            item = patch.object(oracle, name, value)
            item.start(); self.addCleanup(item.stop)
        item = patch.dict(os.environ, {}, clear=True)
        item.start(); self.addCleanup(item.stop)

    def write(self, letter, flags=0):
        result = self.subject.run('write-' + letter)
        self.assertTrue(result['passed'], result)
        unique = 1 if letter == 'a' else 2
        inode = result['write']['inode']
        with self.op_log.open('a') as stream:
            stream.write(f'[write] REQ unique: {unique} inode={inode} fh=1 offset=48 data_len=16 write_flags={flags} flags=0\n')
        return result

    def through_b(self):
        self.write('a')
        result = self.subject.run('check-a')
        self.assertTrue(result['passed'], result)
        self.write('b')

    def test_complete_known_oracle_and_same_then_fresh_remount(self):
        self.through_b()
        result = self.subject.run('verify-b')
        self.assertTrue(result['passed'], result)
        self.assertEqual(self.driver.starts, 2)
        self.assertTrue(self.cache.is_dir())
        self.assertNotEqual(self.driver.root, self.cache)
        self.assertTrue(result['fresh_cache_validation']['new_cache_was_empty'])
        for phase in ('warm_value', 'same_cache_value', 'fresh_cache_value'):
            self.assertEqual(result[phase]['sha256'], oracle.expected_sha(0x42))

    def test_prefix_corruption_rejected(self):
        self.write('a')
        with self.subject.path.open('r+b') as stream:
            stream.write(b'X')
        result = self.subject.run('check-a')
        self.assertFalse(result['passed'])
        self.assertFalse(result['value']['passed'])

    def test_same_wrong_bytes_cannot_serve_as_oracle(self):
        self.through_b()
        oracle.write_tail(self.subject.path, 0x41)
        result = self.subject.run('verify-b')
        self.assertFalse(result['passed'])
        self.assertEqual(self.driver.starts, 0)

    def test_same_cache_stale_a_rejected(self):
        self.through_b()
        self.driver.on_start = lambda _: oracle.write_tail(self.subject.path, 0x41)
        result = self.subject.run('verify-b')
        self.assertFalse(result['passed'])
        self.assertFalse(result['same_cache_value']['passed'])
        self.assertEqual(self.driver.starts, 1)

    def test_fresh_cache_stale_a_rejected(self):
        self.through_b()
        self.driver.on_start = lambda n: oracle.write_tail(self.subject.path, 0x41) if n == 2 else None
        result = self.subject.run('verify-b')
        self.assertFalse(result['passed'])
        self.assertFalse(result['fresh_cache_validation']['passed'])

    def test_nonzero_write_flags_rejected(self):
        self.write('a', flags=1)
        result = self.subject.run('check-a')
        self.assertFalse(result['passed'])
        self.assertIn('non-cached', result['reasons'][0])

    def test_missing_request_proof_rejected(self):
        self.write('a')
        self.op_log.write_text('')
        result = self.subject.run('check-a')
        self.assertFalse(result['passed'])

    def test_forced_unmount_rejected(self):
        self.through_b()
        self.driver.bad_unmount = True
        result = self.subject.run('verify-b')
        self.assertFalse(result['passed'])
        self.assertEqual(self.driver.starts, 0)

    def test_upload_warn_rejected(self):
        self.log.write_text('WARN pipeline upload failed after retries\n')
        result = self.subject.run('write-a')
        self.assertFalse(result['passed'])
        self.assertFalse(self.dataset.exists())

    def test_actual_writeback_environment_rejected(self):
        self.env['BREWFS_FUSE_WRITEBACK'] = '1'
        result = self.subject.run('write-a')
        self.assertFalse(result['passed'])

    def test_unpassed_phase_or_replay_rejected(self):
        self.write('a')
        result = self.subject.run('write-b')
        self.assertFalse(result['passed'])
        with self.assertRaises(ValueError):
            self.subject.run('write-b')

    def test_partial_pwrite_and_no_fsync(self):
        real = os.pwrite
        with patch.object(os, 'pwrite', side_effect=lambda fd, data, offset: real(fd, data[:3], offset)), \
             patch.object(os, 'fsync', side_effect=AssertionError('must not fsync')):
            self.write('a')
        self.assertTrue(oracle.check_value(self.subject.path, 0x41)['passed'])

    def test_truncation_rejected(self):
        self.write('a')
        os.truncate(self.subject.path, 63)
        with self.assertRaises(ValueError):
            oracle.check_value(self.subject.path, 0x41)

    def test_same_cache_binary_change_rejected(self):
        self.through_b()
        original = self.driver.current
        self.driver.current = lambda root: dict(original(root), executable_sha256=('b' if self.driver.starts else 'a') * 64)
        result = self.subject.run('verify-b')
        self.assertFalse(result['passed'])

    def test_fresh_cache_environment_change_rejected(self):
        self.through_b()
        self.driver.on_start = lambda n: self.env.update(BREWFS_FUSE_DIRECT_IO='1') if n == 2 else None
        result = self.subject.run('verify-b')
        self.assertFalse(result['passed'])
        self.assertIn('non-cache environment', result['reasons'][0])

    def test_duplicate_write_request_rejected(self):
        self.write('a')
        self.op_log.write_text(self.op_log.read_text() * 2)
        result = self.subject.run('check-a')
        self.assertFalse(result['passed'])

    def changed_second_stat(self, fields):
        real = os.fstat
        calls = 0
        def capture(fd):
            nonlocal calls
            calls += 1
            value = real(fd)
            values = {'st_' + name: number for name, number in oracle.stat_details(value).items()}
            if calls == 2:
                for name in fields:
                    values['st_' + name] += 1
            return SimpleNamespace(**values)
        return capture

    @contextmanager
    def changed_stat_pair(self, fields):
        real_statx = oracle.force_statx
        statx_calls = 0
        def capture(fd):
            nonlocal statx_calls
            statx_calls += 1
            result = real_statx(fd)
            if statx_calls == 2:
                for name in fields:
                    result['stat'][name] += 1
            return result
        with patch.object(os, 'fstat', side_effect=self.changed_second_stat(fields)), \
             patch.object(oracle, 'force_statx', side_effect=capture):
            yield

    def test_each_original_identity_field_still_rejects_with_detail(self):
        self.write('a')
        for name in ('ino', 'size', 'mtime_ns'):
            with self.subTest(name=name), self.changed_stat_pair([name]):
                with self.assertRaises(oracle.OracleReadError) as raised:
                    oracle.check_value(self.subject.path, 0x41)
            detail = raised.exception.details
            self.assertEqual(detail['changed_identity_fields'], [name])
            self.assertEqual(detail['bytes'], 64)
            self.assertEqual(detail['sha256'], oracle.expected_sha(0x41))
            self.assertEqual(detail['mismatches'], [])
            self.assertEqual(detail['read_calls'], 2)
            self.assertTrue(detail['eof_observed'])
            self.assertGreater(detail['elapsed_ns'], 0)

    def test_ctime_atime_are_diagnostic_and_do_not_add_new_rejections(self):
        self.write('a')
        with self.changed_stat_pair(['ctime_ns', 'atime_ns']):
            detail = oracle.check_value(self.subject.path, 0x41)
        self.assertTrue(detail['passed'])
        self.assertNotEqual(detail['before']['ctime_ns'], detail['after']['ctime_ns'])
        self.assertNotEqual(detail['before']['atime_ns'], detail['after']['atime_ns'])
        self.assertEqual(detail['changed_identity_fields'], [])

    def test_failed_stat_read_details_survive_phase_json(self):
        self.write('a')
        with self.changed_stat_pair(['mtime_ns']):
            result = self.subject.run('check-a')
        saved = json.loads((self.output / 'check-a.json').read_text())
        self.assertFalse(saved['passed'])
        self.assertEqual(saved['read_failure'], result['read_failure'])
        self.assertEqual(saved['read_failure']['changed_identity_fields'], ['mtime_ns'])
        self.assertEqual(saved['read_failure']['bytes'], 64)
        self.assertFalse(saved['drain_evidence']['snapshots_present'])

    def test_short_read_keeps_stable_stats_and_actual_length(self):
        self.write('a')
        with patch.object(os, 'read', side_effect=[bytes(48) + b'A' * 15, b'']):
            with self.assertRaises(oracle.OracleReadError) as raised:
                oracle.check_value(self.subject.path, 0x41)
        detail = raised.exception.details
        self.assertEqual(detail['changed_identity_fields'], [])
        self.assertEqual(detail['failure_conditions'], ['read_length'])
        self.assertEqual(detail['bytes'], 63)
        self.assertEqual(detail['read_calls'], 2)
        self.assertTrue(detail['eof_observed'])

    def test_first_mismatch_is_absolute_and_survives_identity_failure(self):
        self.write('a')
        with self.subject.path.open('r+b') as stream:
            stream.seek(51)
            stream.write(b'Z')
        with self.changed_stat_pair(['mtime_ns']):
            result = self.subject.run('check-a')
        detail = result['read_failure']
        self.assertEqual(detail['first_mismatch']['offset'], 51)
        self.assertEqual(detail['first_mismatch']['actual_byte'], ord('Z'))
        self.assertEqual(detail['first_mismatch']['expected_byte'], ord('A'))
        self.assertTrue(detail['mismatches'])
        self.assertNotEqual(detail['sha256'], detail['expected_sha256'])

    def test_read_io_failure_preserves_partial_hash_and_count(self):
        self.write('a')
        with patch.object(os, 'read', side_effect=[bytes(16), OSError(5, 'injected read error')]):
            result = self.subject.run('check-a')
        detail = result['read_failure']
        self.assertFalse(result['passed'])
        self.assertEqual(detail['bytes'], 16)
        self.assertEqual(detail['read_calls'], 2)
        self.assertEqual(detail['io_error']['errno'], 5)
        self.assertEqual(detail['sha256'], oracle.durability.sha(bytes(16)))
        self.assertIsNone(detail['after'])

    def test_invalid_initial_size_preserves_before_without_reading(self):
        self.write('a')
        os.truncate(self.subject.path, 63)
        result = self.subject.run('check-a')
        detail = result['read_failure']
        self.assertFalse(result['passed'])
        self.assertEqual(detail['before']['size'], 63)
        self.assertEqual(detail['read_calls'], 0)
        self.assertEqual(detail['failure_conditions'], ['initial_mode_or_size'])

    def test_native_drain_snapshots_are_referenced_without_new_fuse_reads(self):
        self.write('a')
        directory = self.output.parent / 'diagnostics'
        directory.mkdir()
        for phase in ('before', 'after'):
            (directory / ('stats-known-tail-write-a-post-write-drained-' + phase + '.txt')).write_text(
                '2026-11-12T21:59:23+00:00\n\nbrewfs_buffer_dirty_bytes 0\n'
                'brewfs_writeback_dirty_bytes 0\nbrewfs_s3_put_ops_total 2\n')
        result = self.subject.run('check-a')
        self.assertTrue(result['passed'])
        snapshots = result['drain_evidence']['snapshots']
        self.assertEqual(len(snapshots), 2)
        for item in snapshots:
            self.assertEqual(item['sha256'], oracle.durability.sha(Path(item['path']).read_bytes()))
            self.assertEqual(item['native_counters']['brewfs_buffer_dirty_bytes'], 0)
            self.assertEqual(item['native_counters']['brewfs_s3_put_ops_total'], 2)

    def test_real_statx_tmpfile_flags_mask_and_fstat_alignment(self):
        self.write('a')
        result = oracle.check_value(self.subject.path, 0x41)
        self.assertTrue(result['passed'])
        for stage in ('before', 'after'):
            fresh = result['statx_' + stage]
            self.assertTrue(fresh['passed'])
            self.assertEqual(fresh['flags'], 0x3000)
            self.assertEqual(fresh['path'], '')
            self.assertEqual(fresh['requested_mask'], 0x7ff)
            self.assertEqual(fresh['missing_required_mask'], 0)
            self.assertEqual(len(bytes.fromhex(fresh['raw_struct_hex'])), 256)
            self.assertEqual(result['statx_' + stage + '_fstat_mismatches'], [])
        self.assertLessEqual(result['opened_unix_ns'], result['statx_before']['started_unix_ns'])
        self.assertLessEqual(result['statx_before']['finished_unix_ns'], result['first_read_started_unix_ns'])
        self.assertLessEqual(result['last_read_finished_unix_ns'], result['statx_after']['started_unix_ns'])

    def test_statx_enosys_fails_before_any_data_read(self):
        self.write('a')
        def denied(*args):
            ctypes.set_errno(errno.ENOSYS)
            return -1
        with patch.object(oracle, 'load_statx', return_value=denied), \
             patch.object(os, 'read', side_effect=AssertionError('must not read after failed statx')):
            result = self.subject.run('check-a')
        self.assertFalse(result['passed'])
        self.assertEqual(result['read_failure']['statx_before']['errno'], errno.ENOSYS)
        self.assertEqual(result['read_failure']['read_calls'], 0)

    def test_statx_missing_symbol_fails_closed(self):
        self.write('a')
        with patch.object(oracle, 'load_statx', side_effect=AttributeError('statx unavailable')):
            result = self.subject.run('check-a')
        self.assertFalse(result['passed'])
        self.assertEqual(result['read_failure']['failure_conditions'], ['statx_before'])

    def test_each_required_statx_mask_bit_fails_closed(self):
        self.write('a')
        function = oracle.load_statx()
        for bit in (0x001, 0x002, 0x040, 0x100, 0x200):
            def missing(fd, path, flags, mask, pointer):
                code = function(fd, path, flags, mask, pointer)
                pointer._obj.stx_mask &= ~bit
                return code
            with self.subTest(bit=bit), patch.object(oracle, 'load_statx', return_value=missing):
                with self.assertRaises(oracle.OracleReadError) as raised:
                    oracle.check_value(self.subject.path, 0x41)
            self.assertEqual(raised.exception.details['statx_before']['missing_required_mask'], bit)
            self.assertEqual(raised.exception.details['read_calls'], 0)

    def test_statx_fstat_mismatch_is_not_accepted_as_fresh(self):
        self.write('a')
        original = oracle.force_statx
        def wrong(fd):
            result = original(fd)
            result['stat']['mtime_ns'] += 1
            return result
        with patch.object(oracle, 'force_statx', side_effect=wrong):
            result = self.subject.run('check-a')
        self.assertFalse(result['passed'])
        self.assertEqual(result['read_failure']['statx_before_fstat_mismatches'], ['mtime_ns'])
        self.assertEqual(result['read_failure']['read_calls'], 0)

    def test_after_statx_failure_preserves_full_content_but_rejects(self):
        self.write('a')
        original = oracle.force_statx
        calls = 0
        def second_fails(fd):
            nonlocal calls
            calls += 1
            result = original(fd)
            if calls == 2:
                result['passed'] = False
                result['error'] = {'type': 'OSError', 'errno': errno.EIO, 'message': 'injected'}
            return result
        with patch.object(oracle, 'force_statx', side_effect=second_fails):
            result = self.subject.run('check-a')
        detail = result['read_failure']
        self.assertFalse(result['passed'])
        self.assertEqual(detail['failure_conditions'], ['statx_after'])
        self.assertEqual(detail['bytes'], 64)
        self.assertEqual(detail['sha256'], oracle.expected_sha(0x41))
        self.assertTrue(detail['eof_observed'])

    def test_statx_invalid_timestamp_fails_closed(self):
        self.write('a')
        function = oracle.load_statx()
        def invalid(fd, path, flags, mask, pointer):
            code = function(fd, path, flags, mask, pointer)
            pointer._obj.stx_mtime.tv_nsec = 1_000_000_000
            return code
        with patch.object(oracle, 'load_statx', return_value=invalid):
            result = self.subject.run('check-a')
        self.assertFalse(result['passed'])
        self.assertIn('invalid timestamp', result['read_failure']['statx_before']['error']['message'])

    def test_bad_abi_is_rejected_without_libc_call(self):
        with patch.object(ctypes, 'sizeof', return_value=1):
            result = oracle.force_statx(-1)
        self.assertFalse(result['passed'])
        self.assertIn('ABI size', result['error']['message'])


class ShellOrderTests(unittest.TestCase):
    def run_shell(self, fail_drain=False):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            fragment = Path(__file__).with_name('known-tail-oracle.sh')
            script = r'''
set -euo pipefail
source "$FRAGMENT"
artifact_dir="$ROOT"
mount_dir=/mnt/brewfs
config_path=/run/brewfs/config.yaml
log_file="$ROOT/log"
export BREWFS_FUSE_WRITEBACK=0 BREWFS_FUSE_OP_LOG=1
export RUST_LOG=warn,asyncfuse::raw::logfs=debug
export BREWFS_FUSE_LOG_FILE="$ROOT/ops"
err() { printf '%s\n' "$*" >&2; }
timeout() {
    local phase="${6}"
    printf '%s\n' "$phase" >>"$ROOT/calls"
    mkdir -p "$ROOT/known-tail-oracle"
    printf '%s\n' /var/lib/brewfs/cache-new >"$ROOT/known-tail-oracle/new-cache-root.txt"
}
wait_for_strict_drain() {
    printf 'drain %s\n' "$*" >>"$ROOT/calls"
    if [[ "$FAIL_DRAIN" == 1 && "$1" == known-tail-write-b ]]; then return 1; fi
}
run_known_tail_oracle
'''
            result = subprocess.run(['bash', '-c', script], env=dict(os.environ, ROOT=str(root), FRAGMENT=str(fragment), FAIL_DRAIN=str(int(fail_drain))), capture_output=True, text=True)
            return result, (root / 'calls').read_text().splitlines(), (root / 'known-tail-oracle.invalid').exists()

    def test_exact_order_and_fixed_drain_contract(self):
        result, calls, invalid = self.run_shell()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(calls, ['write-a', 'drain known-tail-write-a post-write 180 2 0 known-tail-oracle',
                                 'check-a', 'write-b', 'drain known-tail-write-b post-write 180 2 0 known-tail-oracle', 'verify-b'])
        self.assertFalse(invalid)

    def test_drain_failure_prevents_remount_verification(self):
        result, calls, invalid = self.run_shell(True)
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn('verify-b', calls)
        self.assertTrue(invalid)


if __name__ == '__main__':
    unittest.main()
