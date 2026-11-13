import json
import os
from pathlib import Path
import tempfile
import subprocess
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
