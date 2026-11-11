#!/usr/bin/env python3
"""Gracefully stop the exact archived Pool B from the local Aliyun CLI.

Default is read-only preflight. --apply requires a reviewed, SHA-pinned
request; the existing coordinator holds the frozen B locks via SSH. The
Aliyun credential stays local. No delete, detach, restart, or automatic retry.
"""
import argparse
from contextlib import ExitStack, closing, contextmanager
from datetime import datetime, timezone
import importlib.util
import json
import os
from pathlib import Path
import re
import select
import shlex
import shutil
import socket
import sqlite3
import subprocess
import sys
import tarfile
import time

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[3]))
from bench.runtime.pilot_pools import active_conflicts, resource_guard
from bench.suite_plan import check_plan
from bench.archive_check import MANIFEST

spec = importlib.util.spec_from_file_location('pilot_cloud', HERE/'cloud.py')
cloud = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cloud)

CONTROL = Path('/srv/ax-pilot-control/parallel-control-01')
RUNTIME = Path('/srv/ax-pilot-control/fork-control-01/runtime-15-01')
RUNTIME_SHA = 'eb08a7fe5824a6c8d887d660c947aa3f61e760a497b810d8ae1200f12b132ad4'
ALLOWLIST_SHA = '0a7c6e3751f14f3b4d0a45a30d4352bc0e232fc2e39954fec78535ce770f38cf'
ALLOWLIST = HERE.parent/'evidence/preparation/release-pool-b-freeze-01/result.json'
CAMPAIGN = 'ax-pilot-20261111-b'
FAILED_SCHEMA = 'ax-pilot-stop-failed-pool-v1'
C1, C2 = '8e3d340ff23c3ece-r1-juicefs', '05357b5089cd679c-r1-juicefs'
ROLES = ('client', 'metadata', 'objects')
TERMINALS = {
    'ax-pilot-b-c1-mixed-after-c2-01.service': C1,
    'ax-pilot-b-c2-jfs-runtime15-01.service': C2,
    'ax-pilot-b-c2-to-c1-watch-01.service': C2,
}
ACTIVE_OTHER = {'ax-pilot-assets.service', 'ax-pilot-c4-relay-01.service',
                'ax-pilot-pa-c4-flat-reuse-01.service'}
TERMINAL_STATES = {('inactive', 'dead'), ('active', 'exited'), ('failed', 'failed')}
PILOT_SERVICE = re.compile(r'ax-pilot-[a-z0-9-]+\.service')
FIELDS = ('InvocationID', 'ActiveState', 'SubState', 'MainPID', 'ControlPID', 'ControlGroup')


def need(ok, message):
    if not ok:
        raise ValueError(message)


def ref(path):
    return {'path': str(path), 'sha256': cloud.sha(path)}


def pin(value, expected=None):
    path = Path(value['path'])
    need(path.is_absolute() and path.is_file() and path.resolve(strict=True) == path and
         (expected is None or path == expected) and ref(path) == value, 'Evidence changed or misplaced: '+str(path))
    return path


def read(path):
    return json.loads(path.read_bytes())


def recent(value):
    at = datetime.fromisoformat(value)
    need(at.tzinfo is not None and 0 <= (datetime.now(timezone.utc)-at).total_seconds() <= 900,
         'Fresh worker quiescence within 15 minutes required')
    return at


def allowlist(source):
    inventory = read(pin(source))
    need(source['sha256'] == ALLOWLIST_SHA and
         inventory['schema'] == 'ax-pilot-pool-b-release-freeze-public-audit-v1' and
         inventory['campaign'] == CAMPAIGN and inventory['passed'] is True and
         inventory['owned_instance_count'] == 3 and inventory['owned_disk_count'] == 9 and
         set(inventory['roles']) == set(ROLES), 'Exact original Pool B 3-instance/9-disk inventory required')
    rows = inventory['roles']
    resources = {'host:'+row['instance']['id'] for row in rows.values()}
    resources.update('disk:'+disk['id'] for row in rows.values() for disk in row['disks'] if disk['type']=='data')
    return rows, resources, inventory['original_jump_instance_preserved']


