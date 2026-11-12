#!/usr/bin/env python3
"""One bounded C2 stock workspace-v1 + exact Packed lower qualification on A."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import socket
import stat
import subprocess
import sys
import time
import uuid

CLIENT = Path('/srv/ax-client')
REPO = CLIENT/'code-12'
CASE = CLIENT/'trace-exact-02/cases/pytest-full-02'
MANIFEST = CASE.parent.parent/'manifest-replay-inputs-03.json'
MAN_SHA = '71a0661b82a433e9d14da29287a2bc1860460015d4887031f90a03ff5451a021'
BREWFS = CLIENT/'build-ca07159-01/bin/brewfs'
PRODUCER = BREWFS.with_name('packed_v3_snapshot_fixture')
REPLAY = CLIENT/'assets/bin/full-fuse-replay-parent-fd-01'
QUAL = REPO/'experiments/unified-suite/pilot-aliyun/scripts/qualify-trace.py'
PINS = {
    BREWFS:'814499855a949cad7a8752c04f272fa1b02524b36b7ea77b0e4b3bcc6031c5c0',
    PRODUCER:'aa45b1ee9640da92051ead72b17ed41836b57812e05623f860b963e09373b3ab',
    REPLAY:'35c45abd910d826ea17b5d6dfc750c7083cda5e8ce8f3bbe5ef77a78ffbea1aa',
    QUAL:'c034eab754b524bb49fc85e77c76847e3ef88ec7ddb0802e1a66a668d4061e3f',
    CASE/'capture/events.jsonl':'0300a208c3fff9bbe125d40e2214f46d459d7cad3b86141e1960a8cf9384c45f',
    CASE/'initial-tree.json':'c1b9b066e6660301ad944fff78de1910e6388021c6caa90923355848eb1c2847',
}
SLOT = Path('/sys/fs/cgroup/ax-slot-1')
REDIS = 'redis://10.20.2.162:6379'
OBJECTS = 'http://10.20.2.163:9000'
FSYNC_TREE = """import os,sys
from pathlib import Path
root=Path(sys.argv[1])
items=sorted(root.rglob('*'))
for p in items:
    if not p.is_symlink() and p.is_file():
        with p.open('rb') as f: os.fsync(f.fileno())
for p in [*reversed(items),root]:
    if not p.is_symlink() and p.is_dir():
        fd=os.open(p,os.O_RDONLY|os.O_DIRECTORY)
        try: os.fsync(fd)
        finally: os.close(fd)
"""
CLEAN_SCRATCH = """from pathlib import Path
import json,os,shutil,sys
root=Path(sys.argv[1]).resolve(strict=True)
names=('producer','producer-tmp','writer-cache','cold-cache')
for name in names:
    p=root/name
    if not p.exists() and not p.is_symlink(): continue
    if p.is_symlink() or p.resolve()!=p or not p.is_dir() or os.path.ismount(p):
        raise ValueError('Scratch path is not an owned ordinary directory')
    if any(os.path.ismount(x) for x in p.rglob('*') if x.is_dir() and not x.is_symlink()):
        raise ValueError('Nested scratch mount')
    shutil.rmtree(p)
