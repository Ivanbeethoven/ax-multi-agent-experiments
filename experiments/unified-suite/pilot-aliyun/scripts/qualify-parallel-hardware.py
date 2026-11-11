#!/usr/bin/env python3
"""Read actual cloud/host/service values for the two-pool comparison gate."""
import argparse
from datetime import datetime,timezone
import inspect
import json
import re
from pathlib import Path
import shlex
import subprocess
import sys

REPO=Path(__file__).resolve().parents[4]
sys.path.insert(0,str(REPO))
from bench.runtime.common import digest,load,pinned,write_new
from bench.runtime import pilot_context as pilot_runtime
from bench.runtime.pilot_pools import inventories,pool_inventory,fleet_node,alias

FLAGS=('RUSTFS_EC_BLOCK_SIZE_HINT_ENABLE','RUSTFS_EC_BLOCK_SIZE_HINT_FLEET_CONFIRMED',
       'RUSTFS_EC_READ_QUANTUM_ENABLE','RUSTFS_EC_READ_QUANTUM_FLEET_CONFIRMED')
ROLES=('client','metadata','objects')


def ref(path):
    path=Path(path).resolve(strict=True)
    return dict(path=str(path),sha256=digest(path))


def remote_probe(request):
    """Standalone SSH payload. No private env files or credential metadata."""
    import hashlib,json,os,re,socket,subprocess
    from pathlib import Path
    from urllib.request import Request,ProxyHandler,build_opener
    value={'read_only':True,'hostname':socket.gethostname(),'errors':{}}
    def command(argv):return subprocess.check_output(argv,text=True,timeout=20).strip()
    def capture(name,callback):
        try:value[name]=callback()
        except Exception as error:value['errors'][name]=type(error).__name__
    def file(path):return Path(path).read_text().strip()
    def group(path):
        return {name:file(Path('/sys/fs/cgroup')/path.lstrip('/')/name)
                for name in ('cpu.max','memory.max','memory.swap.max')}
    def source(path):
        path=Path(path)
        return {'path':str(path),'sha256':hashlib.sha256(path.read_bytes()).hexdigest(),'value':json.loads(path.read_text())}
    def instance():
        opener=build_opener(ProxyHandler({}))
        with opener.open(Request('http://100.100.100.200/latest/api/token',method='PUT',
             headers={'X-aliyun-ecs-metadata-token-ttl-seconds':'60'}),timeout=3) as response:token=response.read().decode()
        with opener.open(Request('http://100.100.100.200/latest/meta-data/instance-id',
             headers={'X-aliyun-ecs-metadata-token':token}),timeout=3) as response:return response.read().decode().strip()
    capture('instance_id',instance)
    capture('boot_id',lambda:file('/proc/sys/kernel/random/boot_id'))
    capture('cpu_models',lambda:sorted(set(re.findall(r'^model name\s*:\s*(.+)$',file('/proc/cpuinfo'),re.M))))
    capture('affinity_cpu_count',lambda:len(os.sched_getaffinity(0)))
    capture('memory_total_bytes',lambda:int(re.search(r'^MemTotal:\s+(\d+)',file('/proc/meminfo'),re.M)[1])*1024)
    capture('kernel',lambda:command(['uname','-r']))
    capture('private_addresses',lambda:json.loads(command(['ip','-json','-4','address','show'])))
    capture('block_devices',lambda:json.loads(command(['lsblk','--json','--bytes','--paths','--output','PATH,TYPE,SERIAL,SIZE,FSTYPE,MOUNTPOINTS'])))
    roots=([f'/srv/ax-objects-{i}' for i in range(4)] if request['role']=='objects' else [request['work_root']])
    capture('data_mounts',lambda:[dict(path=p,is_mount=os.path.ismount(p),device=Path(p).stat().st_dev) for p in roots])
    if request['role']=='client':capture('slot',lambda:group('/ax-slot-1'))
    if request['role']=='metadata':
        def redis():
            fields=dict(line.split(':',1) for line in command(['redis-cli','--raw','INFO','SERVER']).splitlines() if ':' in line)
            keys=('appendonly','appendfsync','maxmemory','maxmemory-policy','save','databases')
            settings={key:json.loads(command(['redis-cli','--json','CONFIG','GET',key])) for key in keys}
            settings={key:(row[key] if isinstance(row,dict) else row[1]) for key,row in settings.items()}
            cgroup=command(['systemctl','show','ax-pilot-redis','--property=ControlGroup','--value'])
            pid=command(['systemctl','show','ax-pilot-redis','--property=MainPID','--value'])
            return dict(version=fields.get('redis_version'),process_id=fields.get('process_id'),
                version_output=command(['/usr/bin/redis-server','--version']),systemd_pid=pid,
                process_executable_sha256=command(['sha256sum','/proc/'+pid+'/exe']).split()[0],
                io_accounting=command(['systemctl','show','ax-pilot-redis','--property=IOAccounting','--value']),
                active=command(['systemctl','is-active','ax-pilot-redis']),settings=settings,cgroup=group(cgroup),
                binary_sha256=command(['sha256sum','/usr/bin/redis-server']).split()[0],source=source(request['service']['result']))
        capture('redis',redis)
    if request['role']=='objects':
        def rustfs():
            service=request['service'];compose=Path(service['compose'])
            cid=command(['docker','compose','-f',str(compose),'ps','-q','rustfs'])
            fields={'id':'.Id','image_id':'.Image','image_reference':'.Config.Image','running':'.State.Running',
                'nano_cpus':'.HostConfig.NanoCpus','memory_bytes':'.HostConfig.Memory','memory_swap_bytes':'.HostConfig.MemorySwap'}
            fmt='{'+','.join(json.dumps(k)+':{{json '+v+'}}' for k,v in fields.items())+'}'
            actual=json.loads(command(['docker','inspect','--format',fmt,cid]))
            # Whitelist only the four boolean settings; never print .Config.Env.
            flags=request['flags'];tests=['(eq . '+json.dumps(key+'='+v)+')' for key in flags for v in ('true','false')]
            flag_format='{{range .Config.Env}}{{if or '+' '.join(tests)+'}}{{println .}}{{end}}{{end}}'
            actual['flags']=dict(line.split('=',1) for line in command(['docker','inspect','--format',flag_format,cid]).splitlines())
            actual['source_commit']=command(['docker','image','inspect','--format','{{index .Config.Labels "org.opencontainers.image.revision"}}',actual['image_id']])
            actual['repo_digests']=json.loads(command(['docker','image','inspect','--format','{{json .RepoDigests}}',actual['image_id']]))
            actual['binary_sha256']=command(['docker','exec',cid,'sha256sum','/usr/bin/rustfs']).split()[0]
            actual.update(compose_sha256=hashlib.sha256(compose.read_bytes()).hexdigest(),
                source=source(service['result']),image_proof=source(service['image_proof']))
            return actual
        capture('rustfs',rustfs)
    return value


