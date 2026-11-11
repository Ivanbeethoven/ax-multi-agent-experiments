#!/usr/bin/env python3
"""Budget Packed C3 from original pre-producer samples and a new idle snapshot."""
import argparse
from datetime import datetime,timezone
import hashlib
import inspect
import json
from pathlib import Path
import shlex
import subprocess
import sys

REPO=Path(__file__).resolve().parents[4]
sys.path.insert(0,str(REPO))
from bench.runtime.common import digest,load,pinned,write_new
from bench.runtime import pilot_context
from bench.runtime.pilot_pools import alias,fleet_node,pool_inventory,active_conflicts,resource_guard

ROLES=('client','metadata','objects')
def ref(path):return dict(path=str(Path(path).resolve(strict=True)),sha256=digest(path))


def remote_snapshot(request):
    import hashlib,json,os,socket,subprocess
    from datetime import datetime,timezone
    from pathlib import Path
    if socket.gethostname()!=request['hostname']:raise ValueError('Wrong capacity host')
    def original(path):
        path=Path(path);raw=path.read_bytes()
        return dict(path=str(path),sha256=hashlib.sha256(raw).hexdigest(),value=json.loads(raw))
    def command(argv):return subprocess.check_output(argv,text=True)
    block=json.loads(command(['lsblk','--json','--bytes','--output','TYPE,SERIAL,SIZE,MAJ:MIN']))['blockdevices']
    def devices(rows,parent=None):
        for row in rows:
            yield row,parent
            yield from devices(row.get('children',[]),row if row['type']=='disk' else parent)
    def disk(path):
        path=Path(path);v=os.statvfs(path);dev=path.stat().st_dev
        found=next((parent or row for row,parent in devices(block) if row['maj:min']==f'{os.major(dev)}:{os.minor(dev)}'),None)
        if not found or not found.get('serial'):raise ValueError('Actual data-disk serial missing')
        return dict(path=str(path),device=dev,serial=found['serial'].strip(),device_bytes=found['size'],
            total_bytes=v.f_blocks*v.f_frsize,available_bytes=v.f_bavail*v.f_frsize,total_inodes=v.f_files,available_inodes=v.f_favail)
    result=dict(hostname=socket.gethostname(),boot_id=Path('/proc/sys/kernel/random/boot_id').read_text().strip(),
        at=datetime.now(timezone.utc).isoformat(),disks=[disk(path) for path in request['paths']],lsblk=block,meminfo=Path('/proc/meminfo').read_text())
    if request['role']=='metadata':
        result['redis_info']=command(['redis-cli','--raw','INFO','ALL'])
    if request['role']=='objects':
        fmt='{"id":{{json .Id}},"image":{{json .Image}},"pid":{{json .State.Pid}},"running":{{json .State.Running}},"restarts":{{json .RestartCount}},"mounts":{{json .Mounts}}}'
        result['container']=json.loads(command(['docker','inspect','--format',fmt,request['objects_container']]))
    if request['role']=='client':
        if Path('/sys/fs/cgroup/ax-slot-1/cgroup.procs').read_text().strip():raise ValueError('Packed qualification must be completely stopped')
        result['documents']={key:original(request[key]) for key in ('base_started','base_preparation','qualification')}
        result['proofs']={}
        for document in ('base_preparation','qualification'):
            value=result['documents'][document]['value']
            if value.get('passed') is not True or value.get('cell_id')!=request['cell_id']:raise ValueError('Original complete matching qualification required')
            for key in ('normal_exit','original_readback'):
                if key not in value:continue
                source=original(value[key]['path'])
                if source['sha256']!=value[key]['sha256'] or source['value'].get('passed') is not True:raise ValueError('Changed original qualification proof')
                if key=='normal_exit' and (source['value'].get('exit_code')!=0 or source['value'].get('mount_absent') is not True):raise ValueError('Original mount still live')
                result['proofs'][document+'-'+key]=source
    return result


def redis(raw):
    values=dict(line.split(':',1) for line in raw.splitlines() if ':' in line and not line.startswith('#'))
    return {key:int(values[key]) for key in ('used_memory','used_memory_rss','maxmemory','aof_current_size','process_id')}


