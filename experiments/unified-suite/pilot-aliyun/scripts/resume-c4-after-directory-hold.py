#!/usr/bin/env python3
"""Continue one exact C4 step after a real Off1 exit and empty trial-2 fence.

The request selects trial 2..6, or formal, and pins the preceding trials in
order. Only the historical On1 archive recovery can replace a successful
continuation result, through explicit opt-in. Fresh reviewed capacity is
required per step. Failed attempts are preserved and never retried.
"""
import argparse
from contextlib import nullcontext
from datetime import datetime, timezone
import errno
import json
import math
import os
from pathlib import Path
import re
import subprocess
import sys
import tarfile
import time

REPO = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(REPO))
from bench.runtime import c4_verify
from bench.runtime.common import digest, load, pinned, write_new
from bench.runtime.pilot_pools import cell_resources, resource_guard

COLLECTOR = ('collector_name', 'collector_out')
CORE = ('control', 'cell_id', 'asset', 'qualification', 'node_tool', 'observer_tool_name',
        'observer_tool_sha256', 'runtime_control')
WORKERS = ('client', 'metadata', 'objects')
GROWTH = ('data_growth', 'observer_raw', 'archive_peak')
ROOTS = {'client': '/srv/ax-client', 'metadata': '/srv/ax-metadata', 'objects': '/srv/ax-objects-0'}
SCRATCH_ROOT = '/srv/ax-c4-observer-scratch'
SCRATCH_SCHEMA = 'c4-observer-scratch-snapshot-v1'
SCRATCH_TREES = ('o', 'q', 'archives', 'evidence')
RECOVERED_ON1 = 'c4-on1-archival-recovery-v1'


def require(ok, message):
    if not ok:
        raise ValueError(message)


def ref(path):
    p = Path(path).resolve(strict=True)
    return dict(path=str(p), sha256=digest(p))


def tool(path, name):
    import importlib.util
    spec = importlib.util.spec_from_file_location(name, path)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


def current(value):
    return {k: value[k] for k in COLLECTOR}


def trial_body(old, core, number, collector, full, original):
    trial = old['trials'][number-1]
    require(current(trial)==collector, 'Actual collector chain differs from the frozen trial')
    return dict(core, **collector, purpose='observer-calibration-'+trial['mode'],
                attempt=trial['attempt'], out=trial['out'], reviewed=True,
                **original.trial_verification(old['calibration_verification'], number-1, full),
                **{k: trial[k] for k in ('new_collector_name','new_collector_out') if k in trial})


def good_trial(trial, body, finished, pool, cont):
    root = Path(trial['out'])
    end = load(pinned(finished))
    require(finished==ref(root/'finished.json') and load(root/'request.json')==body and
            end['passed'] is True and end['automatic_retry'] is False and end['pool']==pool and
            end['purpose']==body['purpose'], 'Actual closed original calibration evidence required')
    archive = load(pinned(end['archives']))
    roles = set(WORKERS) if trial['mode']=='on' else {'client'}
    require(len(archive)==len(roles) and {Path(row['path']).name for row in archive}=={r+'.tar.gz' for r in roles},
            'Original role archives are missing')
    for key in (*cont.PROOFS, 'cohort'):
        require(load(pinned(end[key]))['passed'] is True, 'Original trial proof did not pass: '+key)
    for row in archive:
        p = Path(row['path'])
        role = p.name.removesuffix('.tar.gz')
        require(p.parent==root/'archives' and p.is_file() and not p.is_symlink() and p.stat().st_size>0,
                'Original role archive not present')
        checked = load(p.parent/(role+'-check.json'))
        require(checked['schema']=='suite-archive-check-v1' and checked['passed'] is True and
                checked['archive']==str(p) and checked['sha256']==row['sha256'] and checked['bytes']==p.stat().st_size and
                checked['kind']==role and checked['cell_id']==body['cell_id'] and checked['deferred']==[],
                'Original verified role archive receipt differs: '+role)
    closed = load(pinned(end['closure']))
    require(closed['all_exited'] is True and closed['all_unmounted'] is True and
            closed['pending_upload_bytes']==closed['pending_upload_operations']==0,
            'Original child did not fully close')
    expected = current(body)
    if 'new_collector_name' in body:
        expected = {k: body['new_'+k] for k in COLLECTOR}
    require(current(end)==expected, 'Completed collector chain changed')
    return end


def historical_on1_format(s, mode):
    """Pin one reader throughout the historical route, including Off steps."""
    if 'historical_on1' not in s:
        return None
    route = s['historical_on1']
    require(isinstance(route, dict) and set(route)=={'receipt','recovery_tool','reader'} and
            s['step']>=3 and s['prefix'][:1]==[route['receipt']],
            'Explicit historical On1 recovery receipt must be the first continuation prefix')
    require(pinned(route['recovery_tool'])==Path(__file__).resolve().with_name('recover-c4-on1-archive.py') and
            pinned(route['reader'])==REPO/'bench/runtime/observe_ndjson.py',
            'Historical On1 requires the pinned local recovery tool and reader')
    overlay = s.get('archive_overlay')
    if mode in ('on','formal'):
        format_spec = (overlay or {}).get('observer_format') or {}
        require(format_spec.get('producer')=='legacy-fold' and format_spec.get('audit_module')==route['reader'],
                'Historical On1 requires the pinned legacy-fold reader; direct NDJSON cannot be mixed')
    else:
        require(not overlay, 'Off steps must not introduce an observer overlay on the historical route')
    return route


def historical_observer_layout(root, attempt):
    """Require the actual archive layout as well as the requested producer."""
    for role in WORKERS:
        directory = root/'raw'/role/'o'/attempt
        end = load(directory/'finished.json')
        stream = directory/'samples.ndjson'
        legacy = directory/'legacy-finished.json'
        require(not end.get('observer_format_sha256'),
                'Historical C4 must not contain direct NDJSON observation: '+role)
        if role=='objects':
            require(end.get('stream') and all(p.is_file() and not p.is_symlink() for p in (stream,legacy)),
                    'Historical C4 objects must retain the original legacy fold')
        else:
            require('stream' not in end and not os.path.lexists(stream),
                    'Historical C4 client and metadata must remain original legacy directories')


