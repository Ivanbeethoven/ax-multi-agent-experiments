#!/usr/bin/env python3
"""Hold the original B locks for one approved cleanup; offline validation never contacts workers."""
import argparse
from contextlib import ExitStack
from datetime import datetime, timezone
import json
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[4]))
from bench.runtime.common import digest, load, pinned, write_new
from bench.runtime.pilot_pools import active_conflicts, resource_guard
from bench.suite_plan import check_plan

ROOT = Path('/srv/ax-pilot-control')
CANONICAL = ROOT/'parallel-control-01'
BASE = ROOT/'preparation/b-c2-observer-readback-01'
DRAFT = {'path': str(BASE/'cleanup.request.draft.json'),
         'sha256': '1248d9eae023bfa6407bd568c68981496703afbfc70e42d4eabad53b8fd0b6e4'}
ACTION = 'remove-verified-b-c2-observer-copies'
IDENTITY = ('host', 'instance_id', 'disk_id', 'serial', 'filesystem_uuid')


def require(ok, reason):
    if not ok:
        raise ValueError(reason)


def ref(path):
    return dict(path=str(path), sha256=digest(path))


def canonical(pin):
    path = pinned(pin)
    require(path.resolve(strict=True) == path and path.is_file(), 'Expected a canonical pinned file')
    return path


def validate():
    draft_path = canonical(DRAFT)
    draft = load(draft_path)
    require(draft['schema']=='b-c2-observer-cleanup-draft-v1' and draft['reviewed'] is False
            and draft['automatic_retry'] is False, 'Expected the immutable unreviewed draft')
    paths = {key: canonical(draft[key]) for key in ('source_checker','archive_proof','readback')}
    checked = load(paths['readback'])
    require(checked['archive_proof_sha256']==draft['archive_proof']['sha256'] and
            checked['serial']==draft['serial'] and checked['filesystem_uuid']==draft['filesystem_uuid'] and
            checked['all_unmounted'] is True and checked['deletion_performed'] is False and
            checked['cleanup_approved'] is False and
            [g['path'] for g in checked['groups']]==draft['targets'] and
            checked['free_bytes']+checked['checked_allocated_bytes']>=draft['minimum_free_bytes'],
            'Original closed no-write disk comparison differs')
    plan = check_plan(load(CANONICAL/'suite/plan.json'))
    cell = next(c for c in plan['cells'] if c['pool']=='pool-b')
    return draft_path, draft, paths, checked, plan, cell


def state_controls():
    return sorted(p.parent for p in ROOT.glob('*/state.sqlite'))


def lock_set(stack, plan, cell):
    controls = state_controls()
    require({CANONICAL, ROOT/'c123-control-01', ROOT/'fork-control-01'} <= set(controls),
            'Original control databases are missing')
    lock_directories = set()
    for ctl in controls:
        locks = ctl/'runner-locks'
        require(locks.is_dir(), 'Missing existing lock directory: '+str(ctl))
        key = locks.resolve(strict=True)
        if key not in lock_directories:
            stack.enter_context(resource_guard(ctl,cell,plan))
            lock_directories.add(key)
    require(controls==state_controls() and not any(active_conflicts(ctl,cell,plan) for ctl in controls),
            'Conflicting original resource lease or changed control membership')
    return controls, lock_directories


