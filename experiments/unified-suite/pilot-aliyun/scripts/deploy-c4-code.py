#!/usr/bin/env python3
"""Install a pinned small code bundle in new paths on one idle physical pool."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import re
import shlex
import subprocess
import sys

bootstrap=argparse.ArgumentParser(add_help=False);bootstrap.add_argument('--runtime-repo',type=Path)
boot,_=bootstrap.parse_known_args();REPO=boot.runtime_repo.resolve(strict=True) if boot.runtime_repo else Path(__file__).resolve().parents[4]
sys.path.insert(0,str(REPO))
from bench.runtime.common import load,pinned,digest,write_new
from bench.runtime.pilot_pools import alias,fleet_node,active_conflicts,resource_guard
from bench.suite_plan import check_plan


def ref(path):return dict(path=str(path),sha256=digest(path))


def run(s):
    control=Path(s['control']);canonical=Path(s['canonical_control']);plan=check_plan(load(control/'suite/plan.json'))
    cell=next(c for c in plan['cells'] if c['id']==s['cell_id']);pool=cell['pool'];fleet=load(control/'fleet.json')
    if not re.fullmatch('code-[0-9]{2}',s['code_directory']):raise ValueError('Explicit frozen code-NN required')
    sources=[pinned(s[k]) for k in ('archive','manifest','installer')];ssh=pinned(s['ssh_config'])
    for proof in s['barriers']:
        value=load(pinned(proof))
        if value.get('passed') is not True and not (value.get('schema')=='suite-cell-close-v1' and value.get('outcome')=='complete'):
            raise ValueError('Original completed phase/staging proof required')
    if len(s['barriers'])<2:raise ValueError('Both phase and staging barriers required')
    expected=load(sources[1]);installer_relative='experiments/unified-suite/pilot-aliyun/scripts/install-code.py'
    if digest(sources[2])!=expected[installer_relative]:raise ValueError('Installer differs from frozen code bundle')
    out=Path(s['out'])
    if out.exists():raise ValueError('Fresh deployment evidence directory required')
    controls=[canonical,*[Path(p) for p in s['other_lease_controls']]]
    with resource_guard(canonical,cell,plan):
        if any(active_conflicts(p,cell,plan) for p in controls):raise ValueError('Physical pool is still leased')
        out.mkdir(parents=True);write_new(out/'request.json',s)
        def execute(directory,name,argv):
            p=directory/name;p.mkdir();write_new(p/'command.json',dict(argv=argv,automatic_retry=False))
            result=subprocess.run(argv,capture_output=True);(p/'stdout').write_bytes(result.stdout);(p/'stderr').write_bytes(result.stderr)
            write_new(p/'exit.json',dict(exit_code=result.returncode))
            if result.returncode:raise RuntimeError('Preserve failed deployment substep '+str(p))
            return result.stdout.decode()
        def role(role):
            directory=out/role;directory.mkdir();node=fleet_node(fleet,pool,role);root=node['work_root']
            remote=root+'/preparation/'+s['attempt'];destination=root+'/'+s['code_directory'];peer=alias(pool,role)
            script='''import pathlib,os,socket,json
root=pathlib.Path(ROOT);new=pathlib.Path(NEW);destination=pathlib.Path(DESTINATION)
if socket.gethostname()!=HOST or not os.path.ismount(root) or root.stat().st_dev==pathlib.Path('/').stat().st_dev:raise ValueError('Wrong frozen data root/host')
if new.exists() or destination.exists():raise ValueError('New version/preparation paths required; never overwrite')
if ROLE=='client' and pathlib.Path('/sys/fs/cgroup/ax-slot-1/cgroup.procs').read_text().strip():raise ValueError('Agent slot is occupied')
v=os.statvfs(root)
if v.f_bavail*v.f_frsize<1024**3 or v.f_favail<10000:raise ValueError('Insufficient small-code installation reserve')
new.mkdir(mode=0o700)
print(json.dumps(dict(hostname=socket.gethostname(),root=str(root),device=root.stat().st_dev,available_bytes=v.f_bavail*v.f_frsize,available_inodes=v.f_favail,new=str(new))))'''
            variables=dict(ROOT=root,NEW=remote,DESTINATION=destination,HOST=node['hostname'],ROLE=role)
            script='\n'.join(k+'='+repr(v) for k,v in variables.items())+'\n'+script
            execute(directory,'preflight',['ssh','-F',str(ssh),peer,shlex.join(['python3','-c',script])])
            execute(directory,'copy',['scp','-F',str(ssh),*[str(p) for p in sources],peer+':'+remote+'/'])
            paths=[remote+'/'+p.name for p in sources]
            raw=execute(directory,'hashes',['ssh','-F',str(ssh),peer,shlex.join(['sha256sum',*paths])])
            actual={line.split(None,1)[1].strip():line.split()[0] for line in raw.splitlines()}
            if any(actual.get(p)!=s[key]['sha256'] for p,key in zip(paths,('archive','manifest','installer'))):raise ValueError('Changed distribution bytes')
            value=execute(directory,'install',['ssh','-F',str(ssh),peer,shlex.join(['python3',paths[2],'--archive',paths[0],
                '--sha256',s['archive']['sha256'],'--manifest',paths[1],'--manifest-sha256',s['manifest']['sha256'],'--destination',destination])])
            result=json.loads(value)
            if not result['passed'] or result['manifest_sha256']!=s['manifest']['sha256']:raise ValueError('Original code installation failed')
            write_new(directory/'result.json',result);return role,ref(directory/'result.json')
        with ThreadPoolExecutor(max_workers=3) as pool_threads:results=dict(pool_threads.map(role,('client','metadata','objects')))
        if s.get('client_inputs'):
            directory=out/'client-inputs';directory.mkdir();remote='/srv/ax-client/preparation/'+s['client_input_directory']
            execute(directory,'mkdir',['ssh','-F',str(ssh),alias(pool,'client'),shlex.join(['mkdir','--mode=0700',remote])])
            for name,source in s['client_inputs'].items():
                if name not in ('plan.json','inventory.json'):raise ValueError('Only small frozen C4 plan/inventory are deployed')
                execute(directory,name+'-copy',['scp','-F',str(ssh),str(pinned(source)),alias(pool,'client')+':'+remote+'/'+name])
                actual=execute(directory,name+'-hash',['ssh','-F',str(ssh),alias(pool,'client'),shlex.join(['sha256sum',remote+'/'+name])]).split()[0]
                if actual!=source['sha256']:raise ValueError('Changed client input metadata')
        write_new(out/'result.json',dict(passed=True,pool=cell['pool'],cell_id=cell['id'],roles=results,
            sources={k:s[k] for k in ('archive','manifest','installer')},barriers=s['barriers'],worker_started=False,services_changed=False))
        return ref(out/'result.json')


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--request',required=True);p.add_argument('--sha256',required=True);p.add_argument('--runtime-repo',type=Path);a=p.parse_args()
    s=load(pinned(dict(path=a.request,sha256=a.sha256)))
    if s.get('reviewed') is not True:raise ValueError('Reviewed explicit deployment request required')
    print(json.dumps(run(s)))
