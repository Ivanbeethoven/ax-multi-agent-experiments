#!/usr/bin/env python3
"""Pool A lock owner for the bounded C2 qualification; ECS lifecycle is external."""
import argparse
import base64
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import sys
import time

CONTROL=Path('/srv/ax-pilot-control')
CANON=CONTROL/'parallel-control-01'
PREP=CONTROL/'preparation'
IDS=dict(client='i-bp1809mw7rlujtj9f705',metadata='i-bp1gy3cfuj9jik0y4oc3',
         objects='i-bp1h9kaoya0predubdkn')
BREWFS='ca07159f3da4b670e4aeb878faef928e69bc1634'
RUSTFS='f4ed202508f2153afb7956bae6f7799b60eccd00'
IMAGE='sha256:942893356344d292d9fcccadfceb039467c589ba10b4045cb148c0a65d2d3fec'
CONTAINER='ax-pilot-20261111-objects-01-rustfs-1'
SWITCHES=('BLOCK_SIZE_HINT_ENABLE','BLOCK_SIZE_HINT_FLEET_CONFIRMED',
          'READ_QUANTUM_ENABLE','READ_QUANTUM_FLEET_CONFIRMED')
SLOT_SHA='3592411238e1af9eb21980c78d61394c24ea33f74d6452babd335a497d0b5d55'
RESTORE_SHA='09616fb18b8f3e75ec7425d19c5171d1b138b9da00e79d15436725b028f60f3d'
FILES=(
    'intent.json','input-inventory.json','initial-tree.json','inventory.exit.json','initial-tree.exit.json',
    'asset-intent.json','producer.exit.json','init.exit.json','packed.json',
    'writer-ready.json','writer-stop.json','strict.exit.json','strict-proof/started.json',
    'strict-proof/command.json','strict-proof/stderr.log','strict-proof/replay.json',
    'strict-proof/verification.json','writer-debt-0.stdout','writer-debt-1.stdout',
    'writer-fsync.exit.json','cold-ready.json','cold-stop.json','cold.exit.json',
    'cold-proof/verification.json','cold-proof/replay-source.json','cold-debt-0.stdout',
    'cold-debt-1.stdout','scratch-cleanup.exit.json','scratch-cleanup.stdout','result.json',
)
DRIVES={'client':['/srv/ax-client'],'metadata':['/srv/ax-metadata'],
        'objects':['/srv/ax-objects-'+str(i) for i in range(4)]}
DISK_IDS={'client':['d-bp1809mw7rlujtjc8epi'],'metadata':['d-bp1gy3cfuj9jik0upt3k'],
          'objects':['d-bp1h9kaoya0predx6kv'+tail for tail in 'defg']}
CHECK_DRIVES="""import json,os,pathlib,socket,subprocess,sys
rows=[]
for p in sys.argv[1:]:
    if not os.path.ismount(p): raise ValueError('Missing original data mount')
    s=os.statvfs(p)
    source=subprocess.check_output(['findmnt','-nr','--output','SOURCE','--mountpoint',p],text=True).strip()
    if not source.startswith('/dev/'): raise ValueError('Data disk source must be a device')
    serial=subprocess.check_output(['lsblk','-dn','-o','SERIAL',source],text=True).strip()
    rows.append(dict(path=p,source=source,serial=serial,device=os.stat(p).st_dev,total_bytes=s.f_blocks*s.f_frsize,
        available_bytes=s.f_bavail*s.f_frsize,total_inodes=s.f_files,available_inodes=s.f_favail))
print(json.dumps(dict(hostname=socket.gethostname(),
    boot_id=pathlib.Path('/proc/sys/kernel/random/boot_id').read_text().strip(),rows=rows)))
"""
READ_FILES="""import base64,hashlib,json,os,pathlib,sys
root=pathlib.Path(sys.argv[1])
out={}
for name in sys.argv[2:]:
    p=root/name
    if not p.is_file() or p.is_symlink(): continue
    if not p.resolve().is_relative_to(root): raise ValueError('Outside worker evidence root')
    with p.open('rb') as f: raw=f.read(2*1024*1024+1)
    if len(raw)>2*1024*1024: raise ValueError('Oversized allowlisted evidence')
    out[name]=dict(sha256=hashlib.sha256(raw).hexdigest(),body=base64.b64encode(raw).decode())
print(json.dumps(out))
"""


def require(ok,reason):
    if not ok:
        raise ValueError(reason)


