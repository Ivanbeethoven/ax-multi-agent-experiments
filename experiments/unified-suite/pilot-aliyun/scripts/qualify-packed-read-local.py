#!/usr/bin/env python3
"""Qualify the read-only Packed controls on one Linux host, without model calls."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import secrets
import shutil
import signal
import socket
import subprocess as sp
import sys
import tempfile
import time
import uuid

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT/'experiments/unified-suite/pilot-aliyun/evidence/preparation/c2-local-packed-warm-01'))
from loopback_probe import meter, probe
from bench.runtime.smoke_journal import SmokeJournal
from bench.runtime.smoke_cleanup import close_private_services


def main():
    p = argparse.ArgumentParser(description=__doc__)
    for name in ('brewfs','producer','out'):
        p.add_argument('--'+name, type=Path, required=True)
    for name in ('brewfs-sha256','producer-sha256','rustfs-image','rustfs-revision'):
        p.add_argument('--'+name, required=True)
    p.add_argument('--arm', choices=('both','overlay-read','lower-read'), default='both')
    p.add_argument('--retained-bytes', type=int, default=8388608)
    p.add_argument('--timed-passes', type=int, default=0)
    a = p.parse_args()
    if os.geteuid()!=0 or not 513<=a.retained_bytes<=64*1024**2 or not 0<=a.timed_passes<=5000:
        p.error('root, retention 513 B..64 MiB and 0..5000 passes required')
    for name in ('brewfs','producer'):
        with getattr(a,name).open('rb') as stream:
            if hashlib.file_digest(stream,'sha256').hexdigest()!=getattr(a,name+'_sha256'):
                p.error(name+' SHA256 differs')
    a.out = a.out.resolve()
    a.out.mkdir(mode=0o700)
    work=Path(tempfile.mkdtemp(prefix='ax-packed-read-'))
    # BrewFS appends '-' and a 36-character workspace UUID to the bucket.
    owner='ax-pr-'+uuid.uuid4().hex[:12]
    access,secret=secrets.token_hex(12),secrets.token_hex(24)
    journal=SmokeJournal(a.out/'stages.jsonl',(access,secret))
    env=dict(os.environ,AWS_ACCESS_KEY_ID=access,AWS_SECRET_ACCESS_KEY=secret,AWS_REGION='us-east-1',
             AWS_EC2_METADATA_DISABLED='true',RUST_LOG='warn',NO_COLOR='1',NO_PROXY='127.0.0.1,localhost',
             BREWFS_FUSE_READ_DIRECT_IO='1',BREWFS_PACKED_VERIFY_MEMO='0',
             BREWFS_PACKED_V3_RETAINED_PAYLOAD_BYTES=str(a.retained_bytes))
    env.pop('BREWFS_PACKED_WORKSPACE_READ_CONTROL',None)
    result=dict(passed=False,formal_sample=False,scope='read-only extents, lookup, non-root getattr and directory opening',
                binary_sha256=dict(brewfs=a.brewfs_sha256,producer=a.producer_sha256),
                rustfs_image=a.rustfs_image,rustfs_revision=a.rustfs_revision,arms={},
                argv=sys.argv,source_bytes=77,retained_bytes=a.retained_bytes)
    redis=None
    proc=None
    mount_processes=[]
    point=work/'mount'
    point.mkdir()
    phase='preflight'

    def clean(text):
        return text.replace(access,'[redacted]').replace(secret,'[redacted]')

    def call(*argv,check=True,timeout=20):
        r=journal.run(argv,env=env,text=True,capture_output=True,timeout=timeout)
        if check and r.returncode:
            raise RuntimeError(str(argv[0])+': '+clean(r.stderr+r.stdout)[-3000:])
        return r

    def mounted():
        return call('mountpoint','-q',point,check=False).returncode==0

    def wait_for(check):
        until=time.monotonic()+20
        while time.monotonic()<until:
            if check(): return
            time.sleep(.1)
        raise TimeoutError('readiness deadline')

    def unmount(strict):
        nonlocal proc
        if proc is None: return
        rc=call('fusermount3','-u',point,check=False).returncode if mounted() else None
        try: code=proc.wait(timeout=10)
        except sp.TimeoutExpired:
            proc.terminate()
            try: code=proc.wait(timeout=5)
            except sp.TimeoutExpired:
                proc.kill(); code=proc.wait(timeout=5)
        result.setdefault('mount_exits',[]).append(dict(unmount_exit=rc,mount_exit=code,mount_absent=not mounted()))
        proc=None
        if strict and (rc!=0 or code!=0 or mounted()):
            raise RuntimeError('non-clean FUSE exit')

    def expire(*_):
        raise TimeoutError('bounded Packed read qualification expired')

    signal.signal(signal.SIGALRM,expire)
    signal.signal(signal.SIGTERM,expire)
    signal.alarm(300)
    try:
        image=json.loads(call('docker','image','inspect',a.rustfs_image).stdout)[0]
        if image['Id']!=a.rustfs_image or image['Config'].get('Labels',{}).get('org.opencontainers.image.revision')!=a.rustfs_revision:
            raise ValueError('RustFS image or source revision differs')
        with socket.socket() as sock:
            sock.bind(('127.0.0.1',0)); rp=sock.getsockname()[1]
        redis=sp.Popen(['redis-server','--bind','127.0.0.1','--port',str(rp),'--save','','--appendonly','no'],
                       stdout=sp.DEVNULL,stderr=sp.DEVNULL)
        wait_for(lambda:call('redis-cli','-p',rp,'ping',check=False).stdout.strip()=='PONG')
        phase='start-private-rustfs'
        work.chmod(0o711)
        for i in range(4):
            d=work/str(i); d.mkdir(); os.chown(d,10001,10001)
        knobs=dict(RUSTFS_ACCESS_KEY=access,RUSTFS_SECRET_KEY=secret,RUSTFS_ADDRESS='0.0.0.0:9000',
                   RUSTFS_CONSOLE_ENABLE='false',RUSTFS_VOLUMES='/data/d{0...3}',
                   RUSTFS_UNSAFE_BYPASS_DISK_CHECK='true',RUSTFS_OBS_LOG_DIRECTORY='/var/log/rustfs')
        knobs.update({f'RUSTFS_EC_{k}':'true' for k in ('BLOCK_SIZE_HINT_ENABLE','BLOCK_SIZE_HINT_FLEET_CONFIRMED','READ_QUANTUM_ENABLE','READ_QUANTUM_FLEET_CONFIRMED')})
        ef=work/'rustfs.env'; ef.write_text(''.join(f'{k}={v}\n' for k,v in knobs.items())); ef.chmod(0o600)
        call('docker','run','--pull=never','-d','--name',owner,
             '--label','ax.experiment.run='+owner,'--label','ax.experiment.attempt='+str(a.out.resolve()),'--memory','2g','--cpus','2',
             '-p','127.0.0.1::9000','--env-file',ef,
             *[v for i in range(4) for v in ('-v',f'{work/str(i)}:/data/d{i}')],a.rustfs_image)
        port=int(call('docker','port',owner,'9000/tcp').stdout.strip().rsplit(':',1)[1])
        endpoint=f'http://127.0.0.1:{port}'
        from boto3 import client
        from botocore.config import Config
        s3=client('s3',endpoint_url=endpoint,aws_access_key_id=access,aws_secret_access_key=secret,
                  region_name='us-east-1',config=Config(signature_version='s3v4',connect_timeout=1,read_timeout=2,
                  retries={'total_max_attempts':1},s3={'addressing_style':'path'}))
        def ready():
            try: s3.list_buckets(); return True
            except Exception: return False
        wait_for(ready)
        s3.create_bucket(Bucket=owner)
        phase='prepare-packed-parent'
        source=work/'source'; (source/'sub').mkdir(parents=True)
        payload={'a.txt':b'packed smoke\n','sub/b.txt':bytes(range(64))}
        rows=[]
        for name,data in payload.items():
            (source/name).write_bytes(data)
            rows.append(dict(path=name,offset=0,length=len(data),sha256=hashlib.sha256(data).hexdigest()))
        manifest=work/'manifest'
        call(a.producer,'--source-directory',source,'--source-consistency','best-effort-detected',
             '--source-hardlink-policy','visible-links','--output-dir',work/'objects','--manifest-output',manifest,
             '--bucket',owner,'--endpoint',endpoint,'--region','us-east-1','--force-path-style','true',
             '--prefix','base','--metadata-codec','raw','--data-codec','raw','--inline-data','off',
             '--chunk-size','4194304','--block-size','4194304','--s3-rustfs-ec-block-size-hint','auto',timeout=40)
        data=dict(backend='s3',s3=dict(bucket=owner,endpoint=endpoint,region='us-east-1',force_path_style=True,rustfs_ec_block_size_hint='auto'))
        cfg=work/'data.json'; cfg.write_text(json.dumps({'data':data}))
        rurl=f'redis://127.0.0.1:{rp}/0'
        parent=json.loads(call(a.brewfs,'workspace','--meta-backend','redis','--meta-url',rurl,'--workspace-namespace',owner,
                         'init-packed-volume','--manifest-key',manifest.read_text().strip(),'--data-config',cfg).stdout)
        result['parent']=parent
        arms=('overlay-read','lower-read') if a.arm=='both' else (a.arm,)
        with meter(port) as (metered,snapshot):
            for arm in arms:
                phase='mount-'+arm
                env['BREWFS_PACKED_WORKSPACE_READ_CONTROL']=arm
                cfg=work/(arm+'.json')
                cfg.write_text(json.dumps(dict(volume_format='workspace-v1',volume={'format':'workspace-v1'},
                    workspace=parent['workspace_id'],workspace_namespace=owner,
                    data=dict(data,s3=dict(data['s3'],endpoint=metered)),meta=dict(backend='redis',redis={'url':rurl}),
                    layout=dict(chunk_size=4194304,block_size=4194304),
                    fuse=dict(workers=4,max_background=128,cache_ttl_ms=0,keep_cache=False),
                    cache=dict(cache_root=str(work/('cache-'+arm)),writeback_mode='upload_before_commit',
                               read_memory_bytes=536870912,read_ssd_bytes=0,compression='none'))))
                with (a.out/(arm+'.log')).open('w') as log:
                    proc=sp.Popen([str(a.brewfs),'mount','--config',str(cfg),str(point)],env=env,stdout=log,stderr=sp.STDOUT)
                    mount_processes.append(proc)
                    def mount_ready():
                        if proc.poll() is not None:
                            raise RuntimeError(arm+' mount exited; see mount log')
                        return mounted()
                    wait_for(mount_ready)
                    phase='probe-'+arm
                    with journal.stage('warm-lower:'+arm):
                        result['arms'][arm]=probe(point,rows,snapshot,a.out/arm,retained_bytes=a.retained_bytes,
                             extent_arm=arm,timed_passes=a.timed_passes,mount_alive=lambda:proc is not None and proc.poll() is None)
                    unmount(True)
        result['passed']=all(row['hot'] and (not a.timed_passes or row['timed_extent_reads']['passed']) for row in result['arms'].values())
    except Exception as e:
        result.update(failed_phase=phase,error=clean(str(e)))
    finally:
        signal.alarm(0)
        try: unmount(False)
        except Exception as e: result['cleanup_error']=clean(str(e))
        cleanup=close_private_services(call=call,owner=owner,out=a.out,scratch=work,
            points=[point],redis=redis,processes=mount_processes,clean=clean)
        result['scratch_path']=str(work)
        if result['passed'] and cleanup['passed'] and not result.get('cleanup_error'):
            try: shutil.rmtree(work)
            except Exception as e: result['scratch_cleanup_error']=clean(str(e))
        cleanup['scratch_removed']=not work.exists()
        result['cleanup']=cleanup
        result['passed']=result['passed'] and cleanup['passed'] and cleanup['scratch_removed'] and not result.get('cleanup_error')
        for log in a.out.glob('*.log'):
            log.write_text(clean(log.read_text(errors='replace')))
        (a.out/'receipt.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(dict(passed=result['passed'],failed_phase=result.get('failed_phase'))))
    return 0 if result['passed'] else 1


if __name__=='__main__':
    raise SystemExit(main())
