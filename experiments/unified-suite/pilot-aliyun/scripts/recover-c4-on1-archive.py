#!/usr/bin/env python3
"""Reassemble only the original, measured A C4 On1 calibration after archive failure.

Run on the coordinator with the original files still present and a reviewed
recovery request. See --help for the request keys. No SSH, measurements, state
database writes, service changes or updates to the original trial.
"""
import argparse
from datetime import datetime
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import sys
import tarfile

REPO = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(REPO))
from bench import archive_check, suite_validate
from bench.runtime import c4_verify, observe, observe_ndjson
from bench.runtime.common import digest, load, write_new
from bench.suite_plan import check_plan

SCHEMA = 'c4-on1-archive-recovery-request-v1'
CELL = 'c5e97774bcffa69a-r1-native'
TAG = 'c4-a-flat-on1'
POOL = 'pool-a'
PURPOSE = 'observer-calibration-on'
ROLES = ('client', 'metadata', 'objects')
BOUNDARIES = (*ROLES, 'objects-scratch')
SCRATCH = '/srv/ax-c4-observer-scratch'
ROLE_ROOTS = dict(client='/srv/ax-client', metadata='/srv/ax-metadata', objects=SCRATCH)
OBS = f'o/{TAG}'
OBS_SPEC = f'q/{TAG}-observer'
CLOSE = f'q/{TAG}-closure/result.json'
OBJECT_LEAVES = {f'{OBS_SPEC}/{name}' for name in (
    'observer.json', 'service-identities.json', 'rustfs-probe.txt', 'collector-probe.txt')} | {CLOSE}
FROZEN_MARKS = ['measure-start', 'measure-end', 'drain-end', 'verify-end']
KEYS = {'schema', 'reviewed', 'original_continuation', 'inventory', 'objects_pack', 'objects_retained', 'plan', 'off1_finished', 'out'}


def require(ok, reason):
    if not ok:
        raise ValueError(reason)


def ref(path):
    p = Path(path)
    require(p.is_absolute() and p.is_file() and p.resolve(strict=True) == p,
            'Original regular canonical file required: '+str(p))
    return dict(path=str(p), sha256=digest(p))


def pin(value, expected=None):
    p = Path(value['path'])
    require(expected is None or p == expected, 'Misplaced original evidence: '+str(p))
    require(ref(p)['sha256'] == value['sha256'], 'Changed pinned evidence: '+str(p))
    if 'bytes' in value:
        require(type(value['bytes']) is int and p.stat().st_size == value['bytes'],
                'Changed evidence length: '+str(p))
    return p


def proof(value, expected=None):
    return load(pin(value, expected))


def source(path):
    return proof(ref(path), path)


def tool(original_batch, name):
    """Use the local frozen validator only when its bytes equal the old pin."""
    p = Path(__file__).with_name(name+'-c4.py')
    require(ref(p)['sha256'] == original_batch['tools'][name]['sha256'],
            'Local frozen '+name+' validator differs from the original tool')
    spec = importlib.util.spec_from_file_location('original_c4_'+name, p)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


def absence(root, continuation):
    for p in (root/'finished.json', continuation/'result.json'):
        require(not os.path.lexists(p), 'Original already finished; cannot recover: '+str(p))


