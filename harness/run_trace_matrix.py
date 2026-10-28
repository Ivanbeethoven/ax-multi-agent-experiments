#!/usr/bin/env python3
"""Run selected, fixed-work trace arms on an isolated worker; never provisions ECS.

The plan command rotates all selected arms across every worker. Each run is an
independent local-backend experiment, not a shared-backend scale experiment.
"""
from __future__ import annotations
import argparse
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tarfile
import time

import aliyun_suite as suite

ARMS = {
    'native-ttl0': ('native', 0), 'overlay-ttl0': ('native-overlay', 0),
    'packed-ttl0': ('native-overlay-pack', 0), 'juicefs-ttl0': ('juicefs', 0),
    'native-default': ('native', None), 'overlay-default': ('native-overlay', None),
    'packed-default': ('native-overlay-pack', None), 'juicefs-default': ('juicefs', None),
}
CACHE = {'read_memory_bytes':512*1024**2, 'write_memory_bytes':512*1024**2,
         'read_ssd_bytes':4*1024**3, 'write_ssd_bytes':4*1024**3,
         'memory_budget_bytes':1024**3, 'compression':'none',
         'verify_cache_checksum':'full', 'writeback_mode':'upload_before_commit'}
IDENTIFIER = re.compile(r'[A-Za-z0-9][A-Za-z0-9_-]{0,60}')


def select_arms(values):
    if not values or len(values)!=len(set(values)) or set(values)-set(ARMS):
        raise ValueError('select unique, supported arms')
    return list(values)


