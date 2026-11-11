#!/usr/bin/env python3
"""Local native fork check using the volume's initial empty sealed revision.

This checks CLI identities, private writes and cold readback. It does not
publish a nonempty base or qualify the million-file C4 performance experiment.
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


def empty_local_db(url):
    parsed = urlsplit(url)
    if parsed.scheme != 'redis' or parsed.hostname != '127.0.0.1' or parsed.username or parsed.password:
        raise ValueError('Only the owned local unauthenticated Redis is supported')
    database = int(parsed.path.removeprefix('/'))
    with socket.create_connection((parsed.hostname, parsed.port or 6379), timeout=5) as sock:
        stream = sock.makefile('rb')
        for args in [('SELECT', str(database)), ('DBSIZE',)]:
            wire = ('*' + str(len(args)) + '\r\n').encode()
            for arg in args:
                wire += ('$' + str(len(arg)) + '\r\n' + arg + '\r\n').encode()
            sock.sendall(wire)
            reply = stream.readline()
            if reply != (b'+OK\r\n' if args[0] == 'SELECT' else b':0\r\n'):
                raise ValueError('Local qualification requires an unused Redis DB; never flush')


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--build-root', type=Path, required=True)
    p.add_argument('--services', type=Path, required=True)
    p.add_argument('--redis-url', required=True)
    p.add_argument('--binary-sha256', required=True)
    p.add_argument('--out', type=Path, required=True)
    a = p.parse_args()
    if os.geteuid() != 0:
        raise PermissionError('Run on the owned local FUSE host as root')
    a.out = a.out.resolve()
    binary = a.build_root / 'bin/brewfs'
    digest = hashlib.file_digest(binary.open('rb'), 'sha256').hexdigest()
    if digest != a.binary_sha256:
        raise ValueError('BrewFS binary does not match the reviewed build')
    empty_local_db(a.redis_url)
    with socket.socket() as probe:
        probe.bind(('127.0.0.1', 18912))
    a.out.mkdir(parents=True, exist_ok=False)
    (a.out / 'executed-source.py').write_bytes(Path(__file__).read_bytes())
    env = dict(os.environ, AWS_EC2_METADATA_DISABLED='true', AWS_REGION='us-east-1',
        RUST_LOG='warn', NO_COLOR='1', NO_PROXY='127.0.0.1,localhost', no_proxy='127.0.0.1,localhost')
    mapping = {'RUSTFS_ACCESS_KEY': 'AWS_ACCESS_KEY_ID', 'RUSTFS_SECRET_KEY': 'AWS_SECRET_ACCESS_KEY'}
    for line in (a.services / 'rustfs.env').read_text().splitlines():
        key, value = line.split('=', 1)
        env[mapping[key]] = value
    import boto3
    from botocore.config import Config
    client = boto3.client('s3', endpoint_url='http://127.0.0.1:19000', region_name='us-east-1',
        aws_access_key_id=env['AWS_ACCESS_KEY_ID'], aws_secret_access_key=env['AWS_SECRET_ACCESS_KEY'],
        config=Config(signature_version='s3v4', retries={'max_attempts': 0}, s3={'addressing_style': 'path'}))
    bucket = 'axf-' + uuid.uuid4().hex[:12]
    namespace = 'ax-pilot-fork-' + uuid.uuid4().hex
    result = dict(passed=False, binary_sha256=digest, bucket=bucket, namespace=namespace,
        scope='Local initial-empty-revision fork only; no nonempty publication, million-file performance or model calls',
        cloud_instances_started=False, model_calls=0, mounts=[])
    prefix = [str(binary), 'workspace', '--meta-backend', 'redis', '--meta-url', a.redis_url,
        '--workspace-namespace', namespace]
    mount = a.out / 'mount'
    mount.mkdir()
    active = None
    streams = []
    active_directory = None

    def save(name, value):
        (a.out / name).write_text(json.dumps(value, indent=2) + '\n')

    def run(name, argv):
        directory = a.out / name
        directory.mkdir()
        (directory / 'command.json').write_text(json.dumps([str(x) for x in argv]) + '\n')
        with (directory / 'stdout').open('xb') as stdout, (directory / 'stderr').open('xb') as stderr:
            code = subprocess.run([str(x) for x in argv], env=env, stdout=stdout, stderr=stderr,
                timeout=120).returncode
        (directory / 'exit.json').write_text(json.dumps({'exit_code': code}) + '\n')
        if code:
            raise RuntimeError('Native command failed: ' + name)
        return json.loads((directory / 'stdout').read_text()) if name.startswith(('init-', 'fork', 'inspect')) else None

    def stop(name):
        nonlocal active, streams
        if active is None:
            return
        if os.path.ismount(mount):
            run('unmount-' + name, ['fusermount3', '-u', mount])
        code = active.wait(timeout=60)
        for stream in streams:
            stream.close()
        closure = dict(exit_code=code, mount_absent=not os.path.ismount(mount))
        (active_directory / 'exit.json').write_text(json.dumps(closure) + '\n')
        active = None
        streams = []
        if code or not closure['mount_absent']:
            raise ValueError('Owned mount did not stop normally')

    def visit(name, record, expected, payload=None):
        nonlocal active, streams, active_directory
        config = json.loads((a.out / 'base-config.json').read_text())
        config['workspace'] = record['workspace_id']
        config['cache']['cache_root'] = str(a.out / ('cache-' + name))
        save('mount-' + name + '.json', config)
        active_directory = a.out / ('process-' + name)
        active_directory.mkdir()
        argv = [str(binary), 'mount', '--config', str(a.out / ('mount-' + name + '.json')),
            '--metrics', '--metrics-addr', '127.0.0.1:18912', '--s3-rustfs-ec-block-size-hint', 'auto', str(mount)]
        (active_directory / 'command.json').write_text(json.dumps(argv) + '\n')
        streams = [(active_directory / x).open('xb') for x in ['stdout', 'stderr']]
        active = subprocess.Popen(argv, env=env, stdout=streams[0], stderr=streams[1])
        identity = dict(pid=active.pid,
            start_ticks=Path(f'/proc/{active.pid}/stat').read_text().rsplit(')', 1)[1].split()[19])
        (active_directory / 'started.json').write_text(json.dumps(identity) + '\n')
        result['mounts'].append(dict(name=name, workspace_id=record['workspace_id'], **identity))
        deadline = time.monotonic() + 60
        while not os.path.ismount(mount):
            if active.poll() is not None or time.monotonic() > deadline:
                raise RuntimeError('Mount failed before readiness: ' + name)
            time.sleep(.05)
        if tree(mount) != expected:
            raise ValueError('Complete tree/bytes mismatch: ' + name)
        if payload is not None:
            with (mount / 'private.txt').open('xb') as output:
                output.write(payload)
                output.flush()
                os.fsync(output.fileno())
            fd = os.open(mount, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
            oracle = a.out / ('oracle-' + name)
            oracle.mkdir()
            (oracle / 'private.txt').write_bytes(payload)
            expected = tree(oracle)
            if tree(mount) != expected:
                raise ValueError('Private bytes differ after fsync: ' + name)
        save('readback-' + name + '.json', dict(passed=True, expected_tree=expected,
            fresh_cache=config['cache']['cache_root'], workspace_id=record['workspace_id']))
        stop(name)
        for sample in range(2):
            inspect = run(f'inspect-{name}-{sample}', [*prefix, 'inspect', record['workspace_id']])
            if inspect['workspace_id'] != record['workspace_id'] or inspect['lease_id'] is not None or \
                    inspect['holder_generation'] is not None or inspect['pending_writeback_bytes'] != 0:
                raise ValueError('Writer lease or debt remained: ' + name)
            if sample == 0:
                time.sleep(1)
        return expected

    try:
        os.umask(0o022)
        client.create_bucket(Bucket=bucket)
        data = dict(backend='s3', s3=dict(bucket=bucket, endpoint='http://127.0.0.1:19000',
            region='us-east-1', force_path_style=True, rustfs_ec_block_size_hint='auto'))
        save('data-config.json', {'data': data})
        parent = run('init-volume', [*prefix, 'init-volume', '--data-config', a.out / 'data-config.json'])
        run('init-native', [*prefix, 'init-native'])
        revision = parent['fork_base']
        exact = f"{revision['layer_id']}:{revision['sealed_version']}:{bytes(revision['root_hash']).hex()}"
        result.update(parent_workspace_id=parent['workspace_id'], initial_revision=exact)
        children = run('fork-initial', [*prefix, 'fork', exact, '--count', '2', '--data-config', a.out / 'data-config.json'])
        if len(children) != 2 or len({x['workspace_id'] for x in [parent, *children]}) != 3 or \
                len({x['upper_bucket'] for x in [parent, *children]}) != 3 or any(x['fork_base'] != revision for x in children):
            raise ValueError('Fork did not produce two distinct children on the exact sealed revision')
        save('base-config.json', dict(volume_format='workspace-native-v2', data=data,
            workspace_namespace=namespace, meta={'backend': 'redis', 'redis': {'url': a.redis_url}},
            volume=dict(format='workspace-native-v2', schema_version=2, native_control_version=2,
                chunk_size=67108864, block_size=4194304),
            native_base=json.loads((Path(__file__).resolve().parents[1] / 'profiles/native-base-p1.json').read_text()),
            layout=dict(chunk_size=67108864, block_size=4194304),
            fuse=dict(workers=4, max_background=512, cache_ttl_ms=0, keep_cache=False),
            cache=dict(read_memory_bytes=536870912, read_ssd_bytes=0, write_memory_bytes=536870912,
                write_ssd_bytes=1073741824, memory_budget_bytes=536870912,
                writeback_mode='upload_before_commit', compression='none')))
        records = [('parent', parent), ('child-1', children[0]), ('child-2', children[1])]
        expected = {}
        for name, record in records:
            expected[name] = visit('write-' + name, record, {}, ('private ' + name + '\n').encode())
        for name, record in records:
            visit('cold-' + name, record, expected[name])
        result.update(passed=True, exact_revision_fork=True, distinct_upper_buckets=True,
            parent_and_siblings_isolated=True, all_three_cold_readback=True, normally_stopped=True)
    except BaseException as error:
        result['error'] = type(error).__name__ + ': ' + str(error)
        raise
    finally:
        try:
            stop('failure-cleanup')
        except BaseException as error:
            result['cleanup_error'] = type(error).__name__ + ': ' + str(error)
            result['passed'] = False
        client.close()
        result.update(at=datetime.now(timezone.utc).isoformat(), mount_present=os.path.ismount(mount),
            process_alive=active is not None and active.poll() is None)
        save('result.json', result)
        print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
