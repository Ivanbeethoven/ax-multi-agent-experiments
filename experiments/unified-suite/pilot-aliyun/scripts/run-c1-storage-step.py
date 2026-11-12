#!/usr/bin/env python3
"""Run one explicitly selected storage-gated step of the frozen B mixed-fio C1."""

import argparse
from contextlib import ExitStack
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import sqlite3
import subprocess
import sys
import tarfile
import time

REPO = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(REPO))
from bench.runtime import pilot_context
from bench.runtime.common import digest, load, mounts, pinned, write_new
from bench.runtime.pilot_pools import (
    ROOTS, active_conflicts, alias, cell_resources, cross_pool_proof, fleet_node,
    pool_inventory, resource_guard, resource_ids,
)
from bench.suite_plan import check_plan
from bench import archive_check
from bench import fio_workload

CANONICAL = Path('/srv/ax-pilot-control/parallel-control-01')
CONTROL = Path('/srv/ax-pilot-control/b-c1-after-reboot-01')
POOL = 'pool-b'
CELL = '8e3d340ff23c3ece-r1-juicefs'
ORIGINAL_FAILED_SUFFIX = 'mxb01'
LABELS = ('off1', 'on1', 'on2', 'off2', 'off3', 'on3', 'formal')
PAIRS = ((0, 1), (3, 2), (4, 5))
ROLES = ('client', 'metadata', 'objects')
GROWTH = ('data_peak', 'preparation_margin', 'missed_peak_margin', 'observer_peak',
          'archive_peak', 'background_margin', 'aof_peak')
# Exact read-only retrieval of the existing completed mxb01 On1. It is a lower
# bound from an old boot, never a substitute for a new mounted-disk snapshot.
HISTORY_SHA256 = '8ce88f3bf399796b57fffa0442a66b51b7955936ad178c650ec9fec62e7e037e'
HISTORY_TAG = 'cal-pool-b-j-mx-mxb01-on1'
# Immutable original Pool B native deployment receipt; inventory order is API order.
DEPLOYMENT_SHA256 = '05637a3d93dab1be99e6f15eb4d3f00767c78d0a5481f3ea659e98ffdab4a9ce'
TOOLS = dict(calibrate='calibrate-c1.py', check='check-calibration.py',
             admit='admit-c1.py', run='run-c1.py')


def require(ok, reason):
    if not ok:
        raise ValueError(reason)


def now():
    return datetime.now(timezone.utc)


def at(value):
    stamp = datetime.fromisoformat(value)
    require(stamp.tzinfo is not None, 'Timestamp lacks timezone')
    return stamp.astimezone(timezone.utc)


def recent(value, clock):
    stamp = at(value)
    require(clock - timedelta(minutes=15) <= stamp <= clock, 'Storage evidence expired or is in the future')
    return stamp


def ref(path):
    p = Path(path).resolve(strict=True)
    return dict(path=str(p), sha256=digest(p))


def within(path, root):
    p = Path(path)
    require(p.is_absolute() and p.resolve() == p and p.is_relative_to(root) and p != root,
            'Noncanonical or unowned path: '+str(p))
    return p


def original_batch(s):
    batch = load(pinned(s['original_request']))
    require(batch.get('reviewed') is True and batch['control'] == str(CONTROL) and
            batch['cell_id'] == CELL and batch['pool'] == POOL, 'Reviewed frozen B C1 request required')
    out = within(batch['out'], CONTROL/'one-cell-runs')
    match = re.fullmatch(re.escape(CELL)+r'-([a-z0-9]{1,6})', out.name)
    require(match is not None and match[1] != ORIGINAL_FAILED_SUFFIX, 'New complete C1 attempt suffix required')
    prefix = 'cal-pool-b-j-mx-'+match[1]
    trials = batch['trials']
    require(len(trials) == 6, 'Exactly six new trials required')
    for t, label in zip(trials, LABELS):
        name = prefix+'-'+label
        new = 'ax-pilot-'+name+'-collector' if label in ('on1', 'on3') else None
        require(t['attempt'] == name and t['mode'] == label[:-1] and
                within(t['out'], CONTROL/'calibration') == CONTROL/'calibration'/name and
                t.get('new_collector_name') == new, 'Changed trial ID, order, path or collector')
        collector(t['collector_name'], t['collector_out'])
    final = 'ax-pilot-'+prefix+'-on3-collector'
    require(batch['final_collector_name'] == final and
            batch['final_collector_out'] == '/srv/ax-objects-0/q/'+final.removeprefix('ax-pilot-'),
            'Changed formal collector')
    require(set(batch['tools']) == set(TOOLS), 'Original four C1 tools required')
    for key, filename in TOOLS.items():
        require(pinned(batch['tools'][key]).name == filename, 'Changed original C1 tool: '+key)
    require(pinned(s['snapshot_tool']).name == 'capacity-check.py', 'Original native snapshot tool required')
    return batch, out


def collector(name, path):
    require(re.fullmatch(r'ax-pilot-[a-z0-9-]+', name) and
            re.fullmatch(r'/srv/ax-objects-0/q/[a-z0-9-]+', path), 'Unowned collector')
    return dict(name=name, out=path)


