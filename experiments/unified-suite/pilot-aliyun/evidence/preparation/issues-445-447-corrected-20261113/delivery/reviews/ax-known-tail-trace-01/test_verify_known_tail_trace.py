import copy
import datetime
import json
from pathlib import Path
import struct
import tempfile
import unittest

import verify_known_tail_trace as gate


class TraceGateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        (self.root / 'known-tail-oracle').mkdir()
        self.lines = []
        self.base = 1794522300 * 1_000_000_000
        self.a = self.wave(0, 401, 0x41)
        self.b = [self.wave(i, 402, 0x42) for i in (1, 2, 3)]
        self.phases = {
            'check-a': {'passed': True, 'reasons': [], 'started_unix_ns': self.base, 'value': self.a},
            'verify-b': {'passed': True, 'reasons': [], 'started_unix_ns': self.base + 100_000_000,
                         'warm_value': self.b[0], 'same_cache_value': self.b[1], 'fresh_cache_value': self.b[2]},
        }

    def tearDown(self):
        self.temp.cleanup()

    def stamp(self, ns):
        return datetime.datetime.fromtimestamp(ns // 1_000_000_000, datetime.timezone.utc).strftime('%Y-%m-%dT%H:%M:%S') + f'.{ns % 1_000_000_000 // 1000:06d}Z'

    def wave(self, index, pid, value):
        base = self.base + index * 100_000_000
        ordinary = {'ino': 6, 'size': gate.FILE_BYTES, 'mtime_ns': self.base - 123456789,
                    'mode': 33152, 'dev': 53, 'atime_ns': self.base, 'ctime_ns': self.base}
        raw = bytearray(256)
        struct.pack_into('<I', raw, 0, 0x7ff)
        struct.pack_into('<H', raw, 28, ordinary['mode'])
        struct.pack_into('<QQ', raw, 32, ordinary['ino'], ordinary['size'])
        struct.pack_into('<qI', raw, 112, *divmod(ordinary['mtime_ns'], 1_000_000_000))

        def fresh(start, end):
            return {'passed': True, 'fd': 3, 'path': '', 'flags': 0x3000, 'requested_mask': 0x7ff,
                    'required_mask': 0x343, 'missing_required_mask': 0, 'return_code': 0, 'errno': 0,
                    'started_unix_ns': base + start * 1_000_000, 'finished_unix_ns': base + end * 1_000_000,
                    'values': {'stx_mask': 0x7ff}, 'raw_struct_hex': raw.hex(), 'stat': ordinary.copy()}

        def line(ms, text):
            self.lines.append(self.stamp(base + ms * 1_000_000) + ' DEBUG asyncfuse::raw::logfs: ' + text)

        def req(ms, ident, op, args):
            line(ms, f'ID: {ident} | [{op}] REQ Request {{ unique: {ident * 2}, uid: 0, gid: 0, pid: {pid} }} - Call_arg: {args}')

        def attr(ms, ident):
            sec, ns = divmod(ordinary['mtime_ns'], 1_000_000_000)
            line(ms, f'ID: {ident} | [getattr] - Success: ReplyAttr {{ ttl: 1s, attr: FileAttr {{ ino: 6, size: {gate.FILE_BYTES}, blocks: 8192, mtime: Timestamp {{ sec: {sec}, nsec: {ns} }} }} }}')

        req(1, 1, 'open', 'inode=6, flags=163840')
        line(2, 'ID: 1 | [open] - Success: ReplyOpen { fh: 12, flags: 1 }')
        req(5, 2, 'getattr', 'inode=6, fh=, flags=0')
        attr(6, 2)
        req(9, 3, 'read', f'inode=6, fh=12, offset=0, size={gate.FILE_BYTES}')
        line(10, f'ID: 3 [ObservedFilesystem<VFS>] read - Read {gate.FILE_BYTES} bytes')
        req(11, 4, 'read', f'inode=6, fh=12, offset={gate.FILE_BYTES}, size=1048576')
        line(12, 'ID: 4 [ObservedFilesystem<VFS>] read - Read 0 bytes')
        req(15, 5, 'getattr', 'inode=6, fh=, flags=0')
        attr(16, 5)
        req(18, 6, 'release', 'inode=6, fh=12, flags=163840')
        line(19, 'ID: 6 | [release] - Success: ()')
        return {'passed': True, 'failure_conditions': [], 'mismatches': [], 'first_mismatch': None,
                'changed_identity_fields': [], 'bytes': gate.FILE_BYTES, 'expected_bytes': gate.FILE_BYTES,
                'eof_observed': True, 'tail_byte': value, 'sha256': gate.expected_sha(value),
                'expected_sha256': gate.expected_sha(value), 'before': ordinary.copy(), 'after': ordinary.copy(),
                'inode': 6, 'reader_pid': pid, 'opened_unix_ns': base + 3_000_000,
                'first_read_started_unix_ns': base + 8_000_000, 'last_read_finished_unix_ns': base + 13_000_000,
                'statx_before': fresh(4, 7), 'statx_after': fresh(14, 17),
                'statx_before_fstat_mismatches': [], 'statx_after_fstat_mismatches': []}

    def assess(self):
        (self.root / 'brewfs_fuse_ops.log').write_text('\n'.join(self.lines) + '\n')
        for name, phase in self.phases.items():
            (self.root / 'known-tail-oracle' / (name + '.json')).write_text(json.dumps(phase))
        return gate.assess(self.root)

    def rejected(self, expected):
        result = self.assess()
        self.assertFalse(result['passed'])
        self.assertIn(expected, ' '.join(result['reasons']))

    def test_four_waves_with_restarted_ids_are_independent(self):
        result = self.assess()
        self.assertTrue(result['passed'], result['reasons'])
        self.assertEqual(len(result['checks']), 4)
        self.assertEqual([x['open']['id'] for x in result['checks']], [1] * 4)
        self.assertEqual(len({x['open']['request_line'] for x in result['checks']}), 4)

    def test_missing_pre_getattr_rejected(self):
        del self.lines[2:4]
        self.rejected('lacks one unambiguous FUSE GETATTR')

    def test_missing_post_getattr_rejected(self):
        del self.lines[8:10]
        self.rejected('lacks one unambiguous FUSE GETATTR')

    def test_getattr_error_rejected(self):
        self.lines[3] = self.lines[3].split(' - Success:')[0] + ' - Error: Errno(5)'
        self.rejected('GETATTR did not succeed')

    def test_wrong_getattr_pid_rejected(self):
        self.lines[2] = self.lines[2].replace('pid: 401', 'pid: 999')
        self.rejected('lacks one unambiguous FUSE GETATTR')

    def test_wrong_reply_mtime_rejected(self):
        self.lines[3] = self.lines[3].replace('nsec: 876543211', 'nsec: 876543212')
        self.rejected('GETATTR reply differs')

    def test_wrong_reply_inode_or_size_rejected(self):
        for old, new in [('ino: 6', 'ino: 7'), (f'size: {gate.FILE_BYTES}', 'size: 5')]:
            saved = self.lines[3]
            self.lines[3] = saved.replace(old, new)
            self.rejected('GETATTR reply differs')
            self.lines[3] = saved

    def test_getattr_outside_statx_window_rejected(self):
        self.a['statx_before']['finished_unix_ns'] = self.base + 4_100_000
        self.rejected('lacks one unambiguous FUSE GETATTR')

    def test_microsecond_tolerance_only_for_window_edges(self):
        self.a['statx_before']['started_unix_ns'] = self.base + 5_000_999
        self.assertTrue(self.assess()['passed'])
        self.a['statx_before']['started_unix_ns'] += 2
        self.rejected('lacks one unambiguous FUSE GETATTR')

    def test_read_gap_rejected(self):
        self.lines[4] = self.lines[4].replace('offset=0', 'offset=1')
        self.rejected('gap/overlap')

    def test_read_error_rejected(self):
        self.lines[5] = self.stamp(self.base + 10_000_000) + ' DEBUG asyncfuse::raw::logfs: ID: 3 | [read] - Error: Errno(5)'
        self.rejected('READ missing successful response')

    def test_wrong_read_handle_rejected(self):
        self.lines[4] = self.lines[4].replace('fh=12', 'fh=99')
        self.rejected('another file handle')

    def test_missing_eof_rejected(self):
        del self.lines[6:8]
        self.rejected('lacks full read plus EOF')

    def test_released_old_handle_cannot_replace_missing_open(self):
        # Remove the second B OPEN; an older same-PID/fh OPEN cannot be reused.
        del self.lines[24:26]
        self.rejected('released before')

    def test_unpassed_or_missing_phase_rejected(self):
        self.phases['verify-b']['passed'] = False
        self.rejected('oracle phase did not pass')

    def test_reusing_one_B_wave_is_rejected(self):
        self.phases['verify-b']['same_cache_value'] = copy.deepcopy(self.b[0])
        self.rejected('overlap or reuse')

    def test_errno_mask_flags_raw_and_hash_rejected(self):
        cases = [(['statx_before', 'errno'], 38, 'statx before failed'),
                 (['statx_before', 'missing_required_mask'], 0x40, 'missing statx result'),
                 (['statx_before', 'flags'], 0x1000, 'wrong statx flags'),
                 (['statx_before', 'raw_struct_hex'], bytes(256).hex(), 'raw statx mask'),
                 (['sha256'], '0' * 64, 'known-value SHA differs')]
        for keys, value, reason in cases:
            with self.subTest(keys=keys):
                subject = self.a
                for key in keys[:-1]:
                    subject = subject[key]
                old = subject[keys[-1]]
                subject[keys[-1]] = value
                self.rejected(reason)
                subject[keys[-1]] = old

    def test_helper_pass_cannot_hide_mtime_or_content_failure(self):
        self.a['changed_identity_fields'] = ['mtime_ns']
        self.rejected('identity changed')
        self.a['changed_identity_fields'] = []
        self.a['mismatches'] = [{'offset': 0}]
        self.rejected('content mismatch')


class ArchivedTraceTests(unittest.TestCase):
    def test_real_v3_retained_failure_and_missing_pre_force_getattr(self):
        root = Path('/home/dev/ax-perf-issues-validation/corrected-runtime-review-02/artifacts')
        self.assertTrue(root.is_dir())
        for artifact in root.glob('*/perf-run-*'):
            with self.subTest(artifact=artifact):
                result = gate.assess(artifact)
                self.assertFalse(result['passed'])
                events, _ = gate.parse_trace(artifact / 'brewfs_fuse_ops.log')
                phase = json.loads((artifact / 'known-tail-oracle/check-a.json').read_text())
                self.assertFalse(phase['passed'])
                inode = phase['read_failure']['before']['ino']
                opens = [x for x in events if x['operation'] == 'open' and x['arguments'].get('inode') == inode]
                opened = opens[-1]
                reads = [x for x in events if x['operation'] == 'read' and x['pid'] == opened['pid'] and x['request_line'] > opened['response_line']]
                self.assertTrue(reads)
                attrs = [x for x in events if x['operation'] == 'getattr' and x['pid'] == opened['pid'] and x['arguments'].get('inode') == inode and opened['response_line'] < x['request_line'] < reads[0]['request_line']]
                self.assertEqual(attrs, [], 'v3 unexpectedly has force-before GETATTR')


if __name__ == '__main__':
    unittest.main()
