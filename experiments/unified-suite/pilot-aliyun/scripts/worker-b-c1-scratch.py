#!/usr/bin/env python3
"""Isolated B worker for a new C1 randrw diagnostic; no original filesystems writable."""
import contextlib
import hashlib
import json
import os
import re
import shutil
import signal
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

RUST_REV = 'f4ed202508f2153afb7956bae6f7799b60eccd00'
RUST_IMAGE = 'sha256:942893356344d292d9fcccadfceb039467c589ba10b4045cb148c0a65d2d3fec'
BREW_REV = 'ca07159f3da4b670e4aeb878faef928e69bc1634'
BREW_SHA = '814499855a949cad7a8752c04f272fa1b02524b36b7ea77b0e4b3bcc6031c5c0'
JFS_SHA = '6d450d3c151b6affede364d9a198d2505111b0612a5fb4d31613444c635174cc'
GIB = 1024**3

def need(ok, message):
    if not ok:
        raise ValueError(message)

def run(*args, timeout=25, check=True, **kw):
    return subprocess.run(list(map(str,args)), text=True, timeout=timeout, check=check, **kw)

def out(*args, **kw):
    return run(*args, capture_output=True, **kw).stdout.strip()

def write(path, value):
    with path.open('x') as stream:
        json.dump(value, stream, indent=2)
        stream.write('\n')

def sha(path):
    with Path(path).open('rb') as file:
        return hashlib.file_digest(file, 'sha256').hexdigest()

def wait(fn, label, seconds):
    end = time.monotonic()+seconds
    while time.monotonic() < end:
        if (value := fn()):
            return value
        time.sleep(.25)
    raise TimeoutError(label)

def instance_id():
    client=urllib.request.build_opener(urllib.request.ProxyHandler({}))
    base='http://100.100.100.200/latest/'
    with client.open(urllib.request.Request(base+'api/token',method='PUT',
             headers={'X-aliyun-ecs-metadata-token-ttl-seconds':'30'}),timeout=3) as response:
        token=response.read().decode()
    with client.open(urllib.request.Request(base+'meta-data/instance-id',
             headers={'X-aliyun-ecs-metadata-token':token}),timeout=3) as response:
        return response.read().decode()

def block_devices():
    return json.loads(out('lsblk','--json','--bytes','--paths','-o',
                          'NAME,TYPE,SERIAL,SIZE,MAJ:MIN,MOUNTPOINTS'))['blockdevices']

def flatten(dev):
    yield dev
    for child in dev.get('children',[]):
        yield from flatten(child)

def disk_for(devs,disk):
    matches=[d for d in devs if (d.get('serial') or '').strip()==disk[2:] and d['type']=='disk']
    need(len(matches)==1,'Disk serial absent or ambiguous: '+disk)
    return matches[0]

def binds(ip,port):
    with socket.socket() as sock:
        try:
            sock.bind((ip,port))
        except OSError:
            return False
    return True

def reaches(ip,port):
    try:
        with socket.create_connection((ip,port),timeout=.4):
            return True
    except OSError:
        return False

def mount_info(path):
    for line in Path('/proc/self/mountinfo').read_text().splitlines():
        left,right=line.split(' - ',1)
        parts=left.split()
        if parts[4] == str(path):
            return dict(id=int(parts[0]),device=parts[2],filesystem=right.split()[0],source=right.split()[1])
    return None

def samples(paths):
    result=[]
    for path in paths:
        info=mount_info(path)
        need(info and info['filesystem']=='ext4','Missing scratch ext4 mount')
        v=os.statvfs(path)
        fields=next(line.split()[2:] for line in Path('/proc/diskstats').read_text().splitlines()
                    if ':'.join(line.split()[:2])==info['device'])
        result.append(dict(path=str(path),device=info['device'],free=v.f_bavail*v.f_frsize,
                           total=v.f_blocks*v.f_frsize,free_inodes=v.f_favail,total_inodes=v.f_files,
                           reads=int(fields[1]),read_bytes=int(fields[3])*512,
                           writes=int(fields[5]),write_bytes=int(fields[7])*512))
    return result

