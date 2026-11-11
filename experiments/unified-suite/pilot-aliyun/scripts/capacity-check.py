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
import sys

REPO=Path(__file__).resolve().parents[4]
sys.path.insert(0,str(REPO))
from bench.runtime import pilot_context as pilot_runtime
from bench.runtime.common import load,pinned
from bench.runtime.pilot_pools import alias,fleet_node,pool_inventory


def write(path,value):
    with path.open('x') as stream:
        json.dump(value,stream,indent=2);stream.write('\n')


def ref(path):return dict(path=str(path),sha256=hashlib.sha256(path.read_bytes()).hexdigest())


def snapshot(args):
    if args.out.exists():raise FileExistsError(args.out)
    runtime=pilot_runtime.select(args.control,pilot_runtime.reference(args))
    fleet=load(runtime/'fleet.json');baseline=load(args.before) if args.before else None
    if baseline and (baseline['inventory']!=ref(runtime/'fleet.json') or baseline.get('pool',args.pool)!=args.pool):
        raise ValueError('Before snapshot belongs to another actual runtime or pool')
    if baseline and baseline.get('measurement_basis')=='available' and not args.preparation_result:
        raise ValueError('Available-only forecast needs the original completed C3 preparation result')
    nodes={}
    for role in ('client','metadata','objects'):
        node=fleet_node(fleet,args.pool,role)
        script=r'''import json,pathlib,os,subprocess,socket,hashlib
from datetime import datetime,timezone
root=pathlib.Path(ROOT)
if EXPECTED_HOSTNAME and socket.gethostname()!=EXPECTED_HOSTNAME:raise ValueError('Wrong actual capacity host')
block=json.loads(subprocess.check_output(['lsblk','--json','--bytes','--output','TYPE,SERIAL,SIZE,MAJ:MIN'],text=True))['blockdevices']
def devices(rows,parent=None):
 for row in rows:
  yield row,parent
  yield from devices(row.get('children',[]),row if row['type']=='disk' else parent)
def disk(p):
 s=os.statvfs(p);dev=p.stat().st_dev
 found=next((parent or row for row,parent in devices(block) if row['maj:min']==str(os.major(dev))+':'+str(os.minor(dev))),None)
 if not found or not found.get('serial'):raise ValueError('Native data-disk serial required')
 return dict(path=str(p),device=dev,serial=found['serial'].strip(),device_bytes=found['size'],total_bytes=s.f_blocks*s.f_frsize,used_bytes=(s.f_blocks-s.f_bfree)*s.f_frsize,available_bytes=s.f_bavail*s.f_frsize,total_inodes=s.f_files,used_inodes=s.f_files-s.f_ffree,available_inodes=s.f_favail)
value=dict(boot_id=pathlib.Path('/proc/sys/kernel/random/boot_id').read_text().strip(),sampled_at=datetime.now(timezone.utc).isoformat(),disks=[disk(root)])
if ROLE=='client' and PREPARATION_RESULT:
 path=pathlib.Path(PREPARATION_RESULT);raw=path.read_bytes();receipt=json.loads(raw)
 if not receipt['passed'] or receipt['cell_id']!=EXPECTED_CELL_ID or receipt['files']!=EXPECTED_FILES:raise ValueError('Original complete C3 base required')
 proofs=[]
 for pin in receipt['normal_exits']:
  original=pathlib.Path(pin['path']);data=original.read_bytes();proof=json.loads(data)
  if hashlib.sha256(data).hexdigest()!=pin['sha256'] or not proof['passed'] or proof['exit_code']!=0 or not proof['mount_absent']:raise ValueError('Original normal exits required')
  proofs.append(dict(path=str(original),sha256=pin['sha256'],value=proof))
 if len(proofs)!=2 or pathlib.Path('/sys/fs/cgroup/ax-slot-1/cgroup.procs').read_text().strip():raise ValueError('C3 must be normally closed with an empty slot')
 value['preparation']=dict(path=str(path),sha256=hashlib.sha256(raw).hexdigest(),value=receipt,normal_exits=proofs)
if ROLE=='objects':
 fmt='{"id":{{json .Id}},"image":{{json .Image}},"pid":{{json .State.Pid}},"running":{{json .State.Running}},"restarts":{{json .RestartCount}},"mounts":{{json .Mounts}}}'
 c=json.loads(subprocess.check_output(['docker','inspect','--format',fmt,CONTAINER]))
 if not c['running'] or c['restarts']:raise ValueError('Original object service required')
 paths=[pathlib.Path(m['Source']) for m in c['mounts'] if m['Destination'].startswith('/data/rustfs')]
 if len(paths)!=4 or len({p.stat().st_dev for p in paths})!=4:raise ValueError('Four independent object devices required')
 if BASELINE_PATHS:
  wanted=[pathlib.Path(p) for p in BASELINE_PATHS]
  if {p.stat().st_dev for p in wanted}!={p.stat().st_dev for p in paths}:raise ValueError('Baseline does not name the original object devices')
  paths=wanted
 value.update(disks=[disk(p) for p in sorted(paths)],container_id=c['id'],image=c['image'],pid=c['pid'])
if ROLE=='metadata':
 raw=subprocess.check_output(['redis-cli','--raw','INFO'],text=True)
 fields=dict(line.split(':',1) for line in raw.splitlines() if ':' in line and not line.startswith('#'))
 value['redis']={k:int(fields[k]) for k in ('used_memory','used_memory_rss','maxmemory','aof_current_size','process_id')}
 if value['redis']['maxmemory']<=0:raise ValueError('Explicit Redis memory bound required')
print(json.dumps(value))
'''
        root={'client':'/srv/ax-client','metadata':'/srv/ax-metadata','objects':'/srv/ax-objects-0'}[role]
        paths=[d['path'] for d in baseline['nodes'][role]['disks']] if baseline else []
        bindings=dict(ROOT=root,ROLE=role,CONTAINER=args.objects_container,EXPECTED_HOSTNAME=node.get('hostname'),BASELINE_PATHS=paths,
            PREPARATION_RESULT=args.preparation_result,EXPECTED_CELL_ID=baseline.get('cell_id') if baseline else None,
            EXPECTED_FILES=baseline.get('files') if baseline else None)
        script=''.join(key+'='+repr(value)+'\n' for key,value in bindings.items())+script
        result=subprocess.run(['ssh','-F',str(runtime/'ssh.conf'),alias(args.pool,role),'python3 -'],input=script,capture_output=True,text=True,check=True)
        nodes[role]=json.loads(result.stdout)
    value=dict(at=datetime.now(timezone.utc).isoformat(),inventory=ref(runtime/'fleet.json'),pool=args.pool,nodes=nodes,
        source_pins=[ref(runtime/'fleet.json'),ref(Path(__file__))]+([ref(args.before)] if args.before else []),
        scope='Actual filesystem allocation and Redis memory/AOF snapshot; no workload or object deletion')
    write(args.out,value);print(json.dumps(dict(recorded=True,path=str(args.out))))