def closure(request, plan, cell_id):
    sources = request['cells'][cell_id]
    close = sources['closing']
    base = CONTROL/'cells'/cell_id
    result = read(pin(close, base.with_name(cell_id+'.close.json')))
    need(result['schema']=='suite-cell-close-v1' and result['cell_id']==cell_id and
         result['plan_sha256']==plan['plan_sha256'] and result['outcome']=='complete' and
         result['retention']['root']==str(base), 'Original successful formal closure required: '+cell_id)
    pin(result['sample'], base/'sample.json')
    seal = read(pin(result['retention']['manifest'], base.with_name(cell_id+'.retention.json')))
    need(seal['schema']=='experiment-retention-v1', 'Original formal archive seal required: '+cell_id)
    checks = {'processes': {'all_exited': True}, 'mounts': {'all_unmounted': True},
              'debt': {'pending_upload_bytes': 0, 'pending_upload_operations': 0}}
    native = base/'closure.json'
    for name, equals in checks.items():
        record = result['closure'][name]
        native_result = read(pin(record['evidence'], native))
        need(record['equals']==equals and native_result['cell_id']==cell_id and native_result['passed'] is True and
             all(type(native_result[k]) is type(v) and native_result[k]==v for k,v in equals.items()),
             'Original closure has outstanding work: '+cell_id)
    cal_path = pin(sources['calibration_result'])
    cal = read(cal_path)
    need(cal_path.name=='result.json' and cal_path.parent.parent==CONTROL/'retained-calibrations' and
         cal['passed'] is True and cal['cell_id']==cell_id and
         [t['mode'] for t in cal['trials']]==['off','on','on','off','off','on'] and
         len({t['tag'] for t in cal['trials']})==6 and all(t['passed'] is True and
             set(t['roles'])==(set(ROLES) if t['mode']=='on' else {'client'}) for t in cal['trials']) and
         ref(cal_path.parent/'closing.json')['sha256']==close['sha256'], 'Exact six original calibrations required: '+cell_id)
    archive = {}
    role_pins = {Path(entry['path']).name: entry for entry in result['archives']}
    need(len(result['archives'])==3 and set(role_pins)=={f'{role}.tar.gz' for role in ROLES},
         'All three original role archives required: '+cell_id)
    jobs = [(role, base/'archives'/(role+'-check.json'), role_pins[role+'.tar.gz'],
             base/'archives'/(role+'.tar.gz'), role+'.tar.gz') for role in ROLES]
    cal_check = cal_path.parent.with_name(cal_path.parent.name+'-archive-check.json')
    cal_archive = cal_path.parent.with_suffix('.tar.gz')
    packed = read(pin(sources['calibration_check'], cal_check))
    jobs += [('six-calibration-trials', cal_check, {'path': packed['archive'], 'sha256': packed['sha256']},
              cal_archive, 'calibrations.tar.gz')]
    for kind, check, data, expected, name in jobs:
        path = pin(data, expected)
        if kind in ROLES:
            for member in (path, check):
                entry = seal['files'][member.relative_to(base).as_posix()]
                digest = data['sha256'] if member == path else cloud.sha(member)
                need(entry==dict(bytes=member.stat().st_size,sha256=digest),
                     'Original formal archive seal differs: '+str(member))
        verified = read(check)
        need(verified['schema']=='suite-archive-check-v1' and verified['passed'] is True and
             verified['archive']==str(path) and verified['sha256']==data['sha256'] and verified['cell_id']==cell_id and
             verified['kind']==kind and verified['bytes']==path.stat().st_size and verified['file_members']>0 and
             verified['deferred']==[], 'Coordinator archive not fully verified: '+str(path))
        archive[name] = data
    # The packer places its manifest first. Bind the verified bundle to the
    # exact six trial records without decompressing its bulk evidence again.
    with tarfile.open(cal_archive,'r|gz') as package:
        first=package.next()
        need(first is not None and first.name==MANIFEST and first.isfile() and first.size<=16*1024**2,
             'Original calibration manifest missing: '+cell_id)
        manifest=json.load(package.extractfile(first))
    members=manifest['files']
    need(manifest['schema']=='suite-archive-manifest-v1' and manifest['cell_id']==cell_id and
         manifest['kind']=='six-calibration-trials' and manifest['deferred']==[] and
         members['result.json']['sha256']==sources['calibration_result']['sha256'] and
         members['closing.json']['sha256']==close['sha256'] and
         members['plan.json']['sha256']==request['plan']['sha256'] and
         all(f'{t["tag"]}/{role}{suffix}' in members for t in cal['trials'] for role in t['roles']
             for suffix in ('.tar.gz','-archive-check.json')),
         'Verified calibration bundle does not contain all original trials/roles: '+cell_id)
    return archive


