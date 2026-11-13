#!/usr/bin/env python3
"""Local writable Packed Overlay: optimized binary, cache off/on, paired runs.

Uses only pinned local Docker images and loopback ports. No cloud credentials,
cloud API, model, privileged host cache flush, or historical result modification.
"""
import argparse
from contextlib import contextmanager
import hashlib
from http.client import HTTPConnection, HTTPException
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
import platform
from pathlib import Path
import re
import secrets
import shutil
import socket
import subprocess
import tempfile
from threading import Lock, Thread
import time
import uuid

import boto3
from botocore.config import Config

REDIS = 'sha256:63218503f6461303e502355b8b0359a1194a190341f954e7fc7e8f92cd382a43'
RUSTFS = 'sha256:942893356344d292d9fcccadfceb039467c589ba10b4045cb148c0a65d2d3fec'
RUSTFS_REPO = 'https://github.com/Ivanbeethoven/rustfs.git'
RUSTFS_BRANCH = 'feat/dynamic-block-size-rebased'


def verify_backend(invoke):
    """Freeze the installed image only when its OCI revision is branch HEAD."""
    source = invoke(['git', 'ls-remote', RUSTFS_REPO, 'refs/heads/' + RUSTFS_BRANCH], timeout=30).stdout.strip().split()
    assert len(source) == 2 and re.fullmatch('[0-9a-f]{40}', source[0]), 'cannot resolve RustFS branch'
    labels = json.loads(invoke(['docker', 'image', 'inspect', '--format', '{{json .Config.Labels}}', RUSTFS]).stdout)
    assert labels.get('org.opencontainers.image.source') == RUSTFS_REPO
    assert labels.get('org.opencontainers.image.revision') == source[0], 'local RustFS image is not branch HEAD; rebuild before freezing a new run'
    return dict(repository=RUSTFS_REPO, branch=RUSTFS_BRANCH, revision_policy='branch-latest',
                source_commit=source[0], built_from_commit=labels['org.opencontainers.image.revision'],
                image_digest=RUSTFS, resolved_at_unix_ns=time.time_ns(),
                resolved_by='git ls-remote + Docker OCI revision/source labels')


def save(path, value):
    with path.open('x', encoding='utf-8') as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.write('\n')