def controller_basis(s, batch, plan):
    """A separately initialized controller, unchanged frozen experiment inputs."""
    original = load(pinned(s['frozen_request']))
    require(original['control'] == str(CANONICAL) and original['cell_id'] == CELL and
            any(t['attempt'] == HISTORY_TAG for t in original['trials']) and
            all(original[k] == batch[k] for k in ('tools', 'node_tool', 'observer_tool')),
            'Original frozen tools and failed mixed-fio request required')
    require(CONTROL != CANONICAL and not batch.get('runtime_control') and
            (CONTROL/'runner-locks').resolve() == (CANONICAL/'runner-locks').resolve(),
            'New controller uses its own probes and the canonical physical locks')
    for relative in ('suite/plan.json', 'provenance/inventory.json',
                     'provenance/build-manifest.json', 'provenance/input-manifest.json'):
        require(digest(CONTROL/relative) == digest(CANONICAL/relative),
                'New controller must retain exact frozen input bytes: '+relative)
    require(load(CONTROL/'suite/plan.json') == plan and
            not (CONTROL/'state.sqlite').samefile(CANONICAL/'state.sqlite'),
            'Independent new state and original frozen plan required')
    allocation = load(CONTROL/'allocation.json')
    require(allocation['passed'] is True and allocation['inventory_sha256'] == digest(CONTROL/'fleet.json'),
            'New controller requires actual successful fleet probes')
    old_fleet, new_fleet = load(CANONICAL/'fleet.json'), load(CONTROL/'fleet.json')
    placement = lambda f: dict(f, nodes=[{k:v for k,v in n.items() if k != 'repo'} for n in f['nodes']])
    require(placement(old_fleet) == placement(new_fleet), 'Reboot cannot change frozen hosts, disks or slots')
    for node in new_fleet['nodes']:
        names = [node['id']] + ([node['id']+'-slot'] if node['role'] == 'executor' else [])
        for name in names:
            pin = ref(CONTROL/'probes'/name/'probe.json')
            probe = load(pinned(pin))
            require(pin in allocation['probes'] and probe['passed'] is True and
                    probe['inventory_sha256'] == digest(CONTROL/'fleet.json') and
                    probe['node_id'] == node['id'], 'Actual new native allocation probe required')
            pinned(ref(CONTROL/'probes'/name/'peer.json'))
    # Before formal admission there is exactly one initialization event and no
    # inherited qualification, asset, lease or stage state. Never copy old DBs.
    with sqlite3.connect((CONTROL/'state.sqlite').as_uri()+'?mode=ro', uri=True) as db:
        require(json.loads(db.execute('SELECT plan FROM campaign').fetchone()[0]) == plan and
                db.execute("SELECT kind FROM events ORDER BY sequence").fetchall() == [('initialized',)] and
                not db.execute("SELECT 1 FROM cells WHERE status!='pending' OR result IS NOT NULL").fetchone() and
                all(db.execute('SELECT COUNT(*) FROM '+name).fetchone()[0] == 0 for name in
                    ('stages', 'qualifications', 'assets', 'cell_leases', 'cell_resources')),
                'Use bench.suite init for a new empty controller; inherited state is not eligible')
    oldq = Path(original['qualification'])/'juicefs'
    newq = Path(batch['qualification'])/'juicefs'
    same_contracts(oldq, newq)
    hardware = load(newq.parent/'hardware.json')
    require(hardware['passed'] is True and hardware['pool'] == POOL and
            hardware['inventory_sha256'] == digest(CONTROL/'fleet.json'), 'New actual hardware qualification required')
    for role in ROLES:
        check = hardware['checks'][role]
        probe = ref(CONTROL/'probes'/alias(POOL, role)/'probe.json')
        require(check['passed'] is True and check['probe'] == probe and
                load(pinned(check['maintenance']))['boot_id'] == load(pinned(probe))['boot_id'],
                'Maintenance and hardware must belong to this boot')
    return original


def same_contracts(oldq, newq):
    # Fresh native qualification changes source paths/probes, not workload or
    # metric semantics. Historical records keep their original pins and dates.
    oldwork, newwork = load(oldq/'workload.json'), load(newq/'workload.json')
    require(all(oldwork[k] == newwork[k] for k in ('schema', 'params', 'seed', 'binary_pins')),
            'Frozen fio workload semantics changed')
    ignore = {'source_pins'}
    oldmetric, newmetric = load(oldq/'metrics.json'), load(newq/'metrics.json')
    require({k:v for k,v in oldmetric.items() if k not in ignore} ==
            {k:v for k,v in newmetric.items() if k not in ignore}, 'Frozen metric semantics changed')
    require(digest(oldq.parent/'assets/juicefs/asset.json') == digest(newq.parent/'assets/juicefs/asset.json'),
            'Original prepared asset must remain byte-identical')


def base_recheck(s, q, cell, runtime):
    """Consume actual fresh-mount base CRC and normal exit on this boot."""
    pins = s['base_recheck']
    require(set(pins) == {'mount_spec', 'mount_ready', 'mount_exit', 'fio_result', 'fio_raw', 'fio_exit'},
            'Fresh base readback requires original native mount/fio receipts')
    values = {k:load(pinned(v)) for k,v in pins.items()}
    spec, ready, stopped = (values[k] for k in ('mount_spec', 'mount_ready', 'mount_exit'))
    asset = load(q.parent/'assets/juicefs/asset.json')
    boot = load(runtime/'probes'/alias(POOL, 'client')/'probe.json')['boot_id']
    require(spec['filesystem'] == 'juicefs' and spec['mount_point'] == asset['mount_point'] and
            spec['suite_binding'] == dict(plan=asset['plan'], cell_id=CELL) and
            ready['passed'] is True and ready['process']['boot_id'] == boot and
            ready['spec']['sha256'] == pins['mount_spec']['sha256'] and
            stopped['passed'] is True and stopped['exit_code'] == 0 and stopped['mount_absent'] is True and
            stopped['anchor'] == ready['anchor'] == spec['anchor'] and stopped['original_mount'] == ready['mount'],
            'Base CRC must use this boot, original asset and a normally closed fresh mount')
    result = values['fio_result']
    expected = dict(cell['params']); actual = dict(result['params'])
    # fio-base-check picks the original base geometry's first fio cell; verify
    # always performs the same complete sequential CRC read, regardless of rw.
    expected.pop('rw'); actual.pop('rw')
    require(result['passed'] is True and result['completed'] is True and result['phase'] == 'verify' and
            actual == expected and result['seed'] == asset['seed'] and
            result['application_bytes'] == cell['params']['file_bytes_per_job']*cell['params']['numjobs'] and
            result['raw_sha256'] == pins['fio_raw']['sha256'] and values['fio_exit']['exit_code'] == 0,
            'Fresh full original-base CRC readback required')
    fio_workload.validate_raw(values['fio_raw'], result['params'], result['seed'], 'verify')
    base = Path(asset['mount_point'])/('base-'+str(cell['params']['block_bytes']))
    require(all(Path(j['job options']['filename']).parent == base for j in values['fio_raw']['jobs']),
            'Fresh CRC must read the immutable original base')