def read(path):
    return json.loads(path.read_text())


def digest(path):
    with path.open('rb') as f:
        return hashlib.file_digest(f,'sha256').hexdigest()


def save(path,value):
    with path.open('x') as f:
        json.dump(value,f,indent=2)
        f.write('\n')


def exact_stopped(api):
    if not isinstance(api,dict) or not isinstance(api.get('Instances'),dict):
        return False
    rows=api['Instances'].get('Instance')
    return bool(api.get('RequestId')) and api.get('TotalCount')==3 and isinstance(rows,list) and len(rows)==3 \
        and all(isinstance(x,dict) for x in rows) \
        and {x.get('InstanceId') for x in rows}==set(IDS.values()) \
        and all(x.get('Status')=='Stopped' and x.get('StoppedMode')=='StopCharging'
                and x.get('ZoneId')=='cn-hangzhou-j' for x in rows)


def latest_ok(receipt, now):
    expected={'brewfs':('https://github.com/brewfs/brewfs','main',BREWFS),
              'rustfs':('https://github.com/Ivanbeethoven/rustfs','feat/dynamic-block-size-rebased',RUSTFS)}
    rows=receipt.get('sources',[])
    if receipt.get('passed') is not True or len(rows)!=2 or {x.get('component') for x in rows}!=set(expected):
        return False
    age=(now-datetime.fromisoformat(receipt['at'])).total_seconds()
    return 0<=age<=900 and all(
        (x.get('repository'),x.get('branch'),x.get('built_commit'))==expected[x['component']]
        and x.get('built_commit')==x.get('remote_commit') and x.get('matches') is True
        and x.get('lookup_exit_code')==0 for x in rows)


def authorization_ok(value,tag,database,*,controller_sha,worker_sha,latest_sha,before_sha):
    fields={'schema','source','user_instruction','user_followup','formal_sample','scope','tag','database',
            'controller_sha256','worker_sha256','latest_sha256','before_sha256'}
    return isinstance(value,dict) and set(value)==fields and value.get('schema')=='c2-current-a-launch-authorization-v1' \
        and value.get('source')=='conversation-user-instruction' \
        and value.get('user_instruction')=='设定目标 完成所有的c1~c4的小规模实验吧' \
        and value.get('user_followup')=='继续完成实验' and value.get('formal_sample') is False \
        and value.get('scope')=='A-only stock workspace-v1 Packed C2 qualification, 45 minute cap' \
        and value.get('tag')==tag and type(value.get('database')) is int and value['database']==database \
        and value.get('controller_sha256')==controller_sha and value.get('worker_sha256')==worker_sha \
        and value.get('latest_sha256')==latest_sha and value.get('before_sha256')==before_sha


def restore_payload(path):
    require(path.is_file() and not path.is_symlink(),'Staged A restore helper missing')
    payload=path.read_bytes()
    require(hashlib.sha256(payload).hexdigest()==RESTORE_SHA,'Staged A restore helper SHA differs')
    return payload


def restore_command(role,tag):
    require(role in IDS and re.fullmatch(r'c2-current-a-[0-9]{8}t[0-9]{6}z',tag),'Invalid restore role or tag')
    stage='/srv/ax-receipts/'+tag+'-restore'
    helper=stage+'/a-pool-up.py'
    argv=['timeout','--kill-after=8s','105s','python3','-B',helper,role,
          '--out','/srv/ax-receipts/a-pool-up/'+role+'-'+tag]
    if role=='objects':
        argv.append('--without-legacy-observer-scratch')
    return ('set -euC; umask 077; '+shlex.join(['mkdir','-m','700',stage])
            +'; cat > '+shlex.quote(helper)
            +'; '+shlex.join(['printf','%s  %s\\n',RESTORE_SHA,helper])+' | sha256sum -c --status'
            +'; exec '+shlex.join(argv))


def persist_payload(out,payload):
    manifest={}
    require(isinstance(payload,dict) and set(payload)<=set(FILES),'Unexpected evidence names')
    for name,v in payload.items():
        body=base64.b64decode(v['body'],validate=True)
        require(len(body)<=2*1024*1024 and hashlib.sha256(body).hexdigest()==v['sha256'],
                'Evidence transfer digest mismatch')
        path=out/'worker'/name
        path.parent.mkdir(parents=True,exist_ok=True)
        with path.open('xb') as f:
            f.write(body)
        manifest[name]=v['sha256']
    save(out/'worker-sha256.json',manifest)
    return manifest