def recovered_on1(route, source, old, cell, stop, full, body, cont):
    """Validate the replacement evidence without changing the original exit or root."""
    recovery = tool(pinned(route['recovery_tool']), 'historical_on1_recovery')
    receipt = load(pinned(route['receipt']))
    require(receipt.get('schema')==RECOVERED_ON1 and type(receipt['source_original_exit_code']) is int and
            receipt['source_original_exit_code']==1 and all(receipt[k] is False for k in
                ('automatic_retry','new_measurement','formal_acceptance')),
            'Historical On1 must be archival recovery only, with original exit 1')
    request = receipt['request']
    s = load(pinned(request))
    require(set(s)==recovery.KEYS and s['schema']==recovery.SCHEMA and s['reviewed'] is True and
            s['off1_finished']==stop['first'] and s['plan']==ref(Path(old['control'])/'suite/plan.json') and
            all(receipt[k]==s[k] for k in ('inventory','objects_pack','objects_retained')),
            'Recovered On1 must bind the reviewed archival request, frozen plan and original Off1')
    original = load(pinned(s['original_continuation']))
    original_format = (original.get('archive_overlay') or {}).get('observer_format')
    require(original['original_request']==source and
            (original_format is None or original_format.get('producer')=='legacy-fold'),
            'Recovered On1 must come from this original batch and a legacy observer')
    root, continuation, _, _, _, frozen, inv, context, _, observers, _, lineage = recovery.exact_history(s)
    require(root==Path(body['out']) and frozen==cell and load(root/'request.json')==body and
            context['full_reference']==full and all(receipt[k]==v for k,v in lineage.items()),
            'Recovered On1 does not match the original measured lineage and Off1 proof')
    out = Path(s['out'])
    require(out.is_absolute() and out.resolve(strict=True)==out and out.is_dir() and
            not any(out.is_relative_to(p) or p.is_relative_to(out) for p in
                    (root,continuation,Path(old['control'])/'cells')) and
            route['receipt']==recovery.ref(out/'recovery.json') and
            recovery.source(out/'archive-recovery-request.json')==s,
            'Recovered On1 output must be separate from the original evidence')
    end = recovery.source(out/'finished.json')
    expected = {k:body['new_'+k] for k in COLLECTOR}
    require(end['passed'] is True and end['automatic_retry'] is False and end['pool']==cell['pool'] and
            end['purpose']==body['purpose'] and current(end)==expected and
            end['recovery']==route['receipt'] and end['cohort']==lineage['original_cohort'],
            'Recovered On1 finished evidence or collector differs')
    for key in (*cont.PROOFS,'cohort'):
        require(recovery.proof(end[key])['passed'] is True, 'Recovered On1 proof did not pass: '+key)
    closed = recovery.proof(end['closure'],out/'closure.json')
    require(closed['all_exited'] is True and closed['all_unmounted'] is True and
            closed['pending_upload_bytes']==closed['pending_upload_operations']==0 and
            recovery.proof(end['work'],out/'work-check.json')['passed'] is True,
            'Recovered On1 is missing closed work and zero debt')
    proof_keys = dict(client_verify='cold_child',parent_verify='cold_parent',
                      native_exit='native_exit',verify_exit='verify_exit')
    q = out/'raw/client/q'/body['attempt']
    proofs = dict(client_verify=q/'verify/result.json',parent_verify=q/'verify/parent.json',
                  native_exit=q/'exit.json',verify_exit=q/'verify-exit.json')
    require(all(recovery.ref(proofs[k])==end[k] and end[k]['sha256']==inv['checks'][v]['sha256']
                for k,v in proof_keys.items()) and recovery.proof(end['client_verify'])['full_reference']==full,
            'Recovered On1 cold proofs must be byte-identical and retain the original Off1')
    package, objects = recovery.objects_input(s)
    archives = recovery.proof(end['archives'],out/'archives.json')
    require(len(archives)==len(WORKERS) and archives==receipt['current_archives'] and
            {Path(a['path']).name for a in archives}=={r+'.tar.gz' for r in WORKERS} and
            recovery.source(out/'observers.json')==observers and set(receipt['observer_audits'])==set(WORKERS),
            'Recovered On1 is missing the original role archives or observers')
    historical_observer_layout(out,body['attempt'])
    for row in archives:
        name = Path(row['path']).name
        role = name.removesuffix('.tar.gz')
        original_archive = objects if role=='objects' else inv['roles'][role]
        path = recovery.pin(row,out/'archives'/name)
        check = recovery.source(path.with_name(role+'-check.json'))
        require(check['schema']=='suite-archive-check-v1' and check['passed'] is True and
                check['sha256']==row['sha256']==original_archive['archive']['sha256'] and
                check['archive']==str(path) and check['bytes']==path.stat().st_size and
                check['cell_id']==cell['id'] and check['kind']==role and check['deferred']==[],
                'Recovered On1 role archive changed: '+role)
        observer_dir = out/'raw'/role/'o'/body['attempt']
        finished = recovery.ref(observer_dir/'finished.json')
        audit = receipt['observer_audits'][role]
        require(audit['passed'] is True and audit['finished_sha256']==finished['sha256'],
                'Recovered On1 observer audit does not bind the archived evidence: '+role)
        if role=='objects':
            require(finished['sha256']==package['folded_finished']['sha256'] and
                    recovery.ref(observer_dir/'legacy-finished.json')['sha256']==package['original_finished']['sha256'],
                    'Recovered On1 objects stream must be the original legacy fold')
    return out, end


def first_reference(old, cell, preparation, first):
    t = old['trials'][0]
    proof = load(pinned(first['client_verify']))
    require(proof['verification_policy']==c4_verify.REFERENCE and proof['child_name']==t['attempt'] and
            proof['binding']['asset']['path']==old['asset']+'/asset.json',
            'Original Off1 must supply its full reference')
    binding = dict(cell_id=cell['id'], arm=cell['arm_id'], files=cell['params']['files'], work_tag='child-01',
                   preparation=preparation, asset=proof['binding']['asset'],
                   source_tree=proof['binding']['source_tree'], expected_child=proof['expected_child'])
    c4_verify.reference_identity(proof, binding, old['formal_attempt'])
    return dict(path='/srv/ax-client/q/'+t['attempt']+'/verify/result.json',
                sha256=first['client_verify']['sha256'])