def current_services(s, cross, runtime):
    snapshot = load(pinned(load(pinned(s['capacity_qualification']))['current_snapshot']))
    for role in ROLES:
        pin, = [p for p in cross['source_pins'] if Path(p['path']).name == alias(POOL, role)+'.json']
        actual = load(pinned(pin))
        require(actual['boot_id'] == snapshot['nodes'][role]['boot_id'] ==
                load(runtime/'probes'/alias(POOL, role)/'probe.json')['boot_id'],
                'Cross-pool service evidence must come from the current boot')
        if role == 'objects':
            require(actual['rustfs']['id'] == snapshot['nodes'][role]['container_id'],
                    'Current capacity must describe the qualified RustFS container')
        if role == 'metadata':
            require(int(actual['redis']['process_id']) == snapshot['nodes'][role]['redis']['process_id'],
                    'Current capacity must describe the qualified Redis process')


def trial_finish(t, q, cell, observed):
    root = Path(t['out'])
    p = ref(root/'finished.json')
    done, start, cohort = (load(f) for f in (p['path'], root/'started.json', root/'cohort.json'))
    purpose = 'observer-calibration-'+t['mode']
    require(done['passed'] is True and start['cell_id'] == CELL and
            start['purpose'] == cohort['purpose'] == purpose and
            cohort['workers'][0]['output'] == '/srv/ax-client/j/'+t['attempt'] and
            len(cohort['workers']) == 1 and cohort['workers'][0]['node_id'] == POOL+'-client' and
            cohort['workload_contract'] == ref(q/'workload.json') and
            cohort['metric_contract'] == ref(q/'metrics.json'), 'Different completed workload or pool')
    for key, path in dict(cohort='cohort/result.json', collector='collector.json',
                          native_exit='client/exit.json', verify_exit='client/verify-exit.json',
                          client_verify='client/verify/result.json').items():
        require(done[key] == ref(root/path), 'Different original trial evidence: '+key)
        proof = load(pinned(done[key]))
        if key == 'collector':
            require(proof['enabled'] is observed and proof['native_rustfs_sender_held_enabled'] is True,
                    'Changed observer mode')
        else:
            require(proof['passed'] is True, 'Failed original trial evidence: '+key)
        if key in ('native_exit', 'verify_exit'):
            require(proof['exit_code'] == 0 and proof['mount_absent'] is True, 'Mount did not close')
        if key == 'client_verify':
            require(proof['application_bytes'] == cell['params']['file_bytes_per_job'] *
                    cell['params']['numjobs'], 'Cold CRC did not verify the full original input')
    expected = {alias(POOL, role) for role in ROLES} if observed else set()
    require({o['id'] for o in cohort['observers']} == expected, 'Changed observer roles')
    raw_pins = {}
    for node in expected:
        raw_pins[node] = ref(root/'observer-stop'/node/'raw-audit.json')
        require(load(pinned(raw_pins[node]))['passed'] is True, 'Original raw audit failed')
    debt = ref(root/'client/debt/result.json')
    debt_result = load(pinned(debt))
    require(debt_result['passed'] is True and debt_result['cell_id'] == CELL and
            len(debt_result['samples']) == 2, 'Missing original two-sample upload drain')
    c = load(pinned(done['collector']))
    name = t.get('new_collector_name') or t['collector_name']
    path = '/srv/ax-objects-0/q/'+t['attempt']+'-collector' if t.get('new_collector_name') else t['collector_out']
    require((c['name'], c['out']) == (name, path), 'Actual collector did not follow the frozen chain')
    return p, collector(name, path), dict(raw_audits=raw_pins, debt=debt, started=ref(root/'started.json'),
                                         cohort=ref(root/'cohort.json'))


def archived_trial(archives, t, proof, trial_pins):
    """Verify native tar members, not a handwritten passed/groups receipt."""
    expected = {'coordinator', 'client'} | (set(ROLES) if t['mode'] == 'on' else set())
    require(set(archives) == expected, 'Every completed prefix trial needs its exact role archives')
    manifests = {}
    for role, pin in archives.items():
        path = pinned(pin)
        checked = archive_check.verify(path, pin['sha256'])
        require(checked['cell_id'] == CELL and checked['kind'] == 'calibration-'+t['attempt']+'-'+role,
                'Archive belongs to another trial or role')
        with tarfile.open(path, 'r:gz') as tar:
            manifests[role] = json.load(tar.extractfile('.suite-manifest.json'))['files']
    def member(role, name, pin):
        p = pinned(pin)
        require(manifests[role].get(name) == dict(bytes=p.stat().st_size, sha256=pin['sha256']),
                'Archive does not bind original native evidence: '+role+'/'+name)
    done = load(pinned(proof))
    member('coordinator', 'finished.json', proof)
    for name in ('started', 'cohort'):
        member('coordinator', name+'.json', trial_pins[name])
    for key, name in dict(client_verify='verify/result.json', native_exit='exit.json',
                          verify_exit='verify-exit.json').items():
        member('client', 'q/'+t['attempt']+'/'+name, done[key])
    member('client', 'q/'+t['attempt']+'/debt/result.json', trial_pins['debt'])
    for role in ROLES if t['mode'] == 'on' else ():
        member('coordinator', 'observer-stop/'+alias(POOL, role)+'/raw-audit.json',
               trial_pins['raw_audits'][alias(POOL, role)])
        # The actual raw audit is bound to each native sample via its finished
        # receipt. Require the lifecycle and samples in the checked role archive.
        names = manifests[role]
        prefix = 'o/'+t['attempt']+'/'
        require(prefix+'ready.json' in names and prefix+'finished.json' in names and
                any(n.startswith(prefix+'sample-') for n in names), 'Missing archived observer raw bytes')
        with tarfile.open(pinned(archives[role]), 'r:gz') as tar:
            ready = json.load(tar.extractfile(prefix+'ready.json'))
            finished = json.load(tar.extractfile(prefix+'finished.json'))
        audit = load(pinned(trial_pins['raw_audits'][alias(POOL, role)]))
        require(ready['passed'] is True and finished['passed'] is True and
                ready['metric_contract_sha256'] == load(pinned(trial_pins['cohort']))['metric_contract']['sha256'] and
                names[prefix+'finished.json']['sha256'] == audit['finished_sha256'] and
                audit['samples'] == finished['samples'] == len(finished['history']),
                'Archived raw lifecycle differs from native audit')
        with tarfile.open(pinned(archives[role]), 'r:gz') as tar:
            for index, item in enumerate(finished['history']):
                base = prefix+f'sample-{index:06}/'
                require(item['index'] == index and names[base+'sample.json']['sha256'] == item['sample_sha256'],
                        'Archived observer sample differs from original lifecycle')
                sample = json.load(tar.extractfile(base+'sample.json'))
                require(sample['passed'] is True and bool(sample['files']) and all(
                    names.get(base+name) == value for name, value in sample['files'].items()),
                    'Archived raw observer members are incomplete')


