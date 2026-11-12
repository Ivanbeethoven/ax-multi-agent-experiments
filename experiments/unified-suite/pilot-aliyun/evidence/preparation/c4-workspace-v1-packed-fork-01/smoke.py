import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
import uuid

import boto3
from botocore.config import Config

result_path = Path(sys.argv[1])
assert not result_path.exists()
work = Path(tempfile.mkdtemp(prefix='ax-c4-packed-fork-'))
name = 'ax-c4-packed-fork-' + uuid.uuid4().hex[:8]
tag = 'ax.c4.packed-fork.owner'
redis, rustfs = name + '-redis', name + '-rustfs'
images = {
    redis: 'sha256:63218503f6461303e502355b8b0359a1194a190341f954e7fc7e8f92cd382a43',
    rustfs: 'sha256:942893356344d292d9fcccadfceb039467c589ba10b4045cb148c0a65d2d3fec',
}
binary_root = Path('/home/hxy/ax-unified-pilot-prep-next-01/bin')
binary = binary_root / 'brewfs'
producer = binary_root / 'packed_v3_snapshot_fixture'
pins = {
    binary: '814499855a949cad7a8752c04f272fa1b02524b36b7ea77b0e4b3bcc6031c5c0',
    producer: 'aa45b1ee9640da92051ead72b17ed41836b57812e05623f860b963e09373b3ab',
}
access, secret = secrets.token_hex(12), secrets.token_hex(24)
env = dict(os.environ, AWS_ACCESS_KEY_ID=access, AWS_SECRET_ACCESS_KEY=secret, AWS_REGION='us-east-1',
    AWS_EC2_METADATA_DISABLED='true', RUST_LOG='warn', NO_COLOR='1', NO_PROXY='127.0.0.1,localhost')
point = work/'mount'
point.mkdir()
proc = None
log = None
active_side = None
phase = 'preflight'
result = dict(schema='c4-local-brewfs-packed-fork-smoke-v1',passed=False,files=2,
    source_commit='ca07159f3da4b670e4aeb878faef928e69bc1634',
    binary_sha256={p.name:s for p,s in pins.items()},
    image_sha256={'redis':images[redis],'rustfs':images[rustfs]},
    no_model=True,no_cloud=True,no_copy_baseline=True,formal_sample=False,readbacks={},
    scope='One WSL FUSE host, tiny nonempty packed-v3 lower, no performance timing.')
deadline = time.monotonic() + 138

def expired(*_):
    raise TimeoutError('Smoke time limit reached')

signal.signal(signal.SIGTERM, expired)
signal.signal(signal.SIGALRM, expired)
signal.alarm(145)

def clean_text(text):
    for token in (access,secret):
        text = text.replace(token,'[redacted]')
    return re.sub(r'\x1b\[[0-?]*[ -/]*[@-~]','',text)[-3000:]

def invoke(argv, check=True, limit=15, bounded=True):
    t=max(.1,min(limit,deadline-time.monotonic())) if bounded else limit
    p=subprocess.run(list(map(str,argv)),env=env,capture_output=True,text=True,timeout=t)
    if check and p.returncode:
        raise RuntimeError(f'{Path(str(argv[0])).name} exited {p.returncode}: '+clean_text(p.stderr+'\n'+p.stdout))
    return p

def docker(*args, **kwargs):
    return invoke(['docker',*args],**kwargs)

def wait_for(check, name, limit=18):
    end=min(deadline,time.monotonic()+limit)
    while time.monotonic()<end:
        val=check()
        if val:return val
        time.sleep(.15)
    raise TimeoutError('Readiness timed out: '+name)

def listening(port):
    try:
        with socket.create_connection(('127.0.0.1',port),timeout=.3): return True
    except OSError: return False

def port(container, spec):
    return int(docker('port',container,spec).stdout.strip().rsplit(':',1)[1])

def mounted():
    return any(row.split()[4]==str(point) for row in Path('/proc/self/mountinfo').read_text().splitlines())

def stop_mount(strict):
    global proc, log, active_side
    if proc is None:return
    unmount = None
    if mounted():
        unmount=invoke(['fusermount3','-u',point],check=False,limit=3,bounded=False).returncode
    try:code=proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.terminate()
        try:code=proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            proc.kill();code=proc.wait(timeout=3)
    log.close()
    result.setdefault('mount_exits',{})[active_side]=dict(exit_code=code,mount_absent=not mounted(),unmount_exit_code=unmount)
    proc=None;log=None
    if strict and (code or mounted() or unmount not in (None,0)):
        raise RuntimeError('Nonclean '+str(active_side)+' FUSE exit')

def json_call(prefix,*args):
    raw=invoke([*prefix,*args]).stdout.strip()
    return json.loads(raw)

def binding(workspace):
    lines=docker('exec',redis,'redis-cli','--scan','--pattern','*packed/v3/current/'+workspace).stdout.splitlines()
    if len(lines)>1: raise RuntimeError('Ambiguous Packed binding keys')
    return bool(lines)

