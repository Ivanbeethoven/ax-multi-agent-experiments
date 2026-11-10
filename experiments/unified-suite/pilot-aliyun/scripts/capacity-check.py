#!/usr/bin/env python3
"""Snapshot actual C3 storage, then budget from one complete imported base.

The budget is a conservative full-copy forecast, not proof of clone sharing or
garbage collection. Run snapshots only at explicitly chosen idle boundaries.
"""
import argparse
from datetime import datetime,timezone
import hashlib
import json
from pathlib import Path
import subprocess


def write(path,value):
    with path.open('x') as stream:
        json.dump(value,stream,indent=2);stream.write('\n')


def ref(path):return dict(path=str(path),sha256=hashlib.sha256(path.read_bytes()).hexdigest())


def snapshot(args):
    if args.out.exists():raise FileExistsError(args.out)
    nodes={}
    for role in ('client','metadata','objects'):
        script=r'''import json,pathlib,os,subprocess
root=pathlib.Path(ROOT)
def disk(p):
 s=os.statvfs(p);return dict(path=str(p),device=p.stat().st_dev,total_bytes=s.f_blocks*s.f_frsize,used_bytes=(s.f_blocks-s.f_bfree)*s.f_frsize,available_bytes=s.f_bavail*s.f_frsize,total_inodes=s.f_files,used_inodes=s.f_files-s.f_ffree)
value=dict(boot_id=pathlib.Path('/proc/sys/kernel/random/boot_id').read_text().strip(),disks=[disk(root)])
if ROLE=='objects':
 c=json.loads(subprocess.check_output(['docker','inspect',CONTAINER]))[0]
 if not c['State']['Running'] or c['RestartCount']:raise ValueError('Original object service required')
 paths=[pathlib.Path(m['Source']) for m in c['Mounts'] if m['Destination'].startswith('/data/rustfs')]
 if len(paths)!=4 or len({p.stat().st_dev for p in paths})!=4:raise ValueError('Four independent object devices required')
 value.update(disks=[disk(p) for p in sorted(paths)],container_id=c['Id'],image=c['Image'],pid=c['State']['Pid'])
if ROLE=='metadata':
 raw=subprocess.check_output(['redis-cli','--raw','INFO'],text=True)
 fields=dict(line.split(':',1) for line in raw.splitlines() if ':' in line and not line.startswith('#'))
 value['redis']={k:int(fields[k]) for k in ('used_memory','used_memory_rss','maxmemory','aof_current_size','process_id')}
 if value['redis']['maxmemory']<=0:raise ValueError('Explicit Redis memory bound required')
print(json.dumps(value))
'''
        root={'client':'/srv/ax-client','metadata':'/srv/ax-metadata','objects':'/srv/ax-objects-0'}[role]
        script='ROOT='+repr(root)+'\nROLE='+repr(role)+'\nCONTAINER='+repr(args.objects_container)+'\n'+script
        result=subprocess.run(['ssh','-F',str(args.control/'ssh.conf'),'pool-a-'+role,'python3 -'],input=script,capture_output=True,text=True,check=True)
        nodes[role]=json.loads(result.stdout)
    value=dict(at=datetime.now(timezone.utc).isoformat(),inventory=ref(args.control/'fleet.json'),nodes=nodes,
        scope='Actual filesystem allocation and Redis memory/AOF snapshot; no workload or object deletion')
    write(args.out,value);print(json.dumps(dict(recorded=True,path=str(args.out))))


def budget(args):
    before=json.loads(args.before.read_text());after=json.loads(args.after.read_text())
    if before['inventory']!=after['inventory'] or not 0<args.reserve<1 or args.extra_full_copies<0:
        raise ValueError('Matching actual inventory, nonnegative copies and explicit reserve required')
    rows=[]
    for role,node in after['nodes'].items():
        old=before['nodes'][role]
        if node['boot_id']!=old['boot_id']:raise ValueError('Host rebooted between capacity observations')
        for key in ('container_id','image','pid'):
            if old.get(key)!=node.get(key):raise ValueError('Object service changed')
        if len(node['disks'])!=len(old['disks']):raise ValueError('Disk membership changed')
        for a,b in zip(old['disks'],node['disks']):
            if any(a[k]!=b[k] for k in ('path','device','total_bytes','total_inodes')):
                raise ValueError('Disk identity/capacity changed; create a new phase inventory')
            # Client input/evidence disk is retained, but an imported remote base
            # does not imply seven additional local source copies.
            copies=args.extra_full_copies if role in ('objects','metadata') else 0
            for quantity,used,total in [('bytes','used_bytes','total_bytes'),('inodes','used_inodes','total_inodes')]:
                increment=max(0,b[used]-a[used]);projected=b[used]+increment*copies
                rows.append(dict(role=role,path=b['path'],quantity=quantity,measured_base_increment=increment,
                    extra_full_copies=copies,projected_used=projected,total=b[total],
                    observed_allocation_decrease=b[used]<a[used],
                    passed=b[used]>=a[used] and projected<=b[total]*(1-args.reserve)))
        if role=='metadata':
            if node['redis']['process_id']!=old['redis']['process_id'] or node['redis']['maxmemory']!=old['redis']['maxmemory']:
                raise ValueError('Redis process or memory limit changed')
            delta=max(0,node['redis']['used_memory']-old['redis']['used_memory'])
            projected=node['redis']['used_memory']+delta*args.extra_full_copies
            rows.append(dict(role=role,quantity='redis-memory-bytes',measured_base_increment=delta,
                extra_full_copies=args.extra_full_copies,projected_used=projected,total=node['redis']['maxmemory'],
                observed_allocation_decrease=node['redis']['used_memory']<old['redis']['used_memory'],
                passed=node['redis']['used_memory']>=old['redis']['used_memory'] and projected<=node['redis']['maxmemory']*(1-args.reserve)))
    result=dict(passed=all(row['passed'] for row in rows),before=ref(args.before),after=ref(args.after),
        reserve_fraction=args.reserve,rows=rows,scope='Forecast from one measured full base; clone sharing and GC require separate actual proof')
    write(args.out,result);print(json.dumps(result))
    if not result['passed']:raise SystemExit(2)


def main():
    p=argparse.ArgumentParser(description=__doc__);sub=p.add_subparsers(dest='action',required=True)
    s=sub.add_parser('snapshot');s.add_argument('--control',type=Path,required=True)
    s.add_argument('--objects-container',required=True);s.add_argument('--out',type=Path,required=True)
    b=sub.add_parser('budget')
    for name in ('before','after','out'):b.add_argument('--'+name,type=Path,required=True)
    b.add_argument('--extra-full-copies',type=int,required=True);b.add_argument('--reserve',type=float,default=.2)
    args=p.parse_args();snapshot(args) if args.action=='snapshot' else budget(args)


if __name__=='__main__':main()
