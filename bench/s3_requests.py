#!/usr/bin/env python3
"""D single steps: freeze requests, upload to an existing owned bucket, or read.

Sequential GET latency tool, not a saturated-throughput generator. No bucket
creation/deletion, service changes, warm/cold transition, or automatic retry.
"""
import argparse
from contextlib import closing
import hashlib
import json
from pathlib import Path
import random
import re
import time
from urllib.parse import urlsplit
from experiment_io import load, digest, write_new


def payload(seed, index, size):
    return hashlib.shake_256(f'ax-D-v1:{seed}:{index}'.encode()).digest(size)


def plan(objects, requests, size, seed):
    if objects < 1 or requests < objects or size < 4096:
        raise ValueError('positive objects, requests >= objects, object bytes >= 4096 required')
    rng = random.Random(seed)
    return {'schema': 's3-request-plan-v1', 'seed': seed, 'object_bytes': size,
            'objects': [{'key': f'o{i:08}', 'sha256': hashlib.sha256(payload(seed,i,size)).hexdigest()}
                        for i in range(objects)],
            'requests': [{'object': i % objects, 'offset': rng.randrange(size-4096+1)} for i in range(requests)],
            'concurrency': 1, 'range_bytes': 4096}


def response_headers(response):
    return {k: v for k,v in response.get('ResponseMetadata', {}).get('HTTPHeaders', {}).items() if k.startswith('x-rustfs-')}


def read_one(client, bucket, key, expected, offset=None):
    kwargs = {'Bucket': bucket, 'Key': key}
    if offset is not None: kwargs['Range'] = f'bytes={offset}-{offset+len(expected)-1}'
    started = time.monotonic_ns()
    reply = client.get_object(**kwargs)
    with closing(reply['Body']) as body: actual = body.read()
    elapsed = (time.monotonic_ns()-started)/1e9
    status = reply['ResponseMetadata']['HTTPStatusCode']
    if status != (206 if offset is not None else 200) or len(actual) != len(expected):
        raise ValueError('wrong HTTP status or short/extra response bytes')
    if actual != expected: raise ValueError('response content mismatch')
    return {'seconds': elapsed, 'application_bytes': len(actual), 'status': status,
            'rustfs_headers': response_headers(reply), 'content_equal': True}


def run(action, client, p, bucket, prefix, output, mode=None, hint=None):
    if p.get('schema') != 's3-request-plan-v1' or p.get('concurrency') != 1 or not p.get('objects') or not p.get('requests'):
        raise ValueError('valid fixed request plan required')
    if not re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9/_-]{2,127}', prefix):
        raise ValueError('dedicated simple object prefix required')
    if any(not re.fullmatch(r'o[0-9]{8}', row['key']) for row in p['objects']):
        raise ValueError('unexpected object keys')
    output = Path(output); output.mkdir(parents=True, exist_ok=False)
    write_new(output/'input-plan.json', p)
    write_new(output/'operation.json', {'action': action, 'bucket': bucket, 'prefix': prefix,
                                       'mode': mode, 'hint': hint, 'automatic_retry': False,
                                       'retained_plan_sha256': digest(output/'input-plan.json')})
    count = 0; transferred = 0; service_seconds = 0; started = time.monotonic()
    try:
        with (output/'requests.jsonl').open('x', encoding='utf-8') as stream:
            sequence = [{'object': i} for i in range(len(p['objects']))] if action == 'upload' else p['requests']
            for request in sequence:
                i = request['object']; obj = p['objects'][i]
                data = payload(p['seed'], i, p['object_bytes'])
                if hashlib.sha256(data).hexdigest() != obj['sha256']:
                    raise ValueError('payload/manifest mismatch')
                key = prefix.rstrip('/')+'/'+obj['key']
                if action == 'upload':
                    # Atomic create-only. A server without support blocks this
                    # operation; never silently fall back to overwriting PUT.
                    meta = {} if hint is None else {'rustfs-ec-block-size-hint': str(hint)}
                    t = time.monotonic_ns()
                    reply = client.put_object(Bucket=bucket, Key=key, Body=data, Metadata=meta, IfNoneMatch='*')
                    row = {'seconds': (time.monotonic_ns()-t)/1e9, 'application_bytes': len(data), 'rustfs_headers': response_headers(reply)}
                else:
                    offset = request['offset'] if mode == 'range' else None
                    expected = data[offset:offset+4096] if offset is not None else data
                    row = read_one(client,bucket,key,expected,offset)
                row.update(index=count, key=key, request=request)
                stream.write(json.dumps(row)+'\n'); stream.flush()
                count += 1; transferred += row['application_bytes']; service_seconds += row['seconds']
        write_new(output/'result.json', {'passed': True, 'requests': count, 'application_bytes': transferred,
            'sum_request_seconds': service_seconds, 'operation_wall_seconds': time.monotonic()-started,
            'sequential_service_mib_per_s': transferred / 2**20 / service_seconds if service_seconds else None,
            'scope': 'request time includes SDK/signing and full body; payload generation/check outside each request timer; no backend/device byte claim'})
    except Exception as error:
        write_new(output/'failed.json', {'passed': False, 'completed_requests': count, 'error_type': type(error).__name__})
        raise


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__); sub = p.add_subparsers(dest='action', required=True)
    q = sub.add_parser('plan'); q.add_argument('--objects', type=int, required=True); q.add_argument('--requests', type=int, required=True)
    q.add_argument('--object-bytes', type=int, default=8388731); q.add_argument('--seed', type=int, default=20261029)
    q.add_argument('--output', required=True)
    for action in ('upload','read'):
        q = sub.add_parser(action)
        for key in ('plan','plan-sha256','endpoint','bucket','prefix','output'): q.add_argument('--'+key, required=True)
        if action == 'upload': q.add_argument('--hint', type=int, choices=[65536,262144,1048576,4194304])
        else: q.add_argument('--mode', choices=['full','range'], required=True)
    a = p.parse_args()
    if a.action == 'plan': write_new(a.output, plan(a.objects,a.requests,a.object_bytes,a.seed))
    else:
        if digest(a.plan) != a.plan_sha256: raise ValueError('request plan changed')
        url = urlsplit(a.endpoint)
        if url.scheme not in ('http','https') or not url.hostname or url.username or url.password or url.query or url.fragment:
            raise ValueError('plain endpoint without credentials/query required')
        import boto3
        from botocore.config import Config
        client = boto3.client('s3', endpoint_url=a.endpoint, region_name='us-east-1',
            config=Config(signature_version='s3v4', retries={'total_max_attempts':1}, connect_timeout=10, read_timeout=120,
                          s3={'addressing_style':'path'}, request_checksum_calculation='when_required', response_checksum_validation='when_required'))
        try: run(a.action,client,load(a.plan),a.bucket,a.prefix,a.output,getattr(a,'mode',None),getattr(a,'hint',None))
        finally: client.close()
