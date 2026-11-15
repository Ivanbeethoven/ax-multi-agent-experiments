#!/usr/bin/env python3
"""Real local S3 qualification, including negative controls and restart readback.

Uses a separately owned Docker container and data directory. Never reports this
as performance or independent-disk EC evidence. No credentials are printed.
"""
import argparse
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import secrets
import subprocess
import tarfile
import time
import uuid

FLAGS = ('RUSTFS_EC_BLOCK_SIZE_HINT_ENABLE', 'RUSTFS_EC_BLOCK_SIZE_HINT_FLEET_CONFIRMED',
         'RUSTFS_EC_READ_QUANTUM_ENABLE', 'RUSTFS_EC_READ_QUANTUM_FLEET_CONFIRMED')
SOURCE = 'a87e8f94a50b9fb6e3da74fed45fe37d4cb969ed'
SUPPORTED = (65536, 262144, 1048576, 4194304)
PROFILES = {'disabled': (False,False), 'block-only': (True,False), 'enabled': (True,True)}

def save(path, value):
    path.write_text(json.dumps(value, indent=2) + '\n')

def docker(*args):
    return subprocess.check_output(['docker', *args], text=True).strip()

def headers(response):
    return {k: v for k, v in response['ResponseMetadata']['HTTPHeaders'].items()
            if k.startswith('x-rustfs-')}

def check_read(client, bucket, key, payload):
    with closing(client.get_object(Bucket=bucket, Key=key)['Body']) as stream:
        assert stream.read() == payload, 'full content mismatch'
    for start in (0, 65520, 262130, 1048560):
        if start >= len(payload):
            continue
        end = min(start + 4095, len(payload) - 1)
        with closing(client.get_object(Bucket=bucket, Key=key, Range=f'bytes={start}-{end}')['Body']) as stream:
            assert stream.read() == payload[start:end + 1], 'range content mismatch'

def ready(client):
    last = None
    for _ in range(60):
        try:
            client.list_buckets()
            return
        except Exception as error:
            last = type(error).__name__
            time.sleep(1)
    raise RuntimeError('backend readiness failed: ' + str(last))

