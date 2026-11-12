#!/usr/bin/env python3
"""Single-host C3 lifecycle smoke; run as root with boto3, Docker and FUSE."""
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

p = argparse.ArgumentParser(description=__doc__)
for flag in ('brewfs', 'producer', 'juicefs', 'smallfiles', 'out'):
    p.add_argument('--'+flag, required=True, type=Path)
p.add_argument('--rustfs-image', required=True)
a = p.parse_args()
a.out.mkdir(mode=0o700)
result = {'formal_sample': False, 'files': 128, 'arms': {}}
run_id = 'ax-rbm-' + uuid.uuid4().hex[:12]
key, secret = secrets.token_hex(12), secrets.token_hex(24)
env = dict(os.environ, AWS_ACCESS_KEY_ID=key, AWS_SECRET_ACCESS_KEY=secret,
           AWS_REGION='us-east-1', AWS_EC2_METADATA_DISABLED='true', NO_PROXY='127.0.0.1,localhost')
opts = ['--files','128','--hot-files','32','--workers','2','--read-ops','128',
        '--cow-files','16','--write-ops','32','--mixed-ops','64']

def call(*argv, timeout=60, check=True):
    return sp.run(list(map(str, argv)), env=env, check=check, timeout=timeout,
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

def workload(stage, root, out, *extra):
    call(a.smallfiles, stage, '--root', root, '--output', out, *opts, *extra)
    data = json.loads((out/'result.json').read_text())
    if not data['passed']:
        raise RuntimeError(stage + ' failed')
    return {k:v for k,v in data.items() if k in (
        'passed','files','checked_files','bytes_checked','verified_reads','writes',
        'actual_read_bytes','actual_write_bytes')}

def mounted(point):
    return call('mountpoint','-q',point,check=False).returncode == 0

def visit(product, point, work, out, cmd, arm, reopen=False):
    label = 'reopen' if reopen else 'initial'
    arm['phase'] = label+'-mount'
    with (out/(label+'.log')).open('w') as log:
        proc = sp.Popen(list(map(str,cmd)), env=env, stdout=log, stderr=sp.STDOUT)
        try:
            def ready():
                if proc.poll() is not None:
                    raise RuntimeError(f'{product} mount exited {proc.returncode}')
                return mounted(point)
            wait(ready)
            if reopen:
                arm['phase'] = 'reopen-verify'
                arm['verify_touched'] = workload('verify-touched',point,out/'verify-touched',
                                                  '--state',out/'run'/'expected-touched.tsv')
            else:
                arm['phase'] = 'prepare-and-verify-base'
                if product == 'juicefs':
                    import shutil
                    shutil.copytree(work/'source'/'small',point/'small')
                arm['verify_base'] = workload('verify-base',point,out/'verify-base')
                arm['phase'] = 'run'
                arm['run'] = workload('run',point,out/'run')
        finally:
            was_mounted = mounted(point)
            try:
                rc = call('fusermount3','-u',point,check=False).returncode if was_mounted else None
                try:
                    code = proc.wait(timeout=10) if was_mounted else None
                except sp.TimeoutExpired:
                    code = None
            finally:
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

def run(work):
    from boto3 import client
    from botocore.config import Config
    result['binary_sha256'] = {}
    for name in ('brewfs','producer','juicefs','smallfiles'):
        with getattr(a,name).open('rb') as f:
            result['binary_sha256'][name] = hashlib.file_digest(f,'sha256').hexdigest()
    result['rustfs_image'] = call('docker','image','inspect','-f','{{.Id}}',a.rustfs_image).stdout.strip()
    rp = local_port()
    redis = sp.Popen(['redis-server','--bind','127.0.0.1','--port',str(rp),
                      '--save','','--appendonly','no'], stdout=sp.DEVNULL, stderr=sp.DEVNULL)
    points = []
    try:
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
        wait(s3_ready)
        result.pop('last_s3_probe_error', None)
        result['phase'] = 'workload'
        result['source'] = workload('prepare',work/'source',a.out/'prepare')
        for product, db in [('brewfs',0),('juicefs',1)]:
            arm = result['arms'][product] = {'phase':'prepare', 'passed':False}
            out = a.out/product
            out.mkdir()
            point = work/product
            point.mkdir()
            points.append(point)
            try:
                bucket = run_id+'-'+product
                s3.create_bucket(Bucket=bucket)
                url = f'redis://127.0.0.1:{rp}/{db}'
                if product == 'brewfs':
                    manifest = work/'manifest'
                    call(a.producer,'--source-directory',work/'source','--source-consistency',
                         'best-effort-detected','--source-hardlink-policy','visible-links','--output-dir',
                         work/'objects','--manifest-output',manifest,'--bucket',bucket,'--endpoint',ep,
                         '--region','us-east-1','--force-path-style','true','--prefix','base',
                         '--metadata-codec','raw','--data-codec','raw','--inline-data','off',
                         '--chunk-size','67108864','--block-size','4194304',
                         '--s3-rustfs-ec-block-size-hint','auto')
                    data = dict(backend='s3',s3=dict(bucket=bucket,endpoint=ep,region='us-east-1',
                                force_path_style=True,rustfs_ec_block_size_hint='auto'))
                    cfg = work/'data.json'
                    cfg.write_text(json.dumps({'data':data}))
                    arm['phase'] = 'init-packed-volume'
                    raw = call(a.brewfs,'workspace','--meta-backend','redis',
                        '--meta-url',url,'--workspace-namespace',run_id,'init-packed-volume',
                        '--manifest-key',manifest.read_text().strip(),'--data-config',cfg).stdout
                    raw = re.sub(r'\x1b\[[0-?]*[ -/]*[@-~]', '', raw)
                    decoder = json.JSONDecoder()
                    workspace = None
                    for match in re.finditer(r'(?m)^\s*\{', raw):
                        try:
                            value, _ = decoder.raw_decode(raw, match.end()-1)
                        except json.JSONDecodeError:
                            continue
                        if isinstance(value, dict) and value.get('workspace_id'):
                            workspace = value
                    if workspace is None:
                        raise RuntimeError('BrewFS init output had no workspace_id: '+raw[-1500:])
                    def command(cache):
                        cfg = work/(cache+'.json')
                        cfg.write_text(json.dumps(dict(volume_format='workspace-v1',
                            volume={'format':'workspace-v1'},workspace=workspace['workspace_id'],
                            workspace_namespace=run_id,data=data,meta=dict(backend='redis',redis={'url':url}),
                            layout=dict(chunk_size=67108864,block_size=4194304),
                            fuse=dict(cache_ttl_ms=0,keep_cache=False),
                            cache=dict(cache_root=str(work/cache),writeback_mode='upload_before_commit'))))
                        return [a.brewfs,'mount','--config',cfg,point]
                else:
                    call(a.juicefs,'format','--storage','s3','--bucket',ep+'/'+bucket,
                         '--access-key',key,'--secret-key',secret,url,'ax-smoke')
                    def command(cache):
                        return [a.juicefs,'mount','--cache-dir',work/cache,url,point]
                visit(product,point,work,out,command('initial-'+product),arm)
                visit(product,point,work,out,command('reopen-'+product),arm,reopen=True)
                arm.update(phase='complete',passed=True)
            except Exception as e:
                arm['error'] = error(e)
    finally:
        result['mounts_absent'] = all(not mounted(point) for point in points)
        logs = call('docker','logs','--tail','25',run_id,check=False)
        (a.out/'rustfs.log').write_text((logs.stdout+logs.stderr).replace(secret,'[redacted]').replace(key,'[redacted]'))
        call('docker','rm','-fv',run_id,check=False)
        result['rustfs_removed'] = call('docker','inspect',run_id,check=False).returncode != 0
        redis.terminate()
        redis.wait(timeout=5)

scratch = Path(tempfile.mkdtemp(prefix=run_id+'-'))
try:
    run(scratch)
except Exception as e:
    result['error'] = error(e)
finally:
    if result.get('mounts_absent', 'rustfs_image' not in result) and result.get('rustfs_removed', 'rustfs_image' not in result):
        import shutil
        shutil.rmtree(scratch)
    result['scratch_removed'] = not scratch.exists()
    result['passed'] = (len(result['arms']) == 2 and all(x['passed'] for x in result['arms'].values())
                        and result['scratch_removed'])
    (a.out/'receipt.json').write_text(json.dumps(result,indent=2)+'\n')
raise SystemExit(not result['passed'])