def failed_batch(request, rows, probes):
    """Preserve original failure records; report raw still on stopped disks."""
    entry = request['cells'][C1]
    base = CONTROL/'one-cell-runs'/C1
    request_path = CONTROL/'drafts/pool-b-c1-mixed-after-c2-01/request.json'
    run = read(pin(entry['request'], request_path))
    failed = read(pin(entry['failed'], base/'failed.json'))
    started = read(base/'started.json')
    need(run['reviewed'] is True and run['cell_id']==C1 and run['pool']=='pool-b' and
         run['control']==str(CONTROL) and run['out']==str(base) and
         started['cell_id']==C1 and started['request']==entry['request'] and
         failed['cell_id']==C1 and failed['passed'] is False and failed['automatic_retry'] is False and
         failed['next_cell'] is False and failed['error']=='Stopped; inspect '+str(base/'trial-3'),
         'Exact original mixed C1 trial-3 failure required')
    suffixes = ('off1','on1','on2','off2','off3','on3')
    trials = run['trials']
    tags = ['cal-pool-b-j-mx-mxb01-'+name for name in suffixes]
    need(len(trials)==6 and [t['attempt'] for t in trials]==tags and
         [t['mode'] for t in trials]==['off','on','on','off','off','on'] and
         [t['out'] for t in trials]==[str(CONTROL/'calibration'/tag) for tag in tags],
         'Original frozen C1 trial roster differs')
    pins = {'failed':entry['failed'], 'request':entry['request'], 'started':ref(base/'started.json')}
    for i, t in enumerate(trials[:3],1):
        directory, measured = base/f'trial-{i}', Path(t['out'])
        intent = read(directory/'intent.json')
        argv = intent['argv']
        need(intent['automatic_retry'] is False and argv[1]==run['tools']['calibrate']['path'] and
             all(argv[argv.index('--'+key)+1]==t[field] for key,field in
                 (('mode','mode'),('attempt','attempt'),('out','out'))) and
             read(directory/'exit.json')['exit_code']==(1 if i==3 else 0),
             'Original calibration intent or exit differs: '+t['attempt'])
        for name in ('intent.json','exit.json','stdout','stderr'):
            pins[f'trial-{i}/{name}'] = ref(directory/name)
        if i < 3:
            end = read(measured/'finished.json')
            need(end['passed'] is True and read(pin(end['cohort'],measured/'cohort/result.json'))['passed'] is True,
                 'Original preceding calibration did not pass')
            pins[f'trial-{i}/finished'] = ref(measured/'finished.json')
    last = Path(trials[2]['out'])
    cohort_path = last/'cohort/result.json'
    cohort = read(cohort_path)
    telemetry = last/'cohort/telemetry-failed.json'
    need(cohort['purpose']=='observer-calibration-on' and cohort['passed'] is False and
         cohort['workload_passed'] is True and cohort['telemetry_live_check_passed'] is False and
         cohort['performance_accepted'] is False and read(telemetry)['error_type'] and
         not (last/'finished.json').exists(), 'Original On2 workload/telemetry outcome differs')
    need(all(not p.exists() and not p.is_symlink() for p in
             [*(base/f'trial-{i}' for i in (4,5,6)), *(Path(t['out']) for t in trials[3:]),
              *(base/name for name in ('pairs.json','check','admit','formal','result.json'))]),
         'A later calibration or formal C1 already started')
    pins.update(cohort=ref(cohort_path),telemetry_failure=ref(telemetry))
    raw = []
    roots = {'client':'/srv/ax-client', 'metadata':'/srv/ax-metadata', 'objects':'/srv/ax-objects-0'}
    for role in ROLES:
        paths = []
        for i,tag in enumerate(tags[:3]):
            if role=='client':
                paths += [f'q/{tag}', f'm/{tag}-p', f'm/{tag}', f'w/{tag}', f'j/{tag}']
                if i < 2:
                    paths.append(f'm/{tag}-v')
            if i > 0:
                paths += [f'o/{tag}', f'q/{tag}-observer']
        if role=='objects':
            collector=trials[2]['collector_out']
            need(collector==roots[role]+'/q/'+tags[1]+'-collector', 'Original On2 collector path differs')
            paths.append(collector.removeprefix(roots[role]+'/'))
        raw.append(dict(role=role,instance_id=rows[role]['instance']['id'],boot_id=probes[role][1],
            disk_ids=[d['id'] for d in rows[role]['disks']],paths=[roots[role]+'/'+name for name in paths],
            status='pending',note='Original paths on attached disks; completeness and archive pending.'))
    return pins,raw


def failed_debt(entry):
    samples = entry['debt_samples']
    need(len(samples)==2 and samples[0]!=samples[1], 'Two native zero-debt samples required')
    values = [read(pin(sample)) for sample in samples]
    gauges = {'juicefs_staging_block_bytes','juicefs_staging_blocks','juicefs_staging_writing_blocks'}
    times = [datetime.fromisoformat(sample['at']) for sample in values]
    need(all(t.tzinfo is not None for t in times) and (times[1]-times[0]).total_seconds()>=1 and
         values[1]['monotonic_ns']-values[0]['monotonic_ns']>=1_000_000_000 and
         all(v['passed'] is True and v['cell_id']==C1 and
             {k.split('{',1)[0] for k in v['native_gauges']}==gauges and
             all(type(n) in (int,float) and n==0 for n in v['native_gauges'].values()) and
             all(type(v[k]) is int and v[k]==0 for k in ('pending_upload_bytes','pending_upload_operations'))
             for v in values), 'Original JuiceFS upload debt is not twice proven zero')
    stop = read(pin(entry['unmount']))
    need(stop['passed'] is True and stop['mount_absent'] is True and type(stop['exit_code']) is int and
         stop['exit_code']==0 and stop['active_workers']==[] and stop['original_mount']['point'],
         'Original On2 mount was not gracefully closed')
    return values[-1],times[-1]



