#!/usr/bin/env python3
"""Two isolated RustFS arms; same B, toggle only the small read frame flags."""
import argparse
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import secrets
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import uuid

IMAGE = 'sha256:9750c5eaaac37e2d6ebaa8aaf39a4aa98a35fca5fba8de5ade26585123058806'
COMMIT = 'f4ed202508f2153afb7956bae6f7799b60eccd00'
BINARY = '1996d66c8d6e322a1e1ac8b398b7d236e1548f74fee4aed2266425c5216c0374'
BRANCH = 'feat/dynamic-block-size-rebased'
ADDRESS = '127.0.0.1:19939'
DOCKER = ['sudo', '-n', 'docker']
MODES = {'block-only': None, 'enabled': 16384}
FLAGS = ('RUSTFS_EC_BLOCK_SIZE_HINT_ENABLE', 'RUSTFS_EC_BLOCK_SIZE_HINT_FLEET_CONFIRMED',
         'RUSTFS_EC_READ_QUANTUM_ENABLE', 'RUSTFS_EC_READ_QUANTUM_FLEET_CONFIRMED')
SIZE, B, WIDTH = 4 * 1024**2, 65536, 4096
STARTS = (0, 16368, 32752, 65520, SIZE - WIDTH)


def sha(data):
    return hashlib.sha256(data).hexdigest()


def require(check, why):
    if not check:
        raise ValueError(why)


def save(path, value):
    path.write_text(json.dumps(value, indent=2) + '\n', encoding='utf-8')


def command(*argv, timeout=12, data=None, check=True):
    return subprocess.run(argv, input=data, capture_output=True, timeout=timeout, check=check)


def payload_bytes():
    return hashlib.shake_256(b'ax-c1-rustfs-q-current-local-v1').digest(SIZE)


def requests():
    import boto3
    from botocore.config import Config
    from botocore.exceptions import EndpointConnectionError
    access, secret, bucket = json.load(sys.stdin)
    config = Config(signature_version='s3v4', retries={'max_attempts': 0}, connect_timeout=1,
                    read_timeout=5, proxies={}, s3={'addressing_style': 'path'})
    with closing(boto3.client('s3', endpoint_url='http://' + ADDRESS, region_name='us-east-1',
                             config=config, aws_access_key_id=access, aws_secret_access_key=secret)) as c:
        deadline = time.monotonic() + 35
        while True:
            try:
                c.list_buckets()
                break
            except EndpointConnectionError:
                if time.monotonic() >= deadline:
                    raise
                time.sleep(.5)
        c.create_bucket(Bucket=bucket)
        payload, key, sent = payload_bytes(), 'same-input', []
        c.meta.events.register('before-send.s3.GetObject',
                               lambda request, **kw: sent.append((request.method, request.headers.get('Range'))))
        put = c.put_object(Bucket=bucket, Key=key, Body=payload,
                           Metadata={'rustfs-ec-block-size-hint': str(B)})
        head = c.head_object(Bucket=bucket, Key=key)
        require(head['ContentLength'] == SIZE, 'HEAD size')
        io = dict(put_http_status=put['ResponseMetadata']['HTTPStatusCode'], head_bytes=head['ContentLength'],
                  requested_block_bytes=B, payload_sha256=sha(payload), gets=[])
        for name, res in [('put', put), ('head', head)]:
            h = res['ResponseMetadata']['HTTPHeaders']
            io[name + '_layout_headers'] = {k: h[k] for k in (
                'x-rustfs-effective-ec-block-size', 'x-rustfs-layout-hint-status', 'x-rustfs-layout-hint-reason')
                if k in h}
        for start in (None, *STARTS):
            hdr = None if start is None else f'bytes={start}-{start+WIDTH-1}'
            res = c.get_object(Bucket=bucket, Key=key, **({'Range': hdr} if hdr else {}))
            with closing(res['Body']) as body:
                got = body.read()
            wanted = payload if start is None else payload[start:start+WIDTH]
            row = dict(range=hdr, http_status=res['ResponseMetadata']['HTTPStatusCode'],
                       content_range=res.get('ContentRange'), content_length=res['ContentLength'],
                       body_bytes=len(got), body_sha256=sha(got), equals_input_slice=got == wanted)
            io['gets'].append(row)
        io['sent_get_requests'] = [[m, h.decode('ascii') if isinstance(h, bytes) else h] for m, h in sent]
    print(json.dumps(io))