def before_sample(pin,started_at,boot,paths):
    path=pinned(pin);value=load(path)
    if value.get('passed') is not True or value.get('errors') or not value.get('files'):raise ValueError('Original successful raw sample required')
    if datetime.fromtimestamp(value['utc_ns']/1e9,timezone.utc)>=started_at:raise ValueError('Sample must precede the original producer')
    if not value.get('processes') or any(p.get('boot_id')!=boot for p in value['processes']):raise ValueError('Before sample and runtime boot differ')
    if set(value['capacity'])!=set(paths):raise ValueError('Original capacity path coverage differs')
    pins=[pin]
    for name,expected in value['files'].items():
        raw=path.parent/name
        if raw.parent!=path.parent or raw.is_symlink() or raw.stat().st_size!=expected['bytes'] or digest(raw)!=expected['sha256']:raise ValueError('Original raw sample changed')
        pins.append(ref(raw))
    capacities={}
    for name in paths:
        original=value['capacity'][name]
        if any(type(original.get(k)) is not int or original[k]<0 for k in ('bytes_free','inodes_free')):raise ValueError('Native available values required')
        capacities[name]=dict(available_bytes=original['bytes_free'],available_inodes=original['inodes_free'])
    return dict(at=datetime.fromtimestamp(value['utc_ns']/1e9,timezone.utc).isoformat(),boot_id=boot,capacity=capacities,
        original_processes=value['processes'],source_pins=pins,before_total_unknown=True)


def forecast(before,after,copies,reserve):
    if type(copies) is not int or copies<7 or not .2<=reserve<1:raise ValueError('At least six calibration copies, one formal copy and twenty percent reserve required')
    rows=[]
    for role in ROLES:
        old=before[role];new=after[role]
        if old['boot_id']!=new['boot_id']:raise ValueError('Host rebooted after the original before observation')
        if {disk['path'] for disk in new['disks']}!=set(old['capacity']):raise ValueError('Capacity paths changed')
        for disk in new['disks']:
            source=old['capacity'][disk['path']]
            for quantity in ('bytes','inodes'):
                free='available_'+quantity;delta=source[free]-disk[free];projected=disk[free]-max(0,delta)*copies
                rows.append(dict(role=role,path=disk['path'],quantity=quantity,before_available=source[free],after_available=disk[free],
                    observed_window_increment=max(0,delta),observed_available_increase=delta<0,extra_full_copies=copies,
                    projected_available=projected,total=disk['total_'+quantity],before_total_unknown=True,
                    passed=delta>=0 and projected>=disk['total_'+quantity]*reserve))
        if role=='metadata':
            a,b=old['redis'],new['redis']
            if a['process_id']!=b['process_id'] or a['maxmemory']!=b['maxmemory'] or b['maxmemory']<=0:raise ValueError('Redis process or bounded memory changed')
            for key,limit in [('used_memory',b['maxmemory']),('aof_current_size',None)]:
                delta=b[key]-a[key];projected=b[key]+max(0,delta)*copies
                rows.append(dict(role=role,quantity=key,before=a[key],after=b[key],observed_window_increment=max(0,delta),extra_full_copies=copies,
                    projected=projected,limit=limit,passed=delta>=0 and (limit is None or projected<=limit*(1-reserve))))
    return rows