def directory_stop(batch, old, request, original, cont):
    require(load(batch/'locks-held.json')['request']==request, 'Original physical guard did not bind this request')
    stop = batch/'trial-2'
    error = str(FileExistsError(errno.EEXIST, os.strerror(errno.EEXIST), str(stop)))
    failed = load(batch/'failed.json')
    require(stop.is_dir() and not stop.is_symlink() and not any(stop.iterdir()) and
            failed['passed'] is False and failed['automatic_retry'] is False and failed['error']==error,
            'Original parent did not stop at the empty pre-spawn trial-2 directory')
    require(load(batch/'trial-1/exit.json')==dict(exit_code=0), 'Original parent did not reap Off1 with exit 0')
    first = ref(Path(old['trials'][0]['out'])/'finished.json')
    r1 = ref(batch/'trial-1-request.json')
    argv = load(batch/'trial-1/intent.json')
    require(argv==dict(argv=['python3', str(pinned(old['tools']['calibrate'])), '--request', r1['path'],
                            '--sha256', r1['sha256']], automatic_retry=False), 'Original Off1 launch differs')
    for number in range(3,7):
        original.absent(batch/('trial-'+str(number)))
        original.absent(batch/('trial-'+str(number)+'-request.json'))
    for name in ('admit', 'formal', 'result.json', 'overhead', 'route.json'):
        original.absent(batch/name)
    releases = sorted((d for d in batch.glob('failure-release-probe-*') if
                       re.fullmatch(r'failure-release-probe-[1-9][0-9]*',d.name)),
                      key=lambda d: int(d.name.rsplit('-',1)[1]))
    require(releases and releases[-1].is_dir() and not releases[-1].is_symlink(),
            'Original parent failure-release receipts are missing')
    receipts = {}
    for role in cont.ROLES:
        path = releases[-1]
        stdout, status = path/(role+'-stdout'), path/(role+'-exit.json')
        row = load(stdout)
        require(load(status)['exit_code']==0 and row['safe_for_release'] is True and
                not any(row[k] for k in ('live','unknown_startup_directories','slot','mounts')),
                'Original parent did not close physical role '+role)
        receipts[role] = dict(stdout=ref(stdout), exit=ref(status))
    return dict(first=first, trial1_request=r1, trial1_exit=ref(batch/'trial-1/exit.json'),
                failed=ref(batch/'failed.json'), trial2_request=ref(batch/'trial-2-request.json'),
                releases=receipts)


def integer(value):
    return type(value) is int and value>=0


