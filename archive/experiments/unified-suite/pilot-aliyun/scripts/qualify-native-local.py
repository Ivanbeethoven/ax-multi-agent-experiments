#!/usr/bin/env python3
"""Write, stop, inspect, and reopen the same native volume with a fresh cache.

Local functional qualification only. Requires root because a new flat volume's
root inode belongs to root. Keeps original metadata, objects and failure logs.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
from urllib.parse import urlsplit
import uuid

REPO = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(REPO / 'scripts'))
from full_trace_common import tree


def require_unused_redis(url):
    endpoint = urlsplit(url)
    if endpoint.scheme != 'redis' or not endpoint.hostname or endpoint.username or endpoint.password or endpoint.query or endpoint.fragment:
        raise ValueError('Explicit plain pilot Redis endpoint required')
    database = int(endpoint.path.lstrip('/') or '0')
    with socket.create_connection((endpoint.hostname, endpoint.port or 6379), timeout=5) as connection:
        with connection.makefile('rwb') as stream:
            for command in (['SELECT', str(database)], ['DBSIZE']):
                values = [v.encode() for v in command]
                stream.write(b'*'+str(len(values)).encode()+b'\r\n')
                for value in values:
                    stream.write(b'$'+str(len(value)).encode()+b'\r\n'+value+b'\r\n')
                stream.flush()
                reply = stream.readline(1024)
                if command[0] == 'SELECT' and reply != b'+OK\r\n':
                    raise ValueError('Redis database selection failed')
                if command[0] == 'DBSIZE' and reply != b':0\r\n':
                    raise ValueError('Qualification needs an unused metadata DB; never reset an existing DB')


def write_challenge(root):
    (root / 'sub').mkdir()
    with (root / 'sub/data').open('xb') as stream:
        stream.write(hashlib.shake_256(b'ax-native-reopen-v1').digest(65536))
        stream.seek(1048576)
        stream.write(b'after-hole')
        stream.flush()
        os.fsync(stream.fileno())
    (root / 'sub/data').rename(root / 'data-renamed')
    (root / 'link').symlink_to('data-renamed')
    for directory in (root / 'sub', root):
        fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)


def write_reopened_challenge(root):
    with (root/'data-renamed').open('r+b') as stream:
        stream.seek(19);stream.write(b'new-writer-after-reopen');stream.flush();os.fsync(stream.fileno())
    with (root/'new-writer.txt').open('xb') as stream:
        stream.write(b'created by the second writer\n');stream.flush();os.fsync(stream.fileno())
    fd=os.open(root,os.O_RDONLY|os.O_DIRECTORY)
    try:os.fsync(fd)
    finally:os.close(fd)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--route', choices=['flat', 'workspace'], required=True)
    p.add_argument('--build-root', type=Path, required=True)
    p.add_argument('--services', type=Path, required=True)
    p.add_argument('--out', type=Path, required=True)
    p.add_argument('--s3-endpoint',default='http://127.0.0.1:19000')
    p.add_argument('--redis-url')
    p.add_argument('--native-base',type=Path)
    p.add_argument('--cloud',action='store_true')
    p.add_argument('--mutate-reopened',action='store_true',help='Write existing/new files after clean reopen, then verify a third cold mount')
    p.add_argument('--log-level',choices=['error','warn'],default='error',help='Warn captures native fsync errors without HTTP debug headers')
    a = p.parse_args()
    if os.geteuid() != 0:
        raise PermissionError('Run on the owned local host as root')
    a.out.mkdir(parents=True, exist_ok=False)
    env = dict(os.environ, AWS_EC2_METADATA_DISABLED='true', AWS_REGION='us-east-1',
        RUST_LOG=a.log_level, NO_COLOR='1',
        NO_PROXY='127.0.0.1,localhost', no_proxy='127.0.0.1,localhost')
    for line in (a.services / 'rustfs.env').read_text().splitlines():
        k, v = line.split('=', 1)
        env[{'RUSTFS_ACCESS_KEY': 'AWS_ACCESS_KEY_ID', 'RUSTFS_SECRET_KEY': 'AWS_SECRET_ACCESS_KEY'}[k]] = v
    import boto3
    from botocore.config import Config
    client = boto3.client('s3', endpoint_url=a.s3_endpoint, region_name='us-east-1',
        aws_access_key_id=env['AWS_ACCESS_KEY_ID'], aws_secret_access_key=env['AWS_SECRET_ACCESS_KEY'],
        config=Config(signature_version='s3v4', retries={'max_attempts': 0}, s3={'addressing_style': 'path'}))
    # init-volume appends a 36-character workspace UUID; keep the base short
    # enough for S3's 63-character bucket limit.
    bucket = 'axp-' + uuid.uuid4().hex[:12]
    catalog = 'ax-pilot-native-' + uuid.uuid4().hex
    redis = a.redis_url or 'redis://127.0.0.1:16379/' + ('1' if a.route == 'flat' else '3')
    binary = a.build_root / 'bin/brewfs'
    base = dict(volume_format='flat-v1' if a.route == 'flat' else 'workspace-native-v2',
        data={'backend': 's3', 's3': dict(bucket=bucket, endpoint=a.s3_endpoint,
            region='us-east-1', force_path_style=True, rustfs_ec_block_size_hint='auto')},
        meta={'backend': 'redis', 'redis': {'url': redis}},
        layout={'chunk_size': 67108864, 'block_size': 4194304},
        fuse={'workers': 4, 'max_background': 512, 'cache_ttl_ms': 0, 'keep_cache': False},
        cache={'read_memory_bytes': 536870912, 'read_ssd_bytes': 0, 'write_memory_bytes': 536870912,
            'write_ssd_bytes': 1073741824, 'memory_budget_bytes': 536870912,
            'writeback_mode': 'upload_before_commit', 'compression': 'none'})
    result = dict(passed=False, route=a.route, bucket=bucket, catalog=catalog,
        binary_sha256=hashlib.sha256(binary.read_bytes()).hexdigest(),mutate_reopened=a.mutate_reopened,log_level=a.log_level,
        scope=('cloud' if a.cloud else 'local')+' native reopen feasibility only; not performance, native immutable publication or cross-machine handoff',
        cloud_instances_started=a.cloud)
    process = None
    mount = a.out / 'mount'
    mount.mkdir()

    def run(name, argv):
        directory = a.out / name
        directory.mkdir()
        (directory / 'command.json').write_text(json.dumps([str(x) for x in argv]) + '\n')
        with (directory / 'stdout').open('xb') as stdout, (directory / 'stderr').open('xb') as stderr:
            code = subprocess.run([str(x) for x in argv], env=env, stdout=stdout, stderr=stderr).returncode
        (directory / 'exit.json').write_text(json.dumps({'exit_code': code}) + '\n')
        if code:
            raise RuntimeError('Native command failed: ' + name)
        return (directory / 'stdout').read_text()

    try:
        if a.cloud or a.route == 'flat':
            require_unused_redis(redis)
            result['metadata_database_empty_verified'] = True
        client.create_bucket(Bucket=bucket)
        prefix = [binary, 'workspace', '--meta-backend', 'redis', '--meta-url', redis,
            '--workspace-namespace', catalog]
        if a.route == 'workspace':
            data_config = a.out / 'data-config.json'
            data_config.write_text(json.dumps({'data': base['data']}) + '\n')
            initialized = json.loads(run('init-volume', [*prefix, 'init-volume', '--data-config', data_config]))
            run('init-native', [*prefix, 'init-native'])
            base.update(workspace=initialized['workspace_id'], workspace_namespace=catalog,
                volume=dict(format='workspace-native-v2', schema_version=2, native_control_version=2,
                    chunk_size=67108864, block_size=4194304),
                native_base=json.loads((a.native_base or Path(__file__).resolve().parents[1] / 'profiles/native-base-p1.json').read_text()))
            result['workspace_id'] = initialized['workspace_id']
        oracle = a.out / 'oracle'
        oracle.mkdir()
        write_challenge(oracle)
        expected = tree(oracle)
        (a.out / 'expected-tree.json').write_text(json.dumps(expected) + '\n')
        for attempt in ((1, 2, 3) if a.mutate_reopened else (1, 2)):
            base['cache']['cache_root'] = str(a.out / f'cache-{attempt}')
            config = a.out / f'mount-{attempt}.json'
            config.write_text(json.dumps(base) + '\n')
            directory = a.out / f'mount-process-{attempt}'
            directory.mkdir()
            argv = [str(binary), 'mount', '--config', str(config), '--metrics',
                '--metrics-addr', '127.0.0.1:18911', '--s3-rustfs-ec-block-size-hint', 'auto', str(mount)]
            (directory / 'command.json').write_text(json.dumps(argv) + '\n')
            stdout, stderr = (directory / 'stdout').open('xb'), (directory / 'stderr').open('xb')
            process = subprocess.Popen(argv, env=env, stdout=stdout, stderr=stderr)
            (directory / 'started.json').write_text(json.dumps(dict(pid=process.pid,
                start_ticks=Path(f'/proc/{process.pid}/stat').read_text().rsplit(')', 1)[1].split()[19])) + '\n')
            for _ in range(1200):
                if os.path.ismount(mount):
                    break
                if process.poll() is not None:
                    raise RuntimeError('Mount exited before readiness')
                time.sleep(.05)
            else:
                raise RuntimeError('Mount readiness failed; preserve process')
            if attempt == 1:
                if tree(mount):
                    raise ValueError('Expected an empty original volume')
                write_challenge(mount)
            actual=tree(mount)
            if actual != expected:
                raise ValueError('Logical tree or bytes differ')
            proof=dict(passed=True,attempt=attempt,full_tree_equal=True,
                expected_tree_sha256=hashlib.sha256(json.dumps(expected,sort_keys=True).encode()).hexdigest(),
                fresh_cache=str(a.out/f'cache-{attempt}'),workspace_id=result.get('workspace_id'),
                before_reopened_write=a.mutate_reopened and attempt==2)
            (a.out/f'readback-{attempt}.json').write_text(json.dumps(proof,indent=2)+'\n')
            if a.mutate_reopened and attempt==2:
                result['clean_reopened_tree_equal_before_write']=True
                write_reopened_challenge(oracle)
                expected=tree(oracle)
                (a.out/'expected-reopened-tree.json').write_text(json.dumps(expected)+'\n')
                write_reopened_challenge(mount)
                if tree(mount)!=expected:raise ValueError('Reopened writer bytes differ')
            run(f'unmount-{attempt}', ['fusermount3', '-u', mount])
            code = process.wait()
            stdout.close()
            stderr.close()
            (directory / 'exit.json').write_text(json.dumps(dict(exit_code=code,
                mount_absent=not os.path.ismount(mount))) + '\n')
            if code or os.path.ismount(mount):
                raise ValueError('Original process/mount did not close normally')
            if a.route == 'workspace':
                inspected = json.loads(run(f'inspect-{attempt}', [*prefix, 'inspect', result['workspace_id']]))
                if inspected['lease_id'] is not None or inspected['holder_generation'] is not None or \
                        inspected['pending_writeback_bytes'] != 0 or inspected['workspace_id'] != result['workspace_id']:
                    raise ValueError('Original workspace writer/debt did not release')
        result.update(passed=True, original_volume_reopened=True, fresh_cache_on_reopen=True,
            logical_tree_equal=True, normally_stopped=True,reopened_writes_persisted=a.mutate_reopened)
    except BaseException as error:
        result['error'] = type(error).__name__ + ': ' + str(error)
        raise
    finally:
        if not result['passed'] and process is not None and process.poll() is None:
            try:
                if os.path.ismount(mount):
                    run('failure-normal-unmount', ['fusermount3', '-u', mount])
                code = process.wait()
                stdout.close(); stderr.close()
                cleanup = dict(exit_code=code, mount_absent=not os.path.ismount(mount),
                               scope='Original Popen normal exit after failed synchronous check; no forced unmount')
                (directory/'failure-exit.json').write_text(json.dumps(cleanup)+'\n')
                result['failure_cleanup'] = cleanup
            except BaseException as error:
                result['cleanup_error'] = type(error).__name__ + ': ' + str(error)
        client.close()
        result.update(at=datetime.now(timezone.utc).isoformat(), mount_present=os.path.ismount(mount),
            process_alive=process is not None and process.poll() is None)
        (a.out / 'result.json').write_text(json.dumps(result, indent=2) + '\n')
        print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