def run(args):
    draft_path, draft, paths, checked, plan, cell = validate()
    with ExitStack() as stack:
        controls, locks = lock_set(stack,plan,cell)
        if args.validate_only:
            require(not (args.approval or args.approval_sha256 or args.out), 'Offline validation does not accept execution fields')
            return dict(draft=DRAFT, checked_control_count=len(controls), lock_root_count=len(locks),
                        checked_allocated_bytes=checked['checked_allocated_bytes'], approval_consumed=False,
                        worker_contacted=False, deletion_performed=False)
        require(args.approval and args.approval_sha256 and args.out, 'Explicit approval and new result directory required')
        approval_pin = dict(path=str(args.approval), sha256=args.approval_sha256)
        approval = load(canonical(approval_pin))
        require(approval['schema']=='b-c2-observer-cleanup-approval-v1' and approval['approved'] is True
                and approval['action']==ACTION and approval['draft']==DRAFT and
                all(str(approval.get(k,'')).strip() for k in ('reviewer','user_message_id')),
                'Explicit approval for the pinned draft required')
        request_pin = approval['reviewed_request']
        request = canonical(request_pin)
        require(load(request)==dict(draft,reviewed=True), 'Reviewed request may only change reviewed')
        executor = canonical(approval['executor'])
        require(executor.name=='apply-b-c2-observer-cleanup.py', 'Unexpected approved executor name')
        out = args.out
        require(out.is_absolute() and out.parent==BASE and out.parent.resolve(strict=True)==out.parent
                and out.name.startswith('approved-cleanup-') and out.name.isascii() and
                all(c.isalnum() or c=='-' for c in out.name), 'Use a fresh owned cleanup output path')
        out.mkdir(mode=0o700)
        write_new(out/'intent.json', dict(draft=DRAFT, request=request_pin, approval=approval_pin,
                  executor=approval['executor'], controls=list(map(str,controls)), lock_roots=sorted(map(str,locks)),
                  automatic_retry=False))
        files = dict(draft=draft_path, reviewed_request=request, approval=canonical(approval_pin),
                     source_checker=paths['source_checker'], archive_proof=paths['archive_proof'],
                     readback=paths['readback'])
        staged = out/'inputs'
        staged.mkdir(mode=0o700)
        copied = {key: staged/(key+'.json' if key!='source_checker' else key+'.py') for key in files}
        for key,path in files.items():
            shutil.copyfile(path,copied[key])
        binary = staged/executor.name
        shutil.copyfile(executor,binary)
        worker = '/srv/ax-receipts/b-c2-'+out.name
        flags = ['-F',str(CANONICAL/'ssh.conf'),'-oBatchMode=yes','-oConnectTimeout=8']
        ssh = ['ssh',*flags,'pool-b-objects']
        subprocess.run(ssh+[shlex.join(['mkdir','-m','700',worker])], check=True,timeout=30)
        def send(files):
            subprocess.run(['scp',*flags,'-q',*map(str,files),'pool-b-objects:'+worker+'/'],
                           check=True,timeout=60)
        send([*copied.values(),binary])
        require(controls==state_controls() and not any(active_conflicts(ctl,cell,plan) for ctl in controls),
                'Control membership or resource lease changed before apply')
        guard = dict(schema='b-c2-observer-cleanup-guard-v1', passed=True, locks_held=True,
                     active_leases=0, at=datetime.now(timezone.utc).isoformat(),
                     reviewed_request=request_pin, approval=approval_pin, **{k:draft[k] for k in IDENTITY})
        guard_path = staged/'guard.json'
        write_new(guard_path,guard)
        send([guard_path])
        worker_out = worker+'/receipts'
        argv = ['timeout','--kill-after=5s','860s','unshare','--mount','--propagation','private',
                'python3','-B',worker+'/'+binary.name,'--apply']
        for key,path in copied.items():
            argv += ['--'+key.replace('_','-'),worker+'/'+path.name]
        argv += ['--reviewed-request-sha256',request_pin['sha256'],'--approval-sha256',approval_pin['sha256'],
                 '--guard',worker+'/'+guard_path.name,'--guard-sha256',digest(guard_path),'--out',worker_out]
        command = shlex.join(argv)
        write_new(out/'worker-intent.json',dict(argv=argv,automatic_retry=False))
        remote_end_bound = time.monotonic()+875
        with (out/'stdout').open('xb') as stdout,(out/'stderr').open('xb') as stderr:
            try:
                code = subprocess.run(ssh+[command],stdout=stdout,stderr=stderr,timeout=900).returncode
            except subprocess.TimeoutExpired:
                code = 124
        write_new(out/'exit.json',dict(exit_code=code,automatic_retry=False))
        # A broken SSH connection does not prove the remote command exited.
        if code==255:
            while time.monotonic()<remote_end_bound:
                time.sleep(min(5, max(0, remote_end_bound-time.monotonic())))
        copy = subprocess.run(['scp',*flags,'-q','-r','pool-b-objects:'+worker_out,str(out/'worker-receipts')],
                              timeout=60).returncode
        write_new(out/'retrieval.json',dict(exit_code=copy,worker_receipts=worker_out))
        require(code==copy==0, 'Retain first attempt and retrieve worker receipts before shutdown: '+worker_out)
        result = load(out/'worker-receipts/result.json')
        require(result['schema']=='b-c2-observer-cleanup-result-v1' and result['passed'] is True
                and result['all_unmounted'] is True and result['after_bytes']>=draft['minimum_free_bytes'],
                'Current disk still fails cleanup floor')
        write_new(out/'result.json',result)
        return result


if __name__=='__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument('--validate-only',action='store_true')
    modes.add_argument('--apply',action='store_true')
    parser.add_argument('--approval',type=Path)
    parser.add_argument('--approval-sha256')
    parser.add_argument('--out',type=Path)
    print(json.dumps(run(parser.parse_args()),indent=2))