def run(out, image, restored, brewfs=None, inline_boundaries=False, profiles=None):
    import boto3
    from botocore.config import Config
    profiles=list(PROFILES) if profiles is None else profiles
    if not profiles or len(set(profiles))!=len(profiles) or any(p not in PROFILES for p in profiles):
        raise ValueError('invalid selected profiles')
    if brewfs is not None and os.geteuid()!=0:
        raise ValueError('BrewFS qualification needs uid 0: a newly formatted root inode is owned by root')
    identity = json.loads(docker('image', 'inspect', image))[0]
    assert identity['Config']['Labels']['org.opencontainers.image.revision'] == SOURCE
    out.mkdir(parents=True, exist_ok=False)
    remote=subprocess.check_output(['git','ls-remote','https://github.com/Ivanbeethoven/rustfs',
                                    'refs/heads/feat/dynamic-block-size-rebased'],text=True).split()
    pin={'repository':'https://github.com/Ivanbeethoven/rustfs','branch':'feat/dynamic-block-size-rebased',
         'frozen_commit':SOURCE,'built_from_commit':identity['Config']['Labels']['org.opencontainers.image.revision'],
         'image_reference':image,'image_digest':restored['original_registry_digest'],
         'runtime_image_id':identity['Id'],'resolved_at':datetime.now(timezone.utc).isoformat(),
         'resolved_by':'git ls-remote, OCI revision, retained original config SHA256 equivalence',
         'brewfs_client_sha':None}
    if not remote or remote[0]!=SOURCE:
        pin['blocked_reason']='branch changed; freeze a new batch'
        save(out/'backend-pin.json',pin)
        raise ValueError(pin['blocked_reason'])
    if brewfs is not None:
        build=brewfs.resolve(strict=True).parents[2]
        pin['brewfs_client_sha']=(build/'brewfs-source-sha.txt').read_text().strip()
        pin['brewfs_features']=(build/'features-fuse.txt').read_text().strip()
        with brewfs.open('rb') as stream:pin['brewfs_binary_sha256']=hashlib.file_digest(stream,'sha256').hexdigest()
        if 'rustfs-ec-block-size-hint' not in pin['brewfs_features']:
            raise ValueError('BrewFS build receipt lacks hint feature')
        receipt=(build/'brewfs-binaries.sha256').read_text()
        if not any(line.split()==[pin['brewfs_binary_sha256'],str(brewfs.resolve())] for line in receipt.splitlines()):
            raise ValueError('BrewFS binary does not match build receipt')
    save(out/'backend-pin.json',pin)
    # Docker 29 uses a manifest ID and may rewrite its descriptor on load.
    # Verify the actual saved config bytes, rather than confusing ID with config.
    saved = out / 'runtime-image.tar'
    docker('image', 'save', '-o', str(saved), image)
    with tarfile.open(saved) as tar:
        manifest = json.load(tar.extractfile('manifest.json'))
        config_bytes = tar.extractfile(manifest[0]['Config']).read()
        actual_config_id = 'sha256:' + hashlib.sha256(config_bytes).hexdigest()
    assert actual_config_id == restored['expected_config_id'], 'image config changed'
    saved.unlink()
    report = {'source': SOURCE, 'image_config_id': actual_config_id, 'runtime_image_id': identity['Id'],
              'registry_manifest_digest': restored['original_registry_digest'],
              'scope': 'local API correctness/activation; no performance or physical fault-domain claim',
              'selected_profiles':profiles,
              'runs': [], 'complete': False, 'persisted_layout_verified': False,
              'brewfs_upload_path_verified': False}
    save(out / 'result.json', report)
    for label in profiles:
        enabled,small_frames=PROFILES[label]
        directory = out / label
        directory.mkdir()
        data = directory / 'data'; data.mkdir(mode=0o777); data.chmod(0o777)
        for i in range(1, 5):
            (data / f'disk{i}').mkdir(mode=0o777)
            (data / f'disk{i}').chmod(0o777)
        access, secret = secrets.token_hex(12), secrets.token_hex(24)
        envfile = directory / 'server.env'
        settings = {k: str(enabled if k in FLAGS[:2] else small_frames).lower() for k in FLAGS}
        settings.update(RUSTFS_ACCESS_KEY=access, RUSTFS_SECRET_KEY=secret,
                        RUSTFS_VOLUMES='/data/disk{1...4}', RUSTFS_ADDRESS='127.0.0.1:19930',
                        # Local correctness only, explicitly not independent disks.
                        RUSTFS_UNSAFE_BYPASS_DISK_CHECK='true',
                        RUSTFS_CONSOLE_ENABLE='false', RUSTFS_OBS_LOG_DIRECTORY='/data/logs')
        with envfile.open('x') as stream:
            os.chmod(envfile, 0o600)
            stream.write(''.join(f'{k}={v}\n' for k, v in settings.items()))
        name = 'ax-layout-' + uuid.uuid4().hex[:12]
        record = {'mode': label, 'flags': {k: settings[k] for k in FLAGS},
                  'block_hint_enabled':enabled,'small_read_frames_enabled':small_frames,
                  'objects': [], 'restart_readback': False, 'container_removed': False}
        report['runs'].append(record)
        record['container_name'] = name
        save(out / 'result.json', report)
        client = boto3.client('s3', endpoint_url='http://127.0.0.1:19930',
                              aws_access_key_id=access, aws_secret_access_key=secret, region_name='us-east-1',
                              config=Config(signature_version='s3v4', connect_timeout=2, read_timeout=30,
                                            retries={'max_attempts': 0}, s3={'addressing_style': 'path'},
                                            request_checksum_calculation='when_required',
                                            response_checksum_validation='when_required'))
        started = False
        try:
            docker('run', '-d', '--name', name, '--network', 'host', '--cpus', '2', '--memory', '4g',
                   '--env-file', str(envfile), '-v', str(data) + ':/data', image)
            started = True
            observed = json.loads(docker('inspect', name))[0]
            actual = dict(v.split('=', 1) for v in observed['Config']['Env'])
            assert all(actual[k] == settings[k] for k in FLAGS), 'runtime flag mismatch'
            record['runtime_flags_verified'] = True
            ready(client)
            bucket = 'ax-layout-' + uuid.uuid4().hex[:12]
            client.create_bucket(Bucket=bucket)
            payload = hashlib.shake_256(b'ax-retest-layout-20261029').digest(5 * 1024 * 1024 + 123)
            for hint in (None, *SUPPORTED, 524288):
                key = 'object-' + str(hint)
                metadata = {} if hint is None else {'rustfs-ec-block-size-hint': str(hint)}
                put = client.put_object(Bucket=bucket, Key=key, Body=payload, Metadata=metadata)
                head = client.head_object(Bucket=bucket, Key=key)
                assert head['ContentLength'] == len(payload)
                check_read(client, bucket, key, payload)
                put_headers, head_headers = headers(put), headers(head)
                expected = hint if enabled and hint in SUPPORTED else 1048576
                effective = head_headers.get('x-rustfs-effective-ec-block-size',
                                             put_headers.get('x-rustfs-effective-ec-block-size'))
                row = {'key': key, 'bucket': bucket, 'requested_bytes': hint, 'expected_bytes': expected,
                       'put_headers': put_headers, 'head_headers': head_headers,
                       'payload_bytes': len(payload), 'payload_sha256': hashlib.sha256(payload).hexdigest(),
                       'content_and_ranges_verified': True, 'effective_bytes': int(effective) if effective else None}
                record['objects'].append(row)
                save(out / 'result.json', report)
                assert row['effective_bytes'] == expected, 'effective EC block size mismatch/missing'
                if hint is not None:
                    status = put_headers.get('x-rustfs-layout-hint-status')
                    assert status == ('applied' if enabled and hint in SUPPORTED else 'ignored'), 'hint status mismatch'
            docker('restart', name)
            ready(client)
            for row in record['objects']:
                check_read(client, bucket, row['key'], payload)
            record['restart_readback'] = True
            if inline_boundaries:
                import importlib.util
                module=importlib.util.spec_from_file_location('inline_boundary_check',Path(__file__).with_name('inline-boundary-check.py'))
                helper=importlib.util.module_from_spec(module);module.loader.exec_module(helper)
                record['inline_boundary_qualification']=helper.check(client,directory/'inline-boundaries',data,enabled,name,small_frames)
            if label=='enabled' and brewfs is not None:
                import importlib.util
                module=importlib.util.spec_from_file_location('client_hint_check',Path(__file__).with_name('client-hint-check.py'))
                helper=importlib.util.module_from_spec(module);module.loader.exec_module(helper)
                result=helper.check(client,directory/'brewfs-check',
                                    {**os.environ,'AWS_ACCESS_KEY_ID':access,'AWS_SECRET_ACCESS_KEY':secret},
                                    brewfs,name,data)
                report['brewfs_upload_path_verified']=result['passed']
        except Exception as error:
            record['failure'] = type(error).__name__ + ': ' + str(error)
            raise
        finally:
            client.close()
            if started:
                log = subprocess.run(['docker', 'logs', name], capture_output=True, text=True)
                # Remove even coincidental credential echoes from retained logs.
                (directory / 'server.log').write_text((log.stdout + log.stderr).replace(access, '[redacted]').replace(secret, '[redacted]'))
                docker('stop', name)
                docker('rm', name)
                record['container_removed'] = True
            envfile.unlink()
            save(out / 'result.json', report)
    report['complete'] = True
    save(out / 'result.json', report)
    print(json.dumps({'api_qualification': 'passed', 'objects': sum(len(r['objects']) for r in report['runs']),
                      'persisted_layout_verified': report['persisted_layout_verified'],
                      'brewfs_upload_path_verified': report['brewfs_upload_path_verified']}))

if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--image', default='ax-retest/rustfs:a87e8f94')
    p.add_argument('--restore-receipt', type=Path, required=True)
    p.add_argument('--brewfs',type=Path)
    p.add_argument('--inline-boundaries',action='store_true')
    p.add_argument('--profiles',nargs='+',choices=list(PROFILES))
    args = p.parse_args()
    run(args.output.resolve(), args.image, json.loads(args.restore_receipt.read_text()),args.brewfs,args.inline_boundaries,args.profiles)