def flatten(devices):
    for device in devices:
        yield device
        yield from flatten(device.get('children',[]))


def evaluate(plan,fleet,cloud,api,observations,node_specs=None):
    """Compare measured values. Missing fields produce failed checks, never True."""
    pools=sorted(p['pool_id'] for p in inventories(cloud));checks={};details={};errors={}
    def check(name,callback):
        try:checks[name]=bool(callback())
        except (KeyError,ValueError,TypeError,StopIteration,IndexError) as error:
            checks[name]=False;errors[name]=type(error).__name__
    check('exactly_two_pools',lambda:pools==['pool-a','pool-b'])
    instances={row['InstanceId']:row for row in api['instances']};disks={row['DiskId']:row for row in api['disks']}
    instance_ids=[];disk_ids=[];network=[];specs=node_specs or {}
    for pool in pools:
        details[pool]={}
        for role in ROLES:
            node=pool_inventory(cloud,pool)['roles'][role];actual=observations.get(alias(pool,role),{})
            instance_ids.append(node['instance_id']);disk_ids+=node['data_disk_ids']
            row=instances.get(node['instance_id'],{});prefix=pool+'/'+role+'/'
            selected=[disks.get(d,{}) for d in node['data_disk_ids']]
            shape={k:row.get(k) for k in ('InstanceType','Cpu','Memory','ZoneId')}
            shape.update(vpc=row.get('VpcAttributes',{}).get('VpcId'),
                data_disks=sorted([(d.get('Category'),d.get('Size'),d.get('PerformanceLevel')) for d in selected],key=repr))
            network.append((shape['ZoneId'],shape['vpc']))
            details[pool][role]=dict(api=shape,cpu_models=actual.get('cpu_models'),
                kernel=actual.get('kernel'),memory_total_bytes=actual.get('memory_total_bytes'),
                instance_id=node['instance_id'],data_disk_ids=node['data_disk_ids'])
            check(prefix+'api_shape_known',lambda:all(bool(shape[k]) for k in ('InstanceType','Cpu','Memory','ZoneId','vpc')) and all(all(bool(x) for x in d) for d in shape['data_disks']))
            check(prefix+'actual_host_identity',lambda:actual['instance_id']==node['instance_id'] and actual['hostname']==node['hostname'] and fleet_node(fleet,pool,role)['host_id']==node['instance_id'])
            check(prefix+'no_probe_errors',lambda:not actual['errors'])
            check(prefix+'actual_private_interface',lambda:any(a.get('local')==node['private_ip'] for i in actual['private_addresses'] for a in i.get('addr_info',[])))
            check(prefix+'host_capacity',lambda:actual['affinity_cpu_count']>=row['Cpu'] and actual['memory_total_bytes']>=row['Memory']*1024**2*.90)
            check(prefix+'role_capacity_with_observer_headroom',lambda:row['Cpu']>=(8 if role=='objects' else 4) and row['Memory']>=(32768 if role=='objects' else 16384))
            check(prefix+'api_disks_attached',lambda:len(selected)==(4 if role=='objects' else 1) and all(d['InstanceId']==node['instance_id'] and d['Type']=='data' and d['ZoneId']==row['ZoneId'] for d in selected))
            check(prefix+'native_data_disks_distinct',lambda:all(d['is_mount'] for d in actual['data_mounts']) and len(actual['data_mounts'])==(4 if role=='objects' else 1) and len({d['device'] for d in actual['data_mounts']})==len(actual['data_mounts']))
            def disk_mapping():
                native=list(flatten(actual['block_devices']['blockdevices']))
                declared=specs.get(pool,{}).get(role,{})
                if declared and (declared['instance_id']!=node['instance_id'] or declared['role']!=role or
                    {d['id'] for d in declared['data_disks']}!=set(node['data_disk_ids'])):return False
                for disk in selected:
                    serial=disk.get('SerialNumber')
                    if not serial:serial=next(d['serial'] for d in declared['data_disks'] if d['id']==disk['DiskId'])
                    matches=[d for d in native if d.get('type')=='disk' and (d.get('serial') or '').strip()==serial]
                    if len(matches)!=1 or int(matches[0]['size'])!=disk['Size']*1024**3:return False
                    mounted={p for d in flatten(matches) for p in (d.get('mountpoints') or []) if p}
                    expected=next((d['mount'] for d in declared.get('data_disks',[]) if d['id']==disk['DiskId']),None)
                    roots={d['path'] for d in actual['data_mounts']}
                    if not mounted.intersection(roots) or (expected and expected not in mounted):return False
                return True
            check(prefix+'native_disk_api_mapping',disk_mapping)
            if role=='client':check(prefix+'agent_slot_2cpu_8g_swap0',lambda:actual['slot']=={'cpu.max':'200000 100000','memory.max':'8589934592','memory.swap.max':'0'})
            if role=='metadata':
                def redis():
                    redis=actual['redis'];source=redis['source']['value']
                    expected={'appendonly':'yes','appendfsync':'always','maxmemory':'10737418240','maxmemory-policy':'noeviction','save':'','databases':'256'}
                    source_ok=source['passed'] is True and ((source.get('action')=='redis' and source.get('instance_id')==node['instance_id']) or
                        ('IOAccounting=yes' in source.get('after','') and re.search(r'MainPID=[1-9][0-9]*',source['after']) is not None))
                    return (redis['active']=='active' and bool(redis['version']) and bool(re.fullmatch('[0-9a-f]{64}',redis['binary_sha256'])) and redis['settings']==expected and
                        redis['cgroup']=={'cpu.max':'300000 100000','memory.max':'12884901888','memory.swap.max':'0'} and
                        redis['systemd_pid']==redis['process_id'] and int(redis['process_id'])>0 and redis['io_accounting']=='yes' and
                        ('v='+redis['version']+' ') in (redis['version_output']+' ') and redis['process_executable_sha256']==redis['binary_sha256'] and source_ok)
                check(prefix+'redis_actual_configuration',redis)
            if role=='objects':
                def rustfs():
                    service=actual['rustfs'];source=service['source']['value'];proof=service['image_proof']['value']
                    image=service['image_reference'];image_digest=image.rsplit('@',1)[1]
                    return (bool(re.fullmatch('[0-9a-f]{40}',service['source_commit'])) and bool(re.fullmatch('[0-9a-f]{64}',service['binary_sha256'])) and
                        bool(re.fullmatch('sha256:[0-9a-f]{64}',image_digest)) and service['running'] is True and source['passed'] is True and source['container_id']==service['id'] and
                        source['image_id']==service['image_id'] and source['compose_sha256']==service['compose_sha256'] and
                        source['binary_sha256']==service['binary_sha256']==proof['binary_sha256'] and
                        service['source_commit']==source['source_commit']==proof['source_commit'] and
                        image_digest==proof['image_digest'] and any(d.endswith('@'+image_digest) for d in service['repo_digests']) and
                        service['flags']=={flag:'true' for flag in FLAGS} and service['nano_cpus']==6000000000 and
                        service['memory_bytes']==service['memory_swap_bytes']==24*1024**3)
                check(prefix+'rustfs_actual_configuration',rustfs)
    check('all_instance_ids_distinct',lambda:len(instance_ids)==len(set(instance_ids)))
    check('all_data_disk_ids_distinct',lambda:len(disk_ids)==len(set(disk_ids)))
    check('same_zone_and_vpc',lambda:len(set(network))==1 and all(x is not None for x in network[0]))
    for role in ROLES:
        check(role+'/matching_api_shape',lambda role=role:details[pools[0]][role]['api']==details[pools[1]][role]['api'])
        check(role+'/matching_actual_cpu_model',lambda role=role:len(details[pools[0]][role]['cpu_models'])==1 and bool(details[pools[0]][role]['cpu_models'][0]) and details[pools[0]][role]['cpu_models']==details[pools[1]][role]['cpu_models'])
        check(role+'/matching_kernel',lambda role=role:bool(details[pools[0]][role]['kernel']) and details[pools[0]][role]['kernel']==details[pools[1]][role]['kernel'])
    check('matching_redis_build',lambda:len({(observations[alias(p,'metadata')]['redis']['version'],observations[alias(p,'metadata')]['redis']['binary_sha256']) for p in pools})==1)
    check('matching_rustfs_build',lambda:len({(observations[alias(p,'objects')]['rustfs']['source_commit'],observations[alias(p,'objects')]['rustfs']['image_reference'].rsplit('@',1)[1],observations[alias(p,'objects')]['rustfs']['binary_sha256']) for p in pools})==1)
    return dict(schema='pilot-cross-pool-equivalence-v1',passed=all(checks.values()),plan_sha256=plan['plan_sha256'],
        pools=pools,checks=checks,unknown_or_failed=[k for k,v in checks.items() if not v],errors=errors,details=details,
        scope='Matching observed hardware, disk placement and current service settings only. This does not establish equal speed, physical-server exclusivity, or performance acceptance. Historical deployment receipts retain their original schema; current Redis PID, executable, CONFIG, version and cgroup are measured separately. Each cell still requires its own six observer-calibration trials and normal correctness/closure checks.')