def complete_worker(out):
    result=read(out/'worker/result.json')
    require(result.get('passed') is True and result.get('formal_sample') is False,'Worker did not pass')
    expected_stop=dict(unmount_exit=0,exit_code=0,mount_absent=True)
    mounts=[read(out/'worker'/f'{side}-ready.json') for side in ('writer','cold')]
    require(mounts[0]['pid']!=mounts[1]['pid'] and mounts[0]['boot_id']==mounts[1]['boot_id']
            and mounts[0]['workspace']==mounts[1]['workspace']
            and mounts[0]['namespace']==mounts[1]['namespace'],'Cold mount did not independently reopen the same volume')
    require(all(read(out/'worker'/f'{side}-stop.json')==expected_stop for side in ('writer','cold')),
            'Normal mount receipts are required')
    for field,path in (('strict','strict-proof/verification.json'),('cold','cold-proof/verification.json')):
        p=out/'worker'/path
        require(result[field]['sha256']==digest(p) and read(p).get('passed') is True,
                'Bound strict and cold receipts are required')
    require(read(out/'worker/cold-proof/replay-source.json').get('sha256')==
            digest(out/'worker/strict-proof/replay.json'),'Cold receipt does not reference the strict replay')
    require(read(out/'worker/packed.json').get('wire')==5,'Missing bound wire005 record')
    require(read(out/'worker/scratch-cleanup.exit.json').get('exit_code')==0,'Owned scratch cleanup proof is missing')


