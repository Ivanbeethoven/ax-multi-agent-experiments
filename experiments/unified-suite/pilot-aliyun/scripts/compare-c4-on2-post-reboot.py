#!/usr/bin/env python3
"""Compare historical C4 growth with new A-pool probes; read only, no admission.

Pin a c4-on2-post-reboot-comparison-request-v1 JSON (see --help).
Take the current capacity snapshot using the original pinned capacity-check.py
against a separately probed control, without --before. Pin the three raw host
probes in the format produced by qualify-parallel-hardware.py. They bind the
capacity sample to actual instance IDs and the current boot. This comparator
performs no SSH or cloud calls.
"""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
from uuid import UUID

REPO = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(REPO))
from bench.runtime.common import digest, load, pinned

POOL = 'pool-a'
CELL = 'c5e97774bcffa69a-r1-native'
MOUNTS = dict(client={'/srv/ax-client'}, metadata={'/srv/ax-metadata'},
              objects={f'/srv/ax-objects-{i}' for i in range(4)})


def require(ok, message):
    if not ok:
        raise ValueError(message)


def moment(value):
    t = datetime.fromisoformat(value)
    require(t.utcoffset() is not None, 'Evidence timestamps require a timezone')
    return t


def integer(value):
    return type(value) is int and value >= 0


def disk_map(node, role):
    disks = node['disks']
    mapping = {d['path']: d for d in disks}
    require(len(mapping)==len(disks)==len(MOUNTS[role]) and
            len({d['device'] for d in disks})==len(disks) and
            len({d['serial'] for d in disks})==len(disks), 'Wrong native disk membership: '+role)
    for d in disks:
        require(all(integer(d[k]) and d[k]>0 for k in ('device','device_bytes','total_bytes','total_inodes')),
                'Invalid native device or totals: '+role)
        for q in ('bytes','inodes'):
            used, free, total = (d[k+'_'+q] for k in ('used','available','total'))
            require(integer(used) and integer(free) and used+free<=total, 'Invalid native allocation: '+role)
    return mapping


def fleet(pin):
    doc = load(pinned(pin))
    values = [n for n in doc['nodes'] if n['cluster_id']==POOL]
    require(len(values)==len(MOUNTS) and {n['id'] for n in values}=={POOL+'-'+r for r in MOUNTS},
            'The three original A roles are required')
    return {n['id'].removeprefix(POOL+'-'):n for n in values}