def visit(side, workspace, data, rurl, namespace):
    global active_side,proc,log
    active_side=side
    cfg=work/(side+'.json')
    cfg.write_text(json.dumps(dict(volume_format='workspace-v1',volume={'format':'workspace-v1'},
        workspace=workspace,workspace_namespace=namespace,data=data,
        meta={'backend':'redis','redis':{'url':rurl}},layout=dict(chunk_size=67108864,block_size=4194304),
        fuse=dict(workers=4,max_background=128,cache_ttl_ms=0,keep_cache=False),
        cache=dict(cache_root=str(work/('cache-'+side)),writeback_mode='upload_before_commit',
            read_memory_bytes=536870912,read_ssd_bytes=0,compression='none'))))
    log_file=work/(side+'.log')
    log=log_file.open('wb')
    proc=subprocess.Popen([binary,'mount','--config',cfg,point],stdout=log,stderr=subprocess.STDOUT,env=env)
    def ready():
        if proc.poll() is not None:
            raise RuntimeError(f'{side} mount exited {proc.returncode}: '+clean_text(log_file.read_text(errors='replace')))
        return mounted()
    wait_for(ready,side+' FUSE mount',20)
    py = 'from pathlib import Path;import hashlib,json,sys;p=Path(sys.argv[1]);print(json.dumps({str(f.relative_to(p)):dict(bytes=f.stat().st_size,sha256=hashlib.sha256(f.read_bytes()).hexdigest()) for f in p.rglob("*") if f.is_file()}))'
    observed=json.loads(invoke([sys.executable,'-c',py,point],limit=12).stdout)
    result['readbacks'][side]=dict(passed=observed==expected,files=observed)
    stop_mount(True)
    return observed==expected

try:
    for p,s in pins.items():
        with p.open('rb') as stream: actual=hashlib.file_digest(stream,'sha256').hexdigest()
        if actual!=s: raise ValueError('Binary pin changed: '+p.name)
    for image in images.values():
        if docker('image','inspect','--format','{{.Id}}',image).stdout.strip()!=image:
            raise ValueError('Image pin changed')
    phase='start-private-services'
    common=['run','--pull','never','-d','--label',tag+'='+name]
    docker(*common,'--name',redis,'-p','127.0.0.1::6379','--memory','256m','--cpus','1',
           images[redis],'redis-server','--appendonly','no','--save','')
    rustenv=work/'rustfs.env'
    rustenv.write_text(f'RUSTFS_ACCESS_KEY={access}\nRUSTFS_SECRET_KEY={secret}\n')
    rustenv.chmod(0o600)
    args=[*common,'--name',rustfs,'-p','127.0.0.1::9000','--memory','2g','--cpus','2',
          '--env-file',rustenv]
    knobs=dict(RUSTFS_ADDRESS='0.0.0.0:9000',RUSTFS_CONSOLE_ENABLE='false',
        RUSTFS_VOLUMES='/data/rustfs{0...3}',RUSTFS_UNSAFE_BYPASS_DISK_CHECK='true',
        RUSTFS_OBS_LOGGER_LEVEL='warn',RUSTFS_OBS_LOG_DIRECTORY='/var/log/rustfs',
        RUSTFS_OBS_METRICS_EXPORT_ENABLED='false',RUSTFS_OBS_TRACES_EXPORT_ENABLED='false',
        RUSTFS_OBS_LOGS_EXPORT_ENABLED='false',RUSTFS_OBS_PROFILING_EXPORT_ENABLED='false',
        RUSTFS_EC_BLOCK_SIZE_HINT_ENABLE='true',RUSTFS_EC_BLOCK_SIZE_HINT_FLEET_CONFIRMED='true',
        RUSTFS_EC_READ_QUANTUM_ENABLE='true',RUSTFS_EC_READ_QUANTUM_FLEET_CONFIRMED='true')
    for k,v in knobs.items():args += ['-e',k+'='+v]
    for leaf,target in [(str(i),'/data/rustfs'+str(i)) for i in range(4)]+[('rustlogs','/var/log/rustfs')]:
        d=work/leaf;d.mkdir();os.chown(d,10001,10001);args+=['-v',str(d)+':'+target]
    docker(*args,images[rustfs])
    rp,sp=port(redis,'6379/tcp'),port(rustfs,'9000/tcp')
    wait_for(lambda:docker('exec',redis,'redis-cli','ping',check=False,limit=3).stdout.strip()=='PONG','Redis')
    wait_for(lambda:listening(sp),'RustFS')
    rurl=f'redis://127.0.0.1:{rp}/0'
    endpoint=f'http://127.0.0.1:{sp}'
    s3=boto3.client('s3',endpoint_url=endpoint,region_name='us-east-1',
        aws_access_key_id=access,aws_secret_access_key=secret,
        config=Config(signature_version='s3v4',connect_timeout=2,read_timeout=5,
                      retries={'total_max_attempts':1},s3={'addressing_style':'path'}))
    bucket='axcf-'+uuid.uuid4().hex[:10]
    phase='create-base-bucket'
    s3.create_bucket(Bucket=bucket)
    src=work/'src';src.mkdir();(src/'sub').mkdir()
    payload={'a.txt':b'packed smoke\n','sub/b.txt':bytes(range(64))}
    for f,data_bytes in payload.items():(src/f).write_bytes(data_bytes)
    expected={f:dict(bytes=len(b),sha256=hashlib.sha256(b).hexdigest()) for f,b in payload.items()}
    result['expected_files']=expected
    phase='publish-fixture'
    manifest=work/'manifest-key'
    invoke([producer,'--source-directory',src,'--source-consistency','best-effort-detected',
        '--source-hardlink-policy','visible-links','--output-dir',work/'objects','--manifest-output',manifest,
        '--bucket',bucket,'--endpoint',endpoint,'--region','us-east-1','--force-path-style','true',
        '--prefix','base','--metadata-codec','raw','--data-codec','raw','--inline-data','off',
        '--chunk-size','67108864','--block-size','4194304','--s3-rustfs-ec-block-size-hint','auto'],limit=25)
    key=manifest.read_text().strip()
    result['manifest_key']=key
    data=dict(backend='s3',s3=dict(bucket=bucket,endpoint=endpoint,region='us-east-1',
                force_path_style=True,rustfs_ec_block_size_hint='auto'))
    config=work/'data.json';config.write_text(json.dumps({'data':data}))
    namespace='packed-fork-'+uuid.uuid4().hex[:10]
    prefix=[binary,'workspace','--meta-backend','redis','--meta-url',rurl,'--workspace-namespace',namespace]
    phase='initialize-packed-parent'
    parent=json_call(prefix,'init-packed-volume','--manifest-key',key,'--data-config',config)
    rev=parent['fork_base']
    revision=f"{rev['layer_id']}:{rev['sealed_version']}:{bytes(rev['root_hash']).hex()}"
    result.update(parent_workspace_id=parent['workspace_id'],exact_revision=revision,parent_binding=binding(parent['workspace_id']))
    phase='parent-readback'
    if not visit('parent',parent['workspace_id'],data,rurl,namespace):
        raise ValueError('Parent did not expose the expected two files')
    phase='fork-exact-revision'
    [child]=json_call(prefix,'fork',revision,'--count','1','--data-config',config)
    result.update(fork_returned_child=True, child_workspace_id=child['workspace_id'],
        child_same_revision=child['fork_base']==rev,
        separate_upper_buckets=bool(child['upper_bucket'] and parent['upper_bucket'] and child['upper_bucket']!=parent['upper_bucket']),
        child_binding=binding(child['workspace_id']))
    phase='child-readback'
    correct=visit('child',child['workspace_id'],data,rurl,namespace)
    result['passed']=bool(correct and result['parent_binding'] and result['child_binding'] and result['child_same_revision'] and result['separate_upper_buckets'])
    if not result['passed']:result['finding']='Fork returned a child but the child lacked the Packed binding or expected bytes.'