def arm(mode, out, scratch, image, record):
    name = 'ax-c1-rustfs-q-' + uuid.uuid4().hex
    record['container'] = name
    record['flags'] = dict(zip(FLAGS, ('true', 'true', *(['true' if mode == 'enabled' else 'false'] * 2))))
    directory = scratch / mode
    directory.mkdir()
    access, secret = secrets.token_hex(12), secrets.token_hex(24)
    env = directory / 'server.env'
    data = directory / 'data'
    data.mkdir()
    for i in range(4):
        (data / f'rustfs{i}').mkdir()
    env.touch(mode=0o600)
    config = dict(record['flags'], RUSTFS_ACCESS_KEY=access, RUSTFS_SECRET_KEY=secret,
                  RUSTFS_VOLUMES='/data/rustfs{0...3}', RUSTFS_ADDRESS=ADDRESS,
                  RUSTFS_CONSOLE_ENABLE='false', RUSTFS_OBS_LOG_DIRECTORY='/data/logs',
                  RUSTFS_UNSAFE_BYPASS_DISK_CHECK='true', OTEL_SDK_DISABLED='true')
    env.write_text(''.join(f'{k}={v}\n' for k, v in config.items()), encoding='ascii')
    bucket = 'ax-c1-q-' + uuid.uuid4().hex
    try:
        record['phase'] = 'start'
        command(*DOCKER, 'run', '-d', '--pull=never', '--name', name, '--network=none', '--cpus=2',
                '--memory=2g', '--memory-swap=2g', '--pids-limit=128',
                '--user', f'{os.getuid()}:{os.getgid()}', '--env-file', str(env),
                '--volume', f'{data}:/data', image, timeout=18)
        record['phase'] = 'identity'
        row = json.loads(command(*DOCKER, 'inspect', name).stdout)[0]
        actual = dict(kv.split('=', 1) for kv in row['Config']['Env'])
        require(all(actual.get(k) == v for k, v in record['flags'].items()), 'runtime flags')
        require(row['Image'] == image and row['State']['Running'] and row['HostConfig']['NetworkMode'] == 'none',
                'runtime isolation or image')
        elf = command(*DOCKER, 'exec', name, 'sha256sum', '/usr/bin/rustfs').stdout.decode().split()[0]
        require(elf == BINARY, 'runtime ELF does not match rbm provenance')
        record.update(runtime_image_sha256=row['Image'], runtime_binary_sha256=elf, network_mode='none',
                      bound_to=ADDRESS, memory_limit_bytes=row['HostConfig']['Memory'], cpu_limit=2)
        record['phase'] = 's3'
        io = command('sudo', '-n', 'nsenter', '-t', str(row['State']['Pid']), '-n', '--', 'runuser',
                     '-u', os.environ['USER'], '--', sys.executable, str(Path(__file__).resolve()), 'requests',
                     data=json.dumps([access, secret, bucket]).encode(), timeout=65)
        save(out / 's3.json', json.loads(io.stdout))
        record['phase'] = 'persisted'
        record['shards'] = []
        for i in range(4):
            path = data / f'rustfs{i}' / bucket / 'same-input' / 'xl.meta'
            raw = path.read_bytes()
            require(8 < len(raw) <= 16 * 1024, 'unexpected XL2 size')
            filename = f'shard-{i}.xl.meta'
            (out / filename).write_bytes(raw)
            sizes = [p.stat().st_size for p in path.parent.glob('*/part.*') if p.is_file()]
            record['shards'].append(dict(path=filename, xl_meta_sha256=sha(raw), external_part_sizes=sizes))
        record['phase'] = 'collected'
    finally:
        stopped = command(*DOCKER, 'stop', '--time=5', name, timeout=12, check=False)
        rm = command(*DOCKER, 'rm', '-f', name, timeout=12, check=False)
        absent = command(*DOCKER, 'ps', '-aq', '--filter', f'name=^{name}$', timeout=12)
        record['cleanup'] = dict(graceful_stop_exit=stopped.returncode, removal_exit=rm.returncode,
                                 owned_container_absent=not absent.stdout.strip())
        require(record['cleanup']['owned_container_absent'], 'owned container survived cleanup')