def run(request, now=None):
    now = now or datetime.now(timezone.utc)
    s = load(pinned(request))
    require(set(s)=={'schema','original_request','historic_growth','current_snapshot','current_probes'} and
            s['schema']=='c4-on2-post-reboot-comparison-request-v1' and set(s['current_probes'])==set(MOUNTS),
            'Exact C4 On2 diagnostic request required')
    old = load(pinned(s['original_request']))
    require(old['schema']=='c4-fork-batch-v1' and old['reviewed'] is True and old['cell_id']==CELL and
            old['trials'][2]['attempt']=='c4-a-flat-on2' and old['trials'][2]['mode']=='on',
            'This diagnostic is only for the original A On2')
    binding = load(pinned(old['runtime_control']))
    cap_path = pinned(s['historic_growth'])
    cap = load(cap_path)
    require(cap_path==Path(old['out'])/'capacity.json' and cap['schema']=='c4-aof-reconciled-capacity-v1' and
            cap['passed'] is True and cap['pool']==POOL and cap['cell_id']==CELL and
            cap['reserve_fraction']==.2 and cap['extra_full_copies']==7 and
            all(cap[k] in cap['source_pins'] for k in ('before','after','original_capacity')) and
            len({cap[k]['path'] for k in ('before','after','original_capacity')})==3,
            'Original reconciled growth must pin three separate historical sources')
    before, after, original_cap = (load(pinned(cap[k])) for k in ('before','after','original_capacity'))
    require(original_cap['before']==cap['before'] and original_cap['after']==cap['after'] and
            cap['before'] in after['source_pins'] and moment(before['at'])<moment(after['at']),
            'Original capacity growth does not bind the same historical window')
    current = load(pinned(s['current_snapshot']))
    for snapshot in (before,after,current):
        require(snapshot['pool']==POOL and set(snapshot['nodes'])==set(MOUNTS) and
                all(p in snapshot['source_pins'] for p in (snapshot['inventory'],old['tools']['capacity'])),
                'Expected original native capacity probe and a bound A fleet')
    pinned(old['tools']['capacity'])
    require(before['inventory']==after['inventory']==binding['fleet'] and
            current['inventory']['path']!=binding['fleet']['path'] and
            set(p['path'] for p in current['source_pins'])==
                {current['inventory']['path'],old['tools']['capacity']['path']},
            'Current native snapshot must use a separate fleet and no historical before input')
    current_at = moment(current['at'])
    require(moment(after['at'])<current_at<=now and (now-current_at).total_seconds()<=900,
            'Current snapshot is stale or not later than the historical window')
    prior_fleet, live_fleet = fleet(binding['fleet']),fleet(current['inventory'])
    history, state, expected_rows = {}, {}, set()
    for role, roots in MOUNTS.items():
        b,a,n = [v['nodes'][role] for v in (before,after,current)]
        probe = load(pinned(s['current_probes'][role]))
        prior, live = prior_fleet[role],live_fleet[role]
        boot = UUID(n['boot_id'])
        require(probe['read_only'] is True and probe['instance_id']==live['host_id']==prior['host_id'] and
                probe['hostname']==live['hostname']==prior['hostname'] and
                b['boot_id']==a['boot_id'] and boot!=UUID(a['boot_id']) and boot==UUID(probe['boot_id']) and
                (now-moment(n['sampled_at'])).total_seconds()<=900 and
                moment(after['at'])<moment(n['sampled_at'])<=current_at,
                'Wrong current host, stale probe or unchanged boot: '+role)
        mount = probe['data_mounts']
        require(len(mount)==len(roots) and {m['path'] for m in mount}==roots and
                all(m['is_mount'] is True for m in mount) and
                {m['device'] for m in mount}=={d['device'] for d in n['disks']},
                'Current mounts and native capacity devices differ: '+role)
        bd,ad,nd = (disk_map(node,role) for node in (b,a,n))
        require(set(bd)==set(ad)==set(nd) and
                {'d-'+d['serial'] for d in nd.values()}==set(live['data_disk_ids'])==set(prior['data_disk_ids']),
                'Native disk paths or attached serials changed: '+role)
        for path,d in nd.items():
            require(all(bd[path][k]==ad[path][k]==d[k] for k in
                        ('serial','device_bytes','total_bytes','total_inodes')) and
                    bd[path]['device']==ad[path]['device'],
                    'Historical disk identity or current disk geometry changed: '+path)
            for q in ('bytes','inodes'):
                key = role,path,q
                expected_rows.add(key)
                history[key] = max(0,ad[path]['used_'+q]-bd[path]['used_'+q])
        state[role] = dict(instance_id=live['host_id'],historic_boot=a['boot_id'],current_boot=n['boot_id'],
            disks=[dict(path=d['path'],serial=d['serial'],available_bytes=d['available_bytes'],
                        available_inodes=d['available_inodes']) for d in nd.values()])
        if role=='objects':
            service = probe['rustfs']
            require(service['running'] is True and service['id']==n['container_id'] and
                    service['image_id']==n['image']==a['image']==b['image'] and
                    a['container_id']==b['container_id'] and integer(n['pid']) and n['pid']>0 and a['pid']==b['pid'],
                    'RustFS image, current container or historical service window differs')
            state[role]['service'] = dict(historic_pid=a['pid'],current_pid=n['pid'],image=n['image'],
                historic_container=a['container_id'],current_container=n['container_id'])
        if role=='metadata':
            b,a,n = (v['redis'] for v in (b,a,n))
            service = probe['redis']
            require(service['active']=='active' and service['settings']['appendonly']=='yes' and
                    int(service['settings']['maxmemory'])==n['maxmemory']==a['maxmemory']==b['maxmemory']>0 and
                    int(service['systemd_pid'])==int(service['process_id'])==n['process_id']>0 and
                    a['process_id']==b['process_id'] and all(
                        integer(v['used_memory']) and v['used_memory']<=v['maxmemory'] and
                        integer(v['aof_current_size']) for v in (b,a,n)),
                    'Redis AOF, memory or current process binding differs')
            state[role]['service'] = dict(historic_pid=a['process_id'],current_pid=n['process_id'],
                current_aof_bytes=n['aof_current_size'],current_used_memory=n['used_memory'],maxmemory=n['maxmemory'])
            for q,k in (('redis-memory-bytes','used_memory'),('redis-aof-bytes','aof_current_size')):
                key = role,None,q
                expected_rows.add(key)
                history[key] = max(0,a[k]-b[k])
    rows = cap['rows']
    require(len(rows)==len(expected_rows) and {(r['role'],r.get('path'),r['quantity']) for r in rows}==expected_rows and
            all(integer(r['measured_base_increment']) and
                r['measured_base_increment']>=history[r['role'],r.get('path'),r['quantity']] for r in rows),
            'Historical growth rows must cover measured same-boot allocation deltas')
    on2 = old['trials'][2]
    growth = [dict(role=r['role'],path=r.get('path'),quantity=r['quantity'],
                   observed_delta=history[r['role'],r.get('path'),r['quantity']],
                   historical_growth_floor=r['measured_base_increment'],
                   **{k:r[k] for k in ('rewrite_headroom_bytes','rewrite_headroom_inodes') if k in r}) for r in rows]
    return dict(schema='c4-on2-post-reboot-comparison-v1',comparison_valid=True,dispatch_eligible=False,
        source_request=request,cell_id=CELL,pool=POOL,current_sample_at=current['at'],nodes=state,
        historic_growth_only=growth,collector_predecessor=dict(name=on2['collector_name'],
            out=on2['collector_out'],state='not-probed',has_declared_replacement='new_collector_name' in on2,
            blocker='On2 expects the predecessor running. If stopped, the frozen calibrator has no replacement; review a fresh collector binding and native scrape.'),
        remaining=['same-asset cold verification','current no-lease and closure evidence',
                   'fresh cleanup, placement, scratch and full capacity budget','reviewed reboot-aware continuation'])


def main():
    p = argparse.ArgumentParser(description=__doc__, epilog=
        'Request fields: schema, original_request, historic_growth, current_snapshot, current_probes '
        '(client/metadata/objects). Every input uses an absolute path/SHA256 pin.')
    p.add_argument('--request',required=True)
    p.add_argument('--sha256',required=True)
    a = p.parse_args()
    print(json.dumps(run(dict(path=a.request,sha256=a.sha256)),indent=2))


if __name__=='__main__':
    main()
