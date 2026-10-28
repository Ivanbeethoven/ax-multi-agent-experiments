#!/usr/bin/env python3
"""Dispatch explicit pre-staged worker jobs; no instance creation or deletion.

Each job has a unique host, Redis DB, work root and output reservation. systemd
keeps the remote job alive if SSH fails. A lost connection is unknown, not failed
replay, and is never automatically resubmitted.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import json
from pathlib import Path, PurePosixPath
import re
import shlex
import subprocess
import time


def validate(plan):
    ids=set(); targets=set(); work=set(); dbs=set()
    if plan['concurrency']<1: raise ValueError('positive concurrency required')
    for job in plan['jobs']:
        if not re.fullmatch('[a-zA-Z0-9][a-zA-Z0-9_-]{0,60}',job['id']): raise ValueError('invalid job id')
        if not re.fullmatch('[a-zA-Z0-9_.@-]+',job['host']) or job['host'].startswith('-'): raise ValueError('invalid SSH host')
        if job['id'] in ids: raise ValueError('duplicate job id')
        ids.add(job['id'])
        for field in ['repository','spec','output','work_root','environment_file']:
            p=PurePosixPath(job[field])
            if not p.is_absolute() or '..' in p.parts or p==PurePosixPath('/'): raise ValueError('use explicit absolute worker paths')
        for values,key in [(targets,'output'),(work,'work_root'),(dbs,'redis_reservation')]:
            # redis_reservation must name endpoint + logical DB, shared across hosts.
            value=job[key] if key=='redis_reservation' else (job['host'],job[key])
            if value in values: raise ValueError('shared job resource: '+key)
            values.add(value)
        if job['mode'] not in ('diagnostics','packed-queries','object-io','fork-base','trace'):
            raise ValueError('unsupported worker mode')


def ssh(job,args):
    # SSH control connection limits do not impose any limit on systemd's workload.
    return subprocess.check_output(['ssh','-o','BatchMode=yes','-o','ConnectTimeout=15',
        '-o','ServerAliveInterval=15','-o','ServerAliveCountMax=3',job['host'],shlex.join(args)],text=True)


def start_command(job):
    return ['systemd-run','--unit=ax-'+job['id'],'--property=RuntimeMaxSec=infinity',
            '--property=TimeoutStartSec=infinity','--property=RemainAfterExit=yes','--property=EnvironmentFile='+job['environment_file'],
            '--property=WorkingDirectory='+job['repository'],'--property=UMask=0077',
            'python3',job['repository']+'/harness/aliyun_suite.py',job['mode'],
            '--spec',job['spec'],'--output',job['output']]


def execute(job,output,collect_only=False):
    result={'id':job['id'],'host':job['host'],'status':'unknown','remote_unit':'ax-'+job['id'],
            'resubmitted':False,'controller_started_ns':time.time_ns()}
    try:
        if not collect_only:
            identity=json.loads(ssh(job,['python3','-c',
                'import json,sys; s=json.load(open(sys.argv[1])); print(json.dumps({k:s[k] for k in ("work_root","redis_url")}))',job['spec']]))
            if identity['work_root']!=job['work_root'] or identity['redis_url']!=job['redis_reservation']:
                raise ValueError('worker spec differs from reserved resources')
            ssh(job,['python3','-c','import os,stat,sys; p=sys.argv[1]; assert not os.path.islink(p); assert stat.S_IMODE(os.stat(p).st_mode)==0o600',job['environment_file']])
            # Existing units/output are never reused; IDs are scoped to the batch by the operator.
            ssh(job,['test','!','-e',job['output']])
            ssh(job,start_command(job))
        while True:
            state=ssh(job,['systemctl','show','ax-'+job['id'],'--property=SubState','--value']).strip()
            if state in ('failed','dead','exited'): break
            if state not in ('running','start','start-pre','start-post','stop','stop-sigterm','stop-sigkill'):
                raise RuntimeError('unknown unit state; inspect remotely without restarting')
            time.sleep(10)
        try:
            result['worker']=json.loads(ssh(job,['cat',job['output']+'/result.json']))
            result['status']=result['worker']['status']
        except subprocess.CalledProcessError:
            result['status']='failed'
            result['reason']='worker exited without result.json; see unit journal'
            (output/(job['id']+'.journal')).write_text(ssh(job,['journalctl','--unit=ax-'+job['id'],'--no-pager']))
        # Keep the complete worker result as a private archive; do not auto-publish/extract it.
        archive=output/(job['id']+'.tar.gz')
        with archive.open('xb') as f:
            subprocess.run(['ssh','-o','BatchMode=yes',job['host'],
                            shlex.join(['tar','-C',job['output'],'-czf','-','.'])],stdout=f,check=True)
        result['archive']=archive.name
    except (OSError,ValueError,RuntimeError,subprocess.CalledProcessError) as exc:
        result['status']='unknown';result['reason']=type(exc).__name__
        result['action']='collect again after reconnecting; remote unit may still be running, do not release resources'
    finally:
        result['controller_finished_ns']=time.time_ns()
        (output/(job['id']+'.json')).write_text(json.dumps(result,indent=2))
    return result


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('plan',type=Path);p.add_argument('--output',type=Path,required=True)
    p.add_argument('--execute',action='store_true');p.add_argument('--collect-only',action='store_true')
    a=p.parse_args();plan=json.loads(a.plan.read_text());validate(plan)
    if not a.execute and not a.collect_only:
        print(json.dumps({'jobs':len(plan['jobs']),'concurrency':plan['concurrency'],'commands':[start_command(j) for j in plan['jobs']]},indent=2));return
    if a.execute and a.collect_only: p.error('choose execute or collect-only')
    a.output.mkdir(parents=True,exist_ok=False)
    start=time.monotonic();results=[]
    with ThreadPoolExecutor(max_workers=plan['concurrency']) as pool:
        pending=[pool.submit(execute,j,a.output,a.collect_only) for j in plan['jobs']]
        for future in as_completed(pending):
            results.append(future.result())
            (a.output/'completion.json').write_text(json.dumps({'jobs':results,'controller_wall_seconds':time.monotonic()-start,
                'scope':'controller dispatch-to-collection, including setup/network; not pure replay makespan'},indent=2))


if __name__=='__main__':main()
