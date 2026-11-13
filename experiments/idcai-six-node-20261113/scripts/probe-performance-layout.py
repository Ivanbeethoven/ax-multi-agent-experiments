"""C1: qualify persisted B/q and exact range bytes on the real four-node pool."""
from contextlib import closing
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import time

from boto3 import client
from botocore.config import Config

ROOT = Path('/srv/ax-idcai-20261113')
REVISION = 'f4ed202508f2153afb7956bae6f7799b60eccd00'
IMAGE = 'sha256:f88833c866a4f5b533be2b202440cf9dcaa2b77b20479ec05ca1c417e6b8b265'
PROFILES = {'perf-enabled': (True, True), 'perf-disabled': (False, False), 'perf-block-only': (True, False)}
HINTS = (None, 65536, 262144, 1048576, 4194304)
PAYLOAD = hashlib.shake_256(b'ax-idcai-c1-layout-v1').digest(5 * 1024**2 + 123)


def require(condition, reason):
    if not condition:
        raise ValueError(reason)


def requests(run, out, profile):
    if len(run) != 20 or not run.startswith('ax-four-') or any(c not in '0123456789abcdef' for c in run[8:]):
        raise ValueError('Invalid run identifier')
    out = Path(out)
    out.mkdir(mode=0o700)
    access = json.load(sys.stdin)
    hint_enabled, quantum_enabled = PROFILES[profile]
    bucket = run + '-layout'
    result = dict(run=run, profile=profile, passed=False, formal_sample=False, rustfs_image=IMAGE,
                  source_commit=REVISION, bucket=bucket, input_sha256=hashlib.sha256(PAYLOAD).hexdigest(), objects=[])
    cfg = Config(signature_version='s3v4', proxies={}, s3={'addressing_style': 'path'},
                 connect_timeout=3, read_timeout=12, retries={'total_max_attempts': 1})
    try:
        with closing(client('s3', endpoint_url='http://10.77.13.21:19000', region_name='us-east-1',
                            aws_access_key_id=access['key'], aws_secret_access_key=access['secret'], config=cfg)) as s3:
            s3.create_bucket(Bucket=bucket)
            for hint in HINTS:
                key = 'object-' + str(hint or 'default')
                expected = hint if hint_enabled and hint else 1048576
                quantum = (expected // 4 if expected <= 1048576 else 1048576) if quantum_enabled else None
                row = dict(key=key, requested_block_bytes=hint, expected_block_bytes=expected,
                           expected_quantum_bytes=quantum, reads=[], shards=[])
                result['objects'].append(row)
                response = s3.put_object(Bucket=bucket, Key=key, Body=PAYLOAD,
                                         Metadata={'rustfs-ec-block-size-hint': str(hint)} if hint else {})
                row['response'] = {k:v for k,v in response['ResponseMetadata']['HTTPHeaders'].items()
                                   if k.startswith('x-rustfs-')}
                for start in (None, 0, 16368, 65520, 262130, 1048560):
                    params = {} if start is None else {'Range': f'bytes={start}-{start+4095}'}
                    begin = time.monotonic_ns()
                    response = s3.get_object(Bucket=bucket, Key=key, **params)
                    with closing(response['Body']) as body:
                        got = body.read()
                    wanted = PAYLOAD if start is None else PAYLOAD[start:start+4096]
                    code = response['ResponseMetadata']['HTTPStatusCode']
                    row['reads'].append(dict(range=params.get('Range'), bytes=len(got), http_status=code,
                                             seconds=(time.monotonic_ns()-begin)/1e9,
                                             sha256=hashlib.sha256(got).hexdigest(), equal=got == wanted))
                    require(got == wanted and code == (200 if start is None else 206), f'Wrong GET result: {key}')
        result['passed'] = True
    except Exception as error:
        message = str(error)
        for secret in access.values():
            message = message.replace(secret, '[redacted]')
        result['error'] = f'{type(error).__name__}: {message}'
    finally:
        (out/'requests.json').write_text(json.dumps(result, indent=2)+'\n')
    return 0 if result['passed'] else 2


def verify(out):
    out = Path(out)
    result = json.loads((out/'requests.json').read_text())
    require(result['passed'] and len(result['objects']) == len(HINTS), 'Request probe incomplete')
    result['passed'] = False
    try:
        decoder_path = ROOT/'src/ax/experiments/retest-2026-10-29/scripts/inspect-layout.py'
        result['decoder_sha256'] = hashlib.sha256(decoder_path.read_bytes()).hexdigest()
        spec = importlib.util.spec_from_file_location('ax_xl2', decoder_path)
        decoder = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(decoder)
        for row in result['objects']:
            key = row['key']
            for node in range(1, 5):
                xl = out/f'{key}-obj{node}.xl.meta'
                parsed = decoder.decode(xl)
                v, = [entry['V2Obj'] for entry in parsed['versions'] if 'V2Obj' in entry]
                current = v['MetaSys'].get('x-rustfs-internal-ec-read-quantum')
                require(current == v['MetaSys'].get('x-minio-internal-ec-read-quantum'), 'Quantum tags differ')
                current = int(current) if current is not None else None
                shard = dict(node=node, index=v['EcIndex'], block_bytes=v['EcBSize'], quantum_bytes=current,
                             data_shards=v['EcM'], parity_shards=v['EcN'], metadata_sha256=parsed['xl_meta_sha256'],
                             logical_bytes=v['Size'], file=xl.name)
                row['shards'].append(shard)
                require((v['EcBSize'], current, v['EcM'], v['EcN'], v['Size']) == (
                    row['expected_block_bytes'], row['expected_quantum_bytes'], 2, 2, len(PAYLOAD)),
                    f'Wrong persisted B/q or shard geometry: {key} node {node}')
            require({x['index'] for x in row['shards']} == {1, 2, 3, 4}, 'Duplicate shard position')
        result['passed'] = True
    except Exception as error:
        result['error'] = f'{type(error).__name__}: {error}'
    finally:
        (out/'result.json').write_text(json.dumps(result, indent=2)+'\n')
    return 0 if result['passed'] else 2


if __name__ == '__main__':
    raise SystemExit({'requests': requests, 'verify': verify}[sys.argv[1]](*sys.argv[2:]))