def prefix_chain(s, batch, out, q, cell):
    number = s['step']
    require(type(number) is int and 1 <= number <= 7 and len(s['prefix']) == number-1,
            'The exact completed prefix is required')
    require(number != 1 or not out.exists(), 'The original attempt directory already exists; do not replay')
    require(len(s.get('prefix_archives', [])) == number-1, 'Exact completed prefix archives required')
    t0 = batch['trials'][0]
    current = collector(t0['collector_name'], t0['collector_out'])
    for i, t in enumerate(batch['trials']):
        require(collector(t['collector_name'], t['collector_out']) == current or i >= number,
                'Actual preceding collector differs from next declared collector')
        step = out/('step-'+str(i+1))
        if i+1 >= number:
            require(not step.exists() and not Path(t['out']).exists(),
                    'An unfinished or future trial already has state; no replay')
            continue
        previous = s['prefix'][i]
        require(Path(previous['path']) == step/'result.json' and not (step/'failed.json').exists(),
                'Only original successful step results can advance the prefix')
        old = load(pinned(previous))
        old_request = load(pinned(old['request']))
        proof, current, trial_pins = trial_finish(t, q, cell, t['mode'] == 'on')
        archived_trial(s['prefix_archives'][i], t, proof, trial_pins)
        require(old['schema'] == 'c1-storage-step-result-v1' and old['passed'] is True and
                old['step'] == i+1 and old['original_request'] == s['original_request'] and
                old_request['original_request'] == s['original_request'] and old_request['prefix'] == s['prefix'][:i] and
                old['finished'] == proof and old['collector'] == current and
                old['trial_pins'] == trial_pins and
                old['closed'] == ref(step/'closed/result.json') and load(pinned(old['closed']))['passed'] is True and
                old['exit'] == ref(step/'calibrate/exit.json') and load(pinned(old['exit']))['exit_code'] == 0,
                'Earlier step does not bind to its original trial and collector')
    if number == 7:
        require(current == collector(batch['final_collector_name'], batch['final_collector_out']),
                'Formal collector does not follow the completed prefix')
    return current


def cleanup(s, clock):
    c = load(pinned(s['cleanup']))
    require(c['schema'] == 'pilot-prestep-cleanup-v1' and c['original_request'] == s['original_request'] and
            c['pool'] == POOL and c['step'] == s['step'] and c['prefix'] == s['prefix'] and
            c['outcome'] in ('cleaned', 'not-needed') and isinstance(c['actions'], list) and
            (c['outcome'] == 'cleaned') == bool(c['actions']) and
            len({v['path'] for v in c['actions']}) == len(c['actions']), 'Exact pre-step cleanup receipt required')
    done = recent(c['finished_at'], clock)
    previous = [at(load(pinned(p))['at']) for p in s['prefix']]
    require(all(v <= done for v in previous), 'Cleanup precedes a finished trial')
    require(c.get('prefix_archives', []) == s.get('prefix_archives', []),
            'Cleanup must name the same verified prefix archives')
    # There is no B executor for the former hypothetical reclaim schema. Do not
    # accept a handwritten passed/applied receipt as proof of deletion or archive.
    require(not c['actions'], 'B cleanup executor and archive binding are not wired; '
            'a passed/applied JSON or an A reclaimer is not a B cleanup proof')
    return done


def integer(v):
    return type(v) is int and v >= 0


def role_mounts(role):
    return [f'/srv/ax-objects-{i}' for i in range(4)] if role == 'objects' else [ROOTS[role]]


def deployment_disk_mapping(request, inventory):
    pin = request.get('disk_mapping')
    require(isinstance(pin, dict) and set(pin) == {'path', 'sha256'} and
            pin['sha256'] == DEPLOYMENT_SHA256, 'Exact original B disk mapping receipt required')
    deployment = load(pinned(pin))
    require(deployment.get('passed') is True and deployment.get('pool_id') == POOL and
            inventory.get('pool_id') == POOL and
            set(deployment['roles']) == set(inventory['roles']) == set(ROLES),
            'Original B deployment and frozen inventory roles do not match')
    result = {}
    for role in ROLES:
        deployed, frozen = deployment['roles'][role], inventory['roles'][role]
        disk_ids, mounts = frozen['data_disk_ids'], set(role_mounts(role))
        require(deployed['instance_id'] == frozen['instance_id'] and
                len(disk_ids) == len(set(disk_ids)) == len(mounts),
                'Original disk mapping requires the exact frozen instance and distinct disk IDs: '+role)
        by_mount, seen = {}, set()
        for disk in deployed['disks']:
            mount, disk_id, serial = disk['mount'], disk['id'], disk['serial']
            require(mount in mounts and mount not in by_mount and disk_id in disk_ids and disk_id not in seen and
                    re.fullmatch(r'd-[a-z0-9]+', disk_id) and serial == disk_id.removeprefix('d-'),
                    'Original disk mapping is ambiguous or outside the frozen set: '+role)
            by_mount[mount] = serial
            seen.add(disk_id)
        require(set(by_mount) == mounts and seen == set(disk_ids),
                'Original disk mapping is incomplete: '+role)
        result[role] = by_mount
    return result


