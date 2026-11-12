#!/usr/bin/env python3
"""Single-host C4 native fork/clone smoke; run as root with boto3, Docker and FUSE."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import socket
import subprocess as sp
import tempfile
import time
import uuid
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[4]))
from bench.runtime.fork_candidate import verify_candidate
from bench.runtime.smoke_tree import file_tree
from bench.runtime.smoke_journal import SmokeJournal
from bench.runtime.smoke_cleanup import close_private_services

p = argparse.ArgumentParser(description=__doc__)
for flag in ('brewfs', 'producer', 'juicefs', 'out'):
    p.add_argument('--'+flag, required=True, type=Path)
p.add_argument('--brewfs-revision', required=True)
p.add_argument('--brewfs-provenance', type=Path)
p.add_argument('--rustfs-image', required=True)
p.add_argument('--product', choices=('brewfs','juicefs'), required=True)
a = p.parse_args()
a.out.mkdir(mode=0o700)
result = {'brewfs_revision': a.brewfs_revision, 'formal_sample': False, 'files': 2, 'arms': {}}
run_id = 'ax-rbm-' + uuid.uuid4().hex[:12]
redis = None
points = []
mount_processes = []
key, secret = secrets.token_hex(12), secrets.token_hex(24)
journal = SmokeJournal(a.out/'stages.jsonl', (key, secret))
env = dict(os.environ, AWS_ACCESS_KEY_ID=key, AWS_SECRET_ACCESS_KEY=secret,
           AWS_REGION='us-east-1', AWS_EC2_METADATA_DISABLED='true', NO_PROXY='127.0.0.1,localhost',
           RUST_LOG='warn', NO_COLOR='1')
def call(*argv, timeout=60, check=True):
    return journal.run(argv, env=env, check=check, timeout=timeout,
                  capture_output=True, text=True)

def error(e):
    return (str(e) + ' ' + (getattr(e, 'stderr', None) or ''))[-3000:].replace(secret,'[redacted]').replace(key,'[redacted]')

def wait(probe):
    for _ in range(150):
        if probe():
            return
        time.sleep(.1)
    raise TimeoutError('readiness timeout')

def local_port():
    with socket.socket() as s:
        s.bind(('127.0.0.1',0))
        return s.getsockname()[1]

def mounted(point):
    return call('mountpoint','-q',point,check=False).returncode == 0

def tree(root):
    return {name: row['sha256'] for name, row in file_tree(root).items() if row['type']=='file'}

def visit(product, point, out, cmd, arm, label, action):
    arm['phase'] = label+'-mount'
    with (out/(label+'.log')).open('w') as log:
        proc = sp.Popen(list(map(str,cmd)), env=env, stdout=log, stderr=sp.STDOUT)
        mount_processes.append(proc)
        try:
            def ready():
                if proc.poll() is not None:
                    raise RuntimeError(f'{product} mount exited {proc.returncode}')
                return mounted(point)
            wait(ready)
            arm['phase'] = label+'-check'
            with journal.stage(label+'-check', timeout=90):
                action()
        finally:
            was_mounted = mounted(point)
            rc = call('fusermount3','-u',point,check=False).returncode if was_mounted else None
            try:
                code = proc.wait(timeout=10) if was_mounted else None
            except sp.TimeoutExpired:
                code = None
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=5)
                except sp.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=5)
            arm[label+'_unmount'] = {'unmount_exit':rc, 'mount_exit':code,
                                     'mount_absent':not mounted(point)}
        arm['phase'] = label+'-unmount'
        if rc != 0 or code != 0 or mounted(point):
            raise RuntimeError('nonclean unmount')

def decode(raw, kind):
    raw = re.sub(r'\x1b\[[0-?]*[ -/]*[@-~]', '', raw)
    for match in re.finditer(r'(?m)^\s*[\[{]', raw):
        try:
            obj, _ = json.JSONDecoder().raw_decode(raw, match.end()-1)
        except json.JSONDecodeError:
            continue
        if kind == 'parent' and isinstance(obj, dict) and obj.get('workspace_id'):
            return obj
        if kind == 'publication' and isinstance(obj, dict) and obj.get('fork_source'):
            return obj
        if kind == 'child' and isinstance(obj,list) and len(obj)==1 and isinstance(obj[0],dict) and obj[0].get('workspace_id'):
            return obj[0]
    raise RuntimeError(f'No {kind} workspace in BrewFS output')

def run(work):
    global redis
    from boto3 import client
    from botocore.config import Config
    if a.product == 'brewfs':
        if a.brewfs_provenance is None:
            raise ValueError('C4 requires --brewfs-provenance for the qualified publication candidate')
        result['build_provenance'] = verify_candidate(a.brewfs, a.brewfs_provenance, a.brewfs_revision)
        if '--require-packed-carrier' not in call(a.brewfs,'workspace','fork','--help').stdout:
            raise ValueError('C4 candidate lacks --require-packed-carrier')
    result['binary_sha256'] = {}
    for name in ('brewfs','producer','juicefs'):
        with getattr(a,name).open('rb') as f:
            result['binary_sha256'][name] = hashlib.file_digest(f,'sha256').hexdigest()
    result['rustfs_image'] = call('docker','image','inspect','-f','{{.Id}}',a.rustfs_image).stdout.strip()
    rp = local_port()
    redis = sp.Popen(['redis-server','--bind','127.0.0.1','--port',str(rp),
                      '--save','','--appendonly','no'], stdout=sp.DEVNULL, stderr=sp.DEVNULL)
    wait(lambda: call('redis-cli','-p',rp,'ping',check=False).stdout.strip()=='PONG')
    for n in range(4):
        d = work/str(n)
        d.mkdir()
        os.chown(d,10001,10001)
    work.chmod(0o711)
    knobs = dict(RUSTFS_ACCESS_KEY=key,RUSTFS_SECRET_KEY=secret,
                 RUSTFS_ADDRESS='0.0.0.0:9000',RUSTFS_CONSOLE_ENABLE='false', RUSTFS_OBS_LOG_DIRECTORY='/var/log/rustfs',RUSTFS_VOLUMES='/data/d{0...3}',
                 RUSTFS_UNSAFE_BYPASS_DISK_CHECK='true')
    knobs.update({f'RUSTFS_EC_{n}': 'true' for n in (
        'BLOCK_SIZE_HINT_ENABLE','BLOCK_SIZE_HINT_FLEET_CONFIRMED',
        'READ_QUANTUM_ENABLE','READ_QUANTUM_FLEET_CONFIRMED')})
    ef = work/'rustfs.env'
    ef.write_text(''.join(f'{k}={v}\n' for k,v in knobs.items()))
    ef.chmod(0o600)
    call('docker','run','--pull=never','-d','--name',run_id,
             '--label','ax.experiment.run='+run_id,'--label','ax.experiment.attempt='+str(a.out.resolve()),'--cpus','2','--memory','2g',
         '-p','127.0.0.1::9000','--env-file',ef,
         *[part for n in range(4) for part in ('-v',f'{work/str(n)}:/data/d{n}')],
         result['rustfs_image'])
    port = call('docker','port',run_id,'9000/tcp').stdout.strip().rsplit(':',1)[1]
    ep = 'http://127.0.0.1:'+port
    s3 = client('s3',endpoint_url=ep,aws_access_key_id=key,aws_secret_access_key=secret,
                region_name='us-east-1',config=Config(signature_version='s3v4',
                connect_timeout=1,read_timeout=2,retries={'total_max_attempts':1},
                s3={'addressing_style':'path'}))
    def s3_ready():
        from botocore.exceptions import BotoCoreError, ClientError
        try:
            s3.list_buckets()
            return True
        except (BotoCoreError, ClientError) as e:
            result['last_s3_probe_error'] = error(e)
            return False
    result['phase'] = 'rustfs-readiness'
    with journal.stage('rustfs-readiness'):
        wait(s3_ready)
    result.pop('last_s3_probe_error', None)
    result['phase'] = 'workload'
    source = work/'source'
    (source/'sub').mkdir(parents=True)
    (source/'base.txt').write_bytes(b'parent bytes\n')
    (source/'sub'/'keep.bin').write_bytes(bytes(range(64)))
    expected = result['source'] = tree(source)
    for product, db in [(a.product, 0)]:
        arm = result['arms'][product] = {'phase':'prepare', 'passed':False}
        out = a.out/product
        out.mkdir()
        point = work/product
        point.mkdir()
        points.append(point)
        try:
            bucket = run_id+'-'+product
            with journal.stage('create-bucket'):
                s3.create_bucket(Bucket=bucket)
            url = f'redis://127.0.0.1:{rp}/{db}'
            if product == 'brewfs':
                manifest = work/'manifest'
                call(a.producer,'--source-directory',source,'--source-consistency',
                     'best-effort-detected','--source-hardlink-policy','visible-links','--output-dir',
                     work/'objects','--manifest-output',manifest,'--bucket',bucket,'--endpoint',ep,
                     '--region','us-east-1','--force-path-style','true','--prefix','base',
                     '--metadata-codec','raw','--data-codec','raw','--inline-data','off',
                     '--chunk-size','4194304','--block-size','4194304',
                     '--s3-rustfs-ec-block-size-hint','auto')
                data = dict(backend='s3',s3=dict(bucket=bucket,endpoint=ep,region='us-east-1',
                            force_path_style=True,rustfs_ec_block_size_hint='auto'))
                cfg = work/'data.json'
                cfg.write_text(json.dumps({'data':data}))
                prefix = [a.brewfs,'workspace','--meta-backend','redis',
                          '--meta-url',url,'--workspace-namespace',run_id]
                arm['phase'] = 'init-packed-volume'
                parent = decode(call(*prefix,'init-packed-volume','--manifest-key',
                                     manifest.read_text().strip(),'--data-config',cfg).stdout,'parent')
                rev = parent['fork_base']
                revision = f"{rev['layer_id']}:{rev['sealed_version']}:{bytes(rev['root_hash']).hex()}"
                arm['revision'] = revision
                def command(cache, workspace):
                    mount_cfg = work/(cache+'.json')
                    mount_cfg.write_text(json.dumps(dict(volume_format='workspace',
                        volume={'format':'workspace'},workspace=workspace['workspace_id'],
                        workspace_namespace=run_id,data=data,meta=dict(backend='redis',redis={'url':url}),
                        layout=dict(chunk_size=4194304,block_size=4194304),
                        fuse=dict(cache_ttl_ms=0,keep_cache=False),
                        cache=dict(cache_root=str(work/cache),writeback_mode='upload_before_commit'))))
                    return [a.brewfs,'mount','--config',mount_cfg,point]
                visit(product,point,out,command('parent-before',parent),arm,'parent-before',
                      lambda: arm.update(parent_before=tree(point)))
                if arm['parent_before'] != expected:
                    raise RuntimeError('BrewFS parent readback differs from source')
                arm['phase'] = 'reject-unpublished-import'
                def buckets():
                    return {item['Name'] for item in s3.list_buckets().get('Buckets',[])}
                before = buckets()
                rejected = call(*prefix,'fork',revision,'--require-packed-carrier','--data-config',cfg,check=False)
                arm['import_rejected_without_bucket_create'] = rejected.returncode != 0 and buckets()==before
                if not arm['import_rejected_without_bucket_create']:
                    raise RuntimeError('Unpublished import was accepted or allocated a bucket')
                arm['phase'] = 'publish-packed-snapshot'
                published = decode(call(*prefix,'publish-packed-snapshot',parent['workspace_id'],
                    '--config',work/'parent-before.json','--scratch',work,'--name','rbm-c4-carrier',
                    '--snapshot-id',uuid.uuid4(),'--journal-id',uuid.uuid4(),'--lease-id',uuid.uuid4(),
                    '--next-head',uuid.uuid4()).stdout,'publication')
                arm['publication'] = published
                carrier = published['fork_source']
                if carrier in (revision,published['native_sealed_source']):
                    raise RuntimeError('Publication did not create a distinct Packed carrier')
                arm['phase'] = 'native-fork'
                child = decode(call(*prefix,'fork',carrier,'--require-packed-carrier',
                                    '--count','1','--data-config',cfg).stdout,'child')
                arm['separate_ids'] = child['workspace_id'] != parent['workspace_id']
                if not arm['separate_ids']:
                    raise RuntimeError('Fork returned the parent workspace ID')
                cr = child['fork_base']
                arm['same_published_carrier'] = f"{cr['layer_id']}:{cr['sealed_version']}:{bytes(cr['root_hash']).hex()}" == carrier
                arm['separate_upper_buckets'] = child['upper_bucket'] != parent['upper_bucket']
                arm['child_upper_initially_empty'] = not s3.list_objects_v2(Bucket=child['upper_bucket']).get('Contents',[])
                if not all(arm[k] for k in ('same_published_carrier','separate_upper_buckets','child_upper_initially_empty')):
                    raise RuntimeError('Child does not have the published lower and a new empty upper')
                for label, record in [('parent',parent),('child',child)]:
                    keys = call('redis-cli','-p',rp,'-n',db,'--scan','--pattern',
                                '*packed/v3/current/'+record['workspace_id']).stdout.splitlines()
                    arm[label+'_packed_binding'] = bool(keys)
                parent_path = child_path = point
            else:
                call(a.juicefs,'format','--storage','s3','--bucket',ep+'/'+bucket,
                     '--access-key',key,'--secret-key',secret,url,'ax-smoke')
                def command(cache, workspace):
                    return [a.juicefs,'mount','--cache-dir',work/cache,url,point]
                parent_path, child_path = point/'parent', point/'child'
                parent = child = None
                def clone():
                    import shutil
                    shutil.copytree(source,parent_path)
                    arm['parent_before'] = tree(parent_path)
                    if arm['parent_before'] != expected:
                        raise RuntimeError('JuiceFS parent readback differs from source')
                    arm['phase'] = 'native-clone'
                    call(a.juicefs,'clone',parent_path,child_path)
                    if parent_path.stat().st_ino == child_path.stat().st_ino:
                        raise RuntimeError('Clone returned the parent inode')
                visit(product,point,out,command('juicefs-clone',None),arm,'clone',clone)
            changed = {'base.txt': b'child overwrite\\n', 'child.txt': b'child new file\\n'}
            expected_after = expected | {k:hashlib.sha256(v).hexdigest() for k,v in changed.items()}
            def modify_child():
                arm['child_before'] = tree(child_path)
                arm['inherited_all'] = arm['child_before'] == expected
                if not arm['inherited_all']:
                    raise RuntimeError('Child does not inherit the full parent before writing')
                for name, payload in changed.items():
                    with (child_path/name).open('wb') as stream:
                        stream.write(payload)
                        stream.flush()
                        os.fsync(stream.fileno())
                arm['child_after_write'] = tree(child_path)
            visit(product,point,out,command('child-write-'+product,child),arm,'child-write',modify_child)
            visit(product,point,out,command('parent-after-'+product,parent),arm,'parent-after',
                  lambda: arm.update(parent_after=tree(parent_path)))
            visit(product,point,out,command('child-reopen-'+product,child),arm,'child-reopen',
                  lambda: arm.update(child_reopened=tree(child_path)))
            arm['parent_unchanged'] = arm['parent_after'] == expected
            arm['child_writes_persist'] = all(arm['child_reopened'].get(k)==expected_after[k] for k in changed)
            arm['full_reopen_match'] = arm['child_reopened'] == expected_after
            arm.update(phase='complete', passed=all(arm[k] for k in (
                'inherited_all','parent_unchanged','child_writes_persist','full_reopen_match')))
        except Exception as e:
            arm['error'] = error(e)

scratch = Path(tempfile.mkdtemp(prefix=run_id+'-'))
try:
    run(scratch)
except Exception as e:
    result['error'] = error(e)
finally:
    closed = close_private_services(call=call, owner=run_id, out=a.out, scratch=scratch,
        points=points, redis=redis, processes=mount_processes, clean=lambda text: text.replace(secret,'[redacted]').replace(key,'[redacted]'))
    result['cleanup'] = closed
    result.update({k: closed[k] for k in ('mounts_absent','rustfs_removed','redis_exited','filesystem_processes_exited')})
    workload_ok = not result.get('error') and len(result['arms']) == 1 and all(x['passed'] for x in result['arms'].values())
    result['scratch_path'] = str(scratch)
    if workload_ok and closed['passed']:
        try:
            import shutil
            shutil.rmtree(scratch)
        except Exception as e:
            result['scratch_cleanup_error'] = error(e)
    result['scratch_removed'] = not scratch.exists()
    result['passed'] = workload_ok and closed['passed'] and result['scratch_removed']
    (a.out/'receipt.json').write_text(json.dumps(result,indent=2)+'\n')
raise SystemExit(not result['passed'])