def exact_history(s):
    continuation = proof(s['original_continuation'])
    continuation_root = Path(continuation['out'])
    require(Path(s['original_continuation']['path']) == continuation_root/'request.json' and
            continuation['schema']=='c4-after-directory-hold-v1' and continuation['reviewed'] is True and
            type(continuation['step']) is int and continuation['step']==2 and continuation['prefix']==[],
            'Reviewed historical On1 continuation required')
    batch = proof(continuation['original_request'])
    order = batch['trials']
    require(len(order)==6 and tuple(t['mode'] for t in order)==('off','on','on','off','off','on') and
            len({t['attempt'] for t in order})==len({t['out'] for t in order})==6 and
            batch['cell_id']==CELL and order[1]['attempt']==TAG and batch['calibration_verification']==c4_verify.TOUCHED,
            'Original six-trial calibration recipe differs')
    root = Path(order[1]['out'])
    absence(root, continuation_root)
    inv = proof(s['inventory'])
    require(inv['schema']=='c4-on1-retained-input-inventory-v1' and inv['cell_id']==CELL and
            inv['original_trial']==str(root) and inv['accepted'] is False and inv['original_finished_exists'] is False,
            'Historical readback inventory differs')
    plan_path = pin(s['plan'], Path(batch['control'])/'suite/plan.json')
    plan = check_plan(load(plan_path))
    cell = next(c for c in plan['cells'] if c['id']==CELL)
    require(cell['workload']=='fork' and cell['pool']==POOL and cell['arm_id']=='native' and
            cell['observation_marks']==FROZEN_MARKS, 'Frozen C4 cell differs')
    stage = continuation_root/'trial'
    command = root/'commands/0032'
    failure = ref(continuation_root/'failed.json')
    trial_exit = pin(inv['original_trial_exit'], stage/'exit.json')
    commands = sorted((root/'commands').iterdir())
    require([p.name for p in commands]==[f'{i:04}' for i in range(33)] and all(
            source(p/'exit.json')=={'exit_code': int(p==command)} for p in commands),
            'Archive must be the sole failed historical command')
    for n, row in inv['late_commands'].items():
        require(load(pin(row, root/'commands'/n/'exit.json'))=={'exit_code': int(n=='0032')},
                'Historical late command exit differs')
    archive_err = pin(inv['failure'], command/'stderr')
    trial_stderr = ref(stage/'stderr')
    remote = source(command/'intent.json')
    archive_error = archive_err.read_text(encoding='utf-8')
    require(proof(failure)==dict(passed=False,error='Inspect original continuation substep '+str(stage),
                                 automatic_retry=False) and load(trial_exit)=={'exit_code':1} and
            Path(trial_stderr['path']).read_text(encoding='utf-8').rstrip().endswith('Inspect failed remote command: '+str(command)) and
            archive_error.rstrip().endswith('ValueError: unbounded internal manifest') and
            'bench/archive_check.py' in archive_error and 'in pack_mapped' in archive_error and
            remote['role']=='objects' and remote['argv'][:2]==['python3','-c'] and
            f'TAG={TAG!r}\n' in remote['argv'][2] and f'ROOT={SCRATCH!r}\n' in remote['argv'][2],
            'Not the original objects manifest limit failure')
    stage_trial = ref(continuation_root/'trial.request.json')
    overlay = ref(continuation_root/'archive-overlay.request.json')
    overlay_body = proof(overlay)
    stage_intent = source(stage/'intent.json')
    argv = stage_intent['argv']
    require(stage_intent['automatic_retry'] is False and len(argv)==6 and
            Path(argv[0]).is_absolute() and argv[1:]==[continuation['archive_overlay']['runner']['path'],
                '--request', overlay['path'], '--sha256', overlay['sha256']] and overlay_body['trial']==stage_trial,
            'Original calibration launch or archive overlay differs')
    request = source(root/'request.json')
    context = source(root/'context.json')
    require(proof(stage_trial)==request and request['reviewed'] is True and request['purpose']==PURPOSE and
            request['attempt']==TAG and request['out']==str(root) and request['cell_id']==CELL and
            request['control']==batch['control'] and request['asset']==batch['asset'] and
            request['verification_policy']==c4_verify.TOUCHED and request['full_reference'] and
            context==dict(request, collector_name=order[1]['new_collector_name'], collector_out=order[1]['new_collector_out']) and
            all(request['new_'+k]==context[k] for k in ('collector_name','collector_out')),
            'Original request, runtime collector or touched-cold binding differs')
    off1 = proof(s['off1_finished'], Path(order[0]['out'])/'finished.json')
    off1_pin = off1['client_verify']
    original_full = proof(off1_pin, Path(order[0]['out'])/'raw/client/q'/order[0]['attempt']/'verify/result.json')
    require(off1['passed'] is True and off1['purpose']=='observer-calibration-off' and off1['pool']==POOL and
            context['full_reference']==dict(path=f"/srv/ax-client/q/{order[0]['attempt']}/verify/result.json",
                                           sha256=off1_pin['sha256']),
            'On1 must keep its original Off1 cold reference')
    observ = source(root/'observers.json')
    observ_ids = [o['id'] for o in observ]
    require(len(observ_ids)==3 and set(observ_ids)=={f'{POOL}-{r}' for r in ROLES} and
            all(o['directory']==ROLE_ROOTS[o['id'].removeprefix(POOL+'-')]+'/'+OBS for o in observ),
            'Historical On1 did not contain the exact three observers')
    cohort_req = source(root/'cohort.json')
    cohort_pin = ref(pin(inv['checks']['cohort'], root/'cohort/result.json'))
    co = proof(cohort_pin)
    require(cohort_req['purpose']==co['purpose']==PURPOSE and cohort_req['observers']==observ and
            co['passed'] is True and co['workload_passed'] is True and co['telemetry_live_check_passed'] is True and
            co['performance_accepted'] is False and co['automatic_retry'] is False and
            co['agents']==co['ready_workers']==co['completed_workers']==co['completed_jobs']==1 and
            co['coordinator_makespan_seconds']>0 and all(
                co[k+'_sha256']==cohort_req[k]['sha256'] for k in ('metric_contract','workload_contract')),
            'Historical cohort did not finish its measured work and live checks')
    for k in ('metric_contract', 'workload_contract'):
        name = {'metric_contract':'metrics.json','workload_contract':'workload.json'}[k]
        pin(cohort_req[k], Path(context['qualification'])/'native'/name)
    drain = proof(inv['checks']['drain'], root/'drain-timing.json')
    require(drain['passed'] is True and drain['cell_id']==CELL and drain['seconds']>0 and
            drain['end_ns']>drain['start_ns'], 'Original measured drain interval missing')
    boundaries = {}
    boundary_pins = {}
    require(set(inv['post_failure_boundaries'])==set(BOUNDARIES), 'Missing original terminal probes')
    for role in BOUNDARIES:
        source_path = pin(inv['post_failure_boundaries'][role], continuation_root/'failure-boundaries-1'/(role+'-stdout'))
        status = ref(source_path.with_name(role+'-exit.json'))
        row = load(source_path)
        require(proof(status)=={'exit_code':0} and row['safe_for_release'] is True and
                not any(row[k] for k in ('live','unknown_startup_directories','slot','mounts')) and
                Path(failure['path']).stat().st_mtime_ns<=source_path.stat().st_mtime_ns<=Path(status['path']).stat().st_mtime_ns,
                'Historical terminal process/mount boundary differs: '+role)
        boundaries[role] = row
        boundary_pins[role] = dict(stdout=ref(source_path), exit=status)
    lineage = dict(original_trial=ref(root/'context.json'), original_cohort=cohort_pin,
                   original_failure=failure, original_exit=ref(trial_exit), trial_stderr=trial_stderr,
                   original_command_error=ref(archive_err), original_boundaries=boundary_pins)
    return root, continuation_root, batch, plan_path, plan, cell, inv, context, original_full, observ, boundaries, lineage