def run(request,out):
    s=load(request)
    if s.get('schema')!='packed-c3-capacity-request-v1' or s.get('reviewed') is not True:raise ValueError('Reviewed actual capacity request required')
    control=Path(s['control']);runtime=pilot_context.select(control,s.get('runtime_control'));fleet=load(runtime/'fleet.json');plan=load(control/'suite/plan.json')
    cell=next(c for c in plan['cells'] if c['id']==s['cell_id']);pool=cell['pool']
    if cell['arm_id']!='packed' or cell['workload']!='smallfiles':raise ValueError('Actual Packed C3 cell required')
    hardware=load(pinned(s['hardware']))
    if hardware.get('passed') is not True or hardware.get('inventory_sha256')!=digest(runtime/'fleet.json'):raise ValueError('Same actual hardware qualification required')
    declared=pool_inventory(load(control/'provenance/inventory.json'),pool)['roles']
    specs={role:load(pinned(s['node_specs'][role])) for role in ROLES}
    with resource_guard(control,cell,plan):
        if active_conflicts(control,cell,plan):raise ValueError('Pool reserved; sample only after qualification and before calibration')
        out.mkdir(parents=True,exist_ok=False);write_new(out/'request.json',s)
        actual={};pins=[ref(request),ref(__file__),ref(runtime/'fleet.json'),s['hardware'],*s['node_specs'].values()]
        for role in ROLES:
            node=fleet_node(fleet,pool,role);paths=['/srv/ax-objects-'+str(i) for i in range(4)] if role=='objects' else [{'client':'/srv/ax-client','metadata':'/srv/ax-metadata'}[role]]
            r=dict(role=role,hostname=node['hostname'],paths=paths,objects_container=s['objects_container'],cell_id=cell['id'],
                **{key:s[key] for key in ('base_started','base_preparation','qualification')})
            directory=out/'raw'/role;directory.mkdir(parents=True);write_new(directory/'intent.json',dict(request=r,read_only=True))
            code='import json\n'+inspect.getsource(remote_snapshot)+'\nprint(json.dumps(remote_snapshot('+repr(r)+')))\n'
            with (directory/'stdout').open('xb') as stdout,(directory/'stderr').open('xb') as stderr:
                result=subprocess.run(['ssh','-F',str(runtime/'ssh.conf'),alias(pool,role),'python3 -'],input=code.encode(),stdout=stdout,stderr=stderr)
            write_new(directory/'exit.json',dict(exit_code=result.returncode))
            if result.returncode:raise ValueError('Inspect failed read-only capacity probe: '+role)
            v=json.loads((directory/'stdout').read_text());actual[role]=v;pins.append(ref(directory/'stdout'))
            if v['boot_id']!=load(runtime/'probes'/alias(pool,role)/'probe.json')['boot_id']:raise ValueError('Runtime boot changed')
            spec=specs[role]
            if spec['instance_id']!=node['host_id'] or spec['instance_id']!=declared[role]['instance_id']:raise ValueError('Disk spec belongs to another host')
            expected={disk['serial']:disk for disk in spec['data_disks']}
            if len(expected)!=len(v['disks']) or {d['id'] for d in expected.values()}!=set(declared[role]['data_disk_ids']):raise ValueError('Frozen actual disk membership differs')
            if len({d['device'] for d in v['disks']})!=len(v['disks']) or {d['serial'] for d in v['disks']}!=set(expected):raise ValueError('Current data disk serials/devices differ')
        docs=actual['client']['documents'];base=docs['base_preparation']['value'];qualified=docs['qualification']['value']
        if qualified['full_base']!=base['asset']:raise ValueError('Cloud child and producer use another original immutable base')
        started=datetime.fromisoformat(docs['base_started']['value']['at']);before={}
        for role in ROLES:
            current=actual[role];paths=[d['path'] for d in current['disks']]
            old=before_sample(s['before_samples'][role],started,current['boot_id'],paths);pins+=old['source_pins']
            if role=='metadata':
                source=Path(s['before_samples'][role]['path']).parent/'redis.txt';old['redis']=redis(source.read_text());current['redis']=redis(current['redis_info'])
                if old['redis']['process_id'] not in [p['pid'] for p in old['original_processes']]:raise ValueError('Before INFO and observer process differ')
            if role=='objects':
                service=current['container']
                if not service['running'] or service['restarts'] or service['pid'] not in [p['pid'] for p in old['original_processes']]:raise ValueError('Original object service changed')
                binds=[m['Source'] for m in service['mounts'] if m['Destination'].startswith('/data/rustfs')]
                if len(binds)!=4 or any(not any(Path(bind)==Path(path) or Path(path) in Path(bind).parents for path in paths) for bind in binds):raise ValueError('Object service uses another data path')
            before[role]=old
        rows=forecast(before,actual,s['extra_full_copies'],s['reserve_fraction'])
        write_new(out/'before.json',dict(measurement_basis='available',nodes=before,source_pins=pins))
        write_new(out/'after.json',dict(nodes=actual,source_pins=[ref(out/'raw'/role/'stdout') for role in ROLES]))
        value=dict(schema='packed-c3-capacity-v1',passed=all(row['passed'] for row in rows),cell_id=cell['id'],pool=pool,
            plan_sha256=plan['plan_sha256'],inventory_sha256=digest(runtime/'fleet.json'),base_preparation=dict(path=s['base_preparation'],sha256=docs['base_preparation']['sha256']),
            qualification=dict(path=s['qualification'],sha256=docs['qualification']['sha256']),before=ref(out/'before.json'),after=ref(out/'after.json'),
            reserve_fraction=s['reserve_fraction'],extra_full_copies=s['extra_full_copies'],rows=rows,source_pins=pins,
            scope='Conservative observed-window growth includes C2 archive/code/setup and full Packed parent plus qualification upper. Forecasts seven additional full-window copies on every retained data disk and Redis; does not claim exact producer attribution or clone savings. Before filesystem totals were not recorded.')
        write_new(out/'result.json',value);print(json.dumps(dict(passed=value['passed'],result=ref(out/'result.json'))))
        return 0 if value['passed'] else 2


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--request',type=Path,required=True);p.add_argument('--out',type=Path,required=True)
    a=p.parse_args();raise SystemExit(run(a.request,a.out))
