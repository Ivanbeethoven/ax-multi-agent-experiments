#!/usr/bin/env python3
"""D single steps: freeze requests, upload to an existing owned bucket, or read.

Bounded GET workers; concurrency is a GLOBAL budget split across requesters. No bucket
creation/deletion, service changes, warm/cold transition, or automatic retry.
"""
import argparse
from contextlib import closing
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
import hashlib
import json
from pathlib import Path
import random
import re
import time
import threading
from urllib.parse import urlsplit
from experiment_io import load, digest, write_new


def payload(seed, index, size):
    return hashlib.shake_256(f'ax-D-v1:{seed}:{index}'.encode()).digest(size)


def plan(objects, requests, size, seed, concurrency=1, requesters=1):
    if objects < 1 or requests < objects or size < 4096:
        raise ValueError('positive objects, requests >= objects, object bytes >= 4096 required')
    if not 1 <= requesters <= concurrency or concurrency > 1024:
        raise ValueError('1 <= active requesters <= global concurrency <= 1024 required')
    rng = random.Random(seed)
    return {'schema': 's3-request-plan-v1', 'seed': seed, 'object_bytes': size,
            'objects': [{'key': f'o{i:08}', 'sha256': hashlib.sha256(payload(seed,i,size)).hexdigest()}
                        for i in range(objects)],
            'requests': [{'object': i % objects, 'offset': rng.randrange(size-4096+1)} for i in range(requests)],
            'concurrency': concurrency, 'requesters': requesters, 'range_bytes': 4096}


def shard_budget(p, shard):
    n = p.get('requesters', 1); total = p['concurrency']
    if not 1 <= n <= total or not 0 <= shard < n: raise ValueError('invalid requester shard')
    return total // n + (shard < total % n)


def response_headers(response):
    return {k: v for k,v in response.get('ResponseMetadata', {}).get('HTTPHeaders', {}).items() if k.startswith('x-rustfs-')}


class RequestFailure(ValueError):
    def __init__(self,message,details):
        super().__init__(message);self.details=details


def read_one(client, bucket, key, expected, offset=None):
    kwargs = {'Bucket': bucket, 'Key': key}
    if offset is not None: kwargs['Range'] = f'bytes={offset}-{offset+len(expected)-1}'
    started = time.monotonic_ns()
    reply = client.get_object(**kwargs)
    with closing(reply['Body']) as body: actual = body.read()
    elapsed = (time.monotonic_ns()-started)/1e9
    status = reply['ResponseMetadata']['HTTPStatusCode']
    if status != (206 if offset is not None else 200) or len(actual) != len(expected) or actual != expected:
        raise RequestFailure('status/length/content mismatch',{'status':status,'application_bytes':len(actual),
            'expected_bytes':len(expected),'seconds':elapsed,'content_equal':actual==expected,
            'rustfs_headers':response_headers(reply)})
    return {'seconds': elapsed, 'application_bytes': len(actual), 'status': status,
            'rustfs_headers': response_headers(reply), 'content_equal': True}