def rotation(workers, rounds, arms):
    arms=select_arms(arms)
    rounds = len(arms) if rounds is None else rounds
    if len(workers)!=len(set(workers)) or not workers or rounds<1 or rounds>len(arms):
        raise ValueError('unique workers and positive rounds required')
    if any(not IDENTIFIER.fullmatch(w) for w in workers): raise ValueError('invalid worker id')
    schedule=[]; assignments={w:[] for w in workers}
    for phase in range(rounds):
        row={'phase':phase+1,'workers':{}}
        for i,worker in enumerate(workers):
            arm=arms[(phase+i)%len(arms)]
            row['workers'][worker]=arm;assignments[worker].append(arm)
        schedule.append(row)
    return {'schema_version':1,'workers':workers,'phases':schedule,
            'assignments':[{'worker_id':w,'arms':v} for w,v in assignments.items()],
            'scope':'independent per-worker backends; order is not a synchronized distributed load',
            'balanced_complete_blocks':rounds%len(arms)==0,
            'planned_cells':rounds*len(workers),
            'independent_blocks':len(workers)*(rounds//len(arms)), 'total_replay_timeout_s':None}


def command(argv, log, env=None):
    with Path(log).open('x',encoding='utf-8') as f:
        return subprocess.run(argv,stdout=f,stderr=subprocess.STDOUT,env=env).returncode


def spec_for(base, label, batch, worker, work, manifest_key=None):
    method,ttl=ARMS[label]
    s=deepcopy(base)
    for key in ('fuse_ttl_ms','fuse_keep_cache','manifest_key','parent_revision','workspace_id'):
        s.pop(key,None)
    s.update(method=method, batch_id=batch, worker_id=worker, work_root=str(work),
             qualify_reopen=True, cache_mib=8192, buffer_mib=1024, brewfs_cache=dict(CACHE))
    if ttl is not None: s.update(fuse_ttl_ms=ttl, fuse_keep_cache=False)
    if method=='native-overlay-pack':
        if not manifest_key: raise ValueError('packed arm needs a real manifest')
        s['manifest_key']=manifest_key
    return s


def new_buckets(endpoint,names):
    import boto3
    from botocore.config import Config
    from botocore.exceptions import ClientError
    client=boto3.client('s3',endpoint_url=suite.no_credentials_in_url(endpoint),region_name='us-east-1',
                        config=Config(signature_version='s3v4',s3={'addressing_style':'path'}))
    for name in names:
        try: client.head_bucket(Bucket=name)
        except ClientError as exc:
            if str(exc.response.get('Error',{}).get('Code')) not in ('404','NoSuchBucket','NotFound'): raise
            client.create_bucket(Bucket=name)
        else: raise ValueError('bucket already exists; never reuse a previous arm')


def cgroup_command(unit,spec,output,limits):
    return ['systemd-run','--unit='+unit,'--service-type=exec',
            '--property=StandardOutput=append:'+str(Path(output).parent/(Path(output).name+'-unit.log')),
            '--property=StandardError=append:'+str(Path(output).parent/(Path(output).name+'-unit.log')),
            '--property=RemainAfterExit=yes','--property=RuntimeMaxSec=infinity',
            '--property=TimeoutStartSec=infinity','--property=MemoryMax='+str(limits['memory_bytes']),
            '--property=MemorySwapMax=0','--property=CPUQuota='+str(limits['cpu_percent'])+'%',
            '--property=CPUQuotaPeriodSec=100ms','--property=UMask=0077',
            # EnvironmentFile keeps credentials out of argv and the journal.
            '--property=EnvironmentFile='+str(limits['environment_file']),
            sys.executable,str(Path(suite.__file__).resolve()),'trace','--spec',str(spec),'--output',str(output)]


def wait_unit(unit):
    # RemainAfterExit keeps the result inspectable. Do not combine it with
    # systemd-run --wait, which waits indefinitely for an active (exited) unit.
    while True:
        try:
            text=subprocess.check_output(['systemctl','show',unit,'--property=LoadState',
                '--property=SubState','--property=ExecMainStatus'],text=True)
            values=dict(line.split('=',1) for line in text.splitlines() if '=' in line)
        except (OSError,subprocess.CalledProcessError):return None,'unknown'
        state=values.get('SubState','unknown')
        if values.get('LoadState')!='loaded':return None,'unknown'
        if state in ('exited','dead','failed'):
            try:return int(values['ExecMainStatus']),state
            except (KeyError,ValueError):return None,'unknown'
        if state not in ('running','start','start-pre','start-post','stop','stop-sigterm','stop-sigkill'):
            return None,state
        time.sleep(5)


def archive_results(output,archive,terminal):
    methods=terminal.get('methods',{})
    if terminal.get('terminal_reason') not in ('completed','failed') or not methods or any(
        m.get('status') not in ('ok','failed','interrupted','unsupported','not_run') for m in methods.values()):
        raise ValueError('no archive/cleanup authorization for potentially live work')
    output=Path(output).resolve();archive=Path(archive).resolve()
    if archive.is_relative_to(output) or archive.exists(): raise ValueError('new archive outside result directory required')
    secrets=[os.environ[k].encode() for k in ('AWS_ACCESS_KEY_ID','AWS_SECRET_ACCESS_KEY','REDISCLI_AUTH') if os.environ.get(k)]
    secrets += [b'OSSAccessKeyId=',b'Signature=',b'X-Amz-Signature=',b'X-Amz-Credential=']
    keep=max(1024,max(map(len,secrets)))
    files=[];manifest={}
    for p in sorted(output.rglob('*')):
        if p.is_symlink(): raise ValueError('result symlink is not archived')
        if not p.is_file() or p.name=='archive-manifest.json': continue
        tail=b''
        with p.open('rb') as f:
            for block in iter(lambda:f.read(1024*1024),b''):
                joined=tail+block
                if any(v in joined for v in secrets): raise ValueError('credential scan failed; retain private results')
                tail=joined[-keep:]
        files.append(p);manifest[p.relative_to(output).as_posix()]={'bytes':p.stat().st_size,'sha256':suite.digest(p)}
    suite.save(output/'archive-manifest.json',{'files':manifest,'known_credential_scan':'passed',
        'code_sha256':{p.name:suite.digest(p) for p in (Path(__file__).resolve(),Path(suite.__file__).resolve())}})
    files.append(output/'archive-manifest.json')
    archive.parent.mkdir(parents=True,exist_ok=True)
    partial=archive.with_name(archive.name+'.partial')
    with tarfile.open(partial,'w:gz') as tar:
        for p in files: tar.add(p,arcname='results/'+p.relative_to(output).as_posix(),recursive=False)
    partial.replace(archive)
    receipt={'run_id':terminal['run_id'],'worker_id':terminal['worker_id'], 'bytes':archive.stat().st_size,
             'sha256':suite.digest(archive),'terminal_reason':terminal['terminal_reason'],'known_credential_scan':'passed'}
    suite.save(archive.with_name(archive.name+'.receipt.json'),receipt)
    return receipt


def worker_qualification(path):
    value=suite.load(path)
    allowed={'worker','runner_commit','runner_dirty','asset_manifest_sha256','image_id','image_digest',
             'rustfs_resources','redis_resources','trace_archive_sha256','prepared'}
    result={k:value[k] for k in allowed if k in value}
    if set(result)!=allowed or result['runner_dirty'] is not False or result['prepared'] is not True:
        raise ValueError('incomplete or unprepared worker qualification')
    if not re.fullmatch('[0-9a-f]{40}',result['runner_commit']):raise ValueError('full runner commit required')
    for key in ('asset_manifest_sha256','trace_archive_sha256'):
        if not re.fullmatch('[0-9a-f]{64}',result[key]):raise ValueError('worker artifact digest invalid')
    for key in ('image_id','image_digest'):
        if not re.fullmatch('sha256:[0-9a-f]{64}',result[key]):raise ValueError('worker image identity invalid')
    rustfs=result['rustfs_resources'];redis=result['redis_resources']
    result['rustfs_resources']={k:rustfs[k] for k in ('NanoCpus','Memory','MemorySwap')}
    result['redis_resources']={k:redis[k] for k in ('CPUQuotaPerSecUSec','MemoryMax','MemorySwapMax')}
    if not all(type(v) is int for v in result['rustfs_resources'].values()):raise ValueError('invalid Docker resource observation')
    if not all(type(v) is str for v in result['redis_resources'].values()):raise ValueError('invalid Redis resource observation')
    if not IDENTIFIER.fullmatch(result['worker']):raise ValueError('invalid worker identity')
    return result


def run(config,arms,archive=None):
    arms=select_arms(arms)
    for k in ('run_id','worker_id'):
        if not IDENTIFIER.fullmatch(config[k]): raise ValueError('invalid '+k)
    base=config['base_spec'];out=Path(config['output']).resolve()
    work=Path(config['work_base']).resolve()
    if out.exists() or work.exists(): raise ValueError('new output/work_base required')
    if out.is_relative_to(work) or work.is_relative_to(out):raise ValueError('work/results must be disjoint')
    if not config.get('environment_file') or not Path(config['environment_file']).is_file():
        raise ValueError('existing protected environment file required')
    if Path(config['environment_file']).stat().st_mode & 0o077:
        raise ValueError('environment file must not be accessible by other users')
    out.mkdir(parents=True);work.mkdir(parents=True)
    progress={'run_id':config['run_id'],'worker_id':config['worker_id'],'round':config.get('round'),
              'arms':{a:{'status':'not_run'} for a in arms},'stage':'prepare','status':'running',
              'total_replay_timeout_s':None,'strict_correctness':False,
              'topology':'independent local backend on each worker','formal_qualified':False}
    state=out/'trace-arms.json';suite.save(state,progress)
    started=time.monotonic();fatal=None
    try:
        # Inputs are pre-staged and hash checked; workers never compile binaries.
        if config.get('worker_qualification_file'):
            qualification=worker_qualification(config['worker_qualification_file'])
            if qualification['worker']!=config['worker_id']:raise ValueError('worker qualification identity mismatch')
            suite.save(out/'worker-qualification.json',qualification)
        suite.save(out/'worker-backend-qualification.json',suite.runtime_pin(suite.load(base['backend_pin']),base['rustfs_container']))
        suite.check_endpoint_container(base['endpoint'],base['rustfs_container'])
        required=['probe','replayer']+(['brewfs'] if any(ARMS[a][0]!='juicefs' for a in arms) else [])
        for key in required:suite.check_binary(base[key])
        if any(ARMS[a][0]=='juicefs' for a in arms):suite.check_binary(base['juicefs'])
        if suite.digest(base['trace_manifest'])!=base['trace_manifest_sha256']:raise ValueError('trace manifest mismatch')
        runhash=hashlib.sha256((config['run_id']+':'+config['worker_id']).encode()).hexdigest()[:16]
        source=work/'packed-source';copied=False
        used=set()
        limits={'memory_bytes':int(config.get('client_memory_bytes',8*1024**3)),
                'cpu_percent':int(config.get('client_cpu_percent',400)), 'environment_file':config['environment_file']}
        if limits['memory_bytes']<1024**3 or limits['cpu_percent']<1:raise ValueError('invalid cgroup limits')
        for i,label in enumerate(arms):
            method,ttl=ARMS[label];entry=progress['arms'][label];progress['stage']=label
            try:
                bucket='ax-'+runhash+'-'+label
                new_buckets(base['endpoint'],[bucket])
                db=int(config.get('redis_db_start',2))+i
                url=base['redis_url'].rstrip('/').rsplit('/',1)[0]+'/'+str(db)
                if url in used:raise ValueError('Redis DB reservation reused')
                used.add(url);manifest_key=None
                if method=='native-overlay-pack':
                    fixture=suite.check_binary(config['fixture'])
                    if config['fixture']['source_sha']!=base['brewfs']['source_sha']:
                        raise ValueError('fixture/source mismatch')
                    if not copied:
                        source.mkdir();tick=time.monotonic()
                        rc=command(['cp','-a',str(Path(base['trace_case'])/'initial'),str(source/'trace-root')],out/'packed-source-copy.log')
                        if rc:raise RuntimeError('packed source copy failed')
                        progress['packed_source_copy_seconds']=time.monotonic()-tick;copied=True
                    key=out/(label+'-manifest-key.txt');tick=time.monotonic()
                    args=[fixture,'--source-directory',str(source),'--source-consistency','best-effort-detected',
                          '--source-hardlink-policy','reject-external','--metadata-codec','raw','--data-codec','raw',
                          '--inline-data','off','--bucket',bucket,'--endpoint',base['endpoint'],'--region','us-east-1',
                          '--force-path-style','true','--manifest-output',str(key),'--output-dir',str(work/(label+'-objects'))]
                    rc=command(args,out/(label+'-pack.log'))
                    entry.update(pack_upload_publish_seconds=time.monotonic()-tick,pack_exit_code=rc,
                                 source_snapshot='quiescent materialized copy; production freeze cost not measured')
                    if rc or not key.is_file():raise RuntimeError('packed producer failed')
                    manifest_key=key.read_text().strip()
                spec=spec_for(base,label,config['run_id'],config['worker_id'],work/label,manifest_key)
                spec.update(bucket=bucket,redis_url=url,volume_name='ax-'+runhash+'-'+str(i),
                            expected_cgroup_limits={'memory_max_bytes':limits['memory_bytes'],'swap_max_bytes':0,
                                                    'cpu_quota_us':limits['cpu_percent']*1000,'cpu_period_us':100000})
                path=out/(label+'.spec.json');suite.save(path,spec)
                entry.update(status='running',bucket=bucket,requested_ttl_ms=ttl,cache_budget=dict(CACHE),
                             client_limits=limits,started_epoch=time.time());suite.save(state,progress)
                unit='ax-cell-'+runhash+'-'+str(i)
                launch=command(cgroup_command(unit,path,out/label,limits),out/(label+'-driver.log'))
                rc,substate=wait_unit(unit)
                if rc is None:
                    entry.update(status='unknown',cleanup_error='client service state is '+substate)
                    fatal='client job may still run; retain resources';break
                if launch:rc=launch
                result=suite.load(out/label/'result.json') if (out/label/'result.json').is_file() else {}
                entry.update(status=result.get('status','failed'),exit_code=rc,finished_epoch=time.time(),
                             cleanup_error=result.get('cleanup_error'))
                if rc and entry['status']=='ok':entry['status']='failed'
                if entry['cleanup_error']:
                    fatal='unmount failed; no later arm started';break
            except Exception as exc:
                entry.update(status='failed',failure_class=type(exc).__name__,finished_epoch=time.time())
            finally:suite.save(state,progress)
        progress['status']='unknown' if any(v['status']=='unknown' for v in progress['arms'].values()) else ('completed' if all(v['status']=='ok' for v in progress['arms'].values()) else 'failed')
    except BaseException as exc:
        progress['status']='interrupted' if isinstance(exc,KeyboardInterrupt) else 'failed'
        fatal=type(exc).__name__
        if isinstance(exc,KeyboardInterrupt):progress['status']='unknown'
    finally:
        for value in progress['arms'].values():
            if value['status']=='running':value['status']='unknown'
        if any(v['status']=='unknown' for v in progress['arms'].values()):progress['status']='unknown'
        progress.update(stage='terminal',finished_epoch=time.time(),wall_seconds=time.monotonic()-started,failure=fatal)
        suite.save(state,progress)
        terminal={'run_id':config['run_id'],'worker_id':config['worker_id'],
                  'terminal_reason':'completed' if progress['status']=='completed' else ('unknown' if progress['status']=='unknown' else 'failed'),
                  'methods':progress['arms'],'strict_correctness':False,'formal_qualified':False,
                  'replay_total_timeout_s':None,'failure':fatal,'finished_epoch':time.time()}
        suite.save(out/'terminal.json',terminal)
    if archive and terminal['terminal_reason']!='unknown':archive_results(out,archive,terminal)
    return 0 if progress['status']=='completed' else 1


def main():
    p=argparse.ArgumentParser(description=__doc__);sub=p.add_subparsers(dest='mode',required=True)
    plan=sub.add_parser('plan');plan.add_argument('--workers',nargs='+',required=True)
    plan.add_argument('--rounds',type=int,default=None);plan.add_argument('--arms',nargs='+',default=[a for a in ARMS if a.endswith('-ttl0')]);plan.add_argument('--output',type=Path,required=True)
    execute=sub.add_parser('run');execute.add_argument('--config',type=Path,required=True)
    execute.add_argument('--arms',nargs='+');execute.add_argument('--archive',type=Path)
    a=p.parse_args()
    if a.mode=='plan':
        if a.output.exists():raise ValueError('new output required')
        suite.save(a.output,rotation(a.workers,a.rounds,a.arms));return 0
    c=suite.load(a.config)
    return run(c,a.arms or c.get('arms',[a for a in ARMS if a.endswith('-ttl0')]),a.archive)


if __name__=='__main__':raise SystemExit(main())