def permitted(role, name):
    roots = [OBS, OBS_SPEC, f'q/{TAG}-closure']
    if role=='client':
        roots += [f'q/{TAG}', f'm/{TAG}', f'm/{TAG}-v', f'w/{TAG}', f'j/{TAG}']
    return any(name.startswith(p+'/') for p in roots)


def archive_input(role, row, original_root):
    old = original_root/'archives' if role!='objects' else None
    a = pin(row['archive'], old/(role+'.tar.gz') if old else None)
    receipt = proof(row['archive_check'], old/(role+'-check.json') if old else None)
    require(receipt['schema']=='suite-archive-check-v1' and receipt['passed'] is True and receipt['deferred']==[] and
            receipt['kind']==role and receipt['cell_id']==CELL and receipt['archive']==row.get('worker_archive',str(a)) and
            receipt['bytes']==a.stat().st_size and receipt['sha256']==row['archive']['sha256'],
            'Original role archive check differs: '+role)
    with tarfile.open(a, 'r|gz') as tar:
        head = tar.next()
        require(head is not None and head.name==archive_check.MANIFEST and head.isfile() and
                0<head.size<=16*1024**2, 'Bounded frozen archive manifest required: '+role)
        manifest = json.load(tar.extractfile(head))
    require(manifest['schema']=='suite-archive-manifest-v1' and manifest['cell_id']==CELL and
            manifest['kind']==role and manifest['deferred']==[] and len(manifest['files'])==receipt['file_members'] and
            all(permitted(role, archive_check.safe(name)) and type(v['bytes']) is int and v['bytes']>=0
                for name,v in manifest['files'].items()), 'Invalid original role archive allowlist: '+role)
    return a, receipt, manifest['files']


