"""Extract counters from completed GraftFS replay mounts on one host."""
import argparse
import json
import re
from pathlib import Path


def measurements(path):
    fields = {
        'flush_s': 'graftfs_fuse_op_latency_seconds_sum{op="flush"}',
        'flush_n': 'graftfs_fuse_op_total{op="flush"}',
        'getxattr_s': 'graftfs_fuse_op_latency_seconds_sum{op="getxattr"}',
        'lookup_s': 'graftfs_fuse_op_latency_seconds_sum{op="lookup"}',
        'put_bytes': 'graftfs_s3_put_bytes_total',
        'put_n': 'graftfs_s3_put_ops_total',
        'put_us': 'graftfs_s3_put_lat_us_total',
    }
    selected = {name: key for key, name in fields.items()}
    values = {}
    with path.open() as stream:
        for line in stream:
            if not line.startswith('graftfs_'):
                continue
            sample = line.split()
            key = selected.get(sample[0])
            if key:
                if key in values:
                    raise ValueError(f'{path}: repeated {key}')
                values[key] = float(sample[1]) if key.endswith('_s') else int(sample[1])
    if values.keys() != fields.keys():
        raise ValueError(f'{path}: missing {sorted(fields.keys() - values.keys())}')
    return values


def read_attempt(audits, attempt):
    if not re.fullmatch(r'04-agent-replay-\d+-r\d+-attempt-\d+', attempt):
        raise ValueError(f'Expected a replay attempt name: {attempt}')
    rows = {}
    for path in audits.glob(f'{attempt}-*-m/*-stop.final.prom.txt'):
        match = re.fullmatch(re.escape(attempt) + r'-.*-(a\d+)-stop\.final\.prom\.txt', path.name)
        if match:
            agent = match[1]
            if agent in rows:
                raise ValueError(f'{path}: duplicate final mount for {agent}')
            rows[agent] = measurements(path)
    if not rows:
        raise ValueError(f'No agent mount records for {attempt} in {audits}')
    return rows


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('audits', type=Path)
    parser.add_argument('on')
    parser.add_argument('off')
    args = parser.parse_args()
    on, off = (read_attempt(args.audits, a) for a in (args.on, args.off))
    if on.keys() != off.keys():
        raise ValueError('Mount records cover different agents')
    print(json.dumps({agent: {'on': on[agent], 'off': off[agent]} for agent in sorted(on)}))
