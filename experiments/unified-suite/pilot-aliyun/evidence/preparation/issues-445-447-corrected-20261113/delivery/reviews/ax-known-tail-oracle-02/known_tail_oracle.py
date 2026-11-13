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


def check_value(path, value):
    """Check every byte against an independent oracle, not another read."""
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode) or before.st_size != FILE_BYTES:
            raise ValueError('oracle file is not a regular full-size file')
        digest, offset = hashlib.sha256(), 0
        mismatches = []
        while data := os.read(fd, 1024 * 1024):
            zeros = min(len(data), max(0, TAIL_OFFSET - offset))
            expected = bytes(zeros) + bytes([value]) * (len(data) - zeros)
            if data != expected:
                mismatches.append({'offset': offset, 'bytes': len(data),
                                   'sha256': durability.sha(data),
                                   'expected_sha256': durability.sha(expected)})
            digest.update(data)
            offset += len(data)
        after = os.fstat(fd)
        if (before.st_ino, before.st_size, before.st_mtime_ns) != (after.st_ino, after.st_size, after.st_mtime_ns) or offset != FILE_BYTES:
            raise ValueError('oracle file changed during its full read')
        return {'passed': not mismatches and digest.hexdigest() == expected_sha(value),
                'inode': before.st_ino, 'bytes': offset, 'sha256': digest.hexdigest(),
                'expected_sha256': expected_sha(value), 'tail_byte': value,
                'mismatches': mismatches}
    finally:
        os.close(fd)


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