def oss_pending(archives, receipts):
    need(set(receipts) <= set(archives), 'Unknown public OSS cell')
    pending = []
    for cell, files in archives.items():
        submitted = receipts.get(cell, {})
        need(set(submitted) <= set(files), 'Unknown public OSS artifact')
        for name, original in files.items():
            if name not in submitted:
                pending.append({'cell_id':cell, 'file':name, 'coordinator_archive':original})
                continue
            result = read(pin(submitted[name]))
            upload = result['upload']
            need(result['schema']=='pilot-cloud-oss-archive-v1' and result['anonymous_public_head'] is True and
                 upload['passed'] is True and upload['full_internal_readback'] is True and
                 upload['source']==original['path'] and upload['readback_sha256']==original['sha256'],
                 'Public OSS receipt does not bind the coordinator archive: '+name)
    return pending


def runtime_probes(source):
    binding=read(pin(source,RUNTIME/'binding.json'))
    need(source['sha256']==RUNTIME_SHA and binding['runtime']==str(RUNTIME) and
         binding['control']==str(RUNTIME.parent), 'Original runtime15 binding required')
    proofs=binding['proofs']
    found={}
    for role in ROLES:
        path=RUNTIME/'probes'/('pool-b-'+role)/'probe.json'
        matches=[p for p in proofs if p['path']==str(path)]
        need(len(matches)==1, 'Original runtime15 probe is not unique: '+role)
        probe=read(pin(matches[0],path))
        need(probe['passed'] is True and probe['checks']['runtime_matches'] is True and
             not probe['runtime_mismatches'], 'Original runtime15 probe did not pass: '+role)
        found[role]=(matches[0],probe['boot_id'])
    return found


def quiescence(request, rows, probes):
    q = read(pin(request['quiescence']))
    at = recent(q['at'])
    failed = request['schema']==FAILED_SCHEMA
    binding = 'failed' if failed else 'closing'
    schema = 'ax-pilot-b-stop-failed-quiescence-v1' if failed else 'ax-pilot-b-stop-quiescence-v1'
    need(q['schema']==schema and q['passed'] is True and q['pool']=='pool-b' and
         q[binding]==request['cells'][C1][binding] and set(q['roles'])==set(ROLES),
         'Exact latest C1 boundary and all three fresh role exits required')
    if failed:
        debt,debt_at=failed_debt(request['cells'][C1])
    else:
        closing=read(pin(q['closing'],CONTROL/'cells'/(C1+'.close.json')))
        debt=read(pin(closing['closure']['debt']['evidence'],CONTROL/'cells'/C1/'closure.json'))
    for role, evidence in q['roles'].items():
        row = read(pin(evidence)); observed = recent(row['at'])
        original, boot=probes[role]
        live=read(pin(row['live_evidence']))
        raw=live['result']
        if failed:
            sync=read(pin(row['sync_evidence'])) if 'sync_evidence' in row else {'exit_code':live.get('sync_exit_code')}
            need(observed>=debt_at and type(sync['exit_code']) is int and sync['exit_code']==0,
                 'Original post-debt sync and live probe required: '+role)
        zeros=('pending_upload_bytes','pending_upload_operations')
        need(observed<=at and row['role']==role and row['instance_id']==rows[role]['instance']['id'] and
             row['original_probe']==original and re.fullmatch('[a-f0-9-]{36}', boot) and row['boot_id']==boot and
             live['at']==row['at'] and live['role']==role and live['boot_id']==boot and
             raw['safe_for_release'] is True and raw['live']==raw['unknown_startup_directories']==raw['mounts']==[] and
             raw['slot']=='' and
             all(row[k] is True for k in ('passed','slot_empty','all_workload_processes_exited','all_mounts_absent')) and
             all(type(row[k]) is type(debt[k]) is int and row[k]==debt[k]==0 for k in zeros) and
             row['source_pins'], 'Worker is not freshly confirmed idle: '+role)
        for source in row['source_pins']:
            pin(source)


def show(unit):
    raw = subprocess.check_output(['systemctl','show',unit,'--all',*['-p'+k for k in FIELDS]],text=True)
    result = dict(line.split('=',1) for line in raw.splitlines())
    need(set(result)==set(FIELDS), 'Incomplete manager state: '+unit)
    return result