def native_rows(snapshot, disk_mapping, runtime, tool):
    require(snapshot['inventory'] == ref(runtime/'fleet.json') and snapshot['pool'] == POOL and
            {snapshot['inventory']['path'], tool['path']} <= {p['path'] for p in snapshot['source_pins']} and
            tool in snapshot['source_pins'] and set(snapshot['nodes']) == set(ROLES),
            'Original native pool-B snapshot provenance required')
    for p in snapshot['source_pins']:
        pinned(p)
    rows, identity = {}, []
    for role in ROLES:
        n = snapshot['nodes'][role]
        disks = n['disks']
        expected = disk_mapping[role]
        actual = {}
        for d in disks:
            roots = [p for p in expected if Path(d['path']).is_relative_to(p)]
            require(len(roots) == 1 and roots[0] not in actual and integer(d['device']) and
                    integer(d['device_bytes']) and d['device_bytes'] > 0, 'Disk does not match its original mount')
            actual[roots[0]] = d['serial']
        require(actual == expected and len({d['device'] for d in disks}) == len(disks) and
                re.fullmatch(r'[0-9a-f-]{36}', n['boot_id']), 'Wrong boot or owned storage devices: '+role)
        identity.append((role, n['boot_id'], tuple(sorted(
            (d['path'], d['serial'], d['device'], d['device_bytes']) for d in disks))))
        for disk in disks:
            for unit in ('bytes', 'inodes'):
                total, free = disk['total_'+unit], disk['available_'+unit]
                require(integer(total) and integer(free) and 0 <= free <= total and total > 0,
                        'Missing native byte/inode capacity')
                rows[role, disk['path'], unit] = (total, free)
    objects, redis = snapshot['nodes']['objects'], snapshot['nodes']['metadata']['redis']
    limit, used, aof = (redis[k] for k in ('maxmemory', 'used_memory', 'aof_current_size'))
    require(integer(limit) and integer(used) and integer(aof) and 0 <= used <= limit and limit > 0,
            'Missing Redis memory and AOF capacity')
    identity.extend([tuple(objects[k] for k in ('container_id', 'image', 'pid')), redis['process_id'], limit])
    rows['metadata', None, 'redis-memory-bytes'] = (limit, limit-used)
    return rows, tuple(identity), aof


def history_lower_bound(cap, q, cell):
    """The retained On1 sample decrease is only an observed lower bound."""
    require(cap['history']['sha256'] == HISTORY_SHA256, 'Exact retained B On1 inspection required')
    history = load(pinned(cap['history']))['observers'][HISTORY_TAG]
    root = CANONICAL/'calibration'/HISTORY_TAG
    require(cap['history_finished'] == ref(root/'finished.json'), 'Original completed On1 required')
    actual = load(root/'collector.json')
    cohort = load(root/'cohort.json')
    oldq = pinned(cohort['workload_contract']).parent
    require(pinned(cohort['metric_contract']) == oldq/'metrics.json', 'Original historical contract directory required')
    same_contracts(oldq, q)
    _, _, native = trial_finish(dict(out=str(root), attempt=HISTORY_TAG, mode='on', collector_name=actual['name'],
                                    collector_out=actual['out']), oldq, cell, True)
    raw = load(pinned(native['raw_audits'][alias(POOL, 'objects')]))
    require(raw['finished_sha256'] == history['lifecycle']['finished.json']['sha256'] and
            raw['samples'] == history['sample_count'], 'Retained history differs from original native raw audit')
    samples = history['rows']
    require(len(samples) == history['sample_count'] == 150 and all(r['passed'] is True for r in samples),
            'Historical On1 samples are incomplete')
    return {(path, unit): max(0, samples[0]['capacity'][path][unit+'_free'] -
                              min(r['capacity'][path][unit+'_free'] for r in samples))
            for path in role_mounts('objects') for unit in ('bytes', 'inodes')}


def budget_floor(basis):
    """Recompute explicit reviewed arithmetic from pinned native numeric fields.

    The reviewer must justify extrapolation/coverage in rationale. This verifies
    source bytes and arithmetic; it cannot turn historical minima into bounds.
    """
    require(isinstance(basis['rationale'], str) and basis['rationale'].strip() and basis['sources'],
            'Every budget component needs source pins and a coverage rationale')
    sources = [load(pinned(p)) for p in basis['sources']]
    total = 0
    for term in basis['terms']:
        require(integer(term['source']) and term['source'] < len(sources) and
                integer(term['numerator']) and term['numerator'] > 0 and
                integer(term['denominator']) and term['denominator'] > 0 and term['pointer'],
                'Invalid native budget arithmetic')
        value = sources[term['source']]
        for key in term['pointer']:
            value = value[key]
        require(integer(value), 'Budget source must be an actual nonnegative integer field')
        total += (value*term['numerator']+term['denominator']-1)//term['denominator']
    require(total > 0 or (not basis['terms'] and basis.get('not_applicable') is True),
            'Zero budgets need an explicit sourced not-applicable rationale')
    return total


def capacity_basis(s, batch, q, plan, disk_mapping, runtime, mode):
    require(bool(s.get('capacity_qualification')), 'Missing independent full C1 mixed-fio capacity qualification')
    cap = load(pinned(s['capacity_qualification']))
    require(cap['schema'] == 'c1-mixed-fio-capacity-qualification-v2' and
            cap['passed'] is True and cap['reviewed'] is True and
            cap['pool'] == POOL and cap['cell_id'] == CELL and mode in cap['modes'] and
            set(cap['modes']) <= {'off', 'on', 'formal'} and
            cap['plan'] == plan and cap['workload'] == ref(q/'workload.json') and
            cap['metrics'] == ref(q/'metrics.json') and cap['asset'] == ref(q.parent/'assets/juicefs/asset.json') and
            cap['runtime'] == batch.get('runtime_control') and cap['node_tool'] == batch['node_tool'] and
            cap['observer_tool'] == batch['observer_tool'] and cap['tools'] == batch['tools'] and
            cap['reserve_percent'] == 20 and cap['history_is_lower_bound'] is True,
            'Missing exact independent C1 phase and archive basis')
    cell, = [c for c in load(pinned(plan))['cells'] if c['id'] == CELL]
    lower = history_lower_bound(cap, q, cell)
    snapshot = load(pinned(cap['current_snapshot']))
    baseline, ident, aof = native_rows(snapshot, disk_mapping, runtime, s['snapshot_tool'])
    stamp = recent(snapshot['at'], now())
    for role in ROLES:
        require(snapshot['nodes'][role]['boot_id'] == load(runtime/'probes'/alias(POOL, role)/'probe.json')['boot_id'] and
                recent(snapshot['nodes'][role]['sampled_at'], now()) <= stamp,
                'Current capacity identity must match freshly qualified runtime boot')
    budgets = {}
    for row in cap['rows']:
        k = row['role'], row['path'], row['quantity']
        parts = {n: row[n] for n in GROWTH}
        require(k not in budgets and all(map(integer, parts.values())) and set(row['basis']) == set(GROWTH),
                'Unique complete per-disk capacity budget required')
        require(all(parts[n] >= budget_floor(row['basis'][n]) for n in GROWTH),
                'Budget understates its pinned arithmetic basis')
        require(parts['background_margin'] > 0 and parts['missed_peak_margin'] > 0,
                'Background and unobserved peaks require separate positive safety budgets')
        if k[0] == 'objects':
            roots = [p for p in role_mounts('objects') if Path(k[1]).is_relative_to(p)]
            require(len(roots) == 1 and parts['data_peak'] >= lower[roots[0], k[2]] and
                    parts['preparation_margin'] > 0, 'Historical On1 lower bound or preparation blind spot understated')
        if k[0] == 'metadata' and k[2] == 'bytes':
            require(parts['aof_peak'] >= max(1, aof), 'Metadata disk must cover current AOF plus growth/rewrite basis')
        budgets[k] = parts
    coordinator = {('coordinator', None, unit) for unit in ('bytes', 'inodes')}
    require(set(budgets) == set(baseline) | coordinator and all(budgets[k]['archive_peak'] > 0 for k in coordinator),
            'C1 capacity must cover every disk, Redis memory and actual coordinator output')
    if set(cap['modes']) & {'on', 'formal'}:
        for role in ROLES:
            require(any(k[0] == role and k[2] == 'bytes' and budgets[k]['observer_peak'] > 0 and
                        Path(k[1]).is_relative_to(ROOTS[role]) for k in budgets if k[1]),
                    'Missing raw observer budget: '+role)
    return ident, budgets, stamp


