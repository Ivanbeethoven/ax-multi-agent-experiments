"""Qualify the private, shared S3 and durable Redis endpoints before measurement."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time

from boto3 import client
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError


def main(run, root):
    out = Path(root)
    creds = json.load(sys.stdin)
    endpoints = [f"10.77.13.{i}:19000" for i in range(21, 25)]
    config = Config(signature_version="s3v4", connect_timeout=2, read_timeout=5,
                    retries={"total_max_attempts": 1}, s3={"addressing_style": "path"}, proxies={})
    clients = [client("s3", endpoint_url=f"http://{ep}", aws_access_key_id=creds["key"],
                      aws_secret_access_key=creds["secret"], region_name="us-east-1", config=config) for ep in endpoints]
    redis = ['redis-cli', '-h', '10.77.13.24', '-p', '16379', '--raw']
    env = dict(os.environ, REDISCLI_AUTH=creds['rpc'])
    def redis_call(*args):
        return subprocess.check_output(redis + list(args), env=env, timeout=5, text=True).strip()
    result = dict(run=run, ready=[], reads=[], purpose='four-node backend qualification; not a performance result')
    name = 'qualification.bin'
    created = False
    try:
        limit = time.monotonic() + 120
        while len(result['ready']) < len(clients):
            for ep, s3 in zip(endpoints, clients):
                if ep in result['ready']:
                    continue
                try:
                    s3.list_buckets()
                except (BotoCoreError, ClientError):
                    continue
                result['ready'].append(ep)
            if len(result['ready']) == len(clients):
                break
            if time.monotonic() > limit:
                raise TimeoutError('S3 readiness')
            time.sleep(1)
        if redis_call('PING') != 'PONG' or redis_call('CONFIG', 'GET', 'appendfsync').splitlines() != ['appendfsync', 'always']:
            raise ValueError('Redis PING/durability mismatch')
        info = redis_call('INFO', 'ALL')
        if 'aof_enabled:1' not in info or 'aof_last_write_status:ok' not in info:
            raise ValueError('Redis AOF is not healthy')
        (out/'redis-info-all.txt').write_text(info + '\n')
        (out/'redis-latency-histogram.txt').write_text(redis_call('LATENCY', 'HISTOGRAM') + '\n')
        src = clients[0]
        src.create_bucket(Bucket=run)
        created = True
        data = hashlib.shake_256(b'idcai-performance-preflight').digest(4194304)
        sha = hashlib.sha256(data).hexdigest()
        src.put_object(Bucket=run, Key=name, Body=data)
        for ep, s3 in zip(endpoints, clients):
            with s3.get_object(Bucket=run, Key=name, Range='bytes=65536-131071')['Body'] as body:
                segment = body.read()
            with s3.get_object(Bucket=run, Key=name)['Body'] as body:
                content = body.read()
            if segment != data[65536:131072] or hashlib.sha256(content).hexdigest() != sha:
                raise ValueError('S3 returned incorrect data')
            result['reads'].append(dict(endpoint=ep, bytes=len(content), range_bytes=len(segment), sha256=sha))
        result.update(passed=True, redis_aof=True, redis_appendfsync='always')
    except Exception as error:
        result.update(passed=False, error=type(error).__name__)
    finally:
        if created:
            try:
                clients[0].delete_object(Bucket=run, Key=name)
                clients[0].delete_bucket(Bucket=run)
                result['probe_bucket_removed'] = True
            except (BotoCoreError, ClientError) as error:
                result.update(passed=False, cleanup_error=type(error).__name__)
        with (out/'backend-probe.json').open('x') as f:
            json.dump(result, f, indent=2)
    return 0 if result.get('passed') else 2


if __name__ == '__main__':
    sys.exit(main(*sys.argv[1:]))
