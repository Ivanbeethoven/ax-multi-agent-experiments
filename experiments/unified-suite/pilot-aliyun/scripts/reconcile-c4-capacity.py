#!/usr/bin/env python3
"""Keep the failed forecast and write a separate AOF-rewrite-aware forecast.

This is for the original closed pool-a flat C4 base only. It uses verified
retired allocation, keeps the original seven-copy and 20% reserve policy,
and also budgets an extra projected AOF for a subsequent rewrite.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

CELL = 'c5e97774bcffa69a-r1-native'


def require(ok, message):
    if not ok:
        raise ValueError(message)


def ref(path):
    path = Path(path).resolve(strict=True)
    return dict(path=str(path), sha256=hashlib.sha256(path.read_bytes()).hexdigest())


def load(path):
    return json.loads(Path(path).read_bytes())


def pin(value):
    require(ref(value['path']) == value, 'Changed evidence: ' + value['path'])


def fresh_time(value, now):
    date = datetime.fromisoformat(value)
    require(date.tzinfo is not None and 0 <= (now-date).total_seconds() <= 900,
            'A fresh capacity snapshot within 15 minutes is required')


def calculate(before, after, fresh, original, recovery):
    require(before['cell_id'] == CELL and before['files'] == 100000,
            'Only the original 100k C4 flat base is supported')
    require(original['passed'] is False and original['extra_full_copies'] == 7 and
            original['reserve_fraction'] == .2, 'Original failed seven-copy forecast required')
    require(recovery['schema'] == 'c4-aof-recovery-proof-v1' and recovery['passed'] is True and
            recovery['cell_id'] == CELL and recovery['pool'] == 'pool-a', 'Verified AOF recovery required')
    require(recovery['retirement_bytes'] > 0 and recovery['retirement_inodes'] > 0 and
            recovery['retired_aof_logical_bytes'] > 0, 'Retired allocations must be positive')
    for value in (before, after, fresh):
        require(value['pool'] == 'pool-a' and value['inventory'] == before['inventory'] and
                set(value['nodes']) == {'client', 'metadata', 'objects'}, 'Actual inventory differs')
    for role in before['nodes']:
        a, b, c = (v['nodes'][role] for v in (before, after, fresh))
        for key in ('boot_id', 'container_id', 'image', 'pid'):
            require(a.get(key) == b.get(key) == c.get(key), 'Service identity changed: '+role)
        require(len(a['disks']) == len(b['disks']) == len(c['disks']), 'Disk count changed')
        for aa, bb, cc in zip(a['disks'], b['disks'], c['disks']):
            for key in ('path', 'device', 'serial', 'device_bytes', 'total_bytes', 'total_inodes'):
                require(aa[key] == bb[key] == cc[key], 'Disk identity changed: '+key)
    rr = [v['nodes']['metadata']['redis'] for v in (before, after, fresh)]
    for key in ('process_id', 'maxmemory'):
        require(rr[0][key] == rr[1][key] == rr[2][key], 'Redis identity changed')
    # Include the entire old logical AOF as an additional allowance, rather
    # than assuming that every byte present before import survived the rewrite.
    aof_growth = max(0, rr[1]['aof_current_size'] - rr[0]['aof_current_size'] +
                     recovery['retired_aof_logical_bytes']) + rr[0]['aof_current_size']
    aof_projected = max(r['aof_current_size'] for r in rr) + 7*aof_growth
    # The extra full projected AOF is deliberately additive to the normal
    # seven-copy forecast; it is not evidence of a measured future peak.
    rewrite_headroom = ((aof_projected + 4095)//4096)*4096 + 4096*16
    expected = {}
    for role in before['nodes']:
        for a, b, c in zip(*(v['nodes'][role]['disks'] for v in (before, after, fresh))):
            for quantity in ('bytes', 'inodes'):
                used = 'used_'+quantity
                growth = max(0, b[used]-a[used])
                copies = 0 if role == 'client' else 7
                if role == 'metadata':
                    retired = recovery['retirement_'+quantity]
                    # The old entire used space bounds any unidentified
                    # pre-existing allocation reclaimed during this interval.
                    growth = max(0, b[used]-a[used]+retired) + a[used]
                projected = max(v[used] for v in (a, b, c)) + copies*growth
                headroom = rewrite_headroom if role == 'metadata' and quantity == 'bytes' else 0
                if role == 'metadata' and quantity == 'inodes':
                    headroom = 16
                expected[role, a['path'], quantity] = dict(role=role, path=a['path'], quantity=quantity,
                    measured_base_increment=growth, extra_full_copies=copies,
                    projected_used=projected+headroom, total=a['total_'+quantity],
                    observed_allocation_decrease=b[used]<a[used],
                    rewrite_headroom_bytes=headroom if quantity == 'bytes' else 0,
                    rewrite_headroom_inodes=headroom if quantity == 'inodes' else 0,
                    passed=5*(projected+headroom) <= 4*a['total_'+quantity])
    growth = max(0, rr[1]['used_memory']-rr[0]['used_memory'])
    projected = max(r['used_memory'] for r in rr)+7*growth
    expected['metadata', None, 'redis-memory-bytes'] = dict(role='metadata', quantity='redis-memory-bytes',
        measured_base_increment=growth, extra_full_copies=7, projected_used=projected,
        total=rr[0]['maxmemory'], observed_allocation_decrease=rr[1]['used_memory']<rr[0]['used_memory'],
        passed=rr[1]['used_memory']>=rr[0]['used_memory'] and 5*projected <= 4*rr[0]['maxmemory'])
    expected['metadata', None, 'redis-aof-bytes'] = dict(role='metadata', quantity='redis-aof-bytes',
        before=rr[0]['aof_current_size'], after=rr[1]['aof_current_size'],
        measured_base_increment=aof_growth, extra_full_copies=7, projected_aof_bytes=aof_projected,
        included_in_metadata_disk_forecast=True, passed=True)
    key = lambda r: (r['role'], r.get('path'), r['quantity'])
    require(len(original['rows']) == len(expected) and {key(r) for r in original['rows']} == set(expected),
            'Original capacity row membership differs')
    for old in original['rows']:
        new = expected[key(old)]
        require(new['measured_base_increment'] >= old['measured_base_increment'], 'Forecast reduced an original increment')
        if old['role'] != 'metadata':
            require(old['passed'] is True, 'Unrelated original capacity failure')
    return [expected[key(old)] for old in original['rows']], rewrite_headroom


def main():
    p = argparse.ArgumentParser(description=__doc__)
    for name in ('before', 'after', 'fresh', 'original-capacity', 'recovery-proof', 'out'):
        p.add_argument('--'+name, type=Path, required=True)
    args = p.parse_args()
    require(not args.out.exists(), 'Never overwrite a capacity receipt')
    docs = {k:load(getattr(args,k)) for k in ('before','after','fresh','original_capacity','recovery_proof')}
    now = datetime.now(timezone.utc)
    fresh_time(docs['fresh']['at'], now)
    for row in docs['fresh']['nodes'].values():
        fresh_time(row['sampled_at'], now)
    for k in ('before','after','original_capacity'):
        require(docs['recovery_proof'][k] == ref(getattr(args,k)), 'Recovery refers to another original '+k)
    for k in ('before','after','fresh','recovery_proof'):
        for source in docs[k]['source_pins']:
            pin(source)
    before, after = (docs[k] for k in ('before','after'))
    preparation = {k:after['nodes']['client']['preparation'][k] for k in ('path','sha256')}
    fresh_prep = docs['fresh']['nodes']['client']['preparation']
    require({k:fresh_prep[k] for k in preparation} == preparation and fresh_prep['value']['passed'] is True,
            'Original closed preparation differs')
    rows, headroom = calculate(**{k:docs[k] for k in ('before','after','fresh')},
        original=docs['original_capacity'], recovery=docs['recovery_proof'])
    sources = [ref(getattr(args,k)) for k in docs] + [ref(__file__)]
    result = dict(schema='c4-aof-reconciled-capacity-v1', passed=all(row['passed'] for row in rows),
        at=now.isoformat(), cell_id=CELL, pool='pool-a', before=ref(args.before),after=ref(args.after),
        fresh=ref(args.fresh), original_capacity=ref(args.original_capacity), preparation=preparation,
        recovery_proof=ref(args.recovery_proof), producer=ref(__file__), rows=rows,
        extra_full_copies=7, reserve_fraction=.2, rewrite_headroom_bytes=headroom,source_pins=sources,
        scope='Original one-base seven-copy forecast with verified AOF retirement, whole-before allocation allowance and one extra projected AOF. Not a measured future peak or performance acceptance.')
    with args.out.open('x') as f:
        json.dump(result,f,indent=2);f.write('\n')
    print(json.dumps(dict(passed=result['passed'],path=str(args.out),sha256=ref(args.out)['sha256'])))
    if not result['passed']:
        raise SystemExit(2)


if __name__ == '__main__':
    main()