def run(args):
    self=Path(__file__).resolve(strict=True)
    out=self.parent
    tag=out.name
    require(out.parent==PREP and re.fullmatch(r'c2-current-a-[0-9]{8}t[0-9]{6}z',tag),
            'Stage in a fresh dedicated coordinator preparation directory')
    require(20<=args.database<=255 and args.database not in (54,70,243),'Select a dedicated unused Redis DB')
    worker=out/'qualify-c2-current-a.py'
    latest,before=out/'latest.json',out/'instances-before.json'
    authorization=read(out/'launch.authorization.json')
    require(authorization_ok(authorization,tag,args.database,controller_sha=digest(self),worker_sha=digest(worker),
                             latest_sha=digest(latest),before_sha=digest(before)),
            'Recorded C2 user authorization must bind exact staged code and preflight receipts')
    require(latest_ok(read(latest),datetime.now(timezone.utc)),'Fresh matching build branch tips required')
    require(exact_stopped(read(before)) and 0<=time.time()-before.stat().st_mtime<=300,
            'Fresh API receipt for exactly three stopped original A hosts required')
    restore_source=restore_payload(out/'a-pool-up.py')
    sys.path.insert(0,str(CONTROL/'code-12'))
    from bench.runtime.pilot_pools import active_conflicts,cell_resources,resource_guard
    plan=read(CANON/'suite/plan.json')
    cell=next(c for c in plan['cells'] if c['pool']=='pool-a' and c['workload']=='trace')
    require(set(cell_resources(plan,cell))=={'host:'+i for i in IDS.values()} |
            {'disk:'+d for group in DISK_IDS.values() for d in group},
            'A lock set does not bind the expected physical hosts and disks')
    controls=lambda: sorted(p.parent for p in CONTROL.glob('*/state.sqlite'))
    active=controls()
    require(CANON in active and len(active)>=5,'Known controls missing')
    flags=['-F',str(CANON/'ssh.conf'),'-oBatchMode=yes','-oConnectTimeout=5']

    def ssh(role,argv,**kw):
        remote=argv if isinstance(argv,str) else shlex.join(list(map(str,argv)))
        return subprocess.run(['ssh',*flags,'pool-a-'+role,remote],check=True,**kw)

    def restore(role):
        deadline=time.monotonic()+130
        while time.monotonic()<deadline:
            try:
                ssh(role,['true'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=8)
                break
            except (subprocess.CalledProcessError,subprocess.TimeoutExpired):
                time.sleep(3)
        else:
            raise ValueError(role+' did not become reachable')
        with (out/(role+'-restore.log')).open('xb') as f:
            ssh(role,restore_command(role,tag),input=restore_source,
                stdout=f,stderr=subprocess.STDOUT,timeout=119)

    def preflight():
        capacity={}
        for role,paths in DRIVES.items():
            snapshot=json.loads(ssh(role,['python3','-B','-c',CHECK_DRIVES,*paths],
                                    capture_output=True,timeout=12).stdout)
            rows=snapshot['rows']
            require(snapshot['hostname']=='ax-pilot-20261111-'+role and bool(snapshot['boot_id'])
                    and [x['path'] for x in rows]==paths and len({x['device'] for x in rows})==len(paths)
                    and ['d-'+x['serial'] for x in rows]==DISK_IDS[role],
                    'A '+role+' data disk map differs')
            require(all(x['available_bytes']>=x['total_bytes']//5+2*1024**3
                        and x['available_inodes']>=x['total_inodes']//5+10000 for x in rows),
                    'A '+role+' is below the qualification reserve')
            capacity[role]=snapshot
        sv=os.statvfs(out)
        require(sv.f_bavail*sv.f_frsize>=sv.f_blocks*sv.f_frsize//5+2*1024**3
                and sv.f_favail>=sv.f_files//5+10000,'Coordinator capacity below qualification reserve')
        capacity['coordinator']=dict(available_bytes=sv.f_bavail*sv.f_frsize,total_bytes=sv.f_blocks*sv.f_frsize,
                                     available_inodes=sv.f_favail,total_inodes=sv.f_files)
        save(out/'capacity.json',capacity)
        redis_prefix=['redis-cli','-h','10.20.2.162','--raw']
        conf=ssh('metadata',[*redis_prefix,'CONFIG','GET','appendonly','appendfsync','maxmemory-policy'],
                 capture_output=True,text=True,timeout=8).stdout.splitlines()
        require(len(conf)%2==0,'Unexpected Redis config response')
        redis_config=dict(zip(conf[::2],conf[1::2]))
        require(redis_config.get('appendonly')=='yes' and redis_config.get('maxmemory-policy')=='noeviction',
                'Original persistent noeviction Redis required')
        info=ssh('metadata',[*redis_prefix,'INFO'],capture_output=True,text=True,timeout=8).stdout.splitlines()
        fields={'redis_version','process_id','used_memory','maxmemory','aof_enabled','aof_rewrite_in_progress'}
        selected=dict(row.split(':',1) for row in info if ':' in row and row.split(':',1)[0] in fields)
        require(set(selected)==fields and selected['aof_enabled']=='1' and selected['aof_rewrite_in_progress']=='0'
                and 0<int(selected['maxmemory']) and 5*int(selected['used_memory'])<4*int(selected['maxmemory']),
                'Redis version/persistence/20 percent memory headroom could not be confirmed')
        save(out/'redis.json',dict(config=redis_config,fields=selected))
        result=ssh('objects',['docker','inspect','-f','{{.Image}} {{.State.Running}} {{.State.Pid}}',CONTAINER],
                   capture_output=True,text=True,timeout=8).stdout.strip().split()
        revision=ssh('objects',['docker','image','inspect','-f','{{index .Config.Labels "org.opencontainers.image.revision"}}',IMAGE],
                     capture_output=True,text=True,timeout=8).stdout.strip()
        require(len(result)==3 and result[:2]==[IMAGE,'true'] and int(result[2])>1 and revision==RUSTFS,
                'Running A RustFS image/revision differs')
        command=shlex.join(['docker','inspect','-f','{{range .Config.Env}}{{println .}}{{end}}',CONTAINER])
        pattern='^RUSTFS_EC_('+'|'.join(SWITCHES)+')='
        switches=ssh('objects',command+' | '+shlex.join(['grep','-E',pattern]),
                     capture_output=True,text=True,timeout=8).stdout.splitlines()
        require(sorted(switches)==sorted('RUSTFS_EC_'+key+'=true' for key in SWITCHES),
                'Expected four enabled RustFS switches')
        save(out/'rustfs.json',dict(image=IMAGE,revision=revision,pid=int(result[2]),switches=switches))

    with ExitStack() as stack:
        roots=set()
        for ctl in active:
            locks=(ctl/'runner-locks').resolve(strict=True)
            if locks not in roots:
                stack.enter_context(resource_guard(ctl,cell,plan))
                roots.add(locks)
        require(active==controls() and not any(active_conflicts(ctl,cell,plan) for ctl in active),
                'A C3 or another shared physical lease is still active')
        start_epoch=int(time.time())
        deadline=start_epoch+2400
        save(out/'locks-held.json',dict(tag=tag,controls=list(map(str,active)),locks=sorted(map(str,roots)),
             armed_epoch=start_epoch,work_deadline_epoch=deadline,stop_by_epoch=start_epoch+2700))
        success=False
        phase='restore'
        remote=Path('/srv/ax-receipts')/tag
        worker_output=Path('/srv/ax-client/preparation')/tag
        launched=False
        try:
            with ThreadPoolExecutor(max_workers=3) as executor:
                list(executor.map(restore,IDS))
            phase='preflight'
            preflight()
            require(active==controls() and not any(active_conflicts(ctl,cell,plan) for ctl in active),
                    'A shared lease appeared while bringing workers up')
            phase='stage-client'
            slot=(CANON/'hardware-inputs-01/data/pool-a-client-spec.json').read_bytes()
            require(hashlib.sha256(slot).hexdigest()==SLOT_SHA,'Original A slot spec differs')
            ssh('client',['mkdir','-m','700',remote],timeout=10)
            for name,data in ((worker.name,worker.read_bytes()),('slot.json',slot)):
                ssh('client','set -C; cat > '+shlex.quote(str(remote/name)),input=data,timeout=10)
            check=ssh('client',['sha256sum',remote/worker.name],capture_output=True,text=True,timeout=8)
            require(check.stdout.split()[0]==authorization['worker_sha256'],'Uploaded worker differs')
            ssh('client','test -d /sys/fs/cgroup/ax-slot-1 || '+shlex.join(
                ['python3','-B','/srv/ax-client/code-12/experiments/unified-suite/pilot-aliyun/scripts/configure-node.py',
                 'slot','--spec',remote/'slot.json','--out',remote/'slot-setup']),timeout=15)
            remaining=deadline-int(time.time())
            require(300<=remaining<=2400,'Too little bounded work time remains')
            phase='worker'
            launched=True
            with (out/'worker.log').open('xb') as f:
                ssh('client',['timeout','--kill-after=25s',str(remaining)+'s','python3','-B',remote/worker.name,
                     '--tag',tag,'--database',args.database,'--deadline-epoch',deadline],
                    stdout=f,stderr=subprocess.STDOUT,timeout=remaining+45)
            success=True
        except (Exception,KeyboardInterrupt) as error:
            save(out/'controller-failure.json',dict(phase=phase,error_class=type(error).__name__,
                 reason=str(error) if type(error) is ValueError else None,automatic_retry=False))
        finally:
            closure=False
            try:
                if launched:
                    payload=ssh('client',['python3','-B','-c',READ_FILES,worker_output,*FILES],
                                capture_output=True,timeout=45)
                    persist_payload(out,json.loads(payload.stdout))
                    probe=ssh('client','cat /sys/fs/cgroup/ax-slot-1/cgroup.procs; '
                        +shlex.join(['python3','-B','-c',
                         'from pathlib import Path; import sys; print(any(line.split()[4] in sys.argv[1:] for line in Path("/proc/self/mountinfo").read_text().splitlines()))',
                         worker_output/'writer-mnt',worker_output/'cold-mnt']),
                        capture_output=True,text=True,timeout=8)
                    closure=probe.stdout.strip()=='False'
                    require(closure,'Client slot or owned mount is still present')
                    if success:
                        complete_worker(out)
            except (Exception,KeyboardInterrupt) as error:
                success=False
                save(out/'closure-failure.json',dict(error_class=type(error).__name__,
                     reason=str(error) if type(error) is ValueError else None))
            finally:
                save(out/'ready-for-stop.json',dict(passed=success and closure,worker_launched=launched,
                     client_slot_and_mounts_clear=closure,formal_sample=False,expected_instances=IDS,
                     stop_by_epoch=start_epoch+2700))
                # Operator owns the ECS API. Never turn an old or missing status into permission to unlock.
                stop=out/'instances-after-stop.json'
                while True:
                    try:
                        if stop.stat().st_mtime>=(out/'ready-for-stop.json').stat().st_mtime and exact_stopped(read(stop)):
                            break
                    except (FileNotFoundError,json.JSONDecodeError):
                        pass
                    time.sleep(3)
                save(out/'closed.json',dict(passed=success and closure,formal_sample=False,
                     stop_evidence_sha256=digest(stop)))
        return 0 if success and closure else 1


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--database',type=int,required=True)
    return run(p.parse_args())


if __name__=='__main__':
    raise SystemExit(main())
