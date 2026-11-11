#!/usr/bin/env python3
"""Continue one exact C4 step after a real Off1 exit and empty trial-2 fence.

The request selects trial 2..6, or formal, and pins each prior successful
continuation result in order. Fresh reviewed capacity is required per step.
No failed or existing attempt is retried and the old fence remains untouched.
"""
import argparse
from datetime import datetime, timezone
import errno
import json
import os
from pathlib import Path
import re
import subprocess
import sys
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


def stage_capacity(capacity, request, old, prefix, cell, number, mode, batch, runtime, out, archive_mode, original, now):
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
    previous = load(pinned(legacy['after']))
    pins = c['source_pins']
    require(c['snapshot'] in pins and len({p['path'] for p in pins})==len(pins)>=2,
            'Fresh native sample and independent growth/placement basis required')
    for pin in [*pins, *snap['source_pins']]:
        pinned(pin)
    stamp = original.recent(c['at'], now)
    sampled = original.recent(snap['at'], now)
    require(sampled <= stamp and all(original.recent(n['sampled_at'],now)<=sampled for n in snap['nodes'].values()) and
            snap['inventory']==ref(runtime/'fleet.json') and snap['pool']==cell['pool'] and
            old['tools']['capacity'] in snap['source_pins'] and
            original.capacity_identity(snap)==original.capacity_identity(previous),
            'Fresh native disk, boot and Redis identities must match the original capacity')
    newest = max(Path(p['path']).stat().st_mtime for p in prefix)
    require(sampled.timestamp()>=newest, 'Native capacity must be sampled after the last finished step')
    keys = original.capacity_rows(snap)
    old_rows = original.row_map(legacy)
    rows = original.row_map(c)
    coordinator = c['coordinator']
    path = out.parent.resolve(strict=True)
    st = os.statvfs(path)
    require(coordinator['path']==str(path) and coordinator['device']==path.stat().st_dev,
            'Capacity does not cover the current coordinator output device')
    for quantity,total,used,available in (
            ('bytes',st.f_blocks*st.f_frsize,(st.f_blocks-st.f_bfree)*st.f_frsize,st.f_bavail*st.f_frsize),
            ('inodes',st.f_files,st.f_files-st.f_ffree,st.f_favail)):
        keys['coordinator',str(path),quantity]=(used,total)
    require(set(keys)==set(rows), 'Stage capacity must cover all native rows and coordinator bytes/inodes')
    disks = {(role,d['path']):d for role,node in snap['nodes'].items() for d in node['disks']}
    observed = mode in ('on','formal')
    placement = load(pinned(c['placement']))
    require(c['placement'] in pins and placement['schema']=='c4-archive-device-placement-v1' and
            placement['runtime']==old['runtime_control'] and placement['pool']==cell['pool'] and
            original.recent(placement['at'],now)<=stamp,
            'Fresh native evidence placement required')
    locations = placement['workers']
    archived_roles = {'coordinator',*WORKERS} if observed else {'client','coordinator'}
    require(set(locations)==archived_roles-{'coordinator'}, 'Archive/observer placement must cover the actual roles')
    archive_keys = {'coordinator': str(path)}
    for role, location in locations.items():
        node = snap['nodes'][role]
        dev = disks[role,location['path']]['device']
        root = {'client':'/srv/ax-client','metadata':'/srv/ax-metadata','objects':'/srv/ax-objects-0'}[role]
        trees = ['archives','evidence'] + (['o'] if observed else [])
        require(location['boot_id']==node['boot_id'] and location['targets']=={root+'/'+name:dev for name in trees},
                'Original archive/observer target placement changed: '+role)
        archive_keys[role]=location['path']
    for key,(used,total) in keys.items():
        role, name, quantity = key
        row = rows[key]
        vals = [row[k] for k in GROWTH]
        require(all(type(x) is int and x>=0 for x in vals), 'Invalid capacity growth: '+str(key))
        data, observer, archive = vals
        if role in ('metadata','objects'):
            require(data>=old_rows[key]['measured_base_increment'], 'Stage growth below original measured child: '+str(key))
        if archive_keys.get(role)==name and quantity in ('bytes','inodes'):
            require(archive>0 and (not observed or observer>0), 'Observer/raw and archive peaks are required: '+str(key))
            if observed and archive_mode=='original' and role!='coordinator':
                require(archive>=observer, 'Original archive duplicates observer scratch: '+str(key))
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
    original.absent(out)
    formal = control/'cells'/cell['id']
    original.absent(formal)
    for t in old['trials'][step-1:]:
        original.absent(Path(t['out']))
    require(len(s['prefix'])==step-2, 'All prior continuation results, in order, are required')
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
        for n,p in enumerate(s['prefix'],2):
            doc = load(pinned(p))
            body = trial_body(old,core,n,collector,full,original)
            prior_request = load(pinned(doc['continuation_request']))
            prior_dir = Path(prior_request['out'])
            trial_request = ref(prior_dir/'trial.request.json')
            prior_overlay = prior_request.get('archive_overlay')
            executable = prior_overlay['runner'] if prior_overlay else old['tools']['calibrate']
            invoked = ref(prior_dir/'archive-overlay.request.json') if prior_overlay else trial_request
            require(p==ref(prior_dir/'result.json') and doc['exit']==ref(prior_dir/'trial/exit.json') and
                    doc['trial_request']==trial_request and doc['capacity']==prior_request['capacity'] and
                    doc['passed'] is True and doc['step']==prior_request['step']==n and
                    doc['original_request']==prior_request['original_request']==source and
                    doc['first']==stop['first'] and doc['prefix']==prior_request['prefix']==prior and
                    doc['full_reference']==full and doc['automatic_retry'] is False and
                    load(pinned(doc['exit']))==dict(exit_code=0) and load(pinned(trial_request))==body and
                    load(prior_dir/'trial/intent.json')==dict(argv=[sys.executable, executable['path'],
                        '--request',invoked['path'],'--sha256',invoked['sha256']],automatic_retry=False),
                    'Prior step did not follow the original continuation chain')
            end = good_trial(old['trials'][n-1],body,doc['finished'],cell['pool'],cont)
            collector = current(end)
            prior.append(p)
        body = trial_body(old,core,step,collector,full,original) if step<7 else None
        mode = old['trials'][step-1]['mode'] if step<7 else 'formal'
        overlay = s.get('archive_overlay')
        require(not overlay or mode in ('on','formal'), 'Direct closed-observer overlay requires active observers')
        if overlay:
            archive_runner = tool(pinned(overlay['runner']),'closed_c4_observer_overlay')
            if mode=='formal':
                archive_runner.overlay_inputs(old['tools']['run'],overlay['calibration_overlay'],
                                              overlay['helper'],overlay['worker_code'])
            else:
                archive_runner.overlay_inputs(old['tools']['calibrate'],old['tools']['run'],
                                              overlay['helper'],overlay['worker_code'])
        archive_mode = 'closed-observer-direct' if overlay else 'original'
        stage_capacity(s['capacity'],source,old,[stop['first'],*prior],cell,step,mode,
                       batch,runtime,out,archive_mode,original,datetime.now(timezone.utc))
        result = dict(passed=True, step=step, pool=cell['pool'], cell_id=cell['id'], original_request=source,
                      continuation_request=request, first=stop['first'], full_reference=full, prefix=prior,
                      capacity=s['capacity'], archive_mode=archive_mode, automatic_retry=False)
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
        if not cont.observe_boundaries(runtime,cell,old,original,out/'before-boundaries'):
            raise ValueError('Original trial process, slot or mount still present; no dispatch')
        try:
            if step<7:
                p = out/'trial.request.json'
                arguments = arg_request(p,body)
                executable = old['tools']['calibrate']
                if overlay:
                    executable = overlay['runner']
                    arguments = arg_request(out/'archive-overlay.request.json',
                        dict(schema='c4-calibration-archive-overlay-v1',reviewed=True,calibrate=old['tools']['calibrate'],
                             runner=old['tools']['run'],helper=overlay['helper'],worker_code=overlay['worker_code'],trial=ref(p)))
                exit_pin = substep(out,'trial',executable,arguments)
                finished = ref(Path(body['out'])/'finished.json')
                good_trial(old['trials'][step-1],body,finished,cell['pool'],cont)
                result.update(trial_request=ref(p),exit=exit_pin,finished=finished)
            else:
                qual=Path(old['qualification'])/cell['arm_id']
                overhead=checker.check([Path(t['out']) for t in old['trials']],qual/'metrics.json',
                                       qual/'workload.json',out/'overhead',old['calibration_verification'])
                body=dict(admission,**collector,out=str(formal),formal_attempt=old['formal_attempt'],overhead=overhead)
                substep(out,'admit',old['tools']['admit'],arg_request(out/'admit.request.json',body))
                if overlay:
                    formal_overlay = out/'formal-archive-overlay.request.json'
                    args = arg_request(formal_overlay,dict(schema='c4-formal-archive-overlay-v1',reviewed=True,
                        wrapper=overlay['runner'],original_runner=old['tools']['run'],
                        calibration_overlay=overlay['calibration_overlay'],helper=overlay['helper'],
                        worker_code=overlay['worker_code'],context=ref(formal/'context.json'),
                        cell_id=cell['id'],pool=cell['pool'],attempt=old['formal_attempt']))
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
            while not cont.observe_boundaries(runtime,cell,old,original,out/('failure-boundaries-'+str(n))):
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