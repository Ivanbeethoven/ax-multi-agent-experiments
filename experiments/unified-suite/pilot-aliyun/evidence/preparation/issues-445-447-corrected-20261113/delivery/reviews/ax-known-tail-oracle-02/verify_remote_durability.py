#!/usr/bin/env python3
"""Qualification-only cold remount gate. Never delete a previous cache/stage."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import time
import uuid


# Only frozen filters whose BrewFS upload/recovery WARN records are visible.
QUALIFIED_LOG_FILTERS = frozenset(('warn', 'brewfs=info,asyncfuse::raw::logfs=debug', 'warn,asyncfuse::raw::logfs=debug'))


FAILURE = re.compile(r"\b(?:upload|put|recovery|recover|writeback)\b.{0,100}\b(?:failed|failure)\b|\b(?:failed|failure)\b.{0,100}\b(?:upload|put|recovery|recover|writeback)\b|\bmax retries exceeded\b", re.I)


def sha(data):
    return hashlib.sha256(data).hexdigest()


def file_sha(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        while data := stream.read(1 << 20):
            digest.update(data)
    return digest.hexdigest()


def write_json(path, value):
    Path(path).write_text(json.dumps(value, indent=2, sort_keys=True) + '\n')


def scan_failures(log):
    found = []
    with Path(log).open(encoding='utf-8', errors='replace') as stream:
        for number, line in enumerate(stream, 1):
            if FAILURE.search(line):
                found.append({'line': number, 'sha256': sha(line.encode()), 'reason': 'upload_or_recovery_failure'})
    return found


def cache_config(text):
    lines = text.splitlines(keepends=True)
    headers = [i for i, line in enumerate(lines) if line.rstrip() == 'cache:']
    if len(headers) != 1:
        raise ValueError('expected one generated cache section')
    start = headers[0] + 1
    end = next((i for i in range(start, len(lines)) if lines[i].strip() and not lines[i][0].isspace() and not lines[i].startswith('#')), len(lines))
    matches = [(i, re.fullmatch(r'  (root|cache_root): ([A-Za-z0-9_./-]+)\n?', lines[i])) for i in range(start, end)]
    matches = [(i, m) for i, m in matches if m]
    root_keys = [line for line in lines[start:end] if re.match(r'\s*(?:root|cache_root)\s*:', line)]
    if len(matches) != 1 or len(root_keys) != 1:
        raise ValueError('expected one plain generated cache.root/cache_root key')
    index, match = matches[0]
    return lines, index, match.group(1), Path(match.group(2))


def owned_cache(path, owner=Path('/var/lib/brewfs')):
    path = Path(path)
    if not path.is_absolute() or path != path.resolve():
        raise ValueError('cache path must be absolute and have no symlink/traversal')
    relative = path.relative_to(owner)
    if not relative.parts or not relative.parts[0].startswith('cache'):
        raise ValueError('cache path is outside the task-owned cache prefix')
    return path


def snapshot(directory):
    directory = Path(directory)
    if directory.is_symlink() or not directory.is_dir():
        raise ValueError('dataset must be an existing real directory')
    rows = []
    for parent, dirs, files in os.walk(directory, followlinks=False):
        dirs.sort()
        for name in dirs:
            if (Path(parent) / name).is_symlink():
                raise ValueError('symlink in dataset')
        for name in sorted(files):
            path = Path(parent) / name
            fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
            try:
                before = os.fstat(fd)
                if not stat.S_ISREG(before.st_mode):
                    raise ValueError('non-regular dataset file')
                digest = hashlib.sha256()
                count = 0
                while data := os.read(fd, 1 << 20):
                    digest.update(data)
                    count += len(data)
                after = os.fstat(fd)
                if count != before.st_size or (before.st_size, before.st_mtime_ns, before.st_ino) != (after.st_size, after.st_mtime_ns, after.st_ino):
                    raise ValueError('dataset changed while hashing')
                rows.append({'path': str(path.relative_to(directory)), 'bytes': count, 'sha256': digest.hexdigest()})
            finally:
                os.close(fd)
    if not rows or sum(row['bytes'] for row in rows) == 0:
        raise ValueError('empty dataset cannot prove durability')
    return rows


def process_identity(pid):
    proc = Path('/proc') / str(pid)
    try:
        raw = (proc / 'stat').read_text()
    except FileNotFoundError:
        return None
    fields = raw[raw.rfind(')') + 2:].split()
    return {'pid': pid, 'state': fields[0], 'starttime': fields[19]}


class RealMount:
    def __init__(self, mount, config, pidfile, timeout):
        self.mount, self.config, self.pidfile = Path(mount), Path(config), Path(pidfile)
        self.timeout = timeout

    def mounted(self):
        # Qualified paths contain no whitespace or mountinfo escapes.
        for line in Path('/proc/self/mountinfo').read_text().splitlines():
            fields = line.split()
            split = fields.index('-')
            if fields[4] == str(self.mount) and fields[split + 1].startswith('fuse'):
                return True
        return False

    def current(self, expected_root):
        pid = int(self.pidfile.read_text().strip())
        identity = process_identity(pid)
        if not identity or identity['state'] in ('Z', 'X') or not self.mounted():
            raise RuntimeError('expected live FUSE mount is absent')
        proc = Path('/proc') / str(pid)
        argv = [x.decode() for x in (proc / 'cmdline').read_bytes().split(b'\0') if x]
        if argv[:2] != ['/usr/local/bin/brewfs', 'mount'] or '--config' not in argv or argv[argv.index('--config') + 1] != str(self.config) or argv[-1] != str(self.mount):
            raise RuntimeError('pid file does not identify the owned mount')
        env = dict(x.split(b'=', 1) for x in (proc / 'environ').read_bytes().split(b'\0') if b'=' in x)
        if env.get(b'RUST_LOG', b'').decode() not in QUALIFIED_LOG_FILTERS:
            raise RuntimeError('actual mount must expose warning-level upload failures')
        if env.get(b'BREWFS_CACHE_ROOT') != str(expected_root).encode():
            raise RuntimeError('actual mount environment does not select the new cache root')
        return dict(identity, argv=argv, cache_root=str(expected_root), executable_sha256=file_sha(proc / 'exe'))

    def unmount(self, old):
        subprocess.run(['fusermount3', '-u', str(self.mount)], check=True, timeout=self.timeout)
        deadline = time.monotonic() + self.timeout
        while True:
            identity = process_identity(old['pid'])
            if identity is None or identity['starttime'] != old['starttime'] or identity['state'] in ('Z', 'X'):
                if self.mounted():
                    raise RuntimeError('mount remains after old process terminated')
                return {'method': 'fusermount3 -u', 'old_process_terminated': True, 'terminal_state': None if identity is None else identity['state'], 'forced': False}
            if time.monotonic() >= deadline:
                raise TimeoutError('old mount process did not exit after normal unmount')
            time.sleep(.02)

    def start(self, new_root):
        env = dict(os.environ, BREWFS_CACHE_ROOT=str(new_root), BREWFS_CONFIG_PATH=str(self.config))
        subprocess.run(['mount', '-t', 'fuse.brewfs', 'brewfs', str(self.mount)], check=True, timeout=self.timeout + 65, env=env)
        return self.current(new_root)


def validate(dataset, config, log, output, driver, owner=Path('/var/lib/brewfs')):
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    result = {'passed': False, 'phase': 'starting', 'reasons': [], 'scope': 'warm final bytes versus full read after normal remount with a fresh empty local cache; not a power-loss or independent application oracle', 'timing_outside_measured_tool_and_strict_drain': True}
    started = time.monotonic_ns()
    result['started_unix_ns'] = time.time_ns()
    write_json(output / 'receipt.json', result)
    try:
        text = Path(config).read_text()
        lines, root_index, key, old_root = cache_config(text)
        old_root = owned_cache(old_root, owner)
        result['old_cache_root'] = str(old_root)
        if os.environ.get('BREWFS_CACHE_ROOT', str(old_root)) != str(old_root):
            raise ValueError('config and inherited cache root differ')
        result['log_failures_before'] = scan_failures(log)
        if result['log_failures_before']:
            raise ValueError('client log reports failed upload/recovery')
        result['phase'] = 'warm_snapshot'
        phase = time.monotonic_ns()
        expected = snapshot(dataset)
        write_json(output / 'expected.json', expected)
        result['warm_hash_ns'] = time.monotonic_ns() - phase
        result['file_count'] = len(expected)
        result['logical_bytes'] = sum(row['bytes'] for row in expected)
        result['old_mount'] = driver.current(old_root)
        result['phase'] = 'normal_unmount'
        phase = time.monotonic_ns()
        result['unmount'] = driver.unmount(result['old_mount'])
        result['unmount_ns'] = time.monotonic_ns() - phase
        if result['unmount'].get('forced') or not result['unmount'].get('old_process_terminated'):
            raise RuntimeError('normal old-process termination was not proved')
        result['phase'] = 'new_cache'
        parent = owned_cache(owner / 'cache-durability', owner)
        parent.mkdir(exist_ok=True)
        new_root = owned_cache(parent / uuid.uuid4().hex, owner)
        new_root.mkdir(exist_ok=False)
        if any(new_root.iterdir()):
            raise RuntimeError('new cache must be empty')
        result['new_cache_root'] = str(new_root)
        result['new_cache_was_empty'] = True
        result['old_cache_preserved'] = True
        lines[root_index] = f'  {key}: {new_root}\n'
        updated = ''.join(lines)
        (output / 'config-before.yaml').write_text(text)
        (output / 'config-after.yaml').write_text(updated)
        result['config_sha256_before'] = sha(text.encode())
        result['config_sha256_after'] = sha(updated.encode())
        Path(config).write_text(updated)
        result['phase'] = 'cold_remount'
        phase = time.monotonic_ns()
        result['new_mount'] = driver.start(new_root)
        if result['new_mount']['pid'] == result['old_mount']['pid'] or result['new_mount']['executable_sha256'] != result['old_mount']['executable_sha256']:
            raise RuntimeError('remount must use a different process and the same binary')
        if result['new_mount']['argv'] != result['old_mount']['argv']:
            raise RuntimeError('remount arguments changed')
        result['remount_ns'] = time.monotonic_ns() - phase
        (output / 'new-cache-root.txt').write_text(str(new_root) + '\n')
        result['phase'] = 'cold_snapshot'
        phase = time.monotonic_ns()
        observed = snapshot(dataset)
        write_json(output / 'observed.json', observed)
        result['cold_hash_ns'] = time.monotonic_ns() - phase
        if observed != expected:
            raise ValueError('cold file inventory/size/SHA differs from expected final bytes')
        result['log_failures_after'] = scan_failures(log)
        if result['log_failures_after']:
            raise ValueError('client log reports failed upload/recovery')
        result['phase'] = 'complete'
        result['passed'] = True
    except Exception as error:
        result['reasons'].append(type(error).__name__ + ': ' + str(error))
    finally:
        result['finished_unix_ns'] = time.time_ns()
        result['total_validation_ns'] = time.monotonic_ns() - started
        write_json(output / 'receipt.json', result)
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--dataset', required=True, type=Path)
    parser.add_argument('--mount', required=True, type=Path)
    parser.add_argument('--config', required=True, type=Path)
    parser.add_argument('--log', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--pidfile', default='/run/brewfs/perf-mount.pid', type=Path)
    parser.add_argument('--unmount-timeout', default=30, type=int)
    args = parser.parse_args()
    # All paths are from the isolated qualification container, never host mounts.
    if args.mount != Path('/mnt/brewfs') or args.dataset.parent != args.mount or not args.dataset.name.startswith('.perf-fio-') or args.config != Path('/run/brewfs/config.yaml'):
        parser.error('unexpected qualification mount/dataset/config path')
    if args.output.parent.name != 'remote-durability' or args.output.resolve() != args.output or not args.output.is_relative_to('/artifacts'):
        parser.error('validation output must be a fresh owned /artifacts/.../remote-durability/tool directory')
    if args.unmount_timeout <= 0 or args.unmount_timeout > 60:
        parser.error('unmount timeout must be 1..60 seconds')
    # The standalone qualification runner fixes this for both arms. Error-only
    # logging would conceal the warning-level upload/recovery failure contract.
    if os.environ.get('RUST_LOG') not in QUALIFIED_LOG_FILTERS:
        parser.error('qualified durability gate requires a frozen WARN-visible RUST_LOG filter')
    driver = RealMount(args.mount, args.config, args.pidfile, args.unmount_timeout)
    result = validate(args.dataset, args.config, args.log, args.output, driver)
    print(json.dumps({'passed': result['passed'], 'phase': result['phase'], 'receipt': str(args.output / 'receipt.json')}))
    return 0 if result['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