def objects_input(s):
    retained_path = pin(s['objects_retained'])
    retained = load(retained_path)
    base = retained_path.parent
    require(retained['archived_only'] is True and retained['formal_acceptance'] is False and
            retained['worker_receipt']==s['objects_pack'] and
            pin(s['objects_pack'],base/'objects-pack.json')==base/'objects-pack.json',
            'Coordinator must retain the exact unmodified worker pack receipt')
    package = proof(s['objects_pack'])
    members = set(package['retained_legacy_members'])
    require(package['schema']=='c4-on1-recovered-objects-v1' and package['cell_id']==CELL and package['tag']==TAG and
            package['new_measurement'] is False and package['formal_acceptance'] is False and
            package['legacy_manifest']['bytes']>16*1024**2 and OBJECT_LEAVES< members and
            {n for n in members if n.startswith('q/')}==OBJECT_LEAVES and
            all(n.startswith((OBS+'/', 'q/')) for n in members),
            'Original objects archival packing receipt differs')
    for key,name in [('archive','objects.tar.gz'),('check','objects-check.json')]:
        p = Path(package[key]['path'])
        require(p.is_absolute() and p.name==name and package[key]['sha256']==retained[key]['sha256'] and
                Path(retained[key]['path'])==base/name,
                'Worker and coordinator copies do not identify the same objects evidence')
    return package, dict(archive=retained['archive'],archive_check=retained['check'],
                         worker_archive=package['archive']['path'])


