#!/usr/bin/env python3
"""Local C4 Packed carrier qualification using private loopback services."""

import argparse
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

SOURCE = {
    'source_commit': 'ca07159f3da4b670e4aeb878faef928e69bc1634',
    'source_tree': '1ee7c97a427156d9c2cfe056f857e2e01efeb714',
    'patch_sha256': '4464b9aaeb287d31c0dd6b8f2fcfdef02193e16d0e85e193b913f86acc1177a2',
}
PRODUCER_SHA256 = 'aa45b1ee9640da92051ead72b17ed41836b57812e05623f860b963e09373b3ab'


def read_artifact(path, expected, name):
    if not path.is_absolute() or not path.is_file() or path.resolve() != path or not os.access(path, os.X_OK):
        parser.error(f'{name} must be an absolute, executable, non-symlink file')
    with path.open('rb') as stream:
        if hashlib.file_digest(stream, 'sha256').hexdigest() != expected:
            parser.error(f'{name} SHA-256 differs from its pin')


parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--candidate', required=True, type=Path, help='absolute path to the patched BrewFS executable')
parser.add_argument('--provenance', required=True, type=Path, help='successful build receipt with the candidate SHA-256')
parser.add_argument('--producer', required=True, type=Path, help='absolute path to the pinned packed_v3_snapshot_fixture')
parser.add_argument('--out', required=True, type=Path, help='absolute path to a new local JSON receipt')
args = parser.parse_args()
result_path = args.out
if not result_path.is_absolute() or result_path.exists() or result_path.is_symlink() or not result_path.parent.is_dir():
    parser.error('--out must be an absolute unused file path with an existing parent directory')
if (os.geteuid(), os.getegid()) != (0, 0):
    parser.error('the matched JuiceFS control principal is root (0:0)')
if args.candidate == args.producer:
    parser.error('candidate and producer must be different executables')
try:
    build = json.loads(args.provenance.read_text(encoding='utf-8'))
except (OSError, ValueError) as error:
    parser.error(f'cannot read build provenance: {error}')
if not isinstance(build, dict):
    parser.error('build provenance must be a JSON object')
patch = build.get('patch_sha256', build.get('candidate_patch_sha256'))
if any(key in build and build[key] != patch for key in ('patch_sha256', 'candidate_patch_sha256')):
    parser.error('build provenance has conflicting patch pins')
if any(build.get(key) != SOURCE[key] for key in ('source_commit', 'source_tree')) or patch != SOURCE['patch_sha256']:
    parser.error('source or patch differs from the qualification')
if build.get('passed') is not True and build.get('build_exit_code') != 0:
    parser.error('the candidate does not have a successful build receipt')
if ('passed' in build and build['passed'] is not True) or ('build_exit_code' in build and build['build_exit_code'] != 0):
    parser.error('the build provenance reports a failure')
sha = build.get('artifact_sha256')
if not isinstance(sha, str) or not re.fullmatch(r'[0-9a-f]{64}', sha):
    parser.error('build provenance lacks an artifact SHA-256')
binary, producer = args.candidate, args.producer
read_artifact(binary, sha, 'candidate')
read_artifact(producer, PRODUCER_SHA256, 'producer')
provenance = {**SOURCE, 'artifact_sha256': sha}
work = Path(tempfile.mkdtemp(prefix='ax-c4-packed-fork-'))
name = 'ax-c4-packed-fork-' + uuid.uuid4().hex[:8]
tag = 'ax.c4.packed-fork.owner'
redis, rustfs = name + '-redis', name + '-rustfs'
images = {
    redis: 'sha256:63218503f6461303e502355b8b0359a1194a190341f954e7fc7e8f92cd382a43',
    rustfs: 'sha256:942893356344d292d9fcccadfceb039467c589ba10b4045cb148c0a65d2d3fec',
}
access, secret = secrets.token_hex(12), secrets.token_hex(24)
env = dict(PATH='/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin', HOME=str(work), LANG='C.UTF-8',
    AWS_ACCESS_KEY_ID=access, AWS_SECRET_ACCESS_KEY=secret, AWS_REGION='us-east-1',
    AWS_EC2_METADATA_DISABLED='true', RUST_LOG='warn', NO_COLOR='1', NO_PROXY='127.0.0.1,localhost')
point = work/'mount'
point.mkdir()
proc = None
log = None
active_side = None
phase = 'preflight'
result = dict(schema='c4-local-packed-fork-qualification-v1',passed=False,files=2,
    source_commit=SOURCE['source_commit'],
    binary_sha256={'brewfs':sha,'packed_v3_snapshot_fixture':PRODUCER_SHA256},
    image_sha256={'redis':images[redis],'rustfs':images[rustfs]},
    no_model=True,no_cloud=True,no_copy_baseline=True,formal_sample=False,readbacks={},
    build_provenance=provenance,
    effective_principal=dict(uid=os.geteuid(),gid=os.getegid()), scope='One WSL FUSE host, one 77-byte packed-v3 lower, no performance timing.')