def run(out, tip):
    import fcntl

    def stop(_signum, _frame):
        raise TimeoutError('local probe stopped')

    signal.signal(signal.SIGTERM, stop)
    require(out.is_absolute() and not out.exists() and out.parent.is_dir(), 'new absolute receipt directory')
    require(os.getuid() != 0 and tip == COMMIT, 'run as local user with fresh observed tip')
    lockpath = '/tmp/ax-c1-rustfs-quantum-rbm.lock'
    fd = os.open(lockpath, os.O_CREAT | os.O_NOFOLLOW | os.O_RDWR, 0o600)
    with os.fdopen(fd, 'rb') as lock:
        require(os.fstat(lock.fileno()).st_uid == os.getuid(), 'unexpected lock owner')
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        for process in ('cargo', 'rustc', 'rustdoc'):
            require(command('pgrep', '-x', process, check=False).returncode == 1, 'compiler or process check')
        out.mkdir(mode=0o700)
        receipt = dict(schema='ax-c1-rustfs-quantum-local-capture-v1', completed=False,
                       scope='single-host S3 mechanism qualification; not C1 FUSE performance',
                       started_utc=datetime.now(timezone.utc).isoformat(), image_sha256=IMAGE,
                       branch=BRANCH, branch_tip=tip, arms={})
        started = time.monotonic()
        scratch = None
        try:
            source = command('git', '--no-optional-locks', '-c', 'safe.directory=/srv/build/rustfs', '-C', '/srv/build/rustfs',
                             'rev-parse', 'HEAD').stdout.decode().strip()
            status = command('git', '--no-optional-locks', '-c', 'safe.directory=/srv/build/rustfs', '-C', '/srv/build/rustfs',
                             'status', '--porcelain').stdout.strip()
            image = json.loads(command(*DOCKER, 'image', 'inspect', IMAGE).stdout)[0]
            require(image['Id'] == IMAGE and image['Config']['Labels']['org.opencontainers.image.revision']
                    == source == tip and not status, 'cached image or source provenance')
            receipt.update(source_head=source, source_clean=True, image_revision=source)
            scratch = Path(tempfile.mkdtemp(prefix='ax-c1-rustfs-q-'))
            for mode in MODES:
                require(time.monotonic() - started < 145, 'bounded start window exhausted')
                (out / mode).mkdir()
                receipt['arms'][mode] = {}
                arm(mode, out / mode, scratch, IMAGE, receipt['arms'][mode])
            receipt['completed'] = True
        except Exception as error:
            receipt['error_type'] = type(error).__name__
            if isinstance(error, ValueError):
                receipt['error_check'] = str(error)
            if isinstance(error, subprocess.CalledProcessError):
                receipt['failed_program'] = Path(error.cmd[0]).name
                receipt['failed_exit'] = error.returncode
            raise
        finally:
            save(out / 'capture.json', receipt)
            if scratch is not None and all(r.get('cleanup', {}).get('owned_container_absent')
                                           for r in receipt['arms'].values()):
                shutil.rmtree(scratch)
            receipt['owned_scratch_removed'] = scratch is None or not scratch.exists()
            receipt['elapsed_seconds'] = round(time.monotonic() - started, 3)
            receipt['finished_utc'] = datetime.now(timezone.utc).isoformat()
            save(out / 'capture.json', receipt)
        require(receipt['owned_scratch_removed'], 'owned scratch survived cleanup')