def check_services(terminal):
    out = subprocess.check_output(['systemctl','list-units','--all','--plain','--full','--no-legend','--no-pager',
                                   '--type=service','ax-pilot-*.service'],text=True)
    for line in out.splitlines():
        if not line.strip():
            continue
        columns = line.split()
        need(len(columns)>=4 and PILOT_SERVICE.fullmatch(columns[0]), 'Unrecognized pilot manager state')
        unit = columns[0]
        if unit in ACTIVE_OTHER:
            continue
        need(tuple(columns[2:4]) in TERMINAL_STATES, 'Pilot unit still active or unknown: '+unit)
        current = show(unit)
        need((current['ActiveState'],current['SubState']) in TERMINAL_STATES and
             current['MainPID']==current['ControlPID']=='0', 'Pilot unit has a live process: '+unit)
        if unit in terminal and current['InvocationID'] not in ('','0'*32):
            need(current['InvocationID']==terminal[unit], 'Original terminal invocation changed: '+unit)
        if current['ControlGroup']:
            group = Path('/sys/fs/cgroup')/current['ControlGroup'].lstrip('/')
            need(group.name==unit and group.is_relative_to('/sys/fs/cgroup') and group.resolve()==group,
                 'Unsafe pilot cgroup: '+unit)
            if group.exists():
                need('populated 0' in (group/'cgroup.events').read_text().splitlines(), 'Pilot cgroup not empty: '+unit)
    jobs = subprocess.check_output(['systemctl','list-jobs','--plain','--full','--no-legend','--no-pager'],text=True)
    for line in jobs.splitlines():
        if line.strip() and line.strip()!='No jobs running.':
            columns = line.split()
            need(len(columns)>=4 and columns[0].isdigit(), 'Unrecognized pending manager job')
            need(not PILOT_SERVICE.fullmatch(columns[1]) or columns[1] in ACTIVE_OTHER,
                 'Pilot manager job pending: '+columns[1])


def terminal_invocations(request):
    evidence = request['terminal_units']
    need(set(evidence)==set(TERMINALS), 'Exact C2/C1 and handoff watcher terminal receipts required')
    result = {}
    for unit, cell in TERMINALS.items():
        row = read(pin(evidence[unit]))
        failed = request['schema']==FAILED_SCHEMA and cell==C1
        key,code = ('failed',1) if failed else ('closing',0)
        need(row['schema']=='ax-pilot-terminal-unit-v1' and row['passed'] is True and row['unit']==unit and
             row[key]==request['cells'][cell][key] and
             re.fullmatch('[a-f0-9]{32}',row['invocation_id']) and int(row['invocation_id'],16)!=0 and
             row['main_pid']==row['control_pid']==0 and type(row['exit_status']) is int and
             row['exit_status']==code and row['source_pins'],
             'Original terminal service evidence differs: '+unit)
        terminal=read(pin(row['terminal_evidence']))
        manager,start,stop=(terminal[k] for k in ('systemd','start','stop'))
        status=(manager['ActiveState'],manager['SubState'])
        expected=('inactive','dead')
        failed_manager = failed and status==('failed','failed')
        retired_failure = failed and status==expected and manager.get('LoadState')=='not-found'
        status_ok = (status==expected or failed_manager) and (manager['ExecMainStatus']==str(code) or
                     retired_failure and manager['ExecMainStatus']=='0')
        stop_message = unit+": Failed with result 'exit-code'." if failed else unit+': Deactivated successfully.'
        need(status_ok and manager['InvocationID'] in ('','0'*32,row['invocation_id']) and
             manager['MainPID']==str(row['main_pid']) and manager.get('ControlPID','0')==str(row['control_pid']) and
             start['UNIT']==stop['UNIT']==unit and start['INVOCATION_ID']==stop['INVOCATION_ID']==row['invocation_id'] and
             start['_BOOT_ID']==stop['_BOOT_ID'] and start['__CURSOR']!=stop['__CURSOR'] and
             int(start['__MONOTONIC_TIMESTAMP'])<int(stop['__MONOTONIC_TIMESTAMP']) and
             start['MESSAGE'].startswith('Started '+unit+' ') and stop['MESSAGE']==stop_message,
             'Raw manager/journal exit does not match wrapper: '+unit)
        if failed:
            journal = [json.loads(line) for line in pin(row['journal_evidence']).read_bytes().splitlines() if line]
            records = {r['__CURSOR']:r for r in journal}
            keys = ('UNIT','INVOCATION_ID','_BOOT_ID','__MONOTONIC_TIMESTAMP','MESSAGE')
            need(all(rec['__CURSOR'] in records and all(records[rec['__CURSOR']].get(k)==rec[k] for k in keys)
                     for rec in (start,stop)) and
                 any(r.get('UNIT')==unit and r.get('INVOCATION_ID')==row['invocation_id'] and
                     r.get('_BOOT_ID')==start['_BOOT_ID'] and str(r.get('EXIT_STATUS'))=='1' for r in journal) and
                 records[stop['__CURSOR']].get('UNIT_RESULT')=='exit-code',
                 'Failed C1 manager summary does not bind the original JSONL exit')
        for source in row['source_pins']:
            pin(source)
        result[unit] = row['invocation_id']
    return result