deadline = time.monotonic() + 156

def expired(*_):
    raise TimeoutError('Smoke time limit reached')

signal.signal(signal.SIGTERM, expired)
signal.signal(signal.SIGALRM, expired)
signal.alarm(165)

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
    return invoke(['docker','--host','unix:///var/run/docker.sock',*args],**kwargs)

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

def visit(side, workspace, data, rurl, namespace, write=None, existing=None):
    global active_side,proc,log
    active_side=side
    cfg=work/(side+'.json')
    cfg.write_text(json.dumps(dict(volume_format='workspace-v1',volume={'format':'workspace-v1'},
        workspace=workspace,workspace_namespace=namespace,data=data,
        meta={'backend':'redis','redis':{'url':rurl}},layout=dict(chunk_size=4194304,block_size=4194304),
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
    py = """from pathlib import Path
import hashlib, json, sys
p = Path(sys.argv[1])
paths = sorted(p.rglob('*'))
def perms(info):
    return dict(uid=info.st_uid, gid=info.st_gid, mode=format(info.st_mode & 0o7777, '04o'))
root_info = p.stat()
rows = {'files': {}, 'nlinks': {'.': root_info.st_nlink}, 'permissions': {'.': perms(root_info)}}
for f in paths:
    name = str(f.relative_to(p))
    info = f.stat()
    rows['nlinks'][name] = info.st_nlink
    rows['permissions'][name] = perms(info)
    if f.is_file():
        rows['files'][name] = dict(bytes=info.st_size, sha256=hashlib.sha256(f.read_bytes()).hexdigest())
print(json.dumps(rows))
"""
    expected_files=dict(expected)
    if write or existing:
        key, payload = write or existing
        expected_files[key] = dict(bytes=len(payload),sha256=hashlib.sha256(payload).hexdigest())
    if write:
        child_write = 'from pathlib import Path;import sys,os; p=Path(sys.argv[1]); f=p.open("wb"); f.write(bytes.fromhex(sys.argv[2])); f.flush(); os.fsync(f.fileno()); f.close()'
        invoke([sys.executable,'-c',child_write,point/key,payload.hex()],limit=10)
    observed=json.loads(invoke([sys.executable,'-c',py,point],limit=12).stdout)
    expected_nlinks = {'.': 3, 'sub': 2, **{key: 1 for key in expected_files}}
    passed = observed['files'] == expected_files and observed['nlinks'] == expected_nlinks
    result['readbacks'][side]=dict(passed=passed,files=observed['files'],nlinks=observed['nlinks'],
        expected_nlinks=expected_nlinks,permissions=observed['permissions'])
    stop_mount(True)
    return passed

try:
    if '--require-packed-carrier' not in invoke([binary,'workspace','fork','--help']).stdout:
        raise ValueError('Expected a newly built Packed carrier CLI')
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
        d=work/leaf
        d.mkdir()
        os.chown(d,10001,10001)
        args+=['-v',str(d)+':'+target]
    docker(*args,images[rustfs])
    rp,sp=port(redis,'6379/tcp'),port(rustfs,'9000/tcp')
    wait_for(lambda:docker('exec',redis,'redis-cli','ping',check=False,limit=3).stdout.strip()=='PONG','Redis')
    wait_for(lambda:listening(sp),'RustFS')
    rurl=f'redis://127.0.0.1:{rp}/0'
    endpoint=f'http://127.0.0.1:{sp}'
    s3=boto3.client('s3',endpoint_url=endpoint,region_name='us-east-1',
        aws_access_key_id=access,aws_secret_access_key=secret,
        config=Config(signature_version='s3v4',connect_timeout=2,read_timeout=5,proxies={},
                      retries={'total_max_attempts':1},s3={'addressing_style':'path'}))
    bucket='axcf-'+uuid.uuid4().hex[:10]
    phase='create-base-bucket'
    s3.create_bucket(Bucket=bucket)
    src=work/'src';src.mkdir();(src/'sub').mkdir()
    payload={'a.txt':b'packed smoke\n','sub/b.txt':bytes(range(64))}
    src.chmod(0o755)
    (src/'sub').chmod(0o755)
    for filename, data_bytes in payload.items():
        path = src / filename
        path.write_bytes(data_bytes)
        path.chmod(0o644)
    expected={f:dict(bytes=len(b),sha256=hashlib.sha256(b).hexdigest()) for f,b in payload.items()}
    # Keep the first smoke's 1000:1000 fixture owner even though the
    # comparison's explicit I/O principal is root in this run.
    for owned in (src, *src.rglob('*')):
        os.chown(owned, 1000, 1000)
    result['fixture_source_permissions'] = {str(p.relative_to(src)): dict(uid=p.stat().st_uid,
        gid=p.stat().st_gid,mode=format(p.stat().st_mode & 0o7777,'04o')) for p in (src,*src.rglob('*'))}
    result['expected_files']=expected
    phase='publish-fixture'
    manifest=work/'manifest-key'
    invoke([producer,'--source-directory',src,'--source-consistency','best-effort-detected',
        '--source-hardlink-policy','visible-links','--output-dir',work/'objects','--manifest-output',manifest,
        '--bucket',bucket,'--endpoint',endpoint,'--region','us-east-1','--force-path-style','true',
        '--prefix','base','--metadata-codec','raw','--data-codec','raw','--inline-data','off',
        '--chunk-size','4194304','--block-size','4194304','--s3-rustfs-ec-block-size-hint','auto'],limit=25)
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
        raise ValueError('Parent file hashes or directory link counts differ from the fixture')
    phase='require-carrier-rejects-import'
    def buckets():
        return {item['Name'] for item in s3.list_buckets().get('Buckets',[])}
    def object_keys(bucket_name):
        return {item['Key'] for item in s3.list_objects_v2(Bucket=bucket_name).get('Contents',[])}
    old_buckets = buckets()
    reject = invoke([*prefix,'fork',revision,'--require-packed-carrier','--data-config',config],check=False)
    result['import_fork_rejected_before_bucket_create'] = bool(reject.returncode) and buckets()==old_buckets
    if not result['import_fork_rejected_before_bucket_create']:
        raise ValueError('Unpublished import was accepted or created a bucket')
    phase='publish-clean-packed-carrier'
    original_config = work/'parent.json'
    publication_args = [*prefix, 'publish-packed-snapshot',parent['workspace_id'],
        '--config',original_config,'--scratch',work,'--name','tiny-packed-carrier',
        '--snapshot-id',uuid.uuid4(),'--journal-id',uuid.uuid4(),'--lease-id',uuid.uuid4(),
        '--next-head',uuid.uuid4()]
    result['published']=json.loads(invoke(publication_args,limit=55).stdout)
    published_revision=result['published']['fork_source']
    if published_revision == revision or published_revision == result['published']['native_sealed_source']:
        raise ValueError('Publication did not identify a distinct immutable carrier')
    phase='fork-published-carrier'
    before_keys=object_keys(bucket)
    children=json_call(prefix,'fork',published_revision,'--require-packed-carrier','--count','2','--data-config',config)
    child,sibling=children
    def render(rev):
        return f"{rev['layer_id']}:{rev['sealed_version']}:{bytes(rev['root_hash']).hex()}"
    result.update(child_workspace_ids=[c['workspace_id'] for c in children],
        both_exact_carrier=all(render(c['fork_base']) == published_revision for c in children),
        separate_upper_buckets=len({row['upper_bucket'] for row in [parent,*children]}) == 3,
        both_child_bindings=all(binding(c['workspace_id']) for c in children),
        shared_object_keys_unchanged=before_keys==object_keys(bucket),
        child_buckets_initially_empty=all(not object_keys(c['upper_bucket']) for c in children))
    phase='read-child-before-write'
    if not visit('child-before-write',child['workspace_id'],data,rurl,namespace):
        raise ValueError('Child did not read original exact hashes')
    phase='write-child'
    if not visit('child-after-write',child['workspace_id'],data,rurl,namespace,('private.txt',b'only this child')):
        raise ValueError('Child did not expose its expected private file')
    phase='cold-reopen-child-after-write'
    if not visit('child-after-cold-reopen',child['workspace_id'],data,rurl,namespace,
                 existing=('private.txt',b'only this child')):
        raise ValueError('Fresh child mount did not retain its private file')
    phase='read-sibling-and-parent'
    correct=visit('sibling',sibling['workspace_id'],data,rurl,namespace) and visit('parent-after-write',parent['workspace_id'],data,rurl,namespace)
    result['root_permissions_match_source_rationale'] = all(
        row['permissions']['.'] == {'uid': 0, 'gid': 0, 'mode': '0755'} for row in result['readbacks'].values())
    result['passed']=bool(correct and result['root_permissions_match_source_rationale'] and all(result[key] for key in (
        'both_exact_carrier','separate_upper_buckets','both_child_bindings','shared_object_keys_unchanged',
        'child_buckets_initially_empty')))
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
            after=docker('ps','-a','--filter','label='+tag+'='+name,'--format','{{.Names}}',limit=4,bounded=False)
            cleanup['owned_containers_absent'][container.removeprefix(name+'-')]=container not in after.stdout.splitlines()
        except Exception as error:
            cleanup['container_error']=type(error).__name__+': '+clean_text(str(error))
    if cleanup['mount_absent'] and len(cleanup['owned_containers_absent'])==2 and all(cleanup['owned_containers_absent'].values()):
        try:
            shutil.rmtree(work)
        except Exception as error:
            cleanup['scratch_error']=type(error).__name__+': '+clean_text(str(error))
    cleanup['scratch_removed']=not work.exists()
    result['cleanup']=cleanup
    result['passed']=result['passed'] and cleanup['scratch_removed']
    with result_path.open('x') as stream:json.dump(result,stream,indent=2);stream.write('\n')
    print(json.dumps({'passed':result['passed'],'failed_phase':result.get('failed_phase')}))
raise SystemExit(0 if result['passed'] else 1)