def main():
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('control','inventory','api-inventory','services','out'):p.add_argument('--'+name,type=Path,required=True)
    pilot_runtime.arguments(p)
    a=p.parse_args();runtime=pilot_runtime.select(a.control,pilot_runtime.reference(a))
    plan=load(a.control/'suite/plan.json');fleet=load(runtime/'fleet.json');cloud=load(a.inventory)
    pools=inventories(cloud);api=load(a.api_inventory);services=load(a.services)
    if any(c['inventory_sha256']!=digest(a.inventory) for c in plan['config']['clusters']):raise ValueError('Cloud inventory differs from frozen plan')
    if not load(runtime/'allocation.json')['passed'] or load(runtime/'allocation.json')['inventory_sha256']!=digest(runtime/'fleet.json'):raise ValueError('Successful matching runtime allocation required')
    a.out.mkdir(parents=True,exist_ok=False);samples={};specs={}
    build_path=a.control/'provenance/build-manifest.json'
    if digest(build_path)!=plan['config']['build_manifest_sha256']:raise ValueError('Build manifest differs from frozen plan')
    pins=[ref(p) for p in (a.control/'suite/plan.json',runtime/'fleet.json',runtime/'allocation.json',build_path,a.inventory,a.api_inventory,a.services,Path(__file__))]
    for pool in pools:
        pool_id=pool['pool_id'];specs[pool_id]={}
        for role in ROLES:
            node=fleet_node(fleet,pool_id,role)
            if node['host_id']!=pool['roles'][role]['instance_id']:raise ValueError('Cloud and SSH fleet disagree')
            if services[pool_id].get('node_specs',{}).get(role):
                pin=services[pool_id]['node_specs'][role];specs[pool_id][role]=load(pinned(pin));pins.append(pin)
            service=services[pool_id].get(role,{})
            for path in service.values():
                if (not isinstance(path,str) or not Path(path).is_absolute() or Path(path).suffix!='.json' or
                    any(word in path.lower() for word in ('/private/','credential','secret'))):raise ValueError('Only explicit public service JSON evidence paths are accepted')
            request=dict(role=role,work_root=node['work_root'],service=service,flags=FLAGS)
            script=inspect.getsource(remote_probe)+'\nimport json\nprint(json.dumps(remote_probe(json.loads('+repr(json.dumps(request))+'))))\n'
            argv=['ssh','-F',str(runtime/'ssh.conf'),node['ssh_alias'],shlex.join([node['python'],'-c',"import sys;exec(compile(sys.stdin.read(),'<parallel-hardware-probe>','exec'))"])]
            result=subprocess.run(argv,input=script,text=True,capture_output=True,timeout=150)
            name=alias(pool_id,role)
            (a.out/(name+'.stderr')).write_text(result.stderr)
            if result.returncode:
                write_new(a.out/(name+'.failed.json'),dict(passed=False,exit_code=result.returncode,read_only=True))
                raise ValueError('SSH probe failed; retained stderr: '+name)
            sample=json.loads(result.stdout);samples[name]=sample
            write_new(a.out/(name+'.json'),sample);pins.append(ref(a.out/(name+'.json')))
    receipt=evaluate(plan,fleet,cloud,api,samples,specs)
    build=load(build_path)
    try:
        source=next(row for row in build['sources'] if row['component']=='rustfs')
        binary=next(row for row in build['files'] if row['file']=='rustfs')
        same=all(samples[alias(pool['pool_id'],'objects')]['rustfs']['source_commit']==source['source_commit'] and
                 samples[alias(pool['pool_id'],'objects')]['rustfs']['binary_sha256']==binary['sha256'] for pool in pools)
    except (KeyError,StopIteration):same=False
    receipt['checks']['rustfs_matches_frozen_build']=same
    receipt['passed']=all(receipt['checks'].values())
    receipt['unknown_or_failed']=[k for k,v in receipt['checks'].items() if not v]
    receipt.update(at=datetime.now(timezone.utc).isoformat(),inventory_sha256=digest(runtime/'fleet.json'),
        cloud_inventory_sha256=digest(a.inventory),source_pins=pins,probe_source_sha256=digest(Path(__file__)))
    write_new(a.out/'result.json',receipt)
    print(json.dumps(dict(passed=receipt['passed'],result=ref(a.out/'result.json'),unknown_or_failed=receipt['unknown_or_failed'])))
    return 0 if receipt['passed'] else 1


if __name__=='__main__':raise SystemExit(main())
