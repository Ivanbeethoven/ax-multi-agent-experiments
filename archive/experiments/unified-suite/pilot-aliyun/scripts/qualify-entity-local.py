#!/usr/bin/env python3
"""Exercise a real entity mount and read its original stopped namespace remotely.

Small local functional check only. Preserves all buckets, metadata and failed
outputs for inspection. Does not create a cloud node or produce performance data.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import secrets
import socket
import subprocess
import sys
import time
import uuid
from urllib.request import urlopen

REPO = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(REPO / 'scripts'))
from full_trace_common import tree


def hardlinks(root):
    groups = {}
    for path in sorted(root.rglob('*')):
        if path.is_file() and not path.is_symlink():
            stat = path.stat()
            groups.setdefault((stat.st_dev, stat.st_ino), []).append(path.relative_to(root).as_posix())
    return sorted(sorted(g) for g in groups.values() if len(g) > 1)


def fixture(root):
    root.mkdir()
    (root / 'empty').mkdir()
    (root / 'base.txt').write_text('base file retained through COW\n')
    (root / 'large.bin').write_bytes(hashlib.shake_256(b'ax-entity-local-v1').digest(9 * 1024**2))
    os.link(root / 'large.bin', root / 'alias.bin')
    (root / 'symbolic').symlink_to('base.txt')


def mutate(root, challenge='full'):
    with (root / 'large.bin').open('r+b') as stream:
        stream.seek(1024**2 + 19)
        stream.write(b'private-upper-change')
        stream.truncate(20 * 1024**2)
        stream.seek(20 * 1024**2 - 8)
        stream.write(b'end-mark')
        stream.flush()
        os.fsync(stream.fileno())
    if challenge == 'full':
        (root / 'base.txt').rename(root / 'renamed.txt')
        (root / 'symbolic').unlink()
        (root / 'symbolic').symlink_to('renamed.txt')
    with (root / 'new.txt').open('xb') as stream:
        stream.write(b'new upper file\n')
        stream.flush()
        os.fsync(stream.fileno())
    fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def redis_command(arguments, username=None, password=None):
    def send(stream, values):
        values = [str(v).encode() for v in values]
        stream.write(b'*' + str(len(values)).encode() + b'\r\n')
        for value in values:
            stream.write(b'$' + str(len(value)).encode() + b'\r\n' + value + b'\r\n')
        stream.flush()
        prefix = stream.read(1)
        line = stream.readline().rstrip(b'\r\n')
        if prefix == b'-':
            raise RuntimeError(line.decode())
        if prefix in (b'+', b':'):
            return line
        raise ValueError('Unexpected Redis administrative response')
    with socket.create_connection(('127.0.0.1', 16379), timeout=5) as sock:
        with sock.makefile('rwb') as stream:
            if username:
                send(stream, ['AUTH', username, password])
            return send(stream, arguments)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--route', choices=['native', 'packed'], required=True)
    parser.add_argument('--challenge', choices=['full', 'data'], default='full',
        help='data checks COW/truncate/hole/create/fsync; it does not qualify rename or symlink creation')
    parser.add_argument('--build-root', type=Path, required=True)
    parser.add_argument('--services', type=Path, required=True)
    parser.add_argument('--reader', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=False)
    args.out.chmod(0o700)
    env = dict(os.environ, AWS_EC2_METADATA_DISABLED='true', AWS_REGION='us-east-1')
    for line in (args.services / 'rustfs.env').read_text().splitlines():
        key, value = line.split('=', 1)
        env[{'RUSTFS_ACCESS_KEY': 'AWS_ACCESS_KEY_ID', 'RUSTFS_SECRET_KEY': 'AWS_SECRET_ACCESS_KEY'}[key]] = value
    endpoint = 'http://127.0.0.1:19000'
    import boto3
    from botocore.config import Config
    client = boto3.client('s3', endpoint_url=endpoint, region_name='us-east-1',
        aws_access_key_id=env['AWS_ACCESS_KEY_ID'], aws_secret_access_key=env['AWS_SECRET_ACCESS_KEY'],
        config=Config(signature_version='s3v4', s3={'addressing_style': 'path'}, retries={'max_attempts': 0}))
    bucket = 'ax-pilot-local-' + uuid.uuid4().hex
    volume, namespace = str(uuid.uuid4()), str(uuid.uuid4())
    catalog = 'ax-pilot-local-' + uuid.uuid4().hex
    report = dict(passed=False, route=args.route, challenge=args.challenge, bucket=bucket, volume=volume, namespace=namespace,
        catalog=catalog, scope='local functional qualification only; no independent disks or performance claim',
        cloud_instances_started=False)
    mount = args.out / 'mount'
    mount.mkdir()
    process = None

    def run(name, argv, environment=env):
        directory = args.out / name
        directory.mkdir()
        (directory / 'command.json').write_text(json.dumps([str(x) for x in argv]) + '\n')
        with (directory / 'stdout').open('xb') as stdout, (directory / 'stderr').open('xb') as stderr:
            code = subprocess.run([str(x) for x in argv], env=environment, stdout=stdout, stderr=stderr).returncode
        (directory / 'exit.json').write_text(json.dumps(dict(exit_code=code)) + '\n')
        if code:
            raise RuntimeError('Native command failed: ' + name)

    try:
        client.create_bucket(Bucket=bucket)
        source = args.out / 'source'
        fixture(source)
        oracle = args.out / 'oracle'
        fixture(oracle)
        initial = tree(source)
        (args.out / 'initial-tree.json').write_text(json.dumps(initial) + '\n')
        mutate(oracle, args.challenge)
        (args.out / 'expected-tree.json').write_text(json.dumps(tree(oracle)) + '\n')
        (args.out / 'expected-hardlinks.json').write_text(json.dumps({'groups': hardlinks(oracle)}) + '\n')
        base = dict(volume_format='workspace-entity-v1', volume={'format': 'workspace-entity-v1'},
            workspace=namespace, workspace_namespace=catalog,
            data={'backend': 's3', 's3': dict(bucket=bucket, endpoint=endpoint, region='us-east-1',
                force_path_style=True, rustfs_ec_block_size_hint='auto')},
            meta={'backend': 'redis', 'redis': {'url': 'redis://127.0.0.1:16379/0'}},
            layout={'chunk_size': 4194304, 'block_size': 4194304},
            fuse={'workers': 4, 'max_background': 512, 'cache_ttl_ms': 0, 'keep_cache': False},
            cache={'cache_root': str(args.out / 'cache'), 'writeback_mode': 'upload_before_commit',
                'read_memory_bytes': 0, 'read_ssd_bytes': 0, 'compression': 'none'})
        config = args.out / 'mount.json'
        config.write_text(json.dumps(base) + '\n')
        argv = [args.build_root / 'bin/brewfs', 'mount', '--config', config,
            '--volume-format', 'workspace-entity-v1', '--experimental-entity-mount',
            '--entity-volume-id', volume, '--workspace', namespace, '--workspace-namespace', catalog,
            '--entity-read-cache-bytes', str(536870912 if args.route == 'packed' else 0),
            '--s3-rustfs-ec-block-size-hint', 'auto', '--metrics', '--metrics-addr', '127.0.0.1:18910']
        if args.route == 'packed':
            manifest = args.out / 'manifest-key.txt'
            run('producer', [args.build_root / 'bin/packed_v3_snapshot_fixture',
                '--source-directory', source, '--source-consistency', 'best-effort-detected',
                '--source-hardlink-policy', 'visible-links', '--output-dir', args.out / 'producer-tmp',
                '--manifest-output', manifest, '--bucket', bucket, '--endpoint', endpoint,
                '--region', 'us-east-1', '--force-path-style', 'true', '--prefix', 'base',
                '--metadata-codec', 'raw', '--data-codec', 'raw', '--inline-data', 'off',
                '--chunk-size', '4194304', '--block-size', '4194304', '--frame-policy', 'size-only',
                '--access-profile', 'random-small-file', '--max-random-frame-raw-bytes', '262144',
                '--s3-rustfs-ec-block-size-hint', 'auto'])
            argv += ['--packed-manifest-key', manifest.read_text().strip()]
        else:
            argv += ['--entity-native-source', source]
        argv.append(mount)
        directory = args.out / 'mount-process'
        directory.mkdir()
        (directory / 'command.json').write_text(json.dumps([str(x) for x in argv]) + '\n')
        stdout = (directory / 'stdout').open('xb')
        stderr = (directory / 'stderr').open('xb')
        process = subprocess.Popen([str(x) for x in argv], env=env, stdout=stdout, stderr=stderr)
        (directory / 'started.json').write_text(json.dumps(dict(pid=process.pid,
            start_ticks=Path(f'/proc/{process.pid}/stat').read_text().rsplit(')', 1)[1].split()[19])) + '\n')
        for _ in range(1200):
            if os.path.ismount(mount):
                break
            if process.poll() is not None:
                raise RuntimeError('Mount exited before readiness')
            time.sleep(.05)
        else:
            raise RuntimeError('Mount did not become ready; preserve process for inspection')
        if tree(mount) != initial or hardlinks(mount) != hardlinks(source):
            raise ValueError('Initial logical tree/hardlinks differ')
        mutate(mount, args.challenge)
        if tree(mount) != tree(oracle) or hardlinks(mount) != hardlinks(oracle):
            raise ValueError('Modified logical tree/hardlinks differ')
        with urlopen('http://127.0.0.1:18910/metrics', timeout=5) as response:
            (args.out / 'native-metrics.prom').write_bytes(response.read())
        run('unmount', ['fusermount3', '-u', mount])
        code = process.wait()
        stdout.close()
        stderr.close()
        (directory / 'exit.json').write_text(json.dumps(dict(exit_code=code, mount_absent=not os.path.ismount(mount))) + '\n')
        if code or os.path.ismount(mount):
            raise ValueError('Original mount did not stop normally')
        # Reconnect through an account that cannot change any metadata row.
        username, password = 'verify-' + uuid.uuid4().hex, secrets.token_hex(24)
        redis_command(['ACL', 'SETUSER', username, 'on', '>' + password,
            '~{brewfs-entity:' + volume + '}/*', '~{brewfs-ws-v1}:' + catalog + ':ws:v1/*',
            '+type', '+get', '+mget', '+exists', '+zscore', '+zrangebylex', '+strlen', '+time',
            '+evalsha', '+script|load', '+ping', '+client|setinfo'])
        try:
            redis_command(['SET', '{brewfs-entity:' + volume + '}/must-not-write', 'invalid'], username, password)
        except RuntimeError as error:
            if not str(error).startswith('NOPERM'):
                raise
        else:
            raise ValueError('Verification account allowed a write')
        verify_env = dict(env, AX_VERIFY_REDIS_URL=f'redis://{username}:{password}@127.0.0.1:16379/0')
        run('remote-reader', [args.reader, args.route, volume, namespace, catalog, bucket, endpoint,
            args.out / 'remote-proof', args.out / 'expected-tree.json', args.out / 'expected-hardlinks.json'], verify_env)
        proof = json.loads((args.out / 'remote-proof/verify.json').read_text())
        if not all(proof.get(k) is True for k in ('passed', 'expected_tree_equal', 'expected_hardlinks_equal',
            'namespace_unchanged', 'authority_unchanged', 'volume_rows_unchanged')):
            raise ValueError('Original namespace proof incomplete')
        if tree(source) != initial:
            raise ValueError('Immutable source changed')
        report.update(passed=True, readonly_acl_write_rejected=True, original_namespace_readback=proof,
            original_process_exited=True, mount_absent=True, immutable_source_unchanged=True)
    except BaseException as error:
        report['error'] = type(error).__name__ + ': ' + str(error)
        raise
    finally:
        client.close()
        report.update(at=datetime.now(timezone.utc).isoformat(),
            mount_present=os.path.ismount(mount), process_alive=process is not None and process.poll() is None)
        (args.out / 'result.json').write_text(json.dumps(report, indent=2) + '\n')
        print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
