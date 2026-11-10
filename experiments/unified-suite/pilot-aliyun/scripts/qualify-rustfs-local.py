#!/usr/bin/env python3
"""Check actual hint outcomes, four persisted B/q copies and exported counters.

Uses an already running, owned local service. Keeps the unique probe bucket and
raw evidence. Shared local disks qualify API behavior, never EC fault domains.
"""
import argparse
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import time
from urllib.request import ProxyHandler, build_opener
import uuid

REPO = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(REPO))
from bench.runtime.observe import prometheus


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--services', type=Path, required=True)
    p.add_argument('--build-root', type=Path, required=True)
    p.add_argument('--collector-name', required=True)
    p.add_argument('--out', type=Path, required=True)
    p.add_argument('--cloud', action='store_true', help='Use the actual private four-disk deployment receipt')
    a = p.parse_args()
    a.out.mkdir(parents=True, exist_ok=False)
    receipt = json.loads((a.services / ('result.json' if a.cloud else 'receipt.json')).read_text())
    entry = receipt if a.cloud else next(x for x in receipt['containers'] if x['name'].endswith('-rustfs'))
    observed = json.loads(subprocess.check_output(['docker', 'inspect', entry['container_id'] if a.cloud else entry['name']]))[0]
    if observed['Id'] != entry['container_id'] or not observed['State']['Running']:
        raise ValueError('Expected the live original local service')
    ip = '127.0.0.1' if a.cloud else observed['NetworkSettings']['Networks']['bridge']['IPAddress']
    collector = json.loads(subprocess.check_output(['docker', 'inspect', a.collector_name]))[0]
    if not collector['State']['Running'] or collector['HostConfig']['NetworkMode'] != ('host' if a.cloud else 'container:' + observed['Id']):
        raise ValueError('Expected collector in the original local RustFS network namespace')
    url = 'http://' + ip + ':18889/metrics'
    opener = build_opener(ProxyHandler({}))
    def snapshot(name):
        with opener.open(url, timeout=5) as response:
            raw = response.read(16 * 1024**2 + 1)
        if len(raw) > 16 * 1024**2:
            raise ValueError('Metrics response exceeds observer cap')
        (a.out / (name + '.prom')).write_bytes(raw)
        return prometheus(raw.decode())
    def headers(response):
        return {k: v for k, v in response['ResponseMetadata']['HTTPHeaders'].items() if k.startswith('x-rustfs-')}
    secret = dict(line.split('=', 1) for line in (a.services / ('private/rustfs.env' if a.cloud else 'rustfs.env')).read_text().splitlines())
    import boto3
    from botocore.config import Config
    checksum_options = {k:'when_required' for k in ('request_checksum_calculation','response_checksum_validation')
                        if k in Config.OPTION_DEFAULTS}
    client = boto3.client('s3', endpoint_url='http://10.20.2.163:9000' if a.cloud else 'http://127.0.0.1:19000', region_name='us-east-1',
        aws_access_key_id=secret['RUSTFS_ACCESS_KEY'], aws_secret_access_key=secret['RUSTFS_SECRET_KEY'],
        config=Config(signature_version='s3v4', retries={'max_attempts': 0}, s3={'addressing_style': 'path'},
            **checksum_options))
    decoder_path = REPO / 'experiments/retest-2026-10-29/scripts/inspect-layout.py'
    spec = importlib.util.spec_from_file_location('persisted_layout_decoder', decoder_path)
    decoder = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(decoder)
    bucket = 'ax-pilot-layout-' + uuid.uuid4().hex
    payload = hashlib.shake_256(b'ax-current-layout-local-v1').digest(5 * 1024**2 + 123)
    result = dict(passed=False, bucket=bucket, objects=[], source_image=receipt['image_reference'] if a.cloud else receipt['rustfs_image'],
        decoder_sha256=hashlib.sha256(decoder_path.read_bytes()).hexdigest(),
        scope='Cloud four-disk API/layout/counter qualification' if a.cloud else 'Local API/layout/counter qualification only; four paths on one disk with explicit test bypass',
        cloud_instances_started=a.cloud)
    layout_source = a.build_root / 'src/rustfs/crates/ecstore/src/object_api/ec_block_layout.rs'
    if a.cloud:
        result['source_commit']=observed['Config']['Labels']['org.opencontainers.image.revision']
        if result['source_commit'] != receipt['source_commit'] or receipt['unsafe_disk_bypass']:
            raise ValueError('Cloud source/bypass identity differs')
        disks=[Path('/srv/ax-rustfs-data-'+str(i)) for i in range(4)]
        if not all(p.is_mount() for p in disks) or len({p.stat().st_dev for p in disks})!=4:
            raise ValueError('Cloud probe requires four actual distinct disk mounts')
    else:
        result['layout_source_sha256'] = hashlib.sha256(layout_source.read_bytes()).hexdigest()
        result['source_commit'] = subprocess.check_output(['git', '-C', str(a.build_root / 'src/rustfs'),
            'rev-parse', 'HEAD'], text=True).strip()
    result['declared_enabled_2plus2_quantum'] = {'65536': 16384, '262144': 65536,
        '1048576': 262144, '4194304': 1048576}
    try:
        client.create_bucket(Bucket=bucket)
        client.put_object(Bucket=bucket, Key='warm', Body=payload)
        with closing(client.get_object(Bucket=bucket, Key='warm')['Body']) as stream:
            if stream.read() != payload:
                raise ValueError('Warmup read differs')
        time.sleep(3)
        before = snapshot('before')
        for hint in (None, 65536, 262144, 1048576, 4194304, 524288):
            key = 'object-' + str(hint)
            metadata = {} if hint is None else {'rustfs-ec-block-size-hint': str(hint)}
            put = client.put_object(Bucket=bucket, Key=key, Body=payload, Metadata=metadata)
            head = client.head_object(Bucket=bucket, Key=key)
            if head['ContentLength'] != len(payload):
                raise ValueError('Object size differs')
            with closing(client.get_object(Bucket=bucket, Key=key)['Body']) as stream:
                if stream.read() != payload:
                    raise ValueError('Full object read differs')
            for start in (0, 65520, 262130, 1048560):
                end = start + 4095
                with closing(client.get_object(Bucket=bucket, Key=key, Range=f'bytes={start}-{end}')['Body']) as stream:
                    if stream.read() != payload[start:end + 1]:
                        raise ValueError('Range read differs')
            put_headers, head_headers = headers(put), headers(head)
            expected = hint if hint in (65536, 262144, 1048576, 4194304) else 1048576
            actual = int(head_headers.get('x-rustfs-effective-ec-block-size', put_headers.get('x-rustfs-effective-ec-block-size', '0')))
            if actual != expected or hint is not None and put_headers.get('x-rustfs-layout-hint-status') != ('applied' if hint == expected else 'ignored'):
                raise ValueError('Hint outcome differs from declared enabled profile')
            shards = []
            for disk in range(4):
                path = (Path('/srv/ax-rustfs-data-'+str(disk)) if a.cloud else a.services / f'volume-{disk}') / bucket / key / 'xl.meta'
                decoded = decoder.decode(path)
                if not decoded['metadata_crc_verified'] or len(decoded['versions']) != 1:
                    raise ValueError('Persisted metadata is not a single authenticated version')
                obj = decoded['versions'][0]['V2Obj']
                b, m, n = obj['EcBSize'], obj['EcM'], obj['EcN']
                q = obj['MetaSys'].get('x-rustfs-internal-ec-read-quantum')
                if q != obj['MetaSys'].get('x-minio-internal-ec-read-quantum'):
                    raise ValueError('Quantum compatibility keys differ')
                q = int(q) if q is not None else None
                shard = b // m
                selected = shard // 2 if b <= 1048576 else 1048576
                expected_q = selected if selected < shard else None
                if (b, m, n, q) != (expected, 2, 2, expected_q):
                    raise ValueError('Persisted B/q or EC geometry differs')
                physical = sum(p.stat().st_size for p in path.parent.glob('*/part.*'))
                if physical <= 0 or decoded['inline_shard_lengths']:
                    raise ValueError('Layout control object must have external payload')
                shards.append(dict(slot=disk, erasure_index=obj['EcIndex'], block_bytes=b,
                    data_shards=m, parity_shards=n, read_quantum_bytes=q, physical_part_bytes=physical,
                    xl_meta_sha256=decoded['xl_meta_sha256']))
            if {s['erasure_index'] for s in shards} != {1, 2, 3, 4}:
                raise ValueError('Four distinct EC shard indices required')
            result['objects'].append(dict(key=key, requested_bytes=hint, effective_bytes=actual,
                put_headers=put_headers, head_headers=head_headers, payload_bytes=len(payload),
                payload_sha256=hashlib.sha256(payload).hexdigest(), full_and_ranges_equal=True, shards=shards))
            (a.out / 'progress.json').write_text(json.dumps(result, indent=2) + '\n')
        time.sleep(3)
        after = snapshot('after')
        name = 'rustfs_s3_http_requests_total'
        old = {k: v for k, v in before.items() if k.split('{', 1)[0] == name}
        new = {k: v for k, v in after.items() if k.split('{', 1)[0] == name}
        if not old or not new or any(new.get(k, -1) < value for k, value in old.items()):
            raise ValueError('Missing or reset native S3 HTTP counters')
        delta = sum(new.values()) - sum(old.values())
        if delta != 42:
            raise ValueError(f'Native S3 HTTP count differs: {delta}, expected 42')
        raw = (a.out / 'after.prom').read_text()
        ages = [time.time() - float(line.rsplit(' ', 1)[1]) / 1000 for line in raw.splitlines()
            if line.startswith(name + '{')]
        if not ages or min(ages) < -1 or max(ages) >= 5:
            raise ValueError('Native HTTP metric timestamps are stale or missing')
        read_self = "import urllib.request;print(urllib.request.build_opener(urllib.request.ProxyHandler({})).open('http://127.0.0.1:8888/metrics',timeout=5).read().decode(),end='')"
        self_raw = subprocess.check_output(['sudo', 'nsenter', '--target', str(observed['State']['Pid']),
            '--net', 'python3', '-c', read_self], text=True)
        (a.out / 'collector-self.prom').write_text(self_raw)
        self_values = prometheus(self_raw)
        receiver = {k: v for k, v in self_values.items() if k.split('{', 1)[0] in
            ('otelcol_receiver_accepted_metric_points', 'otelcol_receiver_failed_metric_points',
             'otelcol_receiver_refused_metric_points')}
        if {k.split('{', 1)[0] for k in receiver} != {'otelcol_receiver_accepted_metric_points',
                'otelcol_receiver_failed_metric_points', 'otelcol_receiver_refused_metric_points'} or \
                not any(v > 0 for k, v in receiver.items() if 'accepted_' in k) or \
                any(v != 0 for k, v in receiver.items() if 'accepted_' not in k):
            raise ValueError('Collector lost or refused native metric points')
        result.update(passed=True, native_http_request_delta=delta, expected_http_requests=42,
            metric_endpoint=url, required_metric=name, metric_series=len(new),
            source_point_ages_seconds=ages, collector_id=collector['Id'], collector_native_counters=receiver,
            persisted_layout_verified=True, readonly_decoder=True)
    except BaseException as error:
        result['error'] = type(error).__name__ + ': ' + str(error)
        raise
    finally:
        client.close()
        result['at'] = datetime.now(timezone.utc).isoformat()
        (a.out / 'result.json').write_text(json.dumps(result, indent=2) + '\n')
        print(json.dumps({k: v for k, v in result.items() if k != 'objects'}, indent=2))


if __name__ == '__main__':
    main()
