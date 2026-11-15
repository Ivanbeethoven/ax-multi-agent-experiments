#!/usr/bin/env python3
"""Check the installed JuiceFS volume, memory cache, clone and fresh reopen.

Local functional qualification only. Uses one empty Redis database and a new
bucket; keeps all original metadata, objects, logs and failures for inspection.
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
import uuid
from urllib.request import urlopen

REPO = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / 'scripts'))
from full_trace_common import tree
from bench.runtime.prepare import native_argv
from bench.runtime.observe import prometheus


def redis_size(database, host='127.0.0.1', port=16379):
    with socket.create_connection((host, port), timeout=5) as sock:
        with sock.makefile('rwb') as stream:
            for command in (['SELECT', str(database)], ['DBSIZE']):
                values = [x.encode() for x in command]
                stream.write(b'*' + str(len(values)).encode() + b'\r\n')
                for value in values:
                    stream.write(b'$' + str(len(value)).encode() + b'\r\n' + value + b'\r\n')
                stream.flush()
                line = stream.readline().strip()
                if line.startswith(b'-'):
                    raise RuntimeError('Redis refused qualification command')
            if not line.startswith(b':'):
                raise ValueError('Unexpected Redis size response')
            return int(line[1:])


def challenge(root, hardlinks):
    root.mkdir()
    (root / 'empty').mkdir()
    (root / 'data').write_bytes(hashlib.shake_256(b'ax-juicefs-local-v1').digest(9 * 1024**2))
    if hardlinks:
        os.link(root / 'data', root / 'alias')
    else:
        (root / 'alias').write_bytes((root / 'data').read_bytes())
    (root / 'symbolic').symlink_to('data')
    with (root / 'data').open('rb') as stream:
        os.fsync(stream.fileno())
    fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def links(root):
    groups = {}
    for path in root.rglob('*'):
        if path.is_file() and not path.is_symlink():
            stat = path.stat()
            groups.setdefault((stat.st_dev, stat.st_ino), []).append(path.relative_to(root).as_posix())
    return sorted(sorted(group) for group in groups.values() if len(group) > 1)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--build-root', type=Path, required=True)
    p.add_argument('--services', type=Path, required=True)
    p.add_argument('--database', type=int, default=5)
    p.add_argument('--hardlinks', action='store_true', help='Also require clone to preserve hardlink groups; failures remain separate')
    p.add_argument('--out', type=Path, required=True)
    p.add_argument('--redis-host',default='127.0.0.1')
    p.add_argument('--redis-port',type=int,default=16379)
    p.add_argument('--s3-endpoint',default='http://127.0.0.1:19000')
    p.add_argument('--cloud',action='store_true')
    a = p.parse_args()
    if os.geteuid() != 0:
        raise PermissionError('Run on the owned local host as root')
    if not 0 <= a.database < (256 if a.cloud else 16) or redis_size(a.database,a.redis_host,a.redis_port) != 0:
        raise ValueError('An empty, dedicated local Redis database is required; never overwrite one')
    with socket.socket() as probe:
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        probe.bind(('127.0.0.1', 19567))
    a.out.mkdir(parents=True, exist_ok=False)
    a.out.chmod(0o700)
    env = dict(os.environ, AWS_EC2_METADATA_DISABLED='true', AWS_REGION='us-east-1',
        NO_PROXY='127.0.0.1,localhost', no_proxy='127.0.0.1,localhost')
    for line in (a.services / 'rustfs.env').read_text().splitlines():
        key, value = line.split('=', 1)
        env[{'RUSTFS_ACCESS_KEY': 'ACCESS_KEY', 'RUSTFS_SECRET_KEY': 'SECRET_KEY'}[key]] = value
    import boto3
    from botocore.config import Config
    client = boto3.client('s3', endpoint_url=a.s3_endpoint, region_name='us-east-1',
        aws_access_key_id=env['ACCESS_KEY'], aws_secret_access_key=env['SECRET_KEY'],
        config=Config(signature_version='s3v4', s3={'addressing_style': 'path'}, retries={'max_attempts': 0}))
    bucket = 'ax-pilot-juicefs-' + uuid.uuid4().hex
    binary = a.build_root / 'bin/juicefs'
    mount = a.out / 'mount'
    mount.mkdir()
    config = dict(metadata_url=f'redis://{a.redis_host}:{a.redis_port}/{a.database}', writeback=False,
        cache_dir='memory', cache_mib=512, buffer_mib=512, attr_cache_seconds=0,
        entry_cache_seconds=0, open_cache_seconds=0, prefetch=1, max_readahead_mib=128,
        backup_meta='0', verify_cache_checksum='full', cache_large_write=True, no_usage_report=True)
    result = dict(passed=False, scope=('cloud' if a.cloud else 'local')+' clone/reopen feasibility only; no performance or cross-machine result',
        cloud_instances_started=a.cloud, bucket=bucket, metadata_database=a.database, client_settings=config,
        fixture_hardlinks=a.hardlinks)
    process = None

    def run(name, argv):
        directory = a.out / name
        directory.mkdir()
        (directory / 'command.json').write_text(json.dumps([str(x) for x in argv]) + '\n')
        with (directory / 'stdout').open('xb') as stdout, (directory / 'stderr').open('xb') as stderr:
            code = subprocess.run([str(x) for x in argv], env=env, stdout=stdout, stderr=stderr).returncode
        (directory / 'exit.json').write_text(json.dumps({'exit_code': code}) + '\n')
        if code:
            raise RuntimeError('Native command failed: ' + name)

    def metrics(name):
        with urlopen('http://127.0.0.1:19567/metrics', timeout=5) as response:
            raw = response.read()
        (a.out / (name + '.prom')).write_bytes(raw)
        values = prometheus(raw.decode())
        cache = {k: v for k, v in values.items() if k.startswith('juicefs_blockcache_')}
        if not cache or any('mp=' + json.dumps(str(mount)) not in k for k in cache):
            raise ValueError('Native metrics belong to another mount, or the cache series are absent')
        return values

    try:
        client.create_bucket(Bucket=bucket)
        run('format', [binary, 'format', '--storage', 's3', '--bucket',
            a.s3_endpoint+'/' + bucket, '--block-size', '4M', '--compress', 'none',
            '--trash-days', '0', config['metadata_url'], 'ax-pilot-local-' + uuid.uuid4().hex[:12]])
        oracle = a.out / 'oracle'
        challenge(oracle, a.hardlinks)
        expected_base = tree(oracle)
        expected_links = links(oracle)
        with (oracle / 'data').open('r+b') as stream:
            stream.seek(1048595)
            stream.write(b'private-child-change')
            stream.truncate(20 * 1024**2)
            stream.seek(20 * 1024**2 - 8)
            stream.write(b'end-mark')
            stream.flush()
            os.fsync(stream.fileno())
        expected_child = tree(oracle)
        for name, data in [('expected-base', expected_base), ('expected-child', expected_child),
                           ('expected-hardlinks', expected_links)]:
            (a.out / (name + '.json')).write_text(json.dumps(data) + '\n')
        for attempt in (1, 2):
            directory = a.out / f'mount-{attempt}'
            directory.mkdir()
            argv = native_argv('juicefs', str(binary), None, config, str(mount), 'C1',
                metrics_address='127.0.0.1:19567', metadata_backend='redis') + ['--hide-internal', '--no-syslog']
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
                raise RuntimeError('Mount readiness unresolved; preserve original process')
            metrics(f'mount-{attempt}-identity')
            if attempt == 1:
                challenge(mount / 'base', a.hardlinks)
                if tree(mount / 'base') != expected_base or links(mount / 'base') != expected_links:
                    raise ValueError('Original tree or hardlinks differ')
                run('clone', [binary, 'clone', '-p', mount / 'base', mount / 'child'])
                if tree(mount / 'child') != expected_base or links(mount / 'child') != expected_links:
                    raise ValueError('Native clone tree or hardlink relationships differ')
                with (mount / 'child/data').open('r+b') as stream:
                    stream.seek(1048595)
                    stream.write(b'private-child-change')
                    stream.truncate(20 * 1024**2)
                    stream.seek(20 * 1024**2 - 8)
                    stream.write(b'end-mark')
                    stream.flush()
                    os.fsync(stream.fileno())
            if tree(mount / 'base') != expected_base or tree(mount / 'child') != expected_child:
                raise ValueError('Parent isolation or child bytes differ')
            if links(mount / 'base') != expected_links or links(mount / 'child') != expected_links:
                raise ValueError('Hardlink relationships differ after COW/reopen')
            before = metrics(f'mount-{attempt}-before-hot-read')
            for _ in range(2):
                with (mount / 'child/data').open('rb') as stream:
                    while stream.read(1048576):
                        pass
            after = metrics(f'mount-{attempt}-after-hot-read')
            result[f'mount_{attempt}_cache_counters'] = {k: dict(before=before.get(k), after=v)
                for k, v in after.items() if k.split('{', 1)[0] in
                ('juicefs_blockcache_hits', 'juicefs_blockcache_miss', 'juicefs_blockcache_hit_bytes',
                 'juicefs_blockcache_miss_bytes', 'juicefs_blockcache_blocks', 'juicefs_blockcache_bytes',
                 'juicefs_store_cache_size_bytes')}
            run(f'unmount-{attempt}', ['fusermount3', '-u', mount])
            code = process.wait()
            stdout.close()
            stderr.close()
            (directory / 'exit.json').write_text(json.dumps(dict(exit_code=code,
                mount_absent=not os.path.ismount(mount))) + '\n')
            if code or os.path.ismount(mount):
                raise ValueError('Original mount did not stop normally')
        result.update(passed=True, native_clone=True, parent_unchanged=True, child_tree_equal=True,
            hardlinks_equal=True, same_volume_reopened=True, normally_stopped=True,
            hardlink_clone_qualified=a.hardlinks,
            memory_cache_cli_accepted=True, actual_cache_effectiveness='see original cache counters; CLI acceptance alone is not a hit proof')
    except BaseException as error:
        result['error'] = type(error).__name__ + ': ' + str(error)
        raise
    finally:
        client.close()
        result.update(at=datetime.now(timezone.utc).isoformat(), mount_present=os.path.ismount(mount),
            process_alive=process is not None and process.poll() is None)
        (a.out / 'result.json').write_text(json.dumps(result, indent=2) + '\n')
        print(json.dumps({k: v for k, v in result.items() if not k.endswith('_cache_counters')}, indent=2))


if __name__ == '__main__':
    main()