def coordinator_rows(out, trial, step):
    devices = set()
    for p in (out, trial, trial/'archives', trial/'raw', step, CONTROL/'state.sqlite'):
        require(p.resolve() == p, 'Noncanonical output or state path')
        while not p.exists():
            p = p.parent
        devices.add(p.stat().st_dev)
    m = mounts(str(CONTROL.parent))
    require(len(m) == 1 and m[0]['filesystem'] == 'ext4' and devices == {CONTROL.parent.stat().st_dev} and
            Path('/').stat().st_dev not in devices, 'Coordinator outputs must all be on the mounted data disk')
    local = os.statvfs(out)
    return {('coordinator', None, 'bytes'): (local.f_blocks*local.f_frsize, local.f_bavail*local.f_frsize),
            ('coordinator', None, 'inodes'): (local.f_files, local.f_favail)}


def capacity_gate(s, snap, disk_mapping, runtime, identity, budgets, done, out, trial, step):
    clock = now()
    rows, ident, aof = native_rows(snap, disk_mapping, runtime, s['snapshot_tool'])
    stamp = recent(snap['at'], clock)
    require(ident == identity and done <= stamp and all(
        done <= recent(n['sampled_at'], clock) <= stamp for n in snap['nodes'].values()),
        'Capacity samples must match qualification identities and follow actual cleanup')
    rows.update(coordinator_rows(out, trial, step))
    result = []
    for (role, path, unit), (total, free) in rows.items():
        growth = sum(budgets[role, path, unit].values())
        minimum = (total + 4)//5
        require(total > 0 and 0 <= free <= total and free-growth >= minimum,
                'C1 would cross 20% reserve on '+str((role, path, unit)))
        result.append(dict(role=role, path=path, quantity=unit, total=total, available=free,
                           growth=growth, reserve=minimum, projected_available=free-growth))
    return dict(rows=result, redis_aof_bytes=aof, at=clock.isoformat())


def controls(s):
    paths = {Path(x) for x in s['lease_controls']}
    require(paths == {p.parent for p in CONTROL.parent.glob('*/state.sqlite')} and {CONTROL, CANONICAL} <= paths,
            'All original coordinator lease databases must be covered')
    return sorted(paths)


def guards(stack, paths, cell, plan):
    seen = set()
    for control in paths:
        folder = (control/'runner-locks').resolve()
        if folder in seen:
            continue
        seen.add(folder)
        stack.enter_context(resource_guard(control, cell, plan))


def idle(paths, cell, plan):
    require(not any(active_conflicts(p, cell, plan) for p in paths), 'A physical resource has an active lease')
    with sqlite3.connect((CONTROL/'state.sqlite').as_uri()+'?mode=ro', uri=True) as db:
        row = db.execute('SELECT status FROM cells WHERE id=?', (CELL,)).fetchone()
        started = db.execute("SELECT 1 FROM stages WHERE cell_id=? AND status!='pending'", (CELL,)).fetchone()
    require(row == ('pending',) and not started and not (CONTROL/'cells'/CELL).exists(),
            'Original formal C1 has started; do not replay')


def substep(directory, command):
    directory.mkdir()
    argv = list(map(str, command))
    write_new(directory/'intent.json', dict(argv=argv, automatic_retry=False))
    with (directory/'stdout').open('xb') as stdout, (directory/'stderr').open('xb') as stderr:
        status = subprocess.run(argv, stdout=stdout, stderr=stderr).returncode
    write_new(directory/'exit.json', dict(exit_code=status))
    require(status == 0, 'Stopped at original substep '+str(directory))
    return ref(directory/'exit.json')