def audit(capture, decoder, out):
    spec = importlib.util.spec_from_file_location('xl2_decoder', decoder)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    meta = json.loads((capture / 'capture.json').read_bytes())
    require(meta['completed'] and meta['owned_scratch_removed'], 'capture did not close')
    require(meta['image_sha256'] == IMAGE and meta['image_revision'] == meta['branch_tip'] == COMMIT
            and set(meta['arms']) == set(MODES), 'capture image or arm identity')
    payload, rows = payload_bytes(), {}
    for mode, q in MODES.items():
        arm = meta['arms'][mode]
        require(arm['runtime_image_sha256'] == IMAGE and arm['runtime_binary_sha256'] == BINARY
                and arm['network_mode'] == 'none' and arm['cleanup']['owned_container_absent'], 'arm identity')
        expected_flags = dict(zip(FLAGS, ('true', 'true', *(['true' if q else 'false'] * 2))))
        require(arm['flags'] == expected_flags, 'incorrect arm switches')
        io = json.loads((capture / mode / 's3.json').read_bytes())
        require(io['put_http_status'] == 200 and io['head_bytes'] == SIZE and io['payload_sha256'] == sha(payload)
                and io['requested_block_bytes'] == B and
                io['put_layout_headers'].get('x-rustfs-layout-hint-status') == 'applied', 'PUT/HEAD')
        headers = dict(io['put_layout_headers'], **io['head_layout_headers'])
        require(headers.get('x-rustfs-effective-ec-block-size') == str(B), 'reported B')
        cohort = (None, *STARTS)
        require(len(io['gets']) == len(cohort), 'GET count')
        for get, start in zip(io['gets'], cohort):
            wanted = payload if start is None else payload[start:start+WIDTH]
            ranged = start is not None
            require(get == dict(range=f'bytes={start}-{start+WIDTH-1}' if ranged else None,
                                http_status=206 if ranged else 200,
                                content_range=f'bytes {start}-{start+WIDTH-1}/{SIZE}' if ranged else None,
                                content_length=len(wanted), body_bytes=len(wanted),
                                body_sha256=sha(wanted), equals_input_slice=True), 'GET response differs')
        require(io['sent_get_requests'] == [['GET', g['range']] for g in io['gets']], 'actual GET sends')
        shards = []
        require(len(arm['shards']) == 4, 'four XL2 files')
        for i, row in enumerate(arm['shards']):
            require(row['path'] == f'shard-{i}.xl.meta', 'invalid captured shard path')
            decoded = mod.decode(capture / mode / row['path'])
            require(decoded['metadata_crc_verified'] and decoded['xl_meta_sha256'] == row['xl_meta_sha256']
                    and not decoded['inline_shard_lengths'] and len(decoded['versions']) == 1
                    and len(row['external_part_sizes']) == 1 and row['external_part_sizes'][0] > 0,
                    'XL2 checksum or external shard')
            obj = decoded['versions'][0]['V2Obj']
            marker = None if q is None else str(q).encode('ascii')
            require(all(obj['MetaSys'].get(k) == marker for k in (
                'x-rustfs-internal-ec-read-quantum', 'x-minio-internal-ec-read-quantum')), 'persisted q')
            require((obj['EcBSize'], obj['EcM'], obj['EcN']) == (B, 2, 2), 'persisted B or shard geometry')
            shards.append(dict(erasure_index=obj['EcIndex'], B_bytes=B, q_bytes=q,
                               physical_part_file_bytes=row['external_part_sizes'][0]))
        require({s['erasure_index'] for s in shards} == {1, 2, 3, 4}, 'duplicate erasure indices')
        rows[mode] = dict(payload_sha256=io['payload_sha256'], full_gets=1, range_gets=len(STARTS),
                          client_response_body_bytes=sum(g['body_bytes'] for g in io['gets']), shards=shards)
    save(out, dict(schema='ax-c1-rustfs-quantum-local-verification-v1', passed=True, formal_sample=False,
                   performance_accepted=False, scope=meta['scope'], image_sha256=IMAGE,
                   image_revision=COMMIT, decoder_sha256=sha(decoder.read_bytes()),
                   capture_sha256=sha((capture / 'capture.json').read_bytes()), arms=rows))


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    subs = p.add_subparsers(dest='mode', required=True)
    launch = subs.add_parser('run')
    launch.add_argument('--out', required=True, type=Path)
    launch.add_argument('--observed-branch-tip', required=True)
    subs.add_parser('requests')
    verify = subs.add_parser('audit')
    verify.add_argument('--capture', required=True, type=Path)
    verify.add_argument('--decoder', required=True, type=Path)
    verify.add_argument('--out', required=True, type=Path)
    a = p.parse_args()
    if a.mode == 'requests':
        requests()
    elif a.mode == 'run':
        run(a.out, a.observed_branch_tip)
    else:
        require(not a.out.exists(), 'new verification receipt required')
        audit(a.capture, a.decoder, a.out)