def lease_controls(request):
    controls = {Path(p) for p in request['lease_controls']}
    need(CONTROL in controls and controls=={p.parent for p in CONTROL.parent.glob('*/state.sqlite')},
         'All actual lease databases required')
    return sorted(controls)


def unique_guard_controls(controls):
    # Some phase directories share the canonical flock directory by symlink.
    # Keep every phase in lease_controls()/idle(); only deduplicate flocks here.
    paths, identities = set(), set()
    for control in controls:
        directory = (control/'runner-locks').resolve()
        try:
            st = directory.stat()
        except FileNotFoundError:
            identity = None
        else:
            identity = (st.st_dev, st.st_ino)
        if directory in paths or identity is not None and identity in identities:
            continue
        paths.add(directory)
        if identity is not None:
            identities.add(identity)
        yield control


def idle(request, cell, plan, terminal):
    for control in lease_controls(request):
        db = control/'state.sqlite'
        need(db.resolve(strict=True)==db and not active_conflicts(control,cell,plan),
             'Pool B or a shared resource is leased: '+str(control))
    with closing(sqlite3.connect((CONTROL/'state.sqlite').as_uri()+'?mode=ro',uri=True)) as db:
        for cell_id in (C1,C2):
            row = db.execute('SELECT status,result FROM cells WHERE id=?',(cell_id,)).fetchone()
            stages = dict(db.execute('SELECT name,status FROM stages WHERE cell_id=?',(cell_id,)))
            if request['schema']==FAILED_SCHEMA and cell_id==C1:
                need(row==('pending',None) and not stages and not (CONTROL/'cells'/C1).exists() and
                     not (CONTROL/'cells'/(C1+'.close.json')).exists(),
                     'Failed calibration must not have started formal C1')
                continue
            expected = next(c['stages'] for c in plan['cells'] if c['id']==cell_id)
            need(row and row[0]=='complete' and json.loads(row[1])==request['cells'][cell_id]['closing'] and
                 stages==dict.fromkeys(expected,'complete'), 'Formal C1/C2 state not fully closed: '+cell_id)
    check_services(terminal)


def snapshot(client, role, expected):
    resource = expected['instance']['id']
    response = client.api('DescribeInstances', InstanceIds=json.dumps([resource]), PageSize=100)
    instances = response['Instances']['Instance']
    need(response['TotalCount']==len(instances)==1 and instances[0]['InstanceId']==resource,
         'Exact owned instance was not returned: '+resource)
    instance = instances[0]
    tags = {t['TagKey']:t['TagValue'] for t in instance['Tags']['Tag']}
    need(tags.get('campaign')==CAMPAIGN and tags.get('role')==role and
         instance['InstanceChargeType']=='PostPaid' and instance['ZoneId']==expected['instance']['zone'] and
         instance['InstanceType']==expected['instance']['type'] and instance['InternetMaxBandwidthOut']==0,
         'Instance ownership or original private placement differs: '+resource)
    data = client.api('DescribeDisks', InstanceId=resource, PageSize=100)
    disks = data['Disks']['Disk']
    known = {d['id']:d for d in expected['disks']}
    need(data['TotalCount']==len(disks)==len(known) and {d['DiskId'] for d in disks}==set(known),
         'Original attached disk set differs: '+resource)
    for disk in disks:
        frozen = known[disk['DiskId']]
        need(disk['InstanceId']==resource and disk['Type']==frozen['type'] and disk['Size']==frozen['gib'] and
             disk['Status']=='In_use' and disk['DeleteWithInstance'] is True,
             'Original disk no longer preserved as attached: '+disk['DiskId'])
    return dict(at=cloud.now(),resource_id=resource,status=instance['Status'],stopped_mode=instance.get('StoppedMode'),
                preserved_disk_ids=sorted(known),instance_readback_request_id=response['RequestId'],
                disk_readback_request_id=data['RequestId'])