def allocated_bytes(size):
    require(integer(size),'Measured member size must be a nonnegative integer')
    return ((size+4095)//4096)*4096


def observer_floor(pin, role, seconds):
    """Recompute raw byte/inode rate and peak from the closed sample manifests."""
    p = pinned(pin)
    end = load(p)
    require(p.name=='finished.json' and p.parent.parent.name=='o' and
            p.parent.parent.parent.name==role and p.parent.parent.parent.parent.name=='raw' and
            end['passed'] is True and end['samples']==len(end['history'])>=2,
            'A closed original observer sample series is required: '+role)
    sizes = []
    previous = None
    for i,h in enumerate(end['history']):
        sample = p.parent/f'sample-{i:06}'/'sample.json'
        v = load(pinned(dict(path=str(sample),sha256=h['sample_sha256'])))
        start, finish = v['monotonic_start_ns'], v['monotonic_end_ns']
        require(h['index']==v['index']==i and h['passed'] is True and v['passed'] is True and
                integer(start) and integer(finish) and start<=finish and
                (previous is None or 0<start-previous<=30_000_000_000) and
                h['monotonic_start_ns']==start and h['monotonic_end_ns']==finish and bool(v['files']),
                'Closed observer timeline or sample differs: '+role)
        previous = start
        byte_count = allocated_bytes(sample.stat().st_size)
        for name,entry in v['files'].items():
            source = sample.parent/name
            require(source.parent==sample.parent and source.is_file() and not source.is_symlink() and
                    source.stat().st_size==entry['bytes'], 'Original raw sample member size differs: '+role)
            byte_count += allocated_bytes(entry['bytes'])
        sizes.append((byte_count,len(v['files'])+2))
    span = end['history'][-1]['monotonic_end_ns']-end['history'][0]['monotonic_start_ns']
    require(span>0 and seconds*1_000_000_000>=span,
            'Reviewed observer window is shorter than its source capture: '+role)
    floors = {}
    for i,q in enumerate(('bytes','inodes')):
        values = [v[i] for v in sizes]
        rate = (sum(values)*1_000_000_000+span-1)//span
        peak = max(values)
        # Marks add samples beyond the ordinary five-second interval.
        floors[q] = max(rate*seconds,peak*((seconds+4)//5+6)) + (
            allocated_bytes(p.stat().st_size) if i==0 else 2)
    return floors


def archive_floor(pin, role, observed, raw):
    """Use an actual member-verified archive, not a reviewer-supplied size."""
    check_path = pinned(pin)
    check = load(check_path)
    expected = check_path.parent/(role+'.tar.gz')
    require(check_path.name==role+'-check.json' and check_path.parent.name=='archives' and
            check['schema']=='suite-archive-check-v1' and check['kind']==role and check['passed'] is True and
            check['archive']==str(expected) and check['deferred']==[] and
            integer(check['bytes']) and check['bytes']>0 and expected.stat().st_size==check['bytes'],
            'An independently member-verified archive is required: '+role)
    pinned(dict(path=str(expected),sha256=check['sha256']))
    with tarfile.open(expected,'r|gz') as packed:
        member = packed.next()
        require(member is not None and member.isfile() and member.name=='.suite-manifest.json' and
                member.size<=16*1024**2,'Original archive manifest required: '+role)
        manifest = json.load(packed.extractfile(member))
    files = manifest['files']
    require(manifest['schema']=='suite-archive-manifest-v1' and manifest['kind']==role and
            manifest['cell_id']==check['cell_id'] and manifest['deferred']==[] and
            len(files)==check['file_members']>0, 'Original archive manifest differs: '+role)
    observer, remaining, dirs = 0, 0, set()
    count = 0
    for name,meta in files.items():
        p = Path(name)
        require(not p.is_absolute() and '..' not in p.parts and len(p.parts)>=2 and
                p.as_posix()==name and re.fullmatch('[a-f0-9]{64}',meta['sha256']),
                'Unsafe or unverified archive member: '+role)
        amount = allocated_bytes(meta['bytes'])
        if p.parts[0]=='o':
            observer += amount
        else:
            remaining += amount
            count += 1
            dirs.update(p.parents)
    require((observer>0)==observed and count>0, 'Archive observation scope differs: '+role)
    # Retain at least the observed compressed allocation and scale its observed
    # ratio when the reviewed raw window is larger than the source observer tree.
    tar_bytes = allocated_bytes(check['bytes'])
    if observed:
        tar_bytes = allocated_bytes(max(tar_bytes,(tar_bytes*raw['bytes']+observer-1)//observer))
    return dict(bytes=remaining+tar_bytes,inodes=count+len(dirs)+1)


def coordinator_disk(c, out, old, cell, number):
    trial = (Path(old['trials'][number-1]['out']) if number<7 else
             Path(old['control'])/'cells'/cell['id'])
    paths = (out,trial,trial/'archives',trial/'raw')
    require(all(p.is_absolute() and p.resolve()==p and not p.is_symlink() for p in paths),
            'Noncanonical coordinator trial/formal/archive destination')
    path = out.parent.resolve(strict=True)
    targets = {}
    for dest in paths:
        parent = dest
        while not parent.exists():
            parent = parent.parent
        require(parent.is_dir(), 'Coordinator destination ancestor is not a directory')
        targets[str(dest)] = parent.stat().st_dev
    device = path.stat().st_dev
    require(c['path']==str(path) and c['device']==device and c['targets']==targets and
            set(targets.values())=={device},
            'Coordinator budget must cover actual trial/formal, archives and raw on the same device')
    return path,os.statvfs(path)


def cleanup_boundary(pin, request, pool, number, prefix, original, now):
    review = load(pinned(pin))
    actions = review['actions']
    require(review['schema']=='pilot-prestep-cleanup-v1' and review['original_request']==request and
            review['pool']==pool and review['step']==number and review['prefix']==prefix and
            review['outcome'] in ('cleaned','not-needed') and isinstance(actions,list) and
            (review['outcome']=='cleaned')==bool(actions) and len({p['path'] for p in actions})==len(actions),
            'Exact current cleanup receipt with real apply actions or not-needed required')
    done = original.recent(review['finished_at'],now)
    newest = max(Path(p['path']).stat().st_mtime for p in prefix)
    require(done.timestamp()>=newest, 'Cleanup check must follow the last finished step')
    for action in actions:
        applied = load(pinned(action))
        started = load(pinned(applied['request']))
        acted = original.recent(applied['at'],now)
        require(applied['passed'] is True and applied['applied'] is True and bool(applied['groups']) and
                any(g['allocated_bytes']>0 for g in applied['groups']) and started['reviewed'] is True and
                started['schema'] in ('c4-closed-object-staging-reclaim-v1','c1-closed-object-observer-reclaim-v1') and
                newest<=acted.timestamp() and acted<=done,
                'Cleanup action must be a real applied, passed scratch reclaimer before the check')
    return done


def off1_observer_timing(first, old, cell):
    """Independently anchor every observed round to the completed A Off1."""
    trial = old['trials'][0]
    root = Path(trial['out'])
    tag = trial['attempt']
    end = load(pinned(first))
    require(first==ref(root/'finished.json') and end['passed'] is True and
            end['pool']==cell['pool'] and end['purpose']=='observer-calibration-off' and
            end['cohort']==ref(root/'cohort/result.json') and
            end['client_verify']==ref(root/'raw/client/q'/tag/'verify/result.json'),
            'Original completed A Off1 timing sources required')
    drain_pin = ref(root/'drain-timing.json')
    cohort, verify, drain = (load(pinned(p)) for p in (end['cohort'],end['client_verify'],drain_pin))
    require(cohort['passed'] is True and cohort['workload_passed'] is True and
            cohort['purpose']==end['purpose'] and set(cohort['worker_ack_times'])=={'a01'} and
            verify['passed'] is True and verify['cell_id']==cell['id'] and verify['child_name']==tag and
            verify['verification_policy']==c4_verify.REFERENCE and verify['full_cold_child'] is True and
            verify['parent_unchanged'] is True and drain['passed'] is True and drain['cell_id']==cell['id'],
            'Original Off1 measure, drain and complete cold verification must all pass')
    measured = cohort['coordinator_makespan_seconds']
    drained = drain['seconds']
    cold = verify['cold_verification_seconds']
    seconds = (measured,drained,cold)
    require(all(type(v) in (int,float) and math.isfinite(v) and v>0 for v in seconds),
            'Original Off1 timings must be finite and positive')
    for row,value in ((drain,drained),(verify,cold)):
        start,stop = row['start_ns'],row['end_ns']
        require(integer(start) and integer(stop) and start<stop and value==(stop-start)/1e9,
                'Original Off1 monotonic timing does not match its recorded duration')
    ack = cohort['worker_ack_times']['a01']['finished_ack']
    require(integer(ack) and 0<ack<=drain['start_ns'],
            'Original Off1 coordinator ACK does not precede drain')
    # This coordinator-monotonic gap is disjoint from makespan and drain.
    after_ack = (drain['start_ns']-ack)/1e9
    return math.ceil(math.fsum((*seconds,after_ack))), [first,end['cohort'],drain_pin,end['client_verify']]


def scratch_overlay(c):
    """Load the optional dedicated observer-scratch snapshot pinned by capacity."""
    return load(pinned(c['observer_scratch'])) if c.get('observer_scratch') else None


def scratch_disk_rows(scratch, snap, cell, old, number, disks, keys, cleanup, sampled, stamp, original, now):
    """Validate the dedicated object scratch disk and charge this stage against it.

    The scratch sample is appended to the native ``disks``/``keys`` structures, so
    the existing 20% reserve gates judge it exactly like an original data disk.
    Returns the root that owns the object observer/archive/evidence trees.
    """
    disk = scratch.get('disk') or {}
    root = scratch.get('root')
    device = scratch.get('device')
    targets = scratch.get('targets')
    attempt = old['formal_attempt'] if number==7 else old['trials'][number-1]['attempt']
    require(scratch.get('schema')==SCRATCH_SCHEMA and scratch.get('passed') is True and
            scratch.get('pool')==cell['pool'] and scratch.get('role')=='objects' and
            root==SCRATCH_ROOT and scratch.get('tag')==attempt and
            scratch.get('boot_id')==snap['nodes']['objects']['boot_id'] and
            type(device) is int and isinstance(targets,dict) and
            set(targets)=={root+'/'+name for name in SCRATCH_TREES} and
            all(type(v) is int for v in targets.values()) and set(targets.values())=={device} and
            isinstance(scratch.get('at'),str) and isinstance(disk,dict),
            'Dedicated observer scratch snapshot does not match this pool, host, attempt or disk')
    at = original.recent(scratch['at'],now)
    require(cleanup<=at<=stamp and cleanup<=sampled,
            'Observer scratch sample must follow the current cleanup check and precede this stage budget')
    for quantity in ('bytes','inodes'):
        used,total = disk.get('used_'+quantity),disk.get('total_'+quantity)
        require(integer(used) and integer(total) and 0<=used<=total and total>0,
                'Invalid observer scratch capacity sample: '+quantity)
        keys['objects',root,quantity]=(used,total)
    available = {'available_'+q:disk.get('available_'+q) for q in ('bytes','inodes')}
    require(all(integer(v) for v in available.values()), 'Invalid observer scratch available space')
    disks['objects',root]=dict(path=root,device=device,**available)
    return root


def calibration_overlay(overlay, old, trial, scratch):
    """Build the reviewed calibration overlay body, routing objects to the scratch."""
    body = dict(schema='c4-calibration-archive-overlay-v1',reviewed=True,calibrate=old['tools']['calibrate'],
                runner=old['tools']['run'],helper=overlay['helper'],worker_code=overlay['worker_code'],trial=trial)
    if scratch:
        body['observer_scratch']=scratch
    if overlay.get('observer_format'):
        body['observer_format']=overlay['observer_format']
    return body


def stage_capacity(capacity, request, old, prefix, cell, number, mode, batch, runtime, out, archive_mode, original, now,
                   fold_peak_bytes=None):
    """Check native disk identities, reviewed growth and local free space before dispatch."""
    c = load(pinned(capacity))
    legacy_pin = ref(batch/'capacity.json')
    require(c['schema']=='c4-resume-stage-capacity-v1' and c['passed'] is True and c['reviewed'] is True and
            c['original_request']==request and c['legacy_capacity']==legacy_pin and
            c['runtime']==old['runtime_control'] and c['pool']==cell['pool'] and c['cell_id']==cell['id'] and
            c['step']==number and c['prefix']==prefix and c['archive_mode']==archive_mode and
            c['reserve_fraction']==.2, 'Reviewed capacity for this exact next step and completed prefix required')
    legacy = load(pinned(legacy_pin))
    snap = load(pinned(c['snapshot']))
    baseline = load(pinned(legacy['before']))
    previous = load(pinned(legacy['after']))
    observed = mode in ('on','formal')
    archived_roles = set(WORKERS) if observed else {'client'}
    scratch = scratch_overlay(c)
    require(not scratch or observed, 'Observer scratch capacity only applies to an observed on/formal stage')
    observers, archives = c.get('observer_basis',{}),c.get('archive_basis',{})
    require(set(observers)==(archived_roles if observed else set()) and set(archives)==archived_roles and
            all(isinstance(v,list) and v for v in observers.values()),
            'Independent measured observer and archive basis is required for each actual role')
    seconds = c.get('observer_seconds',0)
    require(integer(seconds) and (seconds>0)==observed,'Explicit whole observer window required only for observed stages')
    off1_pins = []
    if observed:
        minimum,off1_pins = off1_observer_timing(prefix[0],old,cell)
        require(seconds>=minimum,'Observer window is shorter than original A Off1 measure, drain and full cold verification: '
                +str(minimum)+' seconds minimum')
    pins = c['source_pins']
    require('cleanup' in c, 'Pre-step cleanup check must be recorded before capacity sampling')
    leaves = [c['snapshot'],c['placement'],c['cleanup'],legacy['before'],legacy['after'],*archives.values(),
              *off1_pins,*(p for sources in observers.values() for p in sources)]
    if scratch:
        leaves.append(c['observer_scratch'])
    require(len({p['path'] for p in leaves})==len(leaves) and all(p in pins for p in leaves) and
            len({p['path'] for p in pins})==len(pins),
            'Original data growth, raw samples and archive peaks require distinct pinned sources')
    for pin in [*pins, *snap['source_pins']]:
        pinned(pin)
    cleanup = cleanup_boundary(c['cleanup'],request,cell['pool'],number,prefix,original,now)
    stamp = original.recent(c['at'], now)
    sampled = original.recent(snap['at'], now)
    require(cleanup<=sampled<=stamp and
            all(cleanup<=original.recent(n['sampled_at'],now)<=sampled for n in snap['nodes'].values()),
            'Every native capacity sample must follow the current cleanup check')
    require(snap['inventory']==ref(runtime/'fleet.json') and snap['pool']==cell['pool'] and
            old['tools']['capacity'] in snap['source_pins'] and
            original.capacity_identity(snap)==original.capacity_identity(previous),
            'Fresh native disk, boot and Redis identities must match the original capacity')
    keys = original.capacity_rows(snap)
    before,after = original.capacity_rows(baseline),original.capacity_rows(previous)
    old_rows = original.row_map(legacy)
    require(legacy['passed'] is True and baseline['inventory']==previous['inventory']==snap['inventory'] and
            baseline['pool']==previous['pool']==cell['pool'] and
            original.capacity_identity(baseline)==original.capacity_identity(previous) and
            set(keys)==set(before)==set(after)==set(old_rows),
            'Independent original before/after data growth identity differs')
    for key,row in old_rows.items():
        growth = row['measured_base_increment']
        require(integer(growth) and growth>=max(0,after[key][0]-before[key][0]),
                'Original data growth is below its native snapshots: '+str(key))
    rows = original.row_map(c)
    path,st = coordinator_disk(c['coordinator'],out,old,cell,number)
    for quantity,total,used,available in (
            ('bytes',st.f_blocks*st.f_frsize,(st.f_blocks-st.f_bfree)*st.f_frsize,st.f_bavail*st.f_frsize),
            ('inodes',st.f_files,st.f_files-st.f_ffree,st.f_favail)):
        keys['coordinator',str(path),quantity]=(used,total)
    disks = {(role,d['path']):d for role,node in snap['nodes'].items() for d in node['disks']}
    # The dedicated scratch disk is sampled by the objects-side preparer, not by
    # the native capacity tool, so charge it here with the same (used,total) shape.
    scratch_root = (scratch_disk_rows(scratch,snap,cell,old,number,disks,keys,cleanup,sampled,stamp,original,now)
                    if scratch else None)
    require(scratch_root is None or archive_mode=='closed-observer-direct',
            'Observer scratch capacity requires the reviewed closed-observer overlay')
    require(set(keys)==set(rows),
            'Stage capacity must cover all native rows, coordinator bytes/inodes and observer scratch rows')
    placement = load(pinned(c['placement']))
    require(c['placement'] in pins and placement['schema']=='c4-archive-device-placement-v1' and
            placement['runtime']==old['runtime_control'] and placement['pool']==cell['pool'] and
            cleanup<=original.recent(placement['at'],now)<=stamp,
            'Fresh native evidence placement required')
    locations = placement['workers']
    require(set(locations)==archived_roles, 'Archive/observer placement must cover the actual roles')
    archive_keys = {'coordinator': str(path)}
    for role, location in locations.items():
        node = snap['nodes'][role]
        dev = disks[role,location['path']]['device']
        root = location.get('root',ROOTS[role])
        require(root==(scratch_root if role=='objects' and scratch_root else ROOTS[role]),
                'Original archive/observer root changed: '+role)
        require(scratch_root is None or role!='objects' or
                (location['path']==scratch_root and dev==scratch['device']),
                'Object placement must name the dedicated scratch disk')
        trees = ['archives','evidence'] + (['o'] if observed else [])
        require(location['boot_id']==node['boot_id'] and location['targets']=={root+'/'+name:dev for name in trees},
                'Original archive/observer target placement changed: '+role)
        # The object archive rows live on the scratch disk when one is pinned.
        archive_keys[role] = root if (role=='objects' and scratch_root) else location['path']

    raw_min, archive_min, raw_budget = {}, {}, {}
    for role in sorted(archived_roles):
        samples = [observer_floor(p,role,seconds) for p in observers.get(role,[])]
        raw_min[role] = {q:max((s[q] for s in samples),default=0) for q in ('bytes','inodes')}
        raw_budget[role] = {q:rows[role,archive_keys[role],q]['observer_raw'] for q in ('bytes','inodes')}
        require(all(integer(x) for x in raw_budget[role].values()),'Invalid projected observer raw allocation: '+role)
        archive_min[role] = archive_floor(archives[role],role,observed,raw_budget[role])
    raw_min['coordinator'] = {q:sum(v[q] for v in raw_budget.values()) for q in ('bytes','inodes')}
    archive_min['coordinator'] = {q:sum(v[q] for v in archive_min.values()) for q in ('bytes','inodes')}
    if fold_peak_bytes is not None:
        require(observed and archive_mode=='closed-observer-direct' and
                type(fold_peak_bytes) is int and fold_peak_bytes>=raw_budget['objects']['bytes'],
                'Reviewed fold peak must cover the object observer raw size')
        # On the worker, the entire replacement stream can coexist with the
        # original raw tree. On the coordinator it can expand beyond that tree.
        raw_min['coordinator']['bytes'] += fold_peak_bytes-raw_budget['objects']['bytes']
    for key,(used,total) in keys.items():
        role, name, quantity = key
        row = rows[key]
        vals = [row[k] for k in GROWTH]
        require(all(type(x) is int and x>=0 for x in vals), 'Invalid capacity growth: '+str(key))
        data, observer, archive = vals
        require(observed or observer==0,'An Off step cannot claim observer raw growth')
        if key in old_rows:
            require(data>=old_rows[key]['measured_base_increment'], 'Stage growth below original measured child: '+str(key))
        if archive_keys.get(role)==name and quantity in ('bytes','inodes'):
            require(observer>=raw_min[role][quantity], 'Observer budget below measured raw rate/peak: '+str(key))
            archive_bound = archive_min[role][quantity]
            if observed and archive_mode=='original' and role!='coordinator':
                archive_bound += observer
            if role=='objects' and quantity=='bytes' and fold_peak_bytes is not None:
                archive_bound += fold_peak_bytes
            require(archive>=archive_bound,'Archive budget below measured staging/tar peak: '+str(key))
        if role=='metadata' and quantity in ('bytes','inodes'):
            field = 'rewrite_headroom_'+quantity
            require(data>=old_rows[key]['measured_base_increment']+old_rows[key].get(field,0),
                    'Metadata growth must cover retained Redis rewrite headroom')
        if quantity=='redis-aof-bytes':
            require(row['included_in_metadata_disk_forecast'] is True and observer==archive==0,
                    'AOF growth must be charged to the actual metadata disk')
            mdisk = snap['nodes']['metadata']['disks'][0]['path']
            require(rows['metadata',mdisk,'bytes']['data_growth']>=data, 'AOF growth exceeds metadata disk forecast')
        else:
            extra = sum(vals)
            require(total>0 and 5*(used+extra)<=4*total, 'Stage would breach the 20% reserve: '+str(key))
            if (role,name) in disks:
                require(5*(disks[role,name]['available_'+quantity]-extra)>=total,
                        'Native available space cannot retain 20% reserve: '+str(key))
            elif role=='coordinator':
                available=st.f_bavail*st.f_frsize if quantity=='bytes' else st.f_favail
                require(5*(available-extra)>=total, 'Coordinator cannot retain 20% archive reserve')
    return c


def substep(out, name, executable, arguments):
    d = out/name
    d.mkdir()
    argv = [sys.executable, str(pinned(executable)), *map(str,arguments)]
    write_new(d/'intent.json', dict(argv=argv, automatic_retry=False))
    with (d/'stdout').open('xb') as stdout, (d/'stderr').open('xb') as stderr:
        code = subprocess.run(argv, stdout=stdout, stderr=stderr).returncode
    write_new(d/'exit.json', dict(exit_code=code))
    require(code==0, 'Inspect original continuation substep '+str(d))
    return ref(d/'exit.json')


def arg_request(path, body):
    write_new(path, body)
    return ['--request', path, '--sha256', digest(path)]


def run(request, validate_only=False):
    s = load(pinned(request))
    require(s['schema']=='c4-after-directory-hold-v1' and s['reviewed'] is True,
            'Reviewed original C4 hold continuation request required')
    cont = tool(pinned(s['continuation_tool']), 'original_c4_continuation')
    original = cont.module(s['original_batch_tool'])
    source = s['original_request']
    old, control, batch, plan, cell, canonical, controls, runtime = cont.batch(source)
    require(cell['pool']=='pool-a' and cell['arm_id']=='native' and cell['params']['files']==100000 and
            old['calibration_verification']==c4_verify.TOUCHED,
            'Only the frozen A 100k native/touched C4 hold is eligible')
    original.trial_order(old['trials'], old['formal_attempt'])
    for p in old['tools'].values():
        pinned(p)
    out = Path(s['out'])
    step = s['step']
    require(type(step) is int and 2<=step<=7, 'Choose exactly one original next stage (2..6 or 7=formal)')
    mode = old['trials'][step-1]['mode'] if step<7 else 'formal'
    original.absent(out)
    formal = control/'cells'/cell['id']
    original.absent(formal)
    for t in old['trials'][step-1:]:
        original.absent(Path(t['out']))
    require(len(s['prefix'])==step-2, 'All prior continuation results, in order, are required')
    overlay = s.get('archive_overlay')
    format_spec = (overlay or {}).get('observer_format')
    historical = historical_on1_format(s, mode)
    # A formal run can route scratch only with the pinned observer-format
    # wrapper at both observe-start and archive.
    require(not scratch_overlay(load(pinned(s['capacity']))) or mode=='on' or mode=='formal' and format_spec,
            'Observer scratch requires an observed calibration or pinned formal overlay')
    q = load(batch/'qualification-request.json')
    reuse = load(batch/'pretrial-reuse.json')
    require(q['reviewed'] is True and reuse['passed'] is True and reuse['base_reimported'] is False and
            reuse['capacity']==ref(batch/'capacity.json') and
            q['fork']==dict(tool=old['fork_tool'],preparation=reuse['preparation']) and
            all(q[k]==old[k] for k in (*CORE,*COLLECTOR)), 'Original once-prepared qualification has changed')
    core = {k:q[k] for k in (*CORE,'fork')}
    checker = cont.module(old['tools']['check'])
    admit = cont.module(old['tools']['admit'])
    with resource_guard(canonical,cell,plan):
        terminal = cont.failed_unit(s['original_unit'],s['original_invocation'],source)
        require(set(controls)=={p.parent for p in canonical.parent.glob('*/state.sqlite')},
                'Must check the actual all-phase lease inventory')
        cont.state_clean(controls,control,cell,plan)
        stop = directory_stop(batch,old,source,original,cont)
        first_body = trial_body(old,core,1,current(old),None,original)
        require(load(pinned(stop['trial1_request']))==first_body, 'Original Off1 body differs')
        end = good_trial(old['trials'][0],first_body,stop['first'],cell['pool'],cont)
        full = first_reference(old,cell,reuse['preparation'],end)
        collector = current(end)
        require(load(pinned(stop['trial2_request']))==trial_body(old,core,2,collector,full,original),
                'Old parent did not generate the exact next request from original Off1')
        prior = []
        calibration_roots = [Path(t['out']) for t in old['trials']]
        for n,p in enumerate(s['prefix'],2):
            body = trial_body(old,core,n,collector,full,original)
            if n==2 and historical:
                calibration_roots[1], end = recovered_on1(historical,source,old,cell,stop,full,body,cont)
                collector = current(end)
                prior.append(p)
                continue
            doc = load(pinned(p))
            require(doc.get('schema')!=RECOVERED_ON1, 'Archived On1 is not an original continuation result')
            prior_request = load(pinned(doc['continuation_request']))
            prior_mode = old['trials'][n-1]['mode']
            require(historical_on1_format(prior_request, prior_mode)==historical,
                    'Historical On1 opt-in and pinned reader must stay the same throughout the prefix')
            prior_dir = Path(prior_request['out'])
            trial_request = ref(prior_dir/'trial.request.json')
            prior_overlay = prior_request.get('archive_overlay')
            if (format_spec or {}).get('producer')=='ndjson' and old['trials'][n-1]['mode']=='on':
                require((prior_overlay or {}).get('observer_format')==format_spec,
                        'Earlier observed calibration did not use this reviewed NDJSON writer')
            executable = prior_overlay['runner'] if prior_overlay else old['tools']['calibrate']
            invoked = ref(prior_dir/'archive-overlay.request.json') if prior_overlay else trial_request
            require(p==ref(prior_dir/'result.json') and doc['exit']==ref(prior_dir/'trial/exit.json') and
                    doc['trial_request']==trial_request and doc['capacity']==prior_request['capacity'] and
                    doc['passed'] is True and doc['step']==prior_request['step']==n and
                    doc['original_request']==prior_request['original_request']==source and
                    doc['first']==stop['first'] and doc['prefix']==prior_request['prefix']==prior and
                    doc.get('historical_on1')==historical and
                    doc['full_reference']==full and doc['automatic_retry'] is False and
                    load(pinned(doc['exit']))==dict(exit_code=0) and load(pinned(trial_request))==body and
                    load(prior_dir/'trial/intent.json')==dict(argv=[sys.executable, executable['path'],
                        '--request',invoked['path'],'--sha256',invoked['sha256']],automatic_retry=False),
                    'Prior step did not follow the original continuation chain')
            end = good_trial(old['trials'][n-1],body,doc['finished'],cell['pool'],cont)
            if historical and prior_mode=='on':
                historical_observer_layout(calibration_roots[n-1],body['attempt'])
            collector = current(end)
            prior.append(p)
        body = trial_body(old,core,step,collector,full,original) if step<7 else None
        require(not overlay or mode in ('on','formal'), 'Direct closed-observer overlay requires active observers')
        format_bundle = format_adapter = archive_source = None
        if overlay:
            archive_runner = tool(pinned(overlay['runner']),'closed_c4_observer_overlay')
            if mode=='formal':
                format_adapter,archive_source = archive_runner.overlay_inputs(
                    old['tools']['run'],overlay['calibration_overlay'],overlay['helper'],overlay['worker_code'])
            else:
                archive_runner.overlay_inputs(old['tools']['calibrate'],old['tools']['run'],
                                              overlay['helper'],overlay['worker_code'])
                format_adapter = archive_runner
            if format_spec:
                format_bundle = format_adapter.format_inputs(format_spec)
        archive_mode = 'closed-observer-direct' if overlay else 'original'
        c = stage_capacity(s['capacity'],source,old,[stop['first'],*prior],cell,step,mode,
                           batch,runtime,out,archive_mode,original,datetime.now(timezone.utc),
                           fold_peak_bytes=(format_bundle or {}).get('fold_peak_bytes'))
        scratch = scratch_overlay(c)
        require((format_bundle or {}).get('producer')!='ndjson' or scratch,
                'Direct NDJSON producer requires reviewed observer scratch')
        result = dict(passed=True, step=step, pool=cell['pool'], cell_id=cell['id'], original_request=source,
                      continuation_request=request, first=stop['first'], full_reference=full, prefix=prior,
                      capacity=s['capacity'], archive_mode=archive_mode, automatic_retry=False)
        if historical:
            result['historical_on1'] = historical
        if format_bundle:
            result['observer_producer'] = format_bundle['producer']
            result['observer_format_sha256'] = format_bundle['audit_module']['sha256']
        if validate_only:
            return dict(result, validation_only=True, worker_dispatched=False)
        out.mkdir(exist_ok=False)
        write_new(out/'request.json',s)
        write_new(out/'original-stop.json',dict(stop, terminal=terminal,
                  resources=sorted(cell_resources(plan,cell)), all_lease_controls=list(map(str,controls))))
        route = dict(schema='c4-fork-route-v1',passed=True,cell_id=cell['id'],pool=cell['pool'],trial=stop['first'],
                     **{k:load(pinned(stop['first']))[k] for k in cont.PROOFS})
        write_new(out/'route.json',route)
        admission = dict(core,reviewed=True,route=ref(out/'route.json'),capacity=ref(batch/'capacity.json'))
        if old.get('cross_pool_qualification'):
            admission['cross_pool_qualification']=old['cross_pool_qualification']
        admit.validate(admission,require_overhead=False)
        if not cont.observe_boundaries(runtime,cell,old,original,out/'before-boundaries',scratch):
            raise ValueError('Original trial process, slot or mount still present; no dispatch')
        try:
            if step<7:
                p = out/'trial.request.json'
                arguments = arg_request(p,body)
                executable = old['tools']['calibrate']
                if overlay:
                    executable = overlay['runner']
                    arguments = arg_request(out/'archive-overlay.request.json',
                        calibration_overlay(overlay,old,ref(p),scratch))
                exit_pin = substep(out,'trial',executable,arguments)
                finished = ref(Path(body['out'])/'finished.json')
                good_trial(old['trials'][step-1],body,finished,cell['pool'],cont)
                if historical and mode=='on':
                    historical_observer_layout(Path(body['out']),body['attempt'])
                result.update(trial_request=ref(p),exit=exit_pin,finished=finished)
            else:
                qual=Path(old['qualification'])/cell['arm_id']
                audit = format_adapter.archive_audit(archive_source,format_bundle,checker) if format_bundle else nullcontext()
                with audit:
                    overhead=checker.check(calibration_roots,qual/'metrics.json',
                                           qual/'workload.json',out/'overhead',old['calibration_verification'])
                body=dict(admission,**collector,out=str(formal),formal_attempt=old['formal_attempt'],overhead=overhead)
                substep(out,'admit',old['tools']['admit'],arg_request(out/'admit.request.json',body))
                if overlay:
                    formal_overlay = out/'formal-archive-overlay.request.json'
                    formal_body = dict(schema='c4-formal-archive-overlay-v1',reviewed=True,
                        wrapper=overlay['runner'],original_runner=old['tools']['run'],
                        calibration_overlay=overlay['calibration_overlay'],helper=overlay['helper'],
                        worker_code=overlay['worker_code'],context=ref(formal/'context.json'),
                        cell_id=cell['id'],pool=cell['pool'],attempt=old['formal_attempt'])
                    if format_spec:
                        formal_body['observer_format'] = format_spec
                    if scratch:
                        formal_body['observer_scratch'] = scratch
                    args = arg_request(formal_overlay,formal_body)
                    substep(out,'formal',overlay['runner'],args)
                    result['formal_archive_overlay']=ref(formal_overlay)
                else:
                    substep(out,'formal',old['tools']['run'],['--context',formal/'context.json'])
                result.update(overhead=overhead, sample=ref(formal/'sample.json'),
                              closing=ref(control/'cells'/(cell['id']+'.close.json')),next_cell=False)
            write_new(out/'result.json',result)
        except Exception as error:
            write_new(out/'failed.json',dict(passed=False,error=str(error),automatic_retry=False))
            n=1
            while not cont.observe_boundaries(runtime,cell,old,original,out/('failure-boundaries-'+str(n)),scratch):
                time.sleep(5)
                n+=1
            raise
    return ref(out/'result.json')


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--request',required=True)
    p.add_argument('--sha256',required=True)
    p.add_argument('--validate-only',action='store_true',help='Coordinator gates and lock only; no worker commands')
    a=p.parse_args()
    print(json.dumps(run(dict(path=a.request,sha256=a.sha256),a.validate_only)))