def sha(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


@contextmanager
def meter(port):
    counts = dict(requests=0, gets=0, heads=0, puts=0, deletes=0, received_bytes=0, sent_bytes=0, errors=0, inflight=0)
    lock = Lock()

    class Handler(BaseHTTPRequestHandler):
        protocol_version = 'HTTP/1.1'

        def log_message(self, *_):
            pass

        def forward(self):
            upstream = HTTPConnection('127.0.0.1', port, timeout=15)
            active = True
            with lock:
                counts['requests'] += 1
                counts['inflight'] += 1
                for verb, key in [('GET', 'gets'), ('HEAD', 'heads'), ('PUT', 'puts'), ('DELETE', 'deletes')]:
                    counts[key] += self.command == verb
            try:
                headers = dict(self.headers.items())
                length = int(self.headers.get('Content-Length', '0'))
                if length > 16 * 1024**2 or self.headers.get('Transfer-Encoding'):
                    raise ValueError('unsupported proxy request body')
                body = self.rfile.read(length) if length else None
                upstream.request(self.command, self.path, body=body, headers=headers)
                with lock:
                    counts['sent_bytes'] += length
                response = upstream.getresponse()
                data = response.read(16 * 1024**2 + 1)
                if len(data) > 16 * 1024**2:
                    raise ValueError('oversize local response')
                with lock:
                    counts['errors'] += response.status >= 400
                    counts['received_bytes'] += len(data)
                    counts['inflight'] -= 1
                    active = False
                self.send_response_only(response.status)
                for name, value in response.getheaders():
                    if name.lower() not in ('connection', 'transfer-encoding', 'content-length'):
                        self.send_header(name, value)
                self.send_header('Content-Length', response.getheader('Content-Length', '0') if self.command == 'HEAD' else str(len(data)))
                self.end_headers()
                self.wfile.write(data)
            except Exception:
                with lock:
                    counts['errors'] += 1
                self.close_connection = True
            finally:
                upstream.close()
                if active:
                    with lock:
                        counts['inflight'] -= 1

        do_GET = do_HEAD = do_PUT = do_DELETE = do_POST = forward

    def snapshot():
        with lock:
            return counts.copy()

    with ThreadingHTTPServer(('127.0.0.1', 0), Handler) as server:
        server.daemon_threads = True
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            yield f'http://127.0.0.1:{server.server_port}', snapshot
        finally:
            server.shutdown()
            thread.join(5)


def commandstats(port):
    with socket.create_connection(('127.0.0.1', port), timeout=5) as connection:
        connection.sendall(b'*2\r\n$4\r\nINFO\r\n$12\r\ncommandstats\r\n')
        with connection.makefile('rb') as stream:
            header = stream.readline()
            if not header.startswith(b'$'):
                raise ValueError('Redis INFO did not return a bulk string')
            raw = stream.read(int(header[1:]))
    result = {}
    for line in raw.decode().splitlines():
        if line.startswith('cmdstat_'):
            name, fields = line.split(':', 1)
            result[name[8:]] = {key: float(value) for key, value in (field.split('=') for field in fields.split(','))}
    return result


def delta(after, before):
    return {key: value - before.get(key, 0) for key, value in after.items()}


def metrics(raw):
    return {match[1]: float(match[2]) for line in raw.splitlines()
            if (match := re.fullmatch(r'([a-zA-Z_][a-zA-Z0-9_]*) ([0-9.eE+-]+)', line))}


def measured_round(root, expected):
    start = time.monotonic_ns()
    directory = os.open(root / 'sub', os.O_RDONLY | os.O_DIRECTORY)
    try:
        observed = sorted(os.listdir(directory))
        listed = time.monotonic_ns() - start
        start = time.monotonic_ns()
        st = os.stat('b.txt', dir_fd=directory, follow_symlinks=False)
        stated = time.monotonic_ns() - start
    finally:
        os.close(directory)
    assert observed == sorted(Path(p).name for p in expected if p.startswith('sub/'))
    assert st.st_size == len(expected['sub/b.txt'])
    reads = []
    for name, content in expected.items():
        fd = os.open(root / name, os.O_RDONLY | os.O_NOFOLLOW)
        try:
            assert os.fstat(fd).st_size == len(content)
            start = time.monotonic_ns()
            got = os.pread(fd, len(content), 0)
            elapsed = time.monotonic_ns() - start
            assert got == content, name
            reads.append(dict(path=name, elapsed_ns=elapsed, bytes=len(got)))
        finally:
            os.close(fd)
    return dict(open_and_list_ns=listed, lookup_and_stat_ns=stated, pread=reads)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--binary', type=Path, required=True)
    parser.add_argument('--producer', type=Path, required=True)
    parser.add_argument('--source-commit', required=True)
    parser.add_argument('--source-patch', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--pairs', type=int, default=3)
    parser.add_argument('--passes', type=int, default=128)
    parser.add_argument('--cache-state', choices=('both', 'on', 'off'), default='both',
                        help='Use on for version comparisons with the same enabled read cache')
    parser.add_argument('--metadata-sync', choices=('remote', 'local-journal', 'local-buffered'), default='remote',
                        help='Metadata acknowledgement mode; each cell gets an independent local journal')
    parser.add_argument('--metadata-edits', type=int, default=0,
                        help='After hot reads, time repeated chmod/stat plus final fsync/close separately')
    args = parser.parse_args()
    assert os.geteuid() == 0, 'run under sudo for private RustFS volume ownership'
    assert args.pairs >= 3 and 1 <= args.passes <= 4096
    assert 0 <= args.metadata_edits <= 4096
    assert re.fullmatch('[0-9a-f]{40}', args.source_commit)
    for path in [args.binary, args.producer, args.source_patch]:
        assert path.is_absolute() and path.is_file()
    args.out.mkdir(parents=True, exist_ok=False)
    work = Path(tempfile.mkdtemp(prefix='brewfs-hot-local-'))
    owner = 'brewfs-hot-' + uuid.uuid4().hex[:10]
    containers = [owner + '-redis', owner + '-rustfs']
    access, secret = secrets.token_hex(12), secrets.token_hex(24)
    env = dict(os.environ, AWS_ACCESS_KEY_ID=access, AWS_SECRET_ACCESS_KEY=secret,
               BREWFS_METADATA_SYNC=args.metadata_sync,
               AWS_REGION='us-east-1', AWS_EC2_METADATA_DISABLED='true',
               RUST_LOG='warn', NO_PROXY='127.0.0.1,localhost',
               BREWFS_PACKED_VERIFY_MEMO='0', BREWFS_PACKED_V3_RETAINED_PAYLOAD_BYTES=str(8 << 20),
               BREWFS_FUSE_READ_DIRECT_IO='1', BREWFS_WORKSPACE_ROW_CACHE_TTL_MS='600000')
    for name in ('BREWFS_PACKED_WORKSPACE_READ_CONTROL', 'BREWFS_PACKED_WORKSPACE_EXTENT_CONTROL'):
        env.pop(name, None)
    result = dict(schema='hot-overlay-local-v1', passed=False, source_commit=args.source_commit,
                  owner=owner, temporary_directory=str(work), containers=containers,
                  started_unix_ns=time.time_ns(), host=dict(kernel=platform.release(), machine=platform.machine(),
                                                          cpu_count=os.cpu_count(), python=platform.python_version(),
                                                          swaps=Path('/proc/swaps').read_text(), loadavg=os.getloadavg()),
                  source_patch_sha256=sha(args.source_patch), binary_sha256=sha(args.binary),
                  producer_sha256=sha(args.producer), images=dict(redis=REDIS, rustfs=RUSTFS),
                  argv=list(map(str, __import__('sys').argv)), cells=[], commands=[],
                  runtime_env={key: value for key, value in env.items() if key.startswith('BREWFS_') or key in ('RUST_LOG', 'AWS_EC2_METADATA_DISABLED')},
                  metadata_sync=args.metadata_sync, cache_state=args.cache_state,
                  comparison=('same optimized binary; new empty-query cache disabled/enabled; writable Overlay in both arms'
                              if args.cache_state == 'both' else
                              f'optimized binary; empty-query cache fixed {args.cache_state}; writable Overlay'))
    save(args.out / 'ownership.json', dict(owner=owner, temporary_directory=str(work), containers=containers))
    process, logfile, point = None, None, None

    def invoke(argv, *, check=True, timeout=60):
        argv = list(map(str, argv))
        # Never record the ephemeral access/secret used by local services.
        result['commands'].append([part.replace(access, '[local-access]').replace(secret, '[local-secret]') for part in argv])
        done = subprocess.run(argv, env=env, capture_output=True, text=True, timeout=timeout)
        if check and done.returncode:
            raise RuntimeError(f'{argv[0]} exited {done.returncode}: {done.stderr[-2000:]}')
        return done

    def docker(*argv, **kwargs):
        return invoke(['docker', *argv], **kwargs)

    def mounted():
        return point is not None and any(row.split()[4] == str(point) for row in Path('/proc/self/mountinfo').read_text().splitlines())

    def stop():
        nonlocal process, logfile
        if process is None:
            return
        if mounted():
            invoke(['fusermount3', '-u', point], timeout=15)
        try:
            code = process.wait(timeout=20)
        except subprocess.TimeoutExpired:
            process.terminate()
            try:
                code = process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                code = process.wait(timeout=5)
        logfile.close()
        process = logfile = None
        assert code == 0 and not mounted(), 'mount did not exit cleanly'

    def wait_for(predicate, label):
        until = time.monotonic() + 30
        while time.monotonic() < until:
            if predicate():
                return
            time.sleep(.1)
        raise TimeoutError(label)

    try:
        for image in (REDIS, RUSTFS):
            assert docker('image', 'inspect', '--format', '{{.Id}}', image).stdout.strip() == image
        result['backend_version'] = verify_backend(invoke)
        common = ['run', '--pull', 'never', '-d', '--label', 'brewfs.hot.owner=' + owner]
        docker(*common, '--name', containers[0], '-p', '127.0.0.1::6379', '--memory', '256m', '--cpus', '1',
               REDIS, 'redis-server', '--appendonly', 'no', '--save', '')
        rustenv = work / 'rustfs.env'
        rustenv.write_text(f'RUSTFS_ACCESS_KEY={access}\nRUSTFS_SECRET_KEY={secret}\n')
        rustenv.chmod(0o600)
        command = [*common, '--name', containers[1], '-p', '127.0.0.1::9000', '--memory', '2g', '--cpus', '2', '--env-file', rustenv]
        settings = dict(RUSTFS_ADDRESS='0.0.0.0:9000', RUSTFS_CONSOLE_ENABLE='false', RUSTFS_VOLUMES='/data/rustfs{0...3}',
                        RUSTFS_UNSAFE_BYPASS_DISK_CHECK='true', RUSTFS_OBS_LOGGER_LEVEL='warn', RUSTFS_OBS_LOG_DIRECTORY='/var/log/rustfs',
                        RUSTFS_OBS_METRICS_EXPORT_ENABLED='false', RUSTFS_OBS_TRACES_EXPORT_ENABLED='false', RUSTFS_OBS_LOGS_EXPORT_ENABLED='false',
                        RUSTFS_OBS_PROFILING_EXPORT_ENABLED='false', RUSTFS_EC_BLOCK_SIZE_HINT_ENABLE='true',
                        RUSTFS_EC_BLOCK_SIZE_HINT_FLEET_CONFIRMED='true', RUSTFS_EC_READ_QUANTUM_ENABLE='true', RUSTFS_EC_READ_QUANTUM_FLEET_CONFIRMED='true')
        for key, value in settings.items():
            command += ['-e', key + '=' + value]
        for leaf, target in [(str(i), '/data/rustfs' + str(i)) for i in range(4)] + [('rustlogs', '/var/log/rustfs')]:
            path = work / leaf
            path.mkdir()
            os.chown(path, 10001, 10001)
            command += ['-v', str(path) + ':' + target]
        docker(*command, RUSTFS)
        redis_port = int(docker('port', containers[0], '6379/tcp').stdout.strip().rsplit(':', 1)[1])
        rustfs_port = int(docker('port', containers[1], '9000/tcp').stdout.strip().rsplit(':', 1)[1])
        wait_for(lambda: docker('exec', containers[0], 'redis-cli', 'ping', check=False).stdout.strip() == 'PONG', 'Redis')
        endpoint = f'http://127.0.0.1:{rustfs_port}'
        def rustfs_ready():
            connection = HTTPConnection('127.0.0.1', rustfs_port, timeout=2)
            try:
                connection.request('GET', '/health')
                response = connection.getresponse()
                response.read()
                return response.status == 200
            except (OSError, HTTPException):
                return False
            finally:
                connection.close()
        wait_for(rustfs_ready, 'RustFS health')
        s3 = boto3.client('s3', endpoint_url=endpoint, region_name='us-east-1', aws_access_key_id=access, aws_secret_access_key=secret,
                          config=Config(signature_version='s3v4', connect_timeout=2, read_timeout=10, retries={'total_max_attempts': 3}, s3={'addressing_style': 'path'}))
        with meter(rustfs_port) as (metered_endpoint, s3_counts):
            for scenario in ('two-files', 'modified-upper'):
                payload = {'a.txt': b'packed smoke\n', 'sub/b.txt': bytes(range(64))}
                if scenario == 'modified-upper':
                    payload.update({'sub/edit.bin': bytes(range(256)) * 16, 'sub/delete.txt': b'delete me', 'stable.bin': bytes(range(256)) * 16})
                src = work / ('src-' + scenario)
                (src / 'sub').mkdir(parents=True)
                for name, content in payload.items():
                    (src / name).write_bytes(content)
                # Workspace initialization appends a 36-character UUID.
                bucket = 'hot-' + owner.rsplit('-', 1)[1] + ('-a' if scenario == 'two-files' else '-b')
                s3.create_bucket(Bucket=bucket)
                manifest = work / ('manifest-' + scenario)
                invoke([args.producer, '--source-directory', src, '--source-consistency', 'best-effort-detected', '--source-hardlink-policy', 'visible-links',
                        '--output-dir', work / ('objects-' + scenario), '--manifest-output', manifest, '--bucket', bucket, '--endpoint', endpoint,
                        '--region', 'us-east-1', '--force-path-style', 'true', '--prefix', 'base', '--metadata-codec', 'raw', '--data-codec', 'raw',
                        '--inline-data', 'off', '--chunk-size', '67108864', '--block-size', '4194304', '--s3-rustfs-ec-block-size-hint', 'auto'], timeout=120)
                data = dict(backend='s3', s3=dict(bucket=bucket, endpoint=metered_endpoint, region='us-east-1', force_path_style=True, rustfs_ec_block_size_hint='auto'))
                data_config = work / ('data-' + scenario + '.json')
                data_config.write_text(json.dumps({'data': data}))
                for pair in range(args.pairs):
                    cache_order = [False, True] if pair % 2 == 0 else [True, False]
                    if args.cache_state != 'both':
                        cache_order = [args.cache_state == 'on']
                    for enabled in cache_order:
                        label = f'{scenario}-pair{pair+1}-' + ('on' if enabled else 'off')
                        assert sha(args.binary) == result['binary_sha256'], 'binary changed during pairing'
                        out = args.out / label
                        out.mkdir()
                        namespace = label + '-' + uuid.uuid4().hex[:8]
                        rurl = f'redis://127.0.0.1:{redis_port}/0'
                        parent = json.loads(invoke([args.binary, 'workspace', '--meta-backend', 'redis', '--meta-url', rurl,
                                                   '--workspace-namespace', namespace, 'init-packed-volume', '--manifest-key', manifest.read_text().strip(), '--data-config', data_config]).stdout)
                        point = work / label
                        point.mkdir()
                        config = dict(volume_format='workspace-v1', volume={'format': 'workspace-v1'}, workspace=parent['workspace_id'], workspace_namespace=namespace,
                                      data=data, meta={'backend': 'redis', 'redis': {'url': rurl}}, layout=dict(chunk_size=67108864, block_size=4194304),
                                      fuse=dict(workers=4, max_background=128, cache_ttl_ms=0, keep_cache=False),
                                      cache=dict(cache_root=str(work / ('cache-' + label)), writeback_mode='upload_before_commit', read_memory_bytes=536870912, read_ssd_bytes=0, compression='none'))
                        config_path = out / 'config.json'
                        save(config_path, config)
                        env['BREWFS_WORKSPACE_EMPTY_QUERY_CACHE'] = str(int(enabled))
                        if args.metadata_sync != 'remote':
                            env['BREWFS_METADATA_JOURNAL_DIR'] = str(work / ('journal-' + label))
                        else:
                            env.pop('BREWFS_METADATA_JOURNAL_DIR', None)
                        logfile = (out / 'mount.log').open('wb')
                        mount_command = [str(args.binary), 'mount', '--config', str(config_path), str(point)]
                        result['commands'].append(mount_command)
                        process = subprocess.Popen(mount_command, env=env, stdout=logfile, stderr=subprocess.STDOUT)

                        def ready():
                            if process.poll() is not None:
                                raise RuntimeError(f'mount failed: {out / "mount.log"}')
                            return mounted()
                        wait_for(ready, 'FUSE mount')
                        mount_row = next(row for row in Path('/proc/self/mountinfo').read_text().splitlines() if row.split()[4] == str(point))
                        assert 'rw' in mount_row.split()[5].split(','), 'the comparison requires writable Overlay'
                        (out / 'mountinfo.txt').write_text(mount_row + '\n')
                        expected = payload.copy()
                        # Warm old empty results before mutating: correctness
                        # checks must catch stale negatives in the same mount.
                        measured_round(point, expected)
                        measured_round(point, expected)
                        if scenario == 'modified-upper':
                            path = point / 'sub/edit.bin'
                            fd = os.open(path, os.O_RDWR)
                            try:
                                os.pwrite(fd, b'upper-overwrite', 32)
                                os.fsync(fd)
                                os.ftruncate(fd, 128)
                                os.ftruncate(fd, 4096)
                                os.fsync(fd)
                            finally:
                                os.close(fd)
                            changed = bytearray(payload['sub/edit.bin'])
                            changed[32:47] = b'upper-overwrite'
                            changed[128:] = bytes(4096 - 128)
                            expected['sub/edit.bin'] = bytes(changed)
                            os.unlink(point / 'sub/delete.txt')
                            del expected['sub/delete.txt']
                            (point / 'sub/new.txt').write_bytes(b'new upper file')
                            fd = os.open(point / 'sub/new.txt', os.O_RDONLY)
                            os.fsync(fd)
                            os.close(fd)
                            expected['sub/new.txt'] = b'new upper file'
                            os.rename(point / 'sub/new.txt', point / 'sub/renamed.txt')
                            expected['sub/renamed.txt'] = expected.pop('sub/new.txt')
                        warm = []
                        for index in range(3):
                            before = s3_counts()
                            measured_round(point, expected)
                            warm.append(delta(s3_counts(), before))
                        save(out / 'warm.json', dict(rounds=warm, after=s3_counts()))
                        assert all(value == 0 for value in warm[-1].values()), f'lower/upper not hot: {warm}'
                        (out / 'before.stats').write_text((point / '.stats').read_text())
                        before_stats = metrics((out / 'before.stats').read_text())
                        before_redis = commandstats(redis_port)
                        before_s3 = s3_counts()
                        assert before_s3['inflight'] == 0, 'object request still active before measurement'
                        started = time.monotonic_ns()
                        samples = [measured_round(point, expected) for _ in range(args.passes)]
                        elapsed = time.monotonic_ns() - started
                        after_s3 = s3_counts()
                        after_redis = commandstats(redis_port)
                        (out / 'after.stats').write_text((point / '.stats').read_text())
                        stats_delta = delta(metrics((out / 'after.stats').read_text()), before_stats)
                        remote_delta = delta(after_s3, before_s3)
                        cell = dict(label=label, cache_enabled=enabled, scenario=scenario, pair=pair+1, elapsed_ns=elapsed,
                                    rustfs_before=before_s3, rustfs_after=after_s3,
                                    warm_s3=warm, rustfs_delta=remote_delta, stats_delta=stats_delta,
                                    redis_before=before_redis, redis_after=after_redis,
                                    redis_delta={key: delta({field: number for field, number in value.items() if field != 'usec_per_call'}, before_redis.get(key, {})) for key, value in after_redis.items()},
                                    expected_sha256={name: hashlib.sha256(content).hexdigest() for name, content in expected.items()}, samples=samples)
                        save(out / 'hot-read.json', cell)
                        assert after_s3['inflight'] == 0, 'object request still active after measurement'
                        assert all(value == 0 for value in remote_delta.values()), 'remote traffic in measured phase'
                        assert stats_delta['brewfs_fuse_read_ops_total'] == args.passes * len(expected)
                        if args.metadata_edits:
                            name = sorted(expected)[0]
                            target = point / name
                            fd = os.open(target, os.O_RDONLY)
                            metadata_before = commandstats(redis_port)
                            metadata_s3_before = s3_counts()
                            samples = []
                            chmod_samples, stat_samples = [], []
                            started = time.monotonic_ns()
                            try:
                                for index in range(args.metadata_edits):
                                    permissions = 0o600 if index % 2 == 0 else 0o640
                                    operation_start = time.monotonic_ns()
                                    os.chmod(target, permissions)
                                    chmod_end = time.monotonic_ns()
                                    assert os.stat(target).st_mode & 0o777 == permissions
                                    operation_end = time.monotonic_ns()
                                    samples.append(operation_end - operation_start)
                                    chmod_samples.append(chmod_end - operation_start)
                                    stat_samples.append(operation_end - chmod_end)
                                active_ns = time.monotonic_ns() - started
                                os.fsync(fd)
                            finally:
                                os.close(fd)
                            total_ns = time.monotonic_ns() - started
                            metadata_after = commandstats(redis_port)
                            metadata_s3_after = s3_counts()
                            cell['metadata_edits'] = dict(path=name, count=args.metadata_edits,
                                latency_schema='chmod-stat-v2', chmod_ns=chmod_samples, stat_ns=stat_samples,
                                final_mode=permissions, operation_ns=samples, active_ns=active_ns,
                                total_with_drain_ns=total_ns, redis_before=metadata_before,
                                redis_after=metadata_after,
                                rustfs_before=metadata_s3_before, rustfs_after=metadata_s3_after,
                                rustfs_delta=delta(metadata_s3_after, metadata_s3_before),
                                redis_delta={key: delta({field: number for field, number in value.items() if field != 'usec_per_call'}, metadata_before.get(key, {})) for key, value in metadata_after.items()})
                            save(out / 'metadata-phase.json', cell)
                        stop()
                        if args.metadata_sync != 'remote':
                            journal = work / ('journal-' + label) / 'state.bin'
                            shutil.copyfile(journal, out / 'metadata-journal.bin')
                            cell['metadata_journal_sha256'] = sha(journal)
                            cell['metadata_journal_bytes'] = journal.stat().st_size
                        if args.metadata_sync != 'remote' or args.metadata_edits:
                            # A fresh process in remote mode must see the final
                            # tree without reading our local journal. This is
                            # outside the timed hot-read interval.
                            remount_env = dict(env, BREWFS_METADATA_SYNC='remote')
                            remount_env.pop('BREWFS_METADATA_JOURNAL_DIR', None)
                            logfile = (out / 'remount-remote.log').open('wb')
                            result['commands'].append(mount_command)
                            process = subprocess.Popen(mount_command, env=remount_env, stdout=logfile, stderr=subprocess.STDOUT)
                            wait_for(ready, 'remote-only remount')
                            cell['remote_remount_samples'] = measured_round(point, expected)
                            if args.metadata_edits:
                                assert os.stat(point / cell['metadata_edits']['path']).st_mode & 0o777 == cell['metadata_edits']['final_mode']
                            stop()
                            cell['remote_remount_verified'] = True
                        save(out / 'result.json', cell)
                        result['cells'].append({key: cell[key] for key in ('label', 'scenario', 'pair', 'cache_enabled', 'elapsed_ns')})
                        print(label, round(elapsed / 1e6, 3), 'ms', flush=True)
            result['passed'] = True
    except Exception as error:
        result['error'] = (type(error).__name__ + ': ' + str(error)).replace(access, '[local-access]').replace(secret, '[local-secret]')
        raise
    finally:
        cleanup = {}
        try:
            stop()
        except Exception as error:
            cleanup['mount_error'] = str(error)
        for name in containers:
            try:
                found = docker('inspect', '--format', '{{index .Config.Labels "brewfs.hot.owner"}}', name, check=False)
                if found.returncode == 0 and found.stdout.strip() == owner:
                    docker('rm', '-f', '-v', name)
                cleanup[name] = docker('inspect', name, check=False).returncode != 0
            except Exception as error:
                cleanup[name] = False
                cleanup[name + '_error'] = str(error)
        cleanup['mount_absent'] = not mounted()
        result['cleanup'] = cleanup
        if cleanup['mount_absent'] and all(cleanup.get(name) for name in containers):
            shutil.rmtree(work)
        else:
            result['passed'] = False
        if 'mount_error' in cleanup:
            result['passed'] = False
        result['finished_unix_ns'] = time.time_ns()
        save(args.out / 'run.json', result)
        (args.out / 'SHA256SUMS').write_text(''.join(f'{sha(path)}  {path.relative_to(args.out).as_posix()}\n' for path in sorted(args.out.rglob('*')) if path.is_file()))


if __name__ == '__main__':
    main()