PROBE = '''
import json, pathlib, subprocess, sys
sys.path.insert(0, REPO)
from bench.runtime.common import alive, load, mounts
root = pathlib.Path(ROOT)
bad = []
devices = {}
for point in MOUNT_POINTS:
    matched = mounts(point)
    if len(matched) != 1 or matched[0]['filesystem'] != 'ext4': bad.append('owned data disk not mounted: '+point)
    devices[point] = pathlib.Path(point).stat().st_dev
if len(set(devices.values())) != len(devices) or pathlib.Path('/').stat().st_dev in devices.values():
    bad.append('data disk aliases or root device')
for tag in TAGS:
    states = [(root/'o'/tag, 'ready.json', ('process',))]
    if ROLE == 'client':
        states += [(root/'j'/tag, 'ready.json', ('process',))]
        states += [(root/'m'/(tag+suffix), 'process.json', ('process','supervisor')) for suffix in ('','-p','-v')]
    for state, marker, keys in states:
        if not state.exists(): continue
        if not (state/marker).is_file():
            bad.append(str(state)+': missing identity')
            continue
        original = load(state/marker)
        if any(not original.get(k) or alive(original[k]) for k in keys): bad.append(str(state))
    if ROLE == 'client':
        for p in (root/'j'/tag).glob('job-*/process.json'):
            if alive(load(p)['process']): bad.append(str(p))
if ROLE == 'client':
    if pathlib.Path('/sys/fs/cgroup/ax-slot-1/cgroup.procs').read_text().strip(): bad.append('agent slot')
    if mounts(str(pathlib.Path(ASSET)/'mnt')): bad.append('original asset mount')
actual = None
if ROLE == 'objects' and COLLECTOR:
    fmt = '{"id":{{json .Id}},"running":{{json .State.Running}},"owner":{{json (index .Config.Labels "ax.pilot.owner")}}}'
    actual = json.loads(subprocess.check_output(['docker','inspect','--format',fmt,COLLECTOR['name']],text=True))
    start = load(pathlib.Path(COLLECTOR['out'])/'started.json')
    if (actual['id'] != start['container_id'] or actual['owner'] != COLLECTOR['name'] or
            (RUNNING is not None and actual['running'] is not RUNNING)): bad.append('collector chain')
print(json.dumps(dict(boot_id=pathlib.Path('/proc/sys/kernel/random/boot_id').read_text().strip(),
                      problems=bad,collector=actual,devices=devices)))
raise SystemExit(2 if bad else 0)
'''


def boundary(directory, runtime, asset, tags, current=None, running=None):
    directory.mkdir()
    actual = load(runtime/'fleet.json')
    found = {}
    for role in ROLES:
        n = fleet_node(actual, POOL, role)
        constants = dict(ROOT=ROOTS[role], REPO=n['repo'], ROLE=role, ASSET=asset,
                         TAGS=tags, COLLECTOR=current, RUNNING=running, MOUNT_POINTS=role_mounts(role))
        code = ''.join(k+'='+repr(v)+'\n' for k,v in constants.items())+PROBE
        cmd = ['ssh', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=10', '-F', str(runtime/'ssh.conf'),
               alias(POOL, role), shlex.join([n['python'], '-'])]
        try:
            r = subprocess.run(cmd, input=code, capture_output=True, text=True, timeout=30)
        except subprocess.TimeoutExpired:
            write_new(directory/(role+'.timeout.json'), dict(passed=False, reason='Read-only probe timed out'))
            raise
        (directory/(role+'.stdout')).write_text(r.stdout, encoding='utf-8')
        (directory/(role+'.stderr')).write_text(r.stderr, encoding='utf-8')
        require(r.returncode == 0, 'Pool is not quiescent: '+str(directory/role))
        found[role] = json.loads(r.stdout)
    write_new(directory/'result.json', dict(passed=True, pool=POOL, roles=found, at=now().isoformat()))
    return found


def runtime_args(batch):
    pin = batch.get('runtime_control')
    if not pin:
        return []
    return ['--runtime-control', pinned(pin), '--runtime-control-sha256', pin['sha256']]


def launch(out, batch, cell, number, current):
    cmd = lambda name: [sys.executable, pinned(batch['tools'][name])]
    q = Path(batch['qualification'])
    common = ['--control', CONTROL, '--qualification', q, '--node-tool', batch['node_tool']['path'],
              '--node-tool-sha256', batch['node_tool']['sha256'], '--observer-tool-name', batch['observer_tool']['name'],
              '--observer-tool-sha256', batch['observer_tool']['sha256'], '--collector-name', current['name'],
              *runtime_args(batch)]
    if number < 7:
        t = batch['trials'][number-1]
        argv = [*cmd('calibrate'), *common, '--case', cell['case'], '--product', cell['arm']['product'],
                '--mode', t['mode'], '--attempt', t['attempt'], '--collector-out', current['out'], '--out', t['out']]
        if t.get('new_collector_name'):
            argv += ['--new-collector-name', t['new_collector_name']]
        exit_pin = substep(out/'calibrate', argv)
        finish, actual, trial_pins = trial_finish(t, q/'juicefs', cell, t['mode'] == 'on')
        return dict(exit=exit_pin, finished=finish, collector=actual, trial_pins=trial_pins)
    pairs = out/'pairs.json'
    paths = [t['out'] for t in batch['trials']]
    write_new(pairs, [dict(off=paths[i], on=paths[j]) for i,j in PAIRS])
    substep(out/'check', [*cmd('check'), '--pairs', pairs, '--pool', POOL, '--metric-contract', q/'juicefs/metrics.json',
                         '--workload-contract', q/'juicefs/workload.json', '--out', out/'overhead'])
    formal = CONTROL/'cells'/CELL
    overhead = out/'overhead/result.json'
    substep(out/'admit', [*cmd('admit'), *common, '--cell', CELL, '--owner', batch['owner'],
                         '--latest-check', pinned(batch['latest_check']), '--cross-pool-qualification',
                         pinned(batch['cross_pool_qualification']), '--overhead', overhead, '--out', formal])
    exit_pin = substep(out/'formal', [*cmd('run'), '--context', formal/'context.json'])
    closing = ref(CONTROL/'cells'/(CELL+'.close.json'))
    close = load(pinned(closing))
    with sqlite3.connect((CONTROL/'state.sqlite').as_uri()+'?mode=ro', uri=True) as db:
        state = db.execute('SELECT status,result FROM cells WHERE id=?', (CELL,)).fetchone()
    require(close['cell_id'] == CELL and close['outcome'] == 'complete' and state[0] == 'complete' and
            json.loads(state[1]) == closing, 'No accepted original C1 close')
    return dict(exit=exit_pin, closing=closing, overhead=ref(overhead), collector=current)