def demand(out, planned):
    info = os.statvfs(out.parent)
    unit = info.f_frsize
    directories = {f'raw/{role}/{p.as_posix()}' for role, (_, _, files) in planned.items() for name in files
                   for p in Path(name).parents}
    payload = sum((row['bytes']+unit-1)//unit*unit for _,_,files in planned.values() for row in files.values())
    archives = sum((p.stat().st_size+unit-1)//unit*unit for p,_,_ in planned.values())
    budget = payload + archives + len(directories)*2*unit + 32*1024**2
    inodes = len(directories)+sum(len(files) for _,_,files in planned.values())+100
    require(5*(info.f_bavail*unit-budget)>=info.f_blocks*unit and
            5*(info.f_favail-inodes)>=info.f_files,
            f'Recovery needs {budget} B and {inodes} inodes in addition to the 20% coordinator reserve')
    return dict(estimated_bytes=budget, estimated_inodes=inodes, available_bytes=info.f_bavail*unit,
                available_inodes=info.f_favail)


def materialize(out, role, entry):
    old, original_check, files = entry
    a = out/'archives'/(role+'.tar.gz')
    with old.open('rb') as src, a.open('xb') as dst:
        shutil.copyfileobj(src,dst,1024**2)
    verified = archive_check.verify(a, original_check['sha256'])
    require(all(verified[k]==original_check[k] for k in (
        'bytes','sha256','cell_id','kind','file_members','deferred')), 'Independent role archive check differs')
    write_new(out/'archives'/(role+'-check.json'), verified)
    root = out/'raw'/role
    root.mkdir(parents=True)
    found = set()
    with tarfile.open(a, 'r|gz') as tar:
        for m in tar:
            if m.name==archive_check.MANIFEST or m.isdir():
                continue
            name = archive_check.safe(m.name)
            require(m.isfile() and name in files and name not in found, 'Role archive membership changed')
            target = root/name
            target.parent.mkdir(parents=True,exist_ok=True)
            with tar.extractfile(m) as src, target.open('xb') as dst:
                shutil.copyfileobj(src,dst,1024**2)
            require(target.stat().st_size==files[name]['bytes'] and digest(target)==files[name]['sha256'],
                    'Extracted evidence changed: '+name)
            found.add(name)
    require(found==set(files), 'Role archive extraction incomplete: '+role)
    return ref(a)


def process_key(p):
    boot = p['boot_id']
    require(isinstance(boot,str) and len(boot)==36 and type(p['pid']) is int and p['pid']>0 and
            type(p['start_ticks']) is int and p['start_ticks']>0, 'Original process identity incomplete')
    return boot,p['pid'],p['start_ticks']


def check_objects(root, package, manifest):
    obs = root/OBS
    old = source(obs/'legacy-finished.json')
    folded = source(obs/'finished.json')
    require(ref(obs/'legacy-finished.json')['sha256']==package['original_finished']['sha256'] and
            ref(obs/'finished.json')['sha256']==package['folded_finished']['sha256'] and
            folded['stream']==package['stream'] and manifest[f'{OBS}/samples.ndjson']=={
                k:folded['stream'][k] for k in ('bytes','sha256')} and
            all(manifest.get(name)==row for name,row in package['retained_legacy_members'].items()) and
            not any(name.startswith(f'{OBS}/sample-') for name in manifest) and
            folded['samples']==old['samples']==len(old['history']) and
            all({k:v for k,v in b.items() if k not in ('line_sha256','line_bytes')}==
                {k:v for k,v in a.items() if k!='sample_sha256'}
                for a,b in zip(old['history'],folded['history'],strict=True)) and
            all(folded[k]==old[k] for k in ('marks','spec_sha256','metric_contract_sha256')) and
            not folded.get('observer_format_sha256'), 'Compact objects archive changed historical observer data')

    # Each fold-detail row links the preserved old sample.json SHA to the line
    # digest independently checked by the NDJSON audit. Cross-check the raw
    # record's own SHA field as well, so a mismatched detail file cannot attest
    # for a different payload in the stream. Read one line at a time.
    with (obs/'fold-receipt.ndjson').open('rb') as detail, (obs/'samples.ndjson').open('rb') as stream:
        for index,(a,b,line,raw) in enumerate(zip(old['history'],folded['history'],detail,stream,strict=True)):
            record = json.loads(line)
            sample = json.loads(raw)['files']['sample.json']
            require(record['index']==index and record['directory']==f'sample-{index:06}' and
                    record['line_sha256']==b['line_sha256'] and record['line_bytes']==b['line_bytes'] and
                    record['members']['sample.json']==sample['sha256']==a['sample_sha256'],
                    'Folded sample does not bind the original sample.json SHA')


def observers(out, observed, boundaries, marks):
    audits = {}
    closures = {}
    for o in observed:
        role = o['id'].removeprefix(POOL+'-')
        raw = out/'raw'/role
        cfg = raw/OBS_SPEC/'observer.json'
        ready = source(raw/OBS/'ready.json')
        finished = source(raw/OBS/'finished.json')
        spec = dict(path=ROLE_ROOTS[role]+'/'+OBS_SPEC+'/observer.json',sha256=ref(cfg)['sha256'])
        observer = process_key(ready['process'])
        closure = source(raw/CLOSE)
        physical = boundaries['objects-scratch' if role=='objects' else role]
        processes = closure['processes']
        require(o['spec']==ready['spec']==spec and ready['passed'] is True and finished['passed'] is True and
                ready['metric_contract_sha256']==finished['metric_contract_sha256'] and finished['spec_sha256']==spec['sha256'] and
                closure['passed'] is True and closure['all_exited'] is True and closure['all_unmounted'] is True and
                closure['cell_id']==CELL and sum(process_key(p)==observer for p in processes)==1 and
                not any(p.get('alive',False) for p in processes) and
                observer in {process_key(p) for p in physical['original_processes']},
                'Historical observer or closure identity differs: '+role)
        audit = observe_ndjson.audit if role=='objects' else observe.audit
        result = audit(raw/OBS,30,marks)
        require(result['passed'] is True and result['finished_sha256']==ref(raw/OBS/'finished.json')['sha256'],
                'Archived observation audit differs: '+role)
        audits[role] = result
        closures[role] = closure
    return audits, closures


def client_checks(out, root, inv, context, original_full, plan_path, plan, cell, batch, closed, boundary):
    raw = out/'raw/client'
    q = raw/'q'/TAG
    r = root/'raw/client/q'/TAG
    paths = {'client_verify':'verify/result.json','parent_verify':'verify/parent.json',
             'native_exit':'exit.json','verify_exit':'verify-exit.json'}
    inventory_keys = dict(client_verify='cold_child',parent_verify='cold_parent',
                          native_exit='native_exit',verify_exit='verify_exit')
    proofs = {k:ref(q/v) for k,v in paths.items()}
    require(all(ref(pin(inv['checks'][inventory_keys[k]], r/v))['sha256']==proofs[k]['sha256']
                for k,v in paths.items()), 'Original client raw verification or exit differs from archive')
    child = proof(proofs['client_verify'])
    parent = proof(proofs['parent_verify'])
    identity = c4_verify.reference_identity(original_full, child['binding'], TAG)
    require(child['passed'] is True and parent['passed'] is True and child['cell_id']==parent['cell_id']==CELL and
            child['files']==cell['params']['files'] and child['child_name']==TAG and
            child['verification_policy']==parent['verification_policy']==c4_verify.TOUCHED and
            child['full_reference']==context['full_reference'] and child['parent_identity']==parent['parent_identity']==identity and
            child['parent_matches'] is True and child['child_matches'] is True and child['parent_identity_unchanged'] is True and
            child['full_cold_child'] is False and child['parent_unchanged'] is False and parent['full_cold_parent'] is False and
            bool(child['touched_paths']) and child['cold_verification_seconds']>0 and
            child['binding']['source_tree']==parent['expected'] and child['binding']['preparation']==context['fork']['preparation'],
            'Original touched cold verification differs or was misrepresented as full')
    frozen = tool(batch,'admit')
    exits = {k:frozen.exit_binding(raw,q,TAG,plan_path,cell,context['asset'],PURPOSE,k,proofs[k])
             for k in ('native_exit','verify_exit')}
    mount_ids = {tuple(e['identity']) for e in exits.values()}
    require(len(mount_ids)==2 and mount_ids<={process_key(p) for p in closed['processes']} and
            mount_ids<={process_key(p) for p in boundary['original_processes']},
            'Original closure and terminal probe lack two distinct mount processes')
    request_path = q/'closure-request.json'
    closure_request = source(request_path)
    require(closed['schema']=='suite-closure-v1' and closed['request']==dict(
        path=f'/srv/ax-client/q/{TAG}/closure-request.json',sha256=ref(request_path)['sha256']) and
        closure_request['schema']=='suite-closure-request-v1' and closure_request['cell_id']==CELL and
        closure_request['debt_samples']==closed['debt_samples'] and closure_request['mount_points']==[context['asset']+'/mnt'] and
        closed['mount_points']==[dict(point=context['asset']+'/mnt',mounts=[])] and
        len(closed['debt_samples'])==2 and all(type(closed[k]) is int and closed[k]==0 for k in
            ('pending_upload_bytes','pending_upload_operations')), 'Original client zero-debt closure differs')
    moments = []
    for i,sample in enumerate(closed['debt_samples']):
        path = q/'debt'/f'sample-{i}.json'
        value = source(path)
        require(sample['evidence']==dict(path=f'/srv/ax-client/q/{TAG}/debt/sample-{i}.json',sha256=ref(path)['sha256']) and
                value['passed'] is True and value['cell_id']==CELL and value['at']==sample['observed_at'] and
                all(type(value[k]) is int and value[k]==0 for k in ('pending_upload_bytes','pending_upload_operations')) and
                bool(value['native_gauges']) and all(type(x) in (int,float) and x==0 for x in value['native_gauges'].values()),
                'Native zero-debt evidence differs')
        dt = datetime.fromisoformat(value['at'])
        require(dt.utcoffset() is not None,'Native debt timestamp missing timezone')
        moments.append(dt)
    require((moments[1]-moments[0]).total_seconds()>=1,'Native debt observations are not distinct')
    work = raw/'w'/TAG/'measure'
    measured = source(work/'result.json')
    require(measured['schema']=='c4-fork-work-v1','Original native fork work receipt required')
    packet = dict(cell_id=CELL,results=[dict(agent_index=0,evidence=ref(work/'result.json'),
                                           clock=ref(work/'clock.json'),clock_source=measured['clock'])])
    check = suite_validate.work(plan,CELL,packet)
    require(check['passed'] is True,'Frozen native work check rejected original receipts')
    write_new(out/'work-packet.json',packet)
    write_new(out/'work-check.json',check)
    return proofs, exits


def run(request, validate_only=False):
    s = proof(request)
    require(set(s)==KEYS and s['schema']==SCHEMA and type(s['reviewed']) is bool and
            (validate_only or s['reviewed'] is True), 'Exact human-reviewed archival recovery request required')
    root, continuation, batch, plan_path, plan, cell, inv, context, off1, observed, boundaries, lineage = exact_history(s)
    out = Path(s['out'])
    require(out.is_absolute() and out.resolve()==out and out.parent.is_dir() and not os.path.lexists(out) and
            not any(out.is_relative_to(p) or p.is_relative_to(out) for p in (root, continuation, Path(batch['control'])/'cells')),
            'Use a fresh separate canonical recovery directory outside original trial/formal evidence')
    package, objects = objects_input(s)
    planned = {role:archive_input(role, (objects if role=='objects' else inv['roles'][role]),root) for role in ROLES}
    sizing = demand(out, planned)
    if validate_only:
        return dict(valid=True,validation_only=True,reviewed=s['reviewed'],output_created=False,
                    original_trial_exit_code=1,archives={r:ref(planned[r][0]) for r in ROLES},
                    original_failure=lineage['original_failure'],required_space=sizing,
                    new_measurement=False,formal_acceptance=False)
    out.mkdir(mode=0o700)
    (out/'archives').mkdir()
    write_new(out/'archive-recovery-request.json',s)
    archive_pins = [materialize(out,r,planned[r]) for r in ROLES]
    check_objects(out/'raw/objects',package,planned['objects'][2])
    audits, closures = observers(out,observed,boundaries,cell['observation_marks'])
    for role in ('client','metadata'):
        actual = ref(out/'raw'/role/CLOSE)['sha256']
        original = pin(inv['roles'][role]['closure'],root/'raw'/role/CLOSE)
        require(actual==ref(original)['sha256'],'Original closed role proof differs from archive')
    proofs, exits = client_checks(out,root,inv,context,off1,plan_path,plan,cell,batch,closures['client'],boundaries['client'])
    absence(root,continuation)
    write_new(out/'observers.json',observed)
    write_new(out/'archives.json',archive_pins)
    write_new(out/'closure.json',dict(cell_id=CELL,passed=True,all_exited=True,all_unmounted=True,
              pending_upload_bytes=0,pending_upload_operations=0,nodes=[closures[r] for r in ROLES]))
    recovery = dict(schema='c4-on1-archival-recovery-v1',request=request,**lineage,
                    inventory=s['inventory'],objects_pack=s['objects_pack'],objects_retained=s['objects_retained'],
                    source_original_exit_code=1,
                    current_archives=archive_pins,observer_audits=audits,mount_exits=exits,
                    required_space=sizing,automatic_retry=False,new_measurement=False,formal_acceptance=False,
                    scope='Recovered calibration evidence only. The original failed attempt remains unchanged.')
    write_new(out/'recovery.json',recovery)
    finished = dict(passed=True,pool=POOL,purpose=PURPOSE,cohort=lineage['original_cohort'],**proofs,
                    closure=ref(out/'closure.json'),work=ref(out/'work-check.json'),archives=ref(out/'archives.json'),
                    automatic_retry=False,recovery=ref(out/'recovery.json'),**{k:context[k] for k in ('collector_name','collector_out')})
    write_new(out/'finished.json',finished)
    return dict(recovered_calibration=ref(out/'finished.json'),recovery=ref(out/'recovery.json'),
                new_measurement=False,formal_acceptance=False)


def main():
    parser = argparse.ArgumentParser(description=__doc__, epilog='Request keys: '+', '.join(sorted(KEYS))+
        '. original_continuation pins the original saved request.json, inventory pins its historical '
        'readback, objects_pack pins the retained compact worker receipt, objects_retained pins the '
        'coordinator copy and hash map, plan and off1_finished '
        'pin the original plan and Off1 finished.json, out is a new absolute directory.')
    parser.add_argument('--request',required=True)
    parser.add_argument('--sha256',required=True)
    parser.add_argument('--validate-only',action='store_true',
                        help='check lineage, archive pins/manifests and space; accept reviewed:false and write nothing')
    a = parser.parse_args()
    print(json.dumps(run(dict(path=a.request,sha256=a.sha256),validate_only=a.validate_only)))


if __name__=='__main__':
    main()
