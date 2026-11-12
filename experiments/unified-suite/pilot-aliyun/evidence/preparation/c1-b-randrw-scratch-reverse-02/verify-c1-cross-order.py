"""Read-only verification of the exact C1 forward and reverse scratch receipts."""
import hashlib
import json
from pathlib import Path
import re
import runpy
import sys

repo, reverse, destination = map(Path, sys.argv[1:])
base = repo/'experiments/unified-suite/pilot-aliyun'
forward = base/'evidence/preparation/c1-b-randrw-scratch-02'
audit = runpy.run_path(str(base/'scripts/audit-b-c1-scratch.py'))
ARMS = ('brewfs', 'juicefs')
IO = dict(read_bytes=50479104,write_bytes=16629760,read_ios=12324,write_ios=4060,cold_crc_bytes=67108864)
KEYS = ('inventory','latest','size_gib','disks','old_data_writable')
load = lambda p: json.loads(p.read_bytes())
sha = lambda raw: hashlib.sha256(raw).hexdigest()
pattern = re.compile(rb'(?i)(redis://:|(?:AWS_ACCESS_KEY_ID|AWS_SECRET_ACCESS_KEY|RUSTFS_ACCESS_KEY|RUSTFS_SECRET_KEY)\s*=|requirepass\s|Authorization:\s+Bearer\s|BEGIN\s+[^\r\n]*PRIVATE KEY|x-amz-signature=|x-oss-signature=)')

def source(path):
    return dict(bytes=path.stat().st_size,sha256=sha(path.read_bytes()))

def verify_order(root, expected):
    plan = load(root/'plan.json')
    observed = audit['run'](root)
    if (root/'audit.json').is_file():
        saved = load(root/'audit.json')
        assert all(saved.get(k)==observed.get(k) for k in
                   ('run','completed','partial_measures','failed','resources_closed','paired_diagnostic',
                    'performance_calibrated','historical_acceptance_delta','object_capacity',
                    'timed_object_disk_bracket_available')), 'Saved audit disagrees with fresh raw replay'
    assert plan['phase_order']==list(expected)==observed['phase_order']
    assert observed['resources_closed'] and not observed['performance_calibrated']
    assert observed['historical_acceptance_delta']==0 and not observed['failed']
    assert observed['paired_diagnostic'] and set(observed['completed'])==set(ARMS)
    raw = {}
    sources = {}
    for role in ('client','objects','metadata'):
        p = root/f'{role}-evidence.tar.gz'
        raw[role] = audit['contents'](p)
        assert all(not pattern.search(payload) for payload in raw[role].values()), f'Credential candidate: {role}'
        sources[p.name] = source(p)
    c = raw['client']
    d = lambda name: json.loads(c[name])
    r = {}
    starts = []
    for arm in expected:
        terminal, runtime, launch = [d(arm+'-'+x+'.json') for x in ('terminal','runtime','launch')]
        starts.append(launch['utc_ns'])
        all_status = [json.loads(x) for x in (root/(arm+'-unit-status.ndjson')).read_bytes().splitlines()]
        stats = [x for x in all_status if 'runtime' in x]
        assert stats[-1]['terminal']==terminal and stats[-1]['runtime']==runtime
        assert terminal['runtime']==runtime and terminal['passed']
        assert terminal['result_sha256']==stats[-1]['observed_result_sha256']==sha(c[arm+'/result.json'])
        assert stats[-1]['ActiveState']=='inactive' and stats[-1]['ExecMainStatus']=='0'
        assert all(x.get('NRestarts') in ('','0',None) for x in stats)
        assert {x['runtime']['invocation'] for x in stats if x['runtime']}=={runtime['invocation']}
        row = observed['completed'][arm]
        assert all(row[k]==v for k,v in IO.items())
        phases = {x['label']:x for x in map(json.loads,c[arm+'/phases.ndjson'].splitlines())}
        assert phases['verify-unmounted']['utc_ns'] <= terminal['utc_ns']
        assert (row['seed'],row['input_sha256'])==(9173,d('input.json')['sha256'])
        debt = c.get(arm+'/debt-samples.ndjson',b'')
        if expected[0]=='brewfs':
            assert debt, 'Revised bounded drain is absent'
        series = [json.loads(x) for x in debt.splitlines()]
        r[arm] = dict(
            fio_seconds=row['measure_seconds'],
            drain_seconds=(phases['drain-end']['monotonic_ns']-phases['measure-end']['monotonic_ns'])/1e9,
            sync_p99_ms=row['latency_native_ns']['sync']['p99']/1e6,
            cold_crc_bytes=row['cold_crc_bytes'],cold_sha256=row['cold_sha256'],
            cold_verify_seconds=(phases['cold-verify-end']['monotonic_ns']-phases['cold-verify-start']['monotonic_ns'])/1e9,
            zero_debt_samples=row['native_zero_debt'],
            drain_series_logged=bool(series),drain_samples=len(series) if series else len(row['native_zero_debt']),
            nonzero_samples=sum(bool(x.get('nonzero')) for x in series) if series else None,
            first_debt_sample=series[0] if series else None,
            mount_phases=['prepare','measure','verify'],
            unit_invocation=runtime['invocation'],whole_arm_disk_write_bytes=sum(x['write_bytes'] for x in row['object_arm_bracket']),
            whole_arm_disk_read_bytes=sum(x['read_bytes'] for x in row['object_arm_bracket']))
        sources[arm+'-unit-status.ndjson'] = source(root/(arm+'-unit-status.ndjson'))
    assert starts[0] < starts[1]
    first = d(expected[0]+'-terminal.json')
    second = d(expected[1]+'-launch.json')
    assert first['utc_ns'] < second['utc_ns'], 'Arms overlapped'
    for name in ('plan.json','contract.json','audit.json','stopped.json','released.json','summary.json'):
        if (root/name).is_file():
            sources[name] = source(root/name)
    return dict(plan=plan, audited=observed, rows=r, sources=sources, initial=d('input.json'),
                matched=d('matched-input.json') if 'matched-input.json' in c else None,
                binaries={k:v for k,v in d('client-binaries.json').items() if k.endswith('_sha256')},
                rustfs={k:v for k,v in json.loads(raw['objects']['rustfs.json']).items() if k!='container'},
                redis=json.loads(raw['metadata']['redis.json']),
                disk_ids=json.loads(raw['objects']['format-intent.json'])['disk_ids'])

