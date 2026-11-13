#!/usr/bin/env python3
"""Non-performance known-value FUSE oracle; preserve all old staging/cache roots."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import time

import verify_remote_durability as durability

TAIL_OFFSET = 60 * 1024 * 1024
TAIL_BYTES = 4 * 1024 * 1024
FILE_BYTES = TAIL_OFFSET + TAIL_BYTES
FILE_NAME = 'known-tail.bin'
ENV_KEYS = ('RUST_LOG', 'BREWFS_CACHE_ROOT', 'BREWFS_FUSE_WRITEBACK',
            'BREWFS_FUSE_OP_LOG', 'BREWFS_FUSE_LOG_FILE',
            'BREWFS_FUSE_DIRECT_IO', 'BREWFS_FUSE_WRITE_DIRECT_IO',
            'BREWFS_FUSE_READ_DIRECT_IO', 'BREWFS_CONFIG_PATH')


def expected_sha(value):
    digest = hashlib.sha256()
    zero = bytes(min(1024 * 1024, TAIL_OFFSET))
    for offset in range(0, TAIL_OFFSET, len(zero)):
        digest.update(zero[:min(len(zero), TAIL_OFFSET - offset)])
    digest.update(bytes([value]) * TAIL_BYTES)
    return digest.hexdigest()


class OracleReadError(ValueError):
    def __init__(self, message, details):
        super().__init__(message)
        self.details = details


def stat_details(value):
    return {name: getattr(value, 'st_' + name) for name in
            ('ino', 'size', 'mode', 'dev', 'mtime_ns', 'ctime_ns', 'atime_ns')}


def check_value(path, value):
    """Check every byte against an independent oracle, not another read."""
    started = time.monotonic_ns()
    digest, offset, calls = hashlib.sha256(), 0, 0
    details = {'passed': False, 'before': None, 'after': None,
               'expected_bytes': FILE_BYTES, 'expected_sha256': expected_sha(value),
               'tail_byte': value, 'mismatches': [], 'first_mismatch': None,
               'failure_conditions': [], 'eof_observed': False}
    fd = None
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        before = os.fstat(fd)
        details['before'] = stat_details(before)
        details['inode'] = before.st_ino
        if not stat.S_ISREG(before.st_mode) or before.st_size != FILE_BYTES:
            details['failure_conditions'].append('initial_mode_or_size')
            raise OracleReadError('oracle file is not a regular full-size file', details)
        while True:
            calls += 1
            data = os.read(fd, 1024 * 1024)
            if not data:
                details['eof_observed'] = True
                break
            zeros = min(len(data), max(0, TAIL_OFFSET - offset))
            expected = bytes(zeros) + bytes([value]) * (len(data) - zeros)
            if data != expected:
                details['mismatches'].append({'offset': offset, 'bytes': len(data),
                                             'sha256': durability.sha(data),
                                             'expected_sha256': durability.sha(expected)})
                if details['first_mismatch'] is None:
                    index = next(i for i, pair in enumerate(zip(data, expected))
                                 if pair[0] != pair[1])
                    details['first_mismatch'] = {
                        'offset': offset + index, 'actual_byte': data[index],
                        'expected_byte': expected[index],
                        'actual_next_32_bytes_hex': data[index:index + 32].hex(),
                        'expected_next_32_bytes_hex': expected[index:index + 32].hex()}
            digest.update(data)
            offset += len(data)
        after = os.fstat(fd)
        details['after'] = stat_details(after)
        details['changed_identity_fields'] = [name for name in ('ino', 'size', 'mtime_ns')
                                              if details['before'][name] != details['after'][name]]
        if details['changed_identity_fields']:
            details['failure_conditions'].append('identity_changed')
        if offset != FILE_BYTES:
            details['failure_conditions'].append('read_length')
        if (before.st_ino, before.st_size, before.st_mtime_ns) != (after.st_ino, after.st_size, after.st_mtime_ns) or offset != FILE_BYTES:
            raise OracleReadError('oracle file changed during its full read', details)
        details['passed'] = not details['mismatches'] and digest.hexdigest() == details['expected_sha256']
        if not details['passed']:
            details['failure_conditions'].append('content')
        return details
    except OSError as error:
        details['io_error'] = {'type': type(error).__name__, 'errno': error.errno,
                               'message': str(error)}
        details['failure_conditions'].append('io_error')
        raise OracleReadError('oracle full read failed', details) from error
    finally:
        details.update(bytes=offset, sha256=digest.hexdigest(), read_calls=calls,
                       elapsed_ns=time.monotonic_ns() - started)
        if fd is not None:
            os.close(fd)


def drain_evidence(output, phase):
    """Reference existing native samples; do not perform extra FUSE reads."""
    tool = 'known-tail-write-a' if phase == 'check-a' else 'known-tail-write-b'
    snapshots = []
    for path in sorted((output.parent / 'diagnostics').glob('stats-' + tool + '-post-write-*.txt')):
        raw = path.read_bytes()
        counters = {key: int(value) for key, value in
                    re.findall(r'^(brewfs_(?:writeback_|buffer_dirty_|s3_put_)[a-z0-9_]*) (\d+)$',
                               raw.decode(errors='replace'), re.M)}
        snapshots.append({'path': str(path), 'sha256': durability.sha(raw),
                          'bytes': len(raw), 'native_counters': counters})
    return {'scope': 'Existing shell strict-drain snapshots; diagnostic references only',
            'snapshots_present': bool(snapshots), 'snapshots': snapshots}


def write_tail(path, value, create=False):
    flags = os.O_WRONLY | os.O_NOFOLLOW
    if create:
        flags |= os.O_CREAT | os.O_EXCL
    fd = os.open(path, flags, 0o600)
    try:
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode) or before.st_size != (0 if create else FILE_BYTES):
            raise ValueError('unexpected oracle file before write')
        data = bytes([value]) * TAIL_BYTES
        count, calls = 0, []
        while count < len(data):
            actual = os.pwrite(fd, data[count:], TAIL_OFFSET + count)
            if actual <= 0:
                raise OSError('pwrite made no progress')
            calls.append({'offset': TAIL_OFFSET + count, 'returned_bytes': actual})
            count += actual
        after = os.fstat(fd)
        if after.st_size != FILE_BYTES:
            raise ValueError('tail write did not produce the expected full size')
        return {'inode': after.st_ino, 'file_bytes': after.st_size,
                'tail_byte': value, 'bytes_written': count, 'pwrite_calls': calls,
                'fsync_called': False, 'close': 'normal os.close',
                'expected_full_sha256': expected_sha(value)}
    finally:
        os.close(fd)


def process_env(pid):
    values = dict(item.split(b'=', 1) for item in (Path('/proc') / str(pid) / 'environ').read_bytes().split(b'\0') if b'=' in item)
    return {key: values.get(key.encode(), b'').decode() for key in ENV_KEYS}


def operation_proof(log, start, end, inode):
    with Path(log).open('rb') as stream:
        stream.seek(start)
        raw = stream.read(end - start)
    rows = []
    for line in raw.decode(errors='replace').splitlines():
        if '[write] REQ' not in line:
            continue
        fields = dict(re.findall(r'\b(inode|fh|offset|data_len|write_flags|flags)=(\d+)', line))
        if int(fields.get('inode', '-1')) != inode:
            continue
        unique = re.search(r'\bunique:\s*(\d+)', line)
        if len(fields) != 6 or unique is None:
            raise ValueError('incomplete FUSE write request record')
        rows.append(dict({key: int(value) for key, value in fields.items()}, unique=int(unique.group(1))))
    cursor = TAIL_OFFSET
    for row in sorted(rows, key=lambda item: item['offset']):
        if row['offset'] != cursor or row['data_len'] <= 0 or row['write_flags'] != 0 or row['unique'] <= 0:
            raise ValueError('expected disjoint non-cached FUSE write requests over the tail')
        cursor += row['data_len']
    if cursor != FILE_BYTES or len({row['unique'] for row in rows}) != len(rows):
        raise ValueError('FUSE write requests do not exactly cover the known tail')
    return {'passed': True, 'log_start': start, 'log_end': end,
            'log_slice_sha256': durability.sha(raw), 'requests': rows,
            'request_bytes': sum(row['data_len'] for row in rows),
            'all_write_flags_zero': True}


class Oracle:
    def __init__(self, dataset, config, log, op_log, output, driver,
                 owner=Path('/var/lib/brewfs'), env_reader=process_env):
        self.dataset, self.config = Path(dataset), Path(config)
        self.log, self.op_log, self.output = Path(log), Path(op_log), Path(output)
        self.driver, self.owner, self.env_reader = driver, owner, env_reader
        self.path = self.dataset / FILE_NAME

    def identity(self):
        text = self.config.read_text()
        _, _, _, root = durability.cache_config(text)
        root = durability.owned_cache(root, self.owner)
        identity = self.driver.current(root)
        identity['selected_environment'] = self.env_reader(identity['pid'])
        env = identity['selected_environment']
        if env.get('BREWFS_FUSE_WRITEBACK') != '0' or env.get('BREWFS_FUSE_OP_LOG') != '1':
            raise ValueError('actual mount must disable kernel writeback and enable FUSE request logging')
        if env.get('RUST_LOG') != 'warn,asyncfuse::raw::logfs=debug':
            raise ValueError('actual mount must use the frozen WARN plus FUSE DEBUG filter')
        if env.get('BREWFS_FUSE_LOG_FILE') != str(self.op_log) or env.get('BREWFS_CACHE_ROOT') != str(root):
            raise ValueError('actual mount environment differs from selected log/cache paths')
        identity['config_sha256'] = durability.sha(text.encode())
        return identity

    def prior(self, name):
        result = json.loads((self.output / (name + '.json')).read_text())
        if result.get('passed') is not True:
            raise ValueError('previous oracle phase did not pass: ' + name)
        return result

    def no_failures(self):
        found = durability.scan_failures(self.log)
        if found:
            raise ValueError('client warning log reports upload/recovery failure: ' + json.dumps(found))
        return found

    def same_process(self, before, after):
        for key in ('pid', 'starttime', 'executable_sha256', 'argv', 'cache_root', 'config_sha256', 'selected_environment'):
            if before[key] != after[key]:
                raise ValueError('oracle phase unexpectedly changed mount identity: ' + key)

    def run(self, phase):
        if phase == 'write-a':
            self.output.mkdir(parents=True, exist_ok=False)
        result = {'phase': phase, 'passed': False, 'reasons': [],
                  'scope': 'independent known A then B whole-file oracle; no performance claim',
                  'started_unix_ns': time.time_ns(), 'fsync_called': False}
        destination = self.output / (phase + '.json')
        if destination.exists():
            raise ValueError('refusing to overwrite prior phase evidence')
        try:
            result['log_failures_before'] = self.no_failures()
            result['mount_before'] = self.identity()
            if phase in ('check-a', 'verify-b'):
                result['drain_evidence'] = drain_evidence(self.output, phase)
            if phase in ('write-a', 'write-b'):
                if phase == 'write-a':
                    self.dataset.mkdir(exist_ok=False)
                else:
                    previous = self.prior('check-a')
                    self.same_process(previous['mount_after'], result['mount_before'])
                result['op_log_start'] = self.op_log.stat().st_size
                result['write'] = write_tail(self.path, 0x41 if phase == 'write-a' else 0x42, phase == 'write-a')
            elif phase == 'check-a':
                written = self.prior('write-a')
                self.same_process(written['mount_after'], result['mount_before'])
                result['value'] = check_value(self.path, 0x41)
                if not result['value']['passed']:
                    raise ValueError('post-drain A differs from independent zero-prefix/A-tail oracle')
                result['fuse_write_proof'] = operation_proof(self.op_log, written['op_log_start'], self.op_log.stat().st_size, written['write']['inode'])
            elif phase == 'verify-b':
                written = self.prior('write-b')
                self.same_process(written['mount_after'], result['mount_before'])
                result['warm_value'] = check_value(self.path, 0x42)
                if not result['warm_value']['passed']:
                    raise ValueError('post-drain warm B differs from independent oracle')
                result['fuse_write_proof'] = operation_proof(self.op_log, written['op_log_start'], self.op_log.stat().st_size, written['write']['inode'])
                old = result['mount_before']
                result['same_cache_unmount'] = self.driver.unmount(old)
                if result['same_cache_unmount'].get('forced') or not result['same_cache_unmount'].get('old_process_terminated'):
                    raise ValueError('normal process termination before same-cache remount not proved')
                self.driver.start(Path(old['cache_root']))
                same = self.identity()
                result['same_cache_mount'] = same
                if same['pid'] == old['pid'] or any(same[key] != old[key] for key in ('executable_sha256', 'argv', 'cache_root', 'config_sha256', 'selected_environment')):
                    raise ValueError('same-cache remount changed binary/arguments/config/environment or retained PID')
                result['same_cache_value'] = check_value(self.path, 0x42)
                if not result['same_cache_value']['passed']:
                    raise ValueError('same-cache remount B differs from independent oracle')
                result['log_failures_after_same_cache'] = self.no_failures()
                cold = durability.validate(self.dataset, self.config, self.log,
                                           self.output / 'fresh-cache', self.driver, self.owner)
                result['fresh_cache_validation'] = cold
                if not cold['passed']:
                    raise ValueError('frozen fresh-cache durability gate failed')
                (self.output / 'new-cache-root.txt').write_text(cold['new_cache_root'] + '\n')
                result['fresh_cache_value'] = check_value(self.path, 0x42)
                if not result['fresh_cache_value']['passed']:
                    raise ValueError('fresh-cache remount B differs from independent oracle')
                result['old_cache_preserved'] = True
            else:
                raise ValueError('unknown phase')
            result['mount_after'] = self.identity()
            if phase == 'verify-b':
                old, new = result['mount_before'], result['mount_after']
                if new['executable_sha256'] != old['executable_sha256'] or new['argv'] != old['argv']:
                    raise ValueError('final fresh-cache identity changed binary or arguments')
                for key in ENV_KEYS:
                    if key != 'BREWFS_CACHE_ROOT' and new['selected_environment'][key] != old['selected_environment'][key]:
                        raise ValueError('fresh-cache remount changed non-cache environment: ' + key)
            result['log_failures_after'] = self.no_failures()
            result['passed'] = True
        except Exception as error:
            result['reasons'].append(type(error).__name__ + ': ' + str(error))
            if isinstance(error, OracleReadError):
                result['read_failure'] = error.details
        finally:
            result['finished_unix_ns'] = time.time_ns()
            durability.write_json(destination, result)
        return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('phase', choices=('write-a', 'check-a', 'write-b', 'verify-b'))
    for name in ('dataset', 'mount', 'config', 'log', 'op-log', 'output'):
        parser.add_argument('--' + name, type=Path, required=True)
    parser.add_argument('--pidfile', type=Path, default=Path('/run/brewfs/perf-mount.pid'))
    args = parser.parse_args()
    if args.mount != Path('/mnt/brewfs') or args.dataset != args.mount / '.perf-known-tail-oracle' or args.config != Path('/run/brewfs/config.yaml'):
        parser.error('unexpected owned qualification mount/dataset/config')
    if args.output.resolve() != args.output or not args.output.is_relative_to('/artifacts') or args.output.name != 'known-tail-oracle':
        parser.error('oracle output must be a fresh owned artifact directory')
    driver = durability.RealMount(args.mount, args.config, args.pidfile, 30)
    result = Oracle(args.dataset, args.config, args.log, args.op_log, args.output, driver).run(args.phase)
    print(json.dumps({'phase': args.phase, 'passed': result['passed'], 'reasons': result['reasons']}))
    return 0 if result['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