def guardian(request_ref, apply, source, sink):
    def reply(**value):
        print(json.dumps(value),file=sink,flush=True)

    try:
        need(socket.gethostname()=='ax-c1-jump', 'Exact existing coordinator required')
        request=read(pin(request_ref))
        need(request['schema'] in ('ax-pilot-stop-idle-pool-v1',FAILED_SCHEMA) and request['pool']=='pool-b' and
             (not apply or request['reviewed'] is True) and set(request['cells'])=={C1,C2} and
             pin(request['runner'])==Path(__file__).resolve(),
             'Reviewed exact Pool B stop request required for apply')
        rows, resources, _ = allowlist(request['allowlist'])
        plan=check_plan(read(pin(request['plan'],CONTROL/'suite/plan.json')))
        probes=runtime_probes(request['runtime_binding'])
        cell=next(c for c in plan['cells'] if c['id']==C1)
        need(cell['pool']=='pool-b' and set(cell['resource_ids'])==resources, 'Frozen B physical resources differ')
        terminal=terminal_invocations(request)
        idle(request,cell,plan,terminal)
        with ExitStack() as guards:
            for control in unique_guard_controls(lease_controls(request)):
                guards.enter_context(resource_guard(control,cell,plan))
            idle(request,cell,plan,terminal)
            failure = {}
            if request['schema']==FAILED_SCHEMA:
                pins,raw=failed_batch(request,rows,probes)
                failure=dict(c1_outcome='failed',c1_failure_evidence=pins,raw_archive_pending=raw)
                archives={C2:closure(request,plan,C2)}
            else:
                archives={k:closure(request,plan,k) for k in (C1,C2)}
            pending=oss_pending(archives,request.get('public_oss',{}))
            quiescence(request,rows,probes)
            reply(passed=True,state='ready',request=request_ref,runner_sha256=request['runner']['sha256'],
                  apply=apply,public_oss_pending=pending,coordinator_archives=archives,
                  terminal_invocations=terminal,coordinator=socket.gethostname(),**failure)
            for role in ROLES if apply else ():
                command=source.readline()
                if not command:
                    return 2
                need(command==role+'\n','Unexpected stop authorization order')
                idle(request,cell,plan,terminal)
                quiescence(request,rows,probes)
                reply(passed=True,state='authorized',role=role,at=cloud.now())
            if source.readline()!='finish\n':
                return 2
            idle(request,cell,plan,terminal)
            reply(passed=True,state='finished')
            return 0
    except Exception as error:
        reply(passed=False,error=str(error))
        return 2


class GuardianSession:
    def __init__(self, process):
        self.process=process

    def alive(self):
        need(self.process.poll() is None,'Coordinator guardian exited; inspect receipts before any next action')

    def reply(self, timeout):
        need(select.select([self.process.stdout],[],[],timeout)[0], 'Coordinator guardian reply timed out')
        raw=self.process.stdout.readline(1024*1024)
        need(raw.endswith(b'\n'),'Coordinator guardian disconnected or returned an invalid reply')
        result=json.loads(raw)
        need(result['passed'] is True,'Coordinator preflight blocked: '+str(result.get('error')))
        return result

    def send(self, command):
        self.alive()
        self.process.stdin.write((command+'\n').encode('ascii'))
        self.process.stdin.flush()
        return self.reply(30)

    def finish(self):
        need(self.send('finish')['state']=='finished' and self.process.wait(timeout=10)==0,
             'Final coordinator release check failed')


@contextmanager
def remote_guardian(ssh_binary, ssh_config, runner, request_ref, out, apply):
    remote=['nice','-n','15','ionice','-c','3','/usr/bin/python3',runner,'--guardian',
            '--request',request_ref['path'],'--request-sha256',request_ref['sha256']]
    if apply:
        remote.append('--apply')
    cmd=[ssh_binary,'-T','-F',ssh_config,'-oBatchMode=yes','-oStrictHostKeyChecking=yes',
         '-oServerAliveInterval=10','-oServerAliveCountMax=2','ax-pilot-jump',shlex.join(remote)]
    with (out/'guardian.stderr').open('xb') as stderr:
        process=subprocess.Popen(cmd,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=stderr,bufsize=0)
        try:
            yield GuardianSession(process)
        finally:
            process.stdin.close()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
            process.stdout.close()