def run(request, validate_only=False):
    s = load(pinned(request))
    require(s.get('schema') == 'c1-storage-step-v1' and s['reviewed'] is True,
            'Reviewed explicit B C1 step required')
    batch, out = original_batch(s)
    plan_pin = s['plan']
    require(pinned(plan_pin) == CONTROL/'suite/plan.json', 'Original frozen C1 plan required')
    plan = check_plan(load(pinned(plan_pin)))
    cell, = [c for c in plan['cells'] if c['id'] == CELL]
    require((cell['workload'], cell['case'], cell['arm']['product'], cell['pool']) ==
            ('fio', 'fio-randrw', 'juicefs', POOL), 'Only original mixed C1 on B is eligible')
    q = within(batch['qualification'], CONTROL)/'juicefs'
    controller_basis(s, batch, plan)
    runtime = pilot_context.select(CONTROL, batch.get('runtime_control'))
    require(load(q/'workload.json')['params'] == cell['params'], 'Exact frozen fio required')
    fleet = load(pinned(s['inventory']))
    require(pinned(s['inventory']) == CONTROL/'provenance/inventory.json' and
            s['inventory']['sha256'] == next(c['inventory_sha256'] for c in plan['config']['clusters'] if c['id'] == POOL),
            'Original owned physical inventory required')
    inventory = pool_inventory(fleet, POOL)
    require(set(resource_ids(inventory)) == set(cell_resources(plan, cell)), 'Physical resources changed')
    disk_mapping = deployment_disk_mapping(s, inventory)
    cross = cross_pool_proof(pinned(batch['cross_pool_qualification']), plan, runtime/'fleet.json')
    base_recheck(s, q, cell, runtime)
    latest = load(pinned(batch['latest_check']))
    require(latest['passed'] is True and latest['build_manifest_sha256'] == plan['config']['build_manifest_sha256'],
            'Frozen build lacks its matching latest-branch proof')
    number = s['step']
    current = prefix_chain(s, batch, out, q, cell)
    mode = batch['trials'][number-1]['mode'] if number < 7 else 'formal'
    identity, budgets, qualification_at = capacity_basis(s, batch, q, plan_pin, disk_mapping, runtime, mode)
    current_services(s, cross, runtime)
    done = cleanup(s, now())
    require(qualification_at <= done, 'Independent capacity qualification must close before cleanup')
    paths = controls(s)
    preflight = within(s['preflight_out'], CONTROL.parent/'preparation')
    require(not preflight.exists(), 'A fresh preflight directory is required')
    if validate_only:
        idle(paths, cell, plan)
        return dict(validated=True, launch=False, worker_access=False, step=number)
    asset = load(q/'binding.json')['asset']
    require(re.fullmatch(r'/srv/ax-client/a/[a-z0-9-]+', asset), 'Exact owned client asset required')
    tags = ['cal-pool-b-j-mx-'+ORIGINAL_FAILED_SUFFIX+'-'+label for label in LABELS[:-1]]
    namespace = hashlib.sha256((str(CONTROL)+'\0'+plan_pin['sha256']).encode()).hexdigest()[:12]
    tags += [t['attempt'] for t in batch['trials']] + ['c1-'+namespace+'-'+CELL]
    formal = CONTROL/'cells'/CELL
    trial = Path(batch['trials'][number-1]['out']) if number < 7 else formal
    owner = CONTROL/'one-cell-runs'/(CELL+'.storage-step-owner.json')
    step = out/('step-'+str(number))
    with ExitStack() as stack:
        guards(stack, paths, cell, plan)
        require(paths == controls(s), 'Lease database membership changed while obtaining locks')
        idle(paths, cell, plan)
        require(prefix_chain(s, batch, out, q, cell) == current and not step.exists(), 'Step already started')
        if owner.exists():
            require(load(owner)['original_request'] == s['original_request'],
                    'Another stepwise C1 attempt owns this frozen cell')
        else:
            require(number == 1, 'Missing original attempt owner')
        done = cleanup(s, now())
        preflight.mkdir(parents=True, exist_ok=False)
        write_new(preflight/'request.json', dict(request=request, locked_controls=list(map(str, paths))))
        running = None if number == 1 else batch['trials'][number-2]['mode'] == 'on'
        observed = boundary(preflight/'before', runtime, asset, tags, current, running)
        container = identity[len(ROLES)][0]
        require(re.fullmatch('[0-9a-f]{64}', container), 'Exact RustFS container ID required')
        snapshot = preflight/'snapshot.json'
        substep(preflight/'sample', [sys.executable, pinned(s['snapshot_tool']), 'snapshot', '--control', CONTROL,
                                    '--pool', POOL, '--objects-container', container, '--out', snapshot, *runtime_args(batch)])
        snap = load(snapshot)
        require(all(observed[r]['boot_id'] == snap['nodes'][r]['boot_id'] for r in ROLES), 'Host rebooted at boundary')
        for role in ROLES:
            mounts = observed[role]['devices']
            require(all(any(Path(d['path']).is_relative_to(p) and d['device'] == dev for p,dev in mounts.items())
                        for d in snap['nodes'][role]['disks']), 'Native capacity is not on the proved data mount')
        cap = capacity_gate(s, snap, disk_mapping, runtime, identity, budgets, done, preflight, trial, step)
        write_new(preflight/'result.json', dict(passed=True, request=request, cleanup=s['cleanup'],
                  qualification=s['capacity_qualification'], disk_mapping=s['disk_mapping'],
                  snapshot=ref(snapshot), **cap))
        idle(paths, cell, plan)
        if not owner.exists():
            write_new(owner, dict(original_request=s['original_request'], step1=request))
        step.mkdir(parents=True, exist_ok=False)
        write_new(step/'started.json', dict(request=request, preflight=ref(preflight/'result.json'),
                  at=now().isoformat(), automatic_retry=False))
        try:
            result = launch(step, batch, cell, number, current)
            boundary(step/'closed', runtime, asset, tags, result['collector'], mode != 'off')
            write_new(step/'result.json', dict(schema='c1-storage-step-result-v1', passed=True, step=number,
                      request=request, original_request=s['original_request'], closed=ref(step/'closed/result.json'), **result,
                      at=now().isoformat(), automatic_retry=False, next_step_started=False))
        except Exception as e:
            write_new(step/'failed.json', dict(passed=False, request=request, error=str(e), automatic_retry=False))
            # A failed SSH or native command does not prove the workers stopped.
            # Keep ownership while only checking actual process/mount absence.
            i = 0
            while True:
                i += 1
                try:
                    boundary(step/('failure-boundary-'+str(i)), runtime, asset, tags)
                except (ValueError, OSError, subprocess.TimeoutExpired):
                    time.sleep(10)
                else:
                    break
            raise
    return ref(step/'result.json')


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--request', required=True)
    p.add_argument('--sha256', required=True)
    p.add_argument('--validate-only', action='store_true', help='Read coordinator pins/SQLite only; no worker contact')
    a = p.parse_args()
    print(json.dumps(run(dict(path=a.request, sha256=a.sha256), a.validate_only)))