def arm_unit(root, product):
    return root.name+'-'+product+'.service'


def submit_arm(root, product, limit):
    need(240 <= limit <= 1150,'Arm deadline outside the reviewed scratch window')
    claim=root/(product+'-launch.json')
    try:
        write(claim,dict(unit=arm_unit(root,product),limit_seconds=limit,utc_ns=time.time_ns()))
    except FileExistsError:
        return dict(submitted=False,already_claimed=True)
    run('systemd-run','--quiet','--no-block','--unit',arm_unit(root,product),
        '--property=Restart=no','--property=KillMode=control-group','--property=TimeoutStopSec=15',
        '--property=CPUQuota=200%','--property=MemoryMax=8G','--property=MemorySwapMax=0',
        f'--property=RuntimeMaxSec={limit}',sys.executable,Path(__file__).resolve(),'arm',root.name,product)
    return dict(submitted=True,already_claimed=False)


def arm_status(root,product):
    unit=arm_unit(root,product)
    props=('LoadState','ActiveState','SubState','ExecMainStatus','InvocationID','MainPID','Result','NRestarts')
    proc=run('systemctl','show',unit,*('-p'+p for p in props),check=False,capture_output=True)
    values=dict(row.split('=',1) for row in proc.stdout.splitlines() if '=' in row)
    for name in ('launch','runtime','terminal'):
        path=root/(product+'-'+name+'.json')
        values[name]=json.loads(path.read_bytes()) if path.exists() else None
    result=root/product/'result.json'
    values['observed_result_sha256']=sha(result) if result.is_file() else None
    values['utc_ns']=time.time_ns()
    return values


def run_owned_arm(root,spec,product):
    need((root/(product+'-launch.json')).is_file(),'Run requires a single launch claim')
    invocation=os.environ.get('INVOCATION_ID','')
    need(re.fullmatch('[0-9a-f]{32}',invocation),'Systemd arm invocation ID is required')
    self_start=int(Path('/proc/self/stat').read_text().rsplit(')',1)[1].split()[19])
    runtime=dict(invocation=invocation,pid=os.getpid(),start_ticks=self_start,utc_ns=time.time_ns())
    write(root/(product+'-runtime.json'),runtime)
    terminal=root/(product+'-terminal.json')
    try:
        arm(root,spec,product)
    except Exception as exc:
        write(terminal,dict(passed=False,error_type=type(exc).__name__,runtime=runtime,utc_ns=time.time_ns()))
        raise
    write(terminal,dict(passed=True,runtime=runtime,utc_ns=time.time_ns(),
                        result_sha256=sha(root/product/'result.json')))