except Exception as error:
    result['failed_phase']=phase
    result['error']=type(error).__name__+': '+clean_text(str(error))
finally:
    signal.alarm(0)
    cleanup={}
    try:stop_mount(False)
    except Exception as error:cleanup['mount_error']=type(error).__name__+': '+clean_text(str(error))
    cleanup['mount_absent']=not mounted()
    cleanup['owned_containers_absent']={}
    for container in (rustfs,redis):
        try:
            status=docker('inspect','--format','{{index .Config.Labels "'+tag+'"}}',container,check=False,limit=4,bounded=False)
            if status.returncode==0 and status.stdout.strip()==name:
                if 'failed_phase' in result and result['failed_phase'] in ('start-private-services','create-base-bucket'):
                    result.setdefault('service_log_tails',{})[container.removeprefix(name+'-')]=clean_text(docker('logs','--tail','12',container,check=False,limit=3,bounded=False).stdout)
                docker('rm','-f','-v',container,check=False,limit=5,bounded=False)
            after=docker('inspect',container,check=False,limit=4,bounded=False)
            cleanup['owned_containers_absent'][container.removeprefix(name+'-')]=after.returncode!=0
        except Exception as error:
            cleanup['container_error']=type(error).__name__+': '+clean_text(str(error))
    if cleanup['mount_absent'] and len(cleanup['owned_containers_absent'])==2 and all(cleanup['owned_containers_absent'].values()):
        shutil.rmtree(work)
    cleanup['scratch_removed']=not work.exists()
    result['cleanup']=cleanup
    result['passed']=result['passed'] and cleanup['scratch_removed']
    with result_path.open('x') as stream:json.dump(result,stream,indent=2);stream.write('\n')
    print(json.dumps(result,indent=2))
raise SystemExit(0 if result['passed'] else 1)