a = verify_order(forward,('juicefs','brewfs'))
b = verify_order(reverse,('brewfs','juicefs'))
assert (a['plan']['run'],b['plan']['run'])==('ax-c1b-21eba441c4','ax-c1b-b9d5e18dd7')
assert all(a['plan'][k]==b['plan'][k] for k in KEYS)
assert a['sources']['contract.json']==b['sources']['contract.json']
assert a['initial']==b['initial']==b['matched']['verified_input']
assert b['matched']['prior']==b['plan']['matched_fixture']
assert b['matched']['prior']==dict(run=a['plan']['run'],input_sha256=a['initial']['sha256'],
                                  prior_audit_sha256=a['sources']['audit.json']['sha256'])
assert a['binaries']==b['binaries'] and a['rustfs']==b['rustfs'] and a['redis']==b['redis']
assert len(set(a['disk_ids']))==len(set(b['disk_ids']))==4 and not set(a['disk_ids'])&set(b['disk_ids'])
for root, evidence in ((forward,a),(reverse,b)):
    assert set(load(root/'released.json')['disks'])==set(evidence['disk_ids'])
    originals={disk for role in evidence['plan']['inventory'].values() for disk in role['data_disk_ids']}
    assert not set(evidence['disk_ids']) & originals
out=dict(schema='b-c1-randrw-cross-order-comparison-v1',formal_acceptance_delta=0,
         input=a['initial'],contract_sha256=a['sources']['contract.json']['sha256'],
         equal_client_binary_pins=a['binaries'],equal_rustfs=a['rustfs'],equal_redis=a['redis'],
         io_per_arm=IO,orders=[],
         scope='One sequential diagnostic pair in each order; new matched-class disks per pair and revised drain gate outside fio; no calibrated speedup or causal separation of order from run variation.')
for info in (a,b):
    a0=info['audited']
    row=info['rows']
    out['orders'].append(dict(run=a0['run'],order=a0['phase_order'],roles=row,
        juicefs_over_brewfs_fio_wall_ratio=row['juicefs']['fio_seconds']/row['brewfs']['fio_seconds'],
        timed_object_disk_bracket_available=a0.get('timed_object_disk_bracket_available',False),
        object_capacity=a0['object_capacity'],sources=info['sources']))
with destination.open('x') as stream:
    json.dump(out,stream,indent=2,sort_keys=True); stream.write('\n')