def main():
    action,tag=sys.argv[1:3]
    need(re.fullmatch(r'ax-c1b-[0-9a-f]{10}',tag),'Exact owned scratch tag required')
    root=Path('/var/tmp')/tag
    object_name=tag+'-rustfs'
    redis_unit=tag+'-redis.service'
    watch_unit=tag+'-watch.service'
    need(not root.is_symlink() and root.resolve()==root,'Unsafe scratch root')
    if action=='init':
        need(not root.exists(),'Scratch attempt exists; no replay')
        spec=json.load(sys.stdin)
        role=spec['role']
        identity=spec['inventory'][role]
        need(instance_id()==identity['instance_id'] and socket.gethostname()==identity['hostname'],
             'Wrong worker identity')
        scratch=spec['scratch_disk_ids']
        original={d for r in spec['inventory'].values() for d in r['data_disk_ids']}
        need(len(scratch)==len(set(scratch))==4 and not set(scratch)&original and
             all(re.fullmatch(r'd-[a-z0-9]+',d) for d in scratch),'Four exact new scratch disk IDs required')
        devices=block_devices()
        for disk in identity['data_disk_ids']:
            row=disk_for(devices,disk)
            need(not any(any(x['mountpoints'] or []) for x in flatten(row)),'Original data disk already mounted: '+disk)
        need(shutil.disk_usage('/var/tmp').free>8*GIB,'System staging requires at least 8 GiB')
        root.mkdir(mode=0o700)
        private=root/'private'; private.mkdir(mode=0o700)
        write(private/'spec.json',spec)
        write(root/'probe.json',dict(role=role,instance_id=identity['instance_id'],
              boot_id=Path('/proc/sys/kernel/random/boot_id').read_text().strip(),
              hostname=identity['hostname'],original_disks_unmounted=True,
              system_free_bytes=shutil.disk_usage('/var/tmp').free,
              kernel=out('uname','-r'),clock=out('timedatectl','show','-p','NTPSynchronized',check=False)))
        if role=='objects':
            [image]=json.loads(out('docker','image','inspect',RUST_IMAGE))
            need(image['Id']==RUST_IMAGE and image['Config']['Labels']['org.opencontainers.image.revision']==RUST_REV,
                 'Runtime image/source pin mismatch')
            need(not out('docker','ps','-q') and binds(identity['private_ip'],9000),'Object node already in use')
            owned=[disk_for(devices,disk) for disk in scratch]
            need(len({d['name'] for d in owned})==4 and all(d['size']==100*GIB and not d.get('children')
                 and not any(d['mountpoints'] or []) and not out('wipefs','--noheadings','--no-act',d['name'])
                 for d in owned),'Only four provably blank dedicated 100 GiB disks may be formatted')
            write(root/'format-intent.json',dict(devices=owned,disk_ids=scratch))
            for i,d in enumerate(owned):
                mount=root/f'data{i}'
                mount.mkdir()
                run('mkfs.ext4','-q','-F','-m','0','-i','4096',d['name'],timeout=50)
                run('mount','-o','nodev,nosuid',d['name'],mount)
                os.chown(mount,10001,10001)
            write(root/'object-disks.json',samples([root/f'data{i}' for i in range(4)]))
            logs=root/'rustlogs'; logs.mkdir(); os.chown(logs,10001,10001)
            env=dict(RUSTFS_ACCESS_KEY=spec['access'],RUSTFS_SECRET_KEY=spec['secret'],
              RUSTFS_ADDRESS=identity['private_ip']+':9000',RUSTFS_CONSOLE_ENABLE='false',
              RUSTFS_VOLUMES='/data/rustfs{0...3}',RUSTFS_OBS_LOG_DIRECTORY='/var/log/rustfs',
              RUSTFS_OBS_LOGGER_LEVEL='warn',RUSTFS_OBS_METRICS_EXPORT_ENABLED='false',
              RUSTFS_EC_BLOCK_SIZE_HINT_ENABLE='true',RUSTFS_EC_BLOCK_SIZE_HINT_FLEET_CONFIRMED='true',
              RUSTFS_EC_READ_QUANTUM_ENABLE='true',RUSTFS_EC_READ_QUANTUM_FLEET_CONFIRMED='true')
            f=private/'rustfs.env'
            f.write_text(''.join(f'{k}={v}\n' for k,v in env.items()));f.chmod(0o600)
            volumes=[x for i in range(4) for x in ('--mount',f'type=bind,src={root}/data{i},dst=/data/rustfs{i}')]
            cid=out('docker','run','-d','--pull','never','--restart','no','--name',object_name,
                    '--label','ax.c1.scratch='+tag,'--network','host','--cpus','6','--memory','24g',
                    '--memory-swap','24g','--env-file',f,*volumes,'--mount',
                    f'type=bind,src={logs},dst=/var/log/rustfs',RUST_IMAGE)
            wait(lambda:reaches(identity['private_ip'],9000),'RustFS readiness',60)
            write(root/'rustfs.json',dict(container=cid,image=RUST_IMAGE,revision=RUST_REV,
                  independent_disks=4,unsafe_disk_bypass=False,dynamic_layout=True))
            run('systemd-run','--quiet','--unit',watch_unit,'--property=RuntimeMaxSec=3000',
                sys.executable,Path(__file__).resolve(),'watch',tag)
        elif role=='metadata':
            need(binds(identity['private_ip'],6379),'Redis address already in use')
            data=root/'redis';data.mkdir()
            cfg=private/'redis.conf'
            cfg.write_text(f'bind {identity["private_ip"]}\nprotected-mode yes\nport 6379\n'
                           f'requirepass {spec["redis_secret"]}\ndir {data}\n'
                           'daemonize no\ndatabases 256\nappendonly yes\nappendfsync always\n'
                           'save ""\nmaxmemory 10737418240\nmaxmemory-policy noeviction\n')
            cfg.chmod(0o600)
            run('systemd-run','--quiet','--unit',redis_unit,'--property=RuntimeMaxSec=3000',
                '--property=CPUQuota=300%','--property=MemoryMax=12G','--property=MemorySwapMax=0',
                '/usr/bin/redis-server',cfg)
            wait(lambda:reaches(identity['private_ip'],6379),'Redis readiness',40)
            write(root/'redis.json',dict(version=out('/usr/bin/redis-server','--version'),
                  appendonly=True,appendfsync='always',private_ip=identity['private_ip']))
        elif role=='client':
            dev=disk_for(devices,identity['data_disk_ids'][0])
            orig=root/'old-read-only';orig.mkdir()
            run('unshare','--mount','--propagation','private','sh','-e','-c',
                'mount -o ro,noload,nodev,nosuid,noexec "$1" "$2"; '
                'cp "$2/assets/bin/juicefs" "$3/juicefs-binary"; '
                'cp "$2/build-ca07159-01/bin/brewfs" "$3/brewfs-binary"; umount "$2"',
                'read-artifacts',dev['name'],orig,private,timeout=35)
            need(not mount_info(orig) and sha(private/'juicefs-binary')==JFS_SHA and sha(private/'brewfs-binary')==BREW_SHA,
                 'Client binary integrity or old mount cleanup failed')
            jfs=out(private/'juicefs-binary','version')
            brew=out(private/'brewfs-binary','--version')
            need('1.4.2' in jfs and BREW_REV in brew,'Filesystem binary version mismatch')
            write(root/'client-binaries.json',dict(juicefs=jfs,brewfs=brew,juicefs_sha256=JFS_SHA,
                  brewfs_sha256=BREW_SHA,fio=out('/usr/bin/fio','--version'),fio_sha256=sha('/usr/bin/fio')))
        else:
            raise ValueError('Unknown worker role')
        print(json.dumps(dict(passed=True,role=role)),flush=True)
        return

    private=root/'private'
    spec=json.loads((private/'spec.json').read_text())
    role=spec['role']
    paths=[root/f'data{i}' for i in range(4)]
    if action=='watch':
        need(role=='objects','Object-only sampler')
        with (root/'disk-series.ndjson').open('x',buffering=1) as stream:
            while True:
                rows=samples(paths)
                stream.write(json.dumps(dict(utc_ns=time.time_ns(),monotonic_ns=time.monotonic_ns(),disks=rows))+'\n')
                if any(d['free'] < (d['total']+4)//5+2*GIB or d['free_inodes'] < (d['total_inodes']+4)//5 for d in rows):
                    write(root/'capacity-stop.json',dict(utc_ns=time.time_ns(),disks=rows))
                    run('docker','stop','-t','15',object_name,check=False,timeout=20)
                    return
                time.sleep(2)
    elif action=='headroom':
        rows=samples(paths)
        need((root/'disk-series.ndjson').is_file() and (root/'disk-series.ndjson').stat().st_size>0 and
             out('systemctl','is-active',watch_unit,check=False)=='active' and
             not (root/'capacity-stop.json').exists() and all(d['free'] >= (d['total']+4)//5+28*GIB for d in rows),
             'Per-arm free bytes cannot cover preserved reserve, historical growth and margin')
        print(json.dumps(rows))
    elif action in ('arm','launch-arm','arm-status'):
        need(role=='client' and sys.argv[3] in ('brewfs','juicefs'),'Known client arm required')
        product=sys.argv[3]
        if action=='launch-arm':
            print(json.dumps(submit_arm(root,product,int(sys.argv[4]))))
        elif action=='arm-status':
            print(json.dumps(arm_status(root,product)))
        else:
            run_owned_arm(root,spec,product)
    elif action=='close':
        if role=='objects':
            run('systemctl','stop',watch_unit,check=False)
            inspection=run('docker','inspect',object_name,capture_output=True,check=False)
            if inspection.returncode==0:
                [item]=json.loads(inspection.stdout)
                need(item['Config']['Labels'].get('ax.c1.scratch')==tag,'Wrong container ownership')
                with (root/'rustfs.log').open('xb') as log:
                    run('docker','logs',object_name,stdout=log,stderr=log,check=False)
                run('docker','rm','-f',object_name)
            for path in reversed(paths):
                if mount_info(path):
                    run('umount',path)
        elif role=='metadata':
            run('systemctl','stop',redis_unit,check=False)
            need(not reaches(spec['inventory'][role]['private_ip'],6379),'Owned Redis is still listening')
        else:
            for product in ('brewfs','juicefs'):
                if (root/(product+'-launch.json')).is_file():
                    before=arm_status(root,product)
                    if before.get('ActiveState') in ('active','activating','deactivating','reloading'):
                        run('systemctl','stop',arm_unit(root,product),timeout=25)
                    after=arm_status(root,product)
                    need(after.get('ActiveState') in ('inactive','failed') or
                         after.get('LoadState')=='not-found','Owned arm remains live during cleanup')
                    write(root/(product+'-cleanup-unit.json'),dict(before=before,after=after))
                point=root/product/'mount'
                if mount_info(point):
                    run('fusermount3','-u',point)
        write(root/'close.json',dict(passed=True,role=role,utc_ns=time.time_ns()))
    else:
        raise ValueError('Unknown worker action')


def arm(root,spec,product):
    from fio_workload import run as run_fio
    import boto3
    from botocore.config import Config
    a=root/product
    a.mkdir(mode=0o700)
    private=root/'private'
    pconfig=private/product; pconfig.mkdir(mode=0o700)
    mp=a/'mount';mp.mkdir()
    env=dict(os.environ)
    objects=spec['inventory']['objects']['private_ip']
    metadata=spec['inventory']['metadata']['private_ip']
    bucket=root.name+'-'+product
    endpoint='http://'+objects+':9000'
    redis=f'redis://:{spec["redis_secret"]}@{metadata}:6379/'+str(31 if product=='brewfs' else 32)
    env.update(HOME=str(pconfig),AWS_ACCESS_KEY_ID=spec['access'],AWS_SECRET_ACCESS_KEY=spec['secret'],
               ACCESS_KEY=spec['access'],SECRET_KEY=spec['secret'],AWS_REGION='us-east-1',
               AWS_DEFAULT_REGION='us-east-1',AWS_EC2_METADATA_DISABLED='true',
               NO_PROXY='127.0.0.1,'+objects+','+metadata,no_proxy='127.0.0.1,'+objects+','+metadata,
               RUST_LOG='error',NO_COLOR='1')
    client=boto3.client('s3',endpoint_url=endpoint,region_name='us-east-1',
             aws_access_key_id=spec['access'],aws_secret_access_key=spec['secret'],
             config=Config(signature_version='s3v4',connect_timeout=3,read_timeout=5,
                           retries={'max_attempts':0},s3={'addressing_style':'path'}))
    client.create_bucket(Bucket=bucket)
    client.close()
    binary=private/(product+'-binary')
    need(binary.is_file(),'Pinned filesystem binary missing')
    settings=spec['client_settings'][product]
    url='http://127.0.0.1:'+('8910' if product=='brewfs' else '9567')+'/metrics'
    opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
    if product=='juicefs':
        with (pconfig/'format.log').open('xb') as log:
            run(binary,'format','--storage','s3','--bucket',endpoint+'/'+bucket,'--block-size','4M',
                '--compress','none','--trash-days','0',redis,bucket,env=env,stdout=log,stderr=log,timeout=90)
    proc=None
    fio='/usr/bin/fio'
    def event(label,**payload):
        with (a/'phases.ndjson').open('a',buffering=1) as stream:
            stream.write(json.dumps(dict(label=label,utc_ns=time.time_ns(),monotonic_ns=time.monotonic_ns(),**payload))+'\n')
    def fio_phase(path,label,phase):
        event(label+'-start')
        result=run_fio(dict(root=str(path),binary=dict(path=fio,sha256=sha(fio)),
                           params=spec['params'],seed=9173),phase,
                       (root/label if label=='fixture-prepare' else a/label))
        need(result['passed'],'fio phase rejected: '+label)
        event(label+'-end',application_bytes=result['application_bytes'])
        return result
    def open_fs(label):
        nonlocal proc
        need(proc is None and not mount_info(mp),'Prior mount must be absent before new process')
        if product=='juicefs':
            argv=[binary,'mount',redis,mp,'--cache-dir',settings['cache_dir'],
                '--cache-size',settings['cache_mib'],'--buffer-size',settings['buffer_mib'],
                '--enable-xattr','--enable-cap','--attr-cache',0,'--entry-cache',0,'--dir-entry-cache',0,
                '--open-cache',0,'--prefetch',settings['prefetch'],'--max-readahead',settings['max_readahead_mib'],
                '--backup-meta',settings['backup_meta'],'--verify-cache-checksum',settings['verify_cache_checksum'],
                '--cache-large-write=true','--no-usage-report=true','--metrics','127.0.0.1:9567']
        else:
            cfg=dict(settings)
            cfg.update(meta={'backend':'redis','redis':{'url':redis}},
                data={'backend':'s3','s3':dict(bucket=bucket,endpoint=endpoint,region='us-east-1',
                                            force_path_style=True,rustfs_ec_block_size_hint='auto')},
                layout={'chunk_size':67108864,'block_size':4194304})
            cfg['fuse']=dict(settings['fuse'],workers=4,max_background=512)
            cache=pconfig/('cache-'+label);cache.mkdir()
            cfg['cache']=dict(settings['cache'],cache_root=str(cache))
            config=pconfig/(label+'.json');write(config,cfg)
            argv=[binary,'mount','--config',config,mp,'--s3-rustfs-ec-block-size-hint','auto',
                  '--metrics','--metrics-addr','127.0.0.1:8910']
        with (pconfig/(label+'.log')).open('xb') as log:
            proc=subprocess.Popen(list(map(str,argv)),env=env,stdout=log,stderr=log)
        def ready():
            need(proc.poll() is None,'Filesystem exited before mount readiness')
            info=mount_info(mp)
            if not info:
                return None
            need(info['filesystem'] in ('fuse','fuse.brewfs') if product=='brewfs'
                 else info['filesystem']=='fuse.juicefs','Wrong filesystem mounted')
            return info
        info=wait(ready,'FUSE readiness',60)
        start_ticks=int(Path(f'/proc/{proc.pid}/stat').read_text().rsplit(')',1)[1].split()[19])
        identity=(proc.pid,start_ticks)
        event(label+'-mounted',mount=info,pid=proc.pid,start_ticks=start_ticks)
        return identity
    def close_fs(label):
        nonlocal proc
        if product=='juicefs':
            run(binary,'umount',mp,env=env,capture_output=True,timeout=30)
        else:
            proc.send_signal(signal.SIGINT)
        code=proc.wait(timeout=40)
        need(code==0 and not mount_info(mp),'Filesystem did not exit cleanly')
        event(label+'-unmounted',exit_code=code,pid=proc.pid)
        proc=None
    def snapshot(label):
        if product=='brewfs':
            raw=(mp/'.stats').read_text()
            keys=(
                'brewfs_writeback_live_dirty_bytes','brewfs_writeback_recent_pending_upload_bytes',
                'brewfs_writeback_stage_inflight_bytes','brewfs_writeback_remote_upload_inflight_bytes',
                'brewfs_writeback_live_slices','brewfs_writeback_recent_pending_upload_slices','brewfs_writeback_metadata_inflight',
            )
        else:
            with opener.open(url,timeout=4) as f:
                raw=f.read().decode()
            keys=('juicefs_staging_block_bytes','juicefs_staging_blocks','juicefs_staging_writing_blocks')
        selected=[]
        for line in raw.splitlines():
            if line and not line.startswith('#'):
                head,*parts=line.split()
                if head.split('{',1)[0] in keys and parts:
                    selected.append((head,float(parts[0])))
        with (a/(label+'.prom')).open('x') as f:
            f.write(raw)
        return selected, set(k.split('{',1)[0] for k,v in selected) == set(keys)
    def snapshot_ready(label):
        def collect():
            try:
                return snapshot(label)
            except OSError:
                return None
        return wait(collect,label+' native counters',20)

    try:
        fixture=root/'fixture'
        if not fixture.exists():
            fixture.mkdir()
            fio_phase(fixture,'fixture-prepare','prepare')
            write(root/'input.json',dict(bytes=67108864,sha256=sha(fixture/'fio-0000.dat'),seed=9173))
        canonical=json.loads((root/'input.json').read_text())['sha256']
        before=open_fs('prepare')
        target=mp/'fio-0000.dat'
        need(not target.exists(),'Expected new volume with no existing fio fixture')
        with (fixture/'fio-0000.dat').open('rb') as source,target.open('xb') as dest:
            shutil.copyfileobj(source,dest,1024**2)
            dest.flush()
            os.fsync(dest.fileno())
        need(sha(target)==canonical,'Materialized input differs')
        close_fs('prepare')
        active=open_fs('measure')
        need(active != before,'Need fresh measurement mount process')
        snapshot_ready('before-measure')
        measured=fio_phase(mp,'measure','measure')
        debts=[]
        for i in range(2):
            if i:
                time.sleep(1.05)
            timestamp=time.time_ns()
            values, complete=snapshot_ready('debt-'+str(i))
            need(complete and all(v==0 for k,v in values),'Missing or nonzero native upload debt')
            debts.append(dict(utc_ns=timestamp,metrics=len(values)))
        event('drain-end')
        close_fs('measure')
        cold=open_fs('verify')
        need(cold not in (before,active),'Need independent cold remount process')
        verified=fio_phase(mp,'cold-verify','verify')
        cold_sha=sha(target)
        close_fs('verify')
        job=measured['native_jobs'][0]
        write(a/'result.json',dict(passed=True,product=product,read_bytes=job['read']['io_bytes'],
              write_bytes=job['write']['io_bytes'],read_ios=job['read']['total_ios'],write_ios=job['write']['total_ios'],
              read_iops=job['read']['iops'],write_iops=job['write']['iops'],measure_seconds=measured['wrapper_wall_seconds'],
              seed=9173,input_sha256=canonical,cold_sha256=cold_sha,cold_crc_bytes=verified['application_bytes'],
              native_zero_debt=debts,claim='isolated pilot, no historic acceptance and no calibrated ranking'))
    finally:
        if proc is not None:
            if mount_info(mp):
                run('fusermount3','-u',mp,check=False,capture_output=True,timeout=15)
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=5)


if __name__=='__main__':
    main()