def execute(client, guard_factory, request_ref, out, apply, timeout=180):
    effects=[]
    try:
        rows, _, jump = allowlist(ref(ALLOWLIST))
        need(client.scope['region']=='cn-hangzhou' and client.scope['reuse_jump_instance']==jump and
             all(row['instance']['id']!=jump for row in rows.values()), 'Coordinator/jump must stay running')
        with guard_factory() as guard:
            ready=guard.reply(900)
            need(ready['state']=='ready' and ready['request']==request_ref and ready['apply'] is apply and
                 ready['runner_sha256']==cloud.sha(__file__) and ready['coordinator']=='ax-c1-jump',
                 'Exact coordinator guardian/request not confirmed')
            cloud.save_new(out/'guardian-ready.json',ready)
            pending=ready['public_oss_pending']
            failure = ({k:ready[k] for k in ('c1_outcome','c1_failure_evidence','raw_archive_pending')}
                       if ready.get('c1_outcome')=='failed' else {})
            before={role:snapshot(client,role,rows[role]) for role in ROLES}
            need(all(v['status']=='Running' for v in before.values()),
                 'Original complete running set required; reconcile any previous partial stop manually')
            preflight=dict(passed=True,mode='apply' if apply else 'dry-run',request=request_ref,pool='pool-b',
                instances=before,preserved_disks=True,released=False,public_oss_pending=pending,
                planned=dict(action='StopInstance',ForceStop=False,StoppedMode='StopCharging'),**failure)
            cloud.save_new(out/'preflight.json',preflight)
            for role in ROLES if apply else ():
                original=before[role]
                current=snapshot(client,role,rows[role])
                need(current['status']=='Running','Instance changed since preflight: '+role)
                authorization=guard.send(role)
                need(authorization['state']=='authorized' and authorization['role']==role,
                     'Coordinator did not authorize this exact role: '+role)
                call=dict(InstanceId=original['resource_id'],ForceStop=False,StoppedMode='StopCharging')
                intent=dict(at=cloud.now(),action='StopInstance',params=call,automatic_retry=False,before=current,
                            guardian_authorization=authorization)
                cloud.save_new(out/(role+'-stop-intent.json'),intent)
                guard.alive()
                effects.append(role)
                reply=client.api('StopInstance',**call)
                deadline=time.monotonic()+timeout
                while True:
                    guard.alive()
                    state=snapshot(client,role,rows[role])
                    if (state['status'],state['stopped_mode'])==('Stopped','StopCharging'):
                        break
                    need(state['status'] in {'Running','Stopping'} and time.monotonic()<deadline,
                         'Graceful StopCharging not confirmed; inspect API receipts: '+role)
                    time.sleep(5)
                proof=dict(schema='ax-pilot-stopcharging-rate-change-raw-v1',passed=True,pool='pool-b',role=role,
                    **state,preserved_disks=True,released=False,last_running=current,stop_request_at=intent['at'],
                    stop_request_id=reply['RequestId'],quote_required_for_cost_event=True)
                cloud.save_new(out/(role+'-rate-change-raw.json'),proof)
            guard.finish()
            if not apply:
                return preflight
            done=dict(passed=True,pool='pool-b',preserved_disks=True,released=False,public_oss_pending=pending,
                      stopped=list(effects),automatic_retry=False,**failure)
            cloud.save_new(out/'result.json',done)
            return done
    except Exception as error:
        report=dict(passed=False,pool='pool-b',mode='apply' if apply else 'dry-run',error=str(error),
                    attempted_stop_roles=effects,released=False,automatic_retry=False)
        cloud.save_new(out/'failed.json',report)
        return report


def ssh_config_path(ssh_config):
    windows=re.fullmatch(r'([A-Za-z]):[/\\](.*)',ssh_config)
    if windows and os.name!='nt':
        return Path('/mnt')/windows[1].lower()/windows[2].replace('\\','/')
    return Path(ssh_config)


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--request',required=True)
    p.add_argument('--request-sha256',required=True)
    p.add_argument('--out',type=Path)
    p.add_argument('--aliyun',default=shutil.which('aliyun'))
    p.add_argument('--ssh-binary',default=shutil.which('ssh'))
    p.add_argument('--ssh-config')
    p.add_argument('--guardian-runner')
    p.add_argument('--guardian',action='store_true',help=argparse.SUPPRESS)
    p.add_argument('--apply',action='store_true',help='Opt in to the three graceful StopCharging requests')
    a=p.parse_args()
    request=dict(path=a.request,sha256=a.request_sha256)
    if a.guardian:
        return guardian(request,a.apply,sys.stdin,sys.stdout)
    if not (a.aliyun and a.ssh_binary and a.ssh_config and ssh_config_path(a.ssh_config).is_file() and a.guardian_runner and
            Path(a.guardian_runner).is_absolute() and a.out and a.out.is_absolute() and a.out.resolve()==a.out):
        p.error('Configured local Aliyun CLI, pinned coordinator script, SSH config and fresh absolute output required')
    os.umask(0o077)
    a.out.mkdir(mode=0o700)
    result=execute(cloud.Cloud(a.aliyun,a.out),
                   lambda:remote_guardian(a.ssh_binary,a.ssh_config,a.guardian_runner,request,a.out,a.apply),request,a.out,a.apply)
    print(json.dumps(result,indent=2))
    return 0 if result['passed'] else 2


if __name__=='__main__':
    raise SystemExit(main())