def import_before(args):
    """Recover available-only observations made before the original preparation."""
    runtime=pilot_runtime.select(args.control,pilot_runtime.reference(args));fleet=load(runtime/'fleet.json')
    cloud=load(args.control/'provenance/inventory.json');declared=pool_inventory(cloud,args.pool)['roles']
    plan=load(args.control/'suite/plan.json');started=load(args.preparation_started)
    cell=next(c for c in plan['cells'] if c['id']==started['cell_id'])
    if cell['pool']!=args.pool or cell['workload']!='smallfiles':raise ValueError('Original C3 preparation for this actual pool required')
    cluster=next(c for c in plan['config']['clusters'] if c['id']==args.pool)
    if cluster['inventory_sha256']!=ref(args.control/'provenance/inventory.json')['sha256']:raise ValueError('Before sources do not match frozen cloud inventory')
    boundary=datetime.fromisoformat(started['at']);nodes={};pins=[ref(args.preparation_started),ref(runtime/'fleet.json'),ref(args.control/'provenance/inventory.json'),ref(Path(__file__))]
    for role in ('client','metadata','objects'):
        path=args.source_directory/role/'result.json';source=load(path);pins.append(ref(path))
        sampled=datetime.fromisoformat(source['at'])
        if not source['passed'] or sampled>=boundary or source['instance_id']!=declared[role]['instance_id'] or fleet_node(fleet,args.pool,role)['host_id']!=source['instance_id']:
            raise ValueError('Original observation must precede preparation on the same actual host')
        if {d['id'] for d in source['disks']}!=set(declared[role]['data_disk_ids']):raise ValueError('Before data-disk membership differs')
        node=dict(boot_id=source['boot_id'],sampled_at=source['at'],instance_id=source['instance_id'],
            disks=[dict(path=d['mount'],device=d['st_dev'],disk_id=d['id'],serial=d['serial'],device_bytes=d['gib']*1024**3,
                        available_bytes=d['free_bytes'],available_inodes=d['free_inodes']) for d in sorted(source['disks'],key=lambda d:d['mount'])])
        if role=='objects':
            service=next(c for c in source['containers'] if c.get('allowed_environment',{}).get('RUSTFS_ADDRESS'))
            if not service['running']:raise ValueError('Before object service was not running')
            node.update(container_id=service['id'],image=service['image'],pid=service['pid'])
        if role=='metadata':
            info_path=args.source_directory/role/'redis-info/stdout';pins.append(ref(info_path))
            fields=dict(line.split(':',1) for line in info_path.read_text().splitlines() if ':' in line and not line.startswith('#'))
            node['redis']={key:int(fields[key]) for key in ('used_memory','used_memory_rss','maxmemory','aof_current_size','process_id')}
            if str(node['redis']['process_id'])!=source['redis_status']['MainPID'] or node['redis']['maxmemory']<=0:raise ValueError('Original Redis INFO and process identity differ')
        nodes[role]=node
    value=dict(schema='pilot-capacity-available-before-v1',measurement_basis='available',pool=args.pool,
        cell_id=cell['id'],files=cell['params']['files'],
        at=max(n['sampled_at'] for n in nodes.values()),preparation_started=ref(args.preparation_started),
        inventory=ref(runtime/'fleet.json'),nodes=nodes,source_pins=pins,
        scope='Original available-byte/inode observations predating preparation, rebound only to the same actual hosts/disks. Before filesystem totals/used values were not measured and are absent; runtime bytes were probed separately later.')
    write(args.out,value);print(json.dumps(dict(recorded=True,path=str(args.out))))