def run(action, client, p, bucket, prefix, output, mode=None, hint=None, shard=0):
    if p.get('schema') != 's3-request-plan-v1' or not p.get('objects') or not p.get('requests'):
        raise ValueError('valid fixed request plan required')
    budget = shard_budget(p, shard)
    if action not in ('upload','read') or (action == 'read' and mode not in ('full','range')):
        raise ValueError('explicit upload/read mode required')
    if not re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9/_-]{2,127}', prefix):
        raise ValueError('dedicated simple object prefix required')
    if any(not re.fullmatch(r'o[0-9]{8}', row['key']) for row in p['objects']):
        raise ValueError('unexpected object keys')
    output = Path(output); output.mkdir(parents=True, exist_ok=False)
    write_new(output/'input-plan.json', p)
    write_new(output/'operation.json', {'action': action, 'bucket': bucket, 'prefix': prefix,
                                       'mode': mode, 'hint': hint, 'automatic_retry': False,
                                       'retained_plan_sha256': digest(output/'input-plan.json')})
    count = 0; attempts = 0; transferred = 0; service_seconds = 0; started = time.monotonic()
    lock = threading.Lock(); inflight = 0; max_inflight = 0
    def perform(index, request):
        nonlocal inflight, max_inflight
        i = request['object']; obj = p['objects'][i]
        data = payload(p['seed'], i, p['object_bytes'])
        if hashlib.sha256(data).hexdigest() != obj['sha256']: raise ValueError('payload/manifest mismatch')
        key = prefix.rstrip('/')+'/'+obj['key']
        with lock:
            inflight += 1; max_inflight = max(max_inflight,inflight)
        try:
            if action == 'upload':
                meta = {} if hint is None else {'rustfs-ec-block-size-hint': str(hint)}
                t = time.monotonic_ns()
                reply = client.put_object(Bucket=bucket, Key=key, Body=data, Metadata=meta, IfNoneMatch='*')
                if reply['ResponseMetadata']['HTTPStatusCode'] not in (200,201): raise ValueError('PUT status')
                row = {'seconds':(time.monotonic_ns()-t)/1e9,'application_bytes':len(data),'rustfs_headers':response_headers(reply)}
            else:
                offset = request['offset'] if mode == 'range' else None
                expected = data[offset:offset+4096] if offset is not None else data
                row = read_one(client,bucket,key,expected,offset)
            return {'index':index, 'key':key, 'request':request, **row}
        finally:
            with lock: inflight -= 1
    try:
        with (output/'requests.jsonl').open('x', encoding='utf-8') as stream:
            sequence = [{'object': i} for i in range(len(p['objects']))] if action == 'upload' else p['requests']
            selected = iter((i,r) for i,r in enumerate(sequence) if i % p.get('requesters',1) == shard)
            error = None
            with ThreadPoolExecutor(max_workers=budget) as pool:
                pending = {}
                def fill():
                    while len(pending) < budget:
                        try: i,r = next(selected)
                        except StopIteration: break
                        pending[pool.submit(perform,i,r)] = (i,r)
                fill()
                while pending:
                    done, _ = wait(pending,return_when=FIRST_COMPLETED)
                    for future in done:
                        index, request = pending.pop(future)
                        attempts += 1
                        try:
                            row = future.result()
                            stream.write(json.dumps(row)+'\n'); stream.flush()
                            count += 1; transferred += row['application_bytes']; service_seconds += row['seconds']
                        except Exception as failure:
                            error = failure
                            stream.write(json.dumps({'passed':False,'index':index,'request':request,
                                'key':prefix.rstrip('/')+'/'+p['objects'][request['object']]['key'],
                                'error_type':type(failure).__name__,**getattr(failure,'details',{})})+'\n');stream.flush()
                    if error is None: fill()
            if error is not None: raise error
        wall = time.monotonic()-started
        write_new(output/'result.json', {'passed': True, 'requests': count, 'application_bytes': transferred,
            'sum_request_seconds': service_seconds, 'operation_wall_seconds': wall,
            'global_concurrency':p['concurrency'], 'requesters':p.get('requesters',1), 'shard':shard,
            'local_concurrency_limit':budget, 'observed_local_max_inflight':max_inflight,
            'application_mib_per_s':transferred/2**20/wall,
            'sequential_service_mib_per_s': transferred/2**20/service_seconds if budget == 1 and service_seconds else None,
            'scope': 'wall includes generation/validation; per-request time includes SDK/signing/body; inspect requester CPU saturation; no backend/device byte claim'})
    except Exception as error:
        expected=sum(i % p.get('requesters',1)==shard for i in range(len(p['objects']) if action=='upload' else len(p['requests'])))
        write_new(output/'failed.json', {'passed': False, 'completed_requests': count,'attempted_requests':attempts,
            'not_started_requests':expected-attempts,'operation_wall_seconds':time.monotonic()-started,
            'observed_local_max_inflight':max_inflight,'error_type': type(error).__name__})
        raise


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__); sub = p.add_subparsers(dest='action', required=True)
    q = sub.add_parser('plan'); q.add_argument('--objects', type=int, required=True); q.add_argument('--requests', type=int, required=True)
    q.add_argument('--object-bytes', type=int, default=8388731); q.add_argument('--seed', type=int, default=20261029)
    q.add_argument('--output', required=True)
    q.add_argument('--concurrency', type=int, default=1); q.add_argument('--requesters', type=int, default=1)
    for action in ('upload','read'):
        q = sub.add_parser(action)
        for key in ('plan','plan-sha256','endpoint','bucket','prefix','output'): q.add_argument('--'+key, required=True)
        q.add_argument('--shard', type=int, default=0)
        if action == 'upload': q.add_argument('--hint', type=int, choices=[65536,262144,1048576,4194304])
        else: q.add_argument('--mode', choices=['full','range'], required=True)
    a = p.parse_args()
    if a.action == 'plan': write_new(a.output, plan(a.objects,a.requests,a.object_bytes,a.seed,a.concurrency,a.requesters))
    else:
        if digest(a.plan) != a.plan_sha256: raise ValueError('request plan changed')
        url = urlsplit(a.endpoint)
        if url.scheme not in ('http','https') or not url.hostname or url.username or url.password or url.query or url.fragment:
            raise ValueError('plain endpoint without credentials/query required')
        import boto3
        from botocore.config import Config
        client = boto3.client('s3', endpoint_url=a.endpoint, region_name='us-east-1',
            config=Config(signature_version='s3v4', retries={'total_max_attempts':1}, connect_timeout=10, read_timeout=120,
                          max_pool_connections=shard_budget(load(a.plan),a.shard),
                          s3={'addressing_style':'path'}, request_checksum_calculation='when_required', response_checksum_validation='when_required'))
        try: run(a.action,client,load(a.plan),a.bucket,a.prefix,a.output,getattr(a,'mode',None),getattr(a,'hint',None),a.shard)
        finally: client.close()