print(json.dumps(dict(removed_or_absent=list(names))))
"""
DEBT = (
    'brewfs_writeback_live_dirty_bytes', 'brewfs_writeback_recent_pending_upload_bytes',
    'brewfs_writeback_stage_inflight_bytes', 'brewfs_writeback_remote_upload_inflight_bytes',
    'brewfs_writeback_live_slices', 'brewfs_writeback_recent_pending_upload_slices',
    'brewfs_writeback_metadata_inflight',
)
MIB=2**20
EXPECTED=dict(callbacks=1747109,actual_read_bytes=281774624,actual_write_bytes=194706661)


def require(ok, reason):
    if not ok:
        raise ValueError(reason)


def save(path, value):
    with path.open('x') as f:
        json.dump(value,f,indent=2)
        f.write('\n')


def digest(path):
    with path.open('rb') as f:
        return hashlib.file_digest(f,'sha256').hexdigest()


def mounted(point):
    for line in Path('/proc/self/mountinfo').read_text().splitlines():
        left,right=line.split(' - ',1)
        if left.split()[4]==str(point):
            return {'mount_id':int(left.split()[0]),'type':right.split()[0]}
    return None


def strict_proof(path, *, cold):
    proof=json.loads(path.read_text())
    require(proof.get('passed') is True and proof.get('byte_validation') is True
            and proof.get('final_byte_validation') is True and proof.get('final_tree_equal') is True
            and proof.get('hardlink_topology_equal') is True and proof.get('replayed') is not cold
            and proof.get('replay_mode')=='strict-portable-terminal-probe'
            and proof.get('path_resolution')=='parent-fd-cache'
            and all(proof.get(k)==v for k,v in EXPECTED.items())
            and bool(proof.get('databases')) and all(x['quick_check']=='ok' for x in proof['databases']),
            'Replay or cold correctness proof is incomplete')
    return {'path':str(path),'sha256':digest(path)}


def verify_initial_summary(initial):
    value=json.loads(initial.read_text())
    files=[x for x in value.values() if x.get('type')=='file']
    dirs=[x for x in value.values() if x.get('type')=='directory']
    require(len(value)==714 and len(files)==614 and len(dirs)==100
            and sum(x['bytes'] for x in files)==35526497,'Unexpected trace initial tree')
    return dict(entries=len(value),files=len(files),directories=len(dirs),bytes=35526497)


def app(args):
    require(re.fullmatch(r'c2-current-a-[0-9]{8}t[0-9]{6}z',args.tag),'Use a new exact C2 tag')
    require(20<=args.database<=255 and args.database not in (54,70,243),'Select a dedicated unused Redis DB')
    require(os.geteuid()==0 and socket.gethostname()=='ax-pilot-20261111-client'
            and mounted(CLIENT) is not None,'Use the original mounted pool A client')
    out=CLIENT/'preparation'/args.tag
    out.mkdir(mode=0o700)
    deadline=time.monotonic()+min(2400,args.deadline_epoch-time.time())
    phase='preflight'
    env=dict(os.environ)
    summary=dict(passed=False,formal_sample=False,automatic_retry=False,tag=args.tag,
        boot_id=Path('/proc/sys/kernel/random/boot_id').read_text().strip(),
        route='workspace-v1 + Packed wire005',redis_db=args.database,deadline_epoch=args.deadline_epoch)
    client=None
    stopped={}
    ready={}

    def interrupted(*_):
        raise InterruptedError('Worker cancellation')
    signal.signal(signal.SIGTERM,interrupted)
    signal.signal(signal.SIGINT,interrupted)

    def remaining(limit, reserve=0):
        left=deadline-time.monotonic()-reserve
        require(left>0,'Qualification work deadline reached')
        return min(limit,left)

    def run(name, argv, limit, reserve=0):
        start=time.monotonic()
        argv=list(map(str,argv))
        with (out/(name+'.stdout')).open('xb') as stdout,(out/(name+'.stderr')).open('xb') as stderr:
            child=subprocess.Popen(argv,env=env,stdout=stdout,stderr=stderr,start_new_session=True)
            try:
                status=child.wait(timeout=remaining(limit,reserve))
            except BaseException:
                # The verifier starts the Rust replayer; stop the whole owned group.
                for sig in (signal.SIGTERM,signal.SIGKILL):
                    try:
                        os.killpg(child.pid,sig)
                    except ProcessLookupError:
                        break
                    if sig==signal.SIGTERM:
                        time.sleep(.8)
                child.wait(timeout=5)
                save(out/(name+'.exit.json'),dict(exit_code=child.returncode,interrupted=True,
                     wall_seconds=time.monotonic()-start))
                raise
        save(out/(name+'.exit.json'),dict(exit_code=status,interrupted=False,wall_seconds=time.monotonic()-start))
        require(status==0,name+' exited '+str(status))

    def capture(argv,limit=8):
        return subprocess.check_output(list(map(str,argv)),env=env,text=True,timeout=remaining(limit)).strip()

    def get_manifest(key, bucket):
        obj=client.get_object(Bucket=bucket,Key=key)['Body']
        try:
            raw=obj.read(16*MIB+1)
        finally:
            obj.close()
        require(8<len(raw)<=16*MIB and raw[:8]==b'BRFPM005','RustFS manifest is not Packed wire005')
        return hashlib.sha256(raw).hexdigest()

    def drain(point, side):
        for i in range(2):
            run(f'{side}-debt-{i}',['cat',point/'.stats'],8)
            raw=(out/(f'{side}-debt-{i}.stdout')).read_text()
            seen=set()
            for line in raw.splitlines():
                words=line.split()
                if len(words)!=2:
                    continue
                name=words[0].split('{',1)[0]
                if name in DEBT:
                    require(float(words[1])==0,'Native pending writeback remains')
                    seen.add(name)
            require(seen==set(DEBT),'Missing native writeback counters')
            if i==0:
                time.sleep(1.1)

    def visit(side, workspace, namespace, data, rurl):
        nonlocal phase
        point=out/(side+'-mnt')
        point.mkdir()
        cfg=out/(side+'-mount.json')
        save(cfg,dict(volume_format='workspace-v1',volume={'format':'workspace-v1'},workspace=workspace,
            workspace_namespace=namespace,data=data,meta={'backend':'redis','redis':{'url':rurl}},
            layout=dict(chunk_size=4*MIB,block_size=4*MIB),fuse=dict(workers=4,max_background=128,
            cache_ttl_ms=0,keep_cache=False),cache=dict(cache_root=str(out/(side+'-cache')),
            read_memory_bytes=512*MIB,read_ssd_bytes=0,write_memory_bytes=512*MIB,
            write_ssd_bytes=1024*MIB,memory_budget_bytes=512*MIB,compression='none',
            verify_cache_checksum='full',writeback_mode='upload_before_commit',prefetch_enabled=True,
            prefetch_max_bytes=128*MIB,prefetch_concurrency=64,range_background_prefetch=True,
            populate_write_cache_after_upload=True,persist_write_cache_after_upload=False)))
        with (out/(side+'-mount.log')).open('xb') as log:
            proc=subprocess.Popen([BREWFS,'mount','--config',cfg,point],env=env,stdout=log,stderr=subprocess.STDOUT)
            code=unmount=None
            try:
                start_by=time.monotonic()+remaining(45)
                while mounted(point) is None and proc.poll() is None and time.monotonic()<start_by:
                    time.sleep(.2)
                info=mounted(point)
                require(info and info['type'].startswith('fuse') and proc.poll() is None,'Real FUSE mount not ready')
                ready[side]=dict(**info,pid=proc.pid,workspace=workspace,namespace=namespace,
                                 boot_id=summary['boot_id'])
                if side=='cold':
                    require(ready['writer']['pid']!=proc.pid and mounted(out/'writer-mnt') is None,
                            'Cold mount must use a new process after the original writer exits')
                save(out/(side+'-ready.json'),ready[side])
                flags=['--portable-terminal-probe','--cache-parent-fds']
                if side=='writer':
                    phase='strict-replay'
                    run('strict',[sys.executable,'-B',QUAL,'--case',CASE,'--binary',REPLAY,'--root',point,
                        '--out',out/'strict-proof','--prepared-root',*flags],2400,reserve=180)
                    summary['strict']=strict_proof(out/'strict-proof/verification.json',cold=False)
                    phase='writer-fsync'
                    run('writer-fsync',[sys.executable,'-B','-c',FSYNC_TREE,point],60,reserve=105)
                    phase='writer-drain'
                    drain(point,side)
                else:
                    phase='cold-readback'
                    run('cold',[sys.executable,'-B',QUAL,'--case',CASE,'--binary',REPLAY,'--root',point,
                        '--out',out/'cold-proof','--verify-only','--replay-result',out/'strict-proof/replay.json',
                        *flags],2400)
                    summary['cold']=strict_proof(out/'cold-proof/verification.json',cold=True)
                    drain(point,side)
                require(mounted(point)==info and proc.poll() is None,'Mount identity changed')
            finally:
                try:
                    if mounted(point) is not None:
                        unmount=subprocess.run(['fusermount3','-u',point],timeout=12).returncode
                    code=proc.wait(timeout=15)
                except (OSError,subprocess.TimeoutExpired):
                    # No lazy/forced unmount is considered a normal closure.
                    proc.terminate()
                    try:
                        code=proc.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        proc.kill()
                        code=proc.wait(timeout=5)
                finally:
                    stopped[side]=dict(unmount_exit=unmount,exit_code=code,mount_absent=mounted(point) is None)
                    save(out/(side+'-stop.json'),stopped[side])
                require(stopped[side]==dict(unmount_exit=0,exit_code=0,mount_absent=True),
                        'Mount did not close normally: '+side)

    try:
        for path,sha in PINS.items():
            require(path.resolve(strict=True)==path and digest(path)==sha,'Artifact differs: '+str(path))
        require(digest(MANIFEST)==MAN_SHA and len(json.loads(MANIFEST.read_text()))==67577,
                'Whole trace manifest differs')
        summary['initial']=verify_initial_summary(CASE/'initial-tree.json')
        rurl=REDIS+'/'+str(args.database)
        require(capture(['redis-cli','-u',rurl,'DBSIZE'])=='0','Chosen Redis database is not empty')
        for name,expected in dict(cpu_max='200000 100000',memory_max='8589934592',
                                  memory_swap_max='0',cgroup_procs='').items():
            require((SLOT/name.replace('_','.')).read_text().strip()==expected,'Expected an idle 2CPU/8GiB slot')
        space=os.statvfs(CLIENT)
        require(space.f_bavail*space.f_frsize>=space.f_blocks*space.f_frsize//5+2*1024**3
                and space.f_favail>=space.f_files//5+10000,'A client capacity below this qualification reserve')
        run('inventory',[sys.executable,'-B',REPO/'bench/trace_checks.py','inputs','--case',CASE,
                         '--manifest',MANIFEST,'--sha256',MAN_SHA,'--output',out/'input-inventory.json'],120)
        run('initial-tree',[sys.executable,'-B',REPO/'bench/trace_checks.py','tree','--root',CASE/'initial',
                           '--expected',CASE/'initial-tree.json','--output',out/'initial-tree.json'],30)
        require(all(x.lstat().st_nlink==1 for x in (CASE/'initial').rglob('*')
                    if stat.S_ISREG(x.lstat().st_mode)),'Source initial hardlink topology differs')
        save(out/'intent.json',dict(**summary,pins={str(p):v for p,v in PINS.items()},manifest_sha256=MAN_SHA))
        (SLOT/'cgroup.procs').write_text(str(os.getpid()))
        phase='publish-packed-lower'
        secret=CLIENT/'private/rustfs.env'
        require(secret.is_file() and secret.stat().st_mode & 0o077==0,'Private RustFS env must be owner-only')
        auth=dict(line.split('=',1) for line in secret.read_text().splitlines() if '=' in line)
        tmp=out/'producer-tmp'
        tmp.mkdir()
        env.update(AWS_ACCESS_KEY_ID=auth['RUSTFS_ACCESS_KEY'],AWS_SECRET_ACCESS_KEY=auth['RUSTFS_SECRET_KEY'],
            AWS_EC2_METADATA_DISABLED='true',AWS_REGION='us-east-1',RUST_LOG='error',NO_COLOR='1',
            TMPDIR=str(tmp),NO_PROXY='10.20.2.162,10.20.2.163',no_proxy='10.20.2.162,10.20.2.163')
        for suffix,budget in dict(ROOTS=16,METADATA_OWNED=256,PLANS=32,CONTROL=1,STORED=32,RAW=32,
                                  DECODE_WORKSPACE=8,OUTPUT=32).items():
            env['BREWFS_PACKED_V3_'+suffix+'_BUDGET_BYTES']=str(budget*MIB)
        env['BREWFS_WORKSPACE_ROW_CACHE_TTL_MS']='60000'
        import boto3
        from botocore.config import Config
        client=boto3.client('s3',endpoint_url=OBJECTS,region_name='us-east-1',
            aws_access_key_id=env['AWS_ACCESS_KEY_ID'],aws_secret_access_key=env['AWS_SECRET_ACCESS_KEY'],
            config=Config(signature_version='s3v4',connect_timeout=3,read_timeout=7,
                retries={'total_max_attempts':1},s3={'addressing_style':'path'}))
        bucket='axc2-'+uuid.uuid4().hex[:16]
        namespace='c2-'+uuid.uuid4().hex
        save(out/'asset-intent.json',dict(bucket=bucket,namespace=namespace,database=args.database))
        remaining(15)
        client.create_bucket(Bucket=bucket)
        data=dict(backend='s3',s3=dict(bucket=bucket,endpoint=OBJECTS,region='us-east-1',
                                      force_path_style=True,rustfs_ec_block_size_hint='auto'))
        cfg=out/'data.json'
        save(cfg,dict(data=data))
        mf=out/'manifest-key.txt'
        run('producer',[PRODUCER,'--source-directory',CASE/'initial','--source-consistency','best-effort-detected',
            '--source-hardlink-policy','reject-external','--output-dir',out/'producer','--manifest-output',mf,
            '--bucket',bucket,'--endpoint',OBJECTS,'--region','us-east-1','--force-path-style','true','--prefix','base',
            '--metadata-codec','raw','--data-codec','raw','--inline-data','off','--chunk-size',4*MIB,'--block-size',4*MIB,
            '--frame-policy','size-only','--access-profile','random-small-file','--max-random-frame-raw-bytes',262144,
            '--s3-rustfs-ec-block-size-hint','auto'],300,reserve=150)
        key=mf.read_text().strip()
        require(re.fullmatch(r'base/objects/[0-9]+/[a-f0-9]{64}',key),'Unexpected Packed manifest key')
        manifest_sha=get_manifest(key,bucket)
        phase='init-packed-workspace'
        run('init',[BREWFS,'workspace','--meta-backend','redis','--meta-url',rurl,'--workspace-namespace',namespace,
                    'init-packed-volume','--manifest-key',key,'--data-config',cfg],40,reserve=150)
        initialized=json.loads((out/'init.stdout').read_text())
        workspace=str(uuid.UUID(initialized['workspace_id']))
        upper=initialized['upper_bucket']
        require(upper and upper!=bucket,'No independent upper bucket')
        binding=['redis-cli','-u',rurl,'--scan','--pattern','*packed/v3/current/'+workspace]
        require(len(capture(binding).splitlines())==1,'No unique catalog lower binding')
        summary['packed']=dict(bucket=bucket,key=key,manifest_sha256=manifest_sha,wire=5,
                               upper_bucket=upper,workspace_id=workspace,namespace=namespace)
        save(out/'packed.json',summary['packed'])
        phase='writer-mount'
        visit('writer',workspace,namespace,data,rurl)
        phase='cold-mount'
        visit('cold',workspace,namespace,data,rurl)
        phase='immutable-identity'
        require(len(capture(binding).splitlines())==1 and get_manifest(key,bucket)==manifest_sha,
                'Immutable lower identity changed')
        phase='local-scratch-cleanup'
        run('scratch-cleanup',[sys.executable,'-B','-c',CLEAN_SCRATCH,out],25)
        summary['scratch_cleanup']=json.loads((out/'scratch-cleanup.stdout').read_text())
        summary['passed']=True
    except (Exception,KeyboardInterrupt) as error:
        summary.update(failed_phase=phase,error_class=type(error).__name__)
        print(json.dumps(dict(phase=phase,error=type(error).__name__)))
        if type(error) is ValueError:
            summary['reason']=str(error)
    finally:
        if client is not None:
            client.close()
        summary['normal_stops']=stopped
        save(out/'result.json',summary)
    return 0 if summary['passed'] else 1


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--tag',required=True)
    p.add_argument('--database',type=int,required=True)
    p.add_argument('--deadline-epoch',type=int,required=True)
    return app(p.parse_args())


if __name__=='__main__':
    raise SystemExit(main())