def budget(args):
    before=json.loads(args.before.read_text());after=json.loads(args.after.read_text())
    if before['inventory']!=after['inventory'] or not 0<args.reserve<1 or args.extra_full_copies<0:
        raise ValueError('Matching actual inventory, nonnegative copies and explicit reserve required')
    if before.get('pool')!=after.get('pool'):raise ValueError('Capacity observations belong to different pools')
    for observation in (before,after):
        for pin in observation.get('source_pins',[]):pinned(pin)
    rows=[]
    for role,node in after['nodes'].items():
        old=before['nodes'][role]
        if node['boot_id']!=old['boot_id']:raise ValueError('Host rebooted between capacity observations')
        for key in ('container_id','image','pid'):
            if old.get(key)!=node.get(key):raise ValueError('Object service changed')
        if len(node['disks'])!=len(old['disks']):raise ValueError('Disk membership changed')
        for a,b in zip(old['disks'],node['disks']):
            available_only=before.get('measurement_basis')=='available'
            identity=('path','device','serial','device_bytes') if available_only else ('path','device','total_bytes','total_inodes')
            if any(a[k]!=b[k] for k in identity):
                raise ValueError('Disk identity/capacity changed; create a new phase inventory')
            # Client input/evidence disk is retained, but an imported remote base
            # does not imply seven additional local source copies.
            copies=args.extra_full_copies if role in ('objects','metadata') else 0
            for quantity,used,total in [('bytes','used_bytes','total_bytes'),('inodes','used_inodes','total_inodes')]:
                if available_only:
                    free='available_'+quantity
                    decrease=a[free]-b[free];increment=max(0,decrease);projected=b[free]-increment*copies
                    rows.append(dict(role=role,path=b['path'],quantity=quantity,measurement_basis='available-decrease',
                        before_available=a[free],after_available=b[free],measured_base_increment=increment,
                        extra_full_copies=copies,projected_available=projected,total=b[total],
                        before_total_unknown=True,observed_allocation_decrease=decrease<0,
                        passed=decrease>=0 and projected>=b[total]*args.reserve))
                    continue
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
            if 'aof_current_size' in old['redis'] and 'aof_current_size' in node['redis']:
                delta=node['redis']['aof_current_size']-old['redis']['aof_current_size']
                rows.append(dict(role=role,quantity='redis-aof-bytes',before=old['redis']['aof_current_size'],after=node['redis']['aof_current_size'],
                    measured_base_increment=max(0,delta),extra_full_copies=args.extra_full_copies,
                    projected_aof_bytes=node['redis']['aof_current_size']+max(0,delta)*args.extra_full_copies,
                    included_in_metadata_disk_forecast=True,passed=delta>=0))
    result=dict(passed=all(row['passed'] for row in rows),before=ref(args.before),after=ref(args.after),
        reserve_fraction=args.reserve,extra_full_copies=args.extra_full_copies,rows=rows,
        source_pins=[ref(args.before),ref(args.after),ref(Path(__file__))],
        scope='Forecast from one measured full base, including all concurrent setup growth. Available-only before records use available decrease and after-native capacity, never invented before totals. Clone sharing and GC require separate actual proof.')
    write(args.out,result);print(json.dumps(result))
    if not result['passed']:raise SystemExit(2)


def main():
    p=argparse.ArgumentParser(description=__doc__);sub=p.add_subparsers(dest='action',required=True)
    s=sub.add_parser('snapshot');s.add_argument('--control',type=Path,required=True)
    s.add_argument('--pool',default='pool-a');s.add_argument('--before',type=Path)
    s.add_argument('--preparation-result',help='Original absolute result.json on this pool client, verified closed before sampling')
    pilot_runtime.arguments(s)
    s.add_argument('--objects-container',required=True);s.add_argument('--out',type=Path,required=True)
    i=sub.add_parser('import-before')
    for name in ('control','source-directory','preparation-started','out'):i.add_argument('--'+name,type=Path,required=True)
    i.add_argument('--pool',required=True);pilot_runtime.arguments(i)
    b=sub.add_parser('budget')
    for name in ('before','after','out'):b.add_argument('--'+name,type=Path,required=True)
    b.add_argument('--extra-full-copies',type=int,required=True);b.add_argument('--reserve',type=float,default=.2)
    args=p.parse_args()
    {'snapshot':snapshot,'import-before':import_before,'budget':budget}[args.action](args)


if __name__=='__main__':main()
