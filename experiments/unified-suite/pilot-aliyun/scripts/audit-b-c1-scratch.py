#!/usr/bin/env python3
"""Audit a closed B scratch attempt; partial diagnostics stay separate from a pair."""
import argparse
from datetime import datetime,timezone
import hashlib
import json
from pathlib import Path
import tarfile

ARMS=('juicefs','brewfs')
EXPECTED=67108864
def sha(data):
    return hashlib.sha256(data).hexdigest()
def load(path):
    return json.loads(path.read_bytes())
def source(path):
    return {'file':path.name,'sha256':sha(path.read_bytes())}
def need(ok,msg):
    if not ok:
        raise ValueError(msg)

def contents(path):
    values={}
    with tarfile.open(path) as archive:
        for entry in archive.getmembers():
            p=Path(entry.name)
            need(not p.is_absolute() and '..' not in p.parts and 'private' not in p.parts
                 and (entry.isfile() or entry.isdir()),'Unsafe archived member')
            if entry.isfile():
                need(str(p) not in values and entry.size<=16*1024**2,'Repeated or huge archive member')
                values[str(p)]=archive.extractfile(entry).read()
    return values

def records(payload):
    return [json.loads(x) for x in payload.splitlines()]

def run(root):
    plan=load(root/'plan.json')
    summary=load(root/'summary.json')
    order=tuple(plan['phase_order'])
    need(len(order)==len(ARMS) and set(order)==set(ARMS),'Invalid planned arm order')
    result=dict(schema='b-c1-randrw-scratch-audit-v1',utc=datetime.now(timezone.utc).isoformat(),
                run=plan['run'],source_pins=[source(root/'plan.json'),source(root/'summary.json')],
                phase_order=list(order),completed={},partial_measures={},failed=list(summary['errors']),performance_calibrated=False,
                historical_acceptance_delta=0)
    client,objects={},{}
    for role in ('client','objects','metadata'):
        path=root/(role+'-evidence.tar.gz')
        if not path.is_file():
            continue
        try:
            role_members=contents(path)
        except (OSError,tarfile.TarError,ValueError) as e:
            result['failed'].append(role+' archive: '+str(e))
            continue
        result['source_pins'].append(source(path))
        if role=='client':
            client=role_members
        if role=='objects':
            objects=role_members
    samples=records(objects.get('disk-series.ndjson',b''))
    synced=all(json.loads(data.get('probe.json',b'{}')).get('clock')=='NTPSynchronized=yes'
               for data in (client,objects))
    if samples:
        first=samples[0]['disks']
        need(len(first)==4 and len({x['device'] for x in first})==4 and
             all([d['device'] for d in row['disks']]==[d['device'] for d in first] for row in samples),
             'Actual object sample device identity changed')
        result['object_capacity']=dict(samples=len(samples),clocks_synchronized=synced,
            reserve_stop_observed='capacity-stop.json' in objects,
            disks=[dict(device=d['device'],total_bytes=d['total'],initial_free_bytes=d['free'],
                        minimum_free_bytes=min(x['disks'][i]['free'] for x in samples),
                        final_free_bytes=samples[-1]['disks'][i]['free'])
                   for i,d in enumerate(first)])
    heads={}
    for a in ARMS:
        path=root/(a+'-headroom.json')
        if path.is_file() and path.stat().st_size:
            heads[a]=load(path)
            result['source_pins'].append(source(path))
    phases_by_arm={}
    for a in order:
        if a+'/result.json' not in client:
            continue
        phases=records(client[a+'/phases.ndjson'])
        event={x['label']:x for x in phases}
        need(len(event)==len(phases),'Repeated phase marker: '+a)
        phases_by_arm[a]=event
    if set(phases_by_arm)==set(ARMS):
        first,second=(phases_by_arm[a] for a in order)
        need(first['verify-unmounted']['monotonic_ns']<second['prepare-mounted']['monotonic_ns'],
             'Actual client order overlaps or differs from the plan')
    for a in order:
        key=a+'/result.json'
        if key not in client:
            partial=a+'/measure/result.json'
            if partial in client:
                raw=json.loads(client[partial])
                [job]=raw['native_jobs']
                if raw['passed'] is True and raw['application_bytes']==EXPECTED and job['error']==0:
                    result['partial_measures'][a]=dict(bytes=EXPECTED,
                         read_bytes=job['read']['io_bytes'],write_bytes=job['write']['io_bytes'],
                         seconds=raw['wrapper_wall_seconds'],raw_fio_sha256=sha(client[a+'/measure/fio.json']),
                         scope='fio exit and native JSON only; durable cold closure is absent')
            continue
        p=json.loads(client[key])
        raw_measure=client[a+'/measure/fio.json']
        raw_verify=client[a+'/cold-verify/fio.json']
        fio=json.loads(raw_measure)['jobs']
        cold=json.loads(raw_verify)['jobs']
        event=phases_by_arm[a]
        prior=set()
        for label in ('prepare','measure','verify'):
            started,closed=event[label+'-mounted'],event[label+'-unmounted']
            identity=(started['pid'],started['start_ticks'])
            need(identity not in prior and started['pid']==closed['pid'] and closed['exit_code']==0 and
                 started['monotonic_ns']<closed['monotonic_ns'] and
                 (label=='prepare' or previous_closed<started['monotonic_ns']),
                 'Fresh supervised remount or clean exit proof failed: '+a)
            prior.add(identity)
            previous_closed=closed['monotonic_ns']
        begin,end=event['measure-start']['utc_ns'],event['measure-end']['utc_ns']
        [job]=fio
        [verification]=cold
        need(p['passed'] and p['read_bytes']+p['write_bytes']==p['cold_crc_bytes']==EXPECTED and
             p['read_bytes']==job['read']['io_bytes'] and p['write_bytes']==job['write']['io_bytes'] and
             verification['error']==job['error']==0 and verification['read']['io_bytes']==EXPECTED and
             all(p[k]==job[d]['total_ios'] for k,d in (('read_ios','read'),('write_ios','write'))) and
             len(p['native_zero_debt'])==2 and p['native_zero_debt'][1]['utc_ns']-p['native_zero_debt'][0]['utc_ns']>=10**9,
             'Invalid native I/O, cold CRC or drain evidence: '+a)
        p.update(raw_fio_sha256=sha(raw_measure),cold_fio_sha256=sha(raw_verify),
                 native_debt_sha256=[sha(client[f'{a}/debt-{i}.prom']) for i in range(2)])
        p['latency_native_ns']={}
        for operation,kind in (('read','clat_ns'),('write','clat_ns'),('sync','lat_ns')):
            native=job.get(operation,{}).get(kind,{})
            percentiles=native.get('percentile',{})
            p['latency_native_ns'][operation]=dict(count=native.get('N'),
                  mean=native.get('mean'),p50=percentiles.get('50.000000'),
                  p95=percentiles.get('95.000000'),p99=percentiles.get('99.000000'),
                  kind=kind)
        before=[x for x in samples if x['utc_ns']<=begin]
        after=[x for x in samples if x['utc_ns']>=end]
        if synced and before and after and begin-before[-1]['utc_ns']<=5*10**9 and after[0]['utc_ns']-end<=5*10**9:
            low,high=before[-1],after[0]
            window=[x for x in samples if low['utc_ns']<=x['utc_ns']<=high['utc_ns']]
            need(all(len(x['disks'])==4 for x in window),'Incomplete object disk coverage')
            p['object_measure_bracket']=dict(samples=len(window),max_gap_seconds=max(
                ((y['utc_ns']-x['utc_ns'])/10**9 for x,y in zip(window,window[1:])),default=0),
                disks=[dict(device=x['device'],observed_peak_net_growth_bytes=max(0,x['free']-min(
                    s['disks'][i]['free'] for s in window)),read_bytes=y['read_bytes']-x['read_bytes'],
                    write_bytes=y['write_bytes']-x['write_bytes'])
                    for i,(x,y) in enumerate(zip(low['disks'],high['disks']))],
                scope='Whole-device counters, including background and ±5 s timestamp bracket')
        if samples and a in heads:
            low=heads[a]
            high=heads.get(order[1],samples[-1]['disks']) if a==order[0] else samples[-1]['disks']
            need(len(low)==len(high)==4 and all(x['device']==y['device'] and
                 all(x[k]<=y[k] for k in ('read_bytes','write_bytes')) for x,y in zip(low,high)),
                 'Invalid per-arm controller device bracket')
            p['object_arm_bracket']=[dict(device=x['device'],initial_free_bytes=x['free'],
                 final_free_bytes=y['free'],read_bytes=y['read_bytes']-x['read_bytes'],
                 write_bytes=y['write_bytes']-x['write_bytes']) for x,y in zip(low,high)]
            p['object_arm_bracket_scope']='Includes setup, drain, cold verify and object-node background activity'
        result['completed'][a]=p
    stopped=root/'stopped.json'
    released=root/'released.json'
    if stopped.exists() and released.exists():
        actual_stopped=load(stopped)
        actual_released=load(released)
        result['source_pins'] += [source(stopped),source(released)]
        result['resources_closed']=(set(actual_stopped)=={x['instance_id'] for x in plan['inventory'].values()}
            and all(v==['Stopped','StopCharging'] for v in actual_stopped.values())
            and len(actual_released['disks'])==4 and set(actual_released['disks'])==set(summary['created'])
            and not actual_released['available'])
    else:
        result['resources_closed']=False
    if set(result['completed'])==set(ARMS):
        a,b=(result['completed'][x] for x in ARMS)
        if (prior:=plan.get('matched_fixture')) is not None:
            matched=json.loads(client['matched-input.json'])
            need(matched['prior']==prior and
                 matched['verified_input']==json.loads(client['input.json'])==
                     dict(bytes=EXPECTED,sha256=prior['input_sha256'],seed=9173) and
                 all(p['input_sha256']==prior['input_sha256'] for p in (a,b)),
                 'Prior exact-input evidence did not match the pair')
            result['matched_fixture']=matched
        result['paired_diagnostic']=(a['input_sha256']==b['input_sha256'] and a['seed']==b['seed']==9173
             and all(a[k]==b[k] for k in ('read_bytes','write_bytes','read_ios','write_ios'))
             and all('object_arm_bracket' in p for p in (a,b)))
        result['timed_object_disk_bracket_available']=all('object_measure_bracket' in p and
             p['object_measure_bracket']['max_gap_seconds']<=5 for p in (a,b))
    else:
        result['paired_diagnostic']=False
    return result

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('directory',type=Path)
    p.add_argument('--out',type=Path,required=True)
    a=p.parse_args()
    with a.out.open('x') as f:
        json.dump(run(a.directory),f,indent=2)
        f.write('\n')
if __name__=='__main__':
    main()
