#!/usr/bin/env python3
"""Exercise actual native FUSE writer exclusion and expired-writer publication.

Uses a new workspace and bucket. Preserves objects, metadata and all logs. This
is a correctness qualification, never an uninterrupted performance sample.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import time
import uuid
from urllib.parse import urlsplit

REPO = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(REPO))
from bench.runtime.common import digest, identity, alive, write_new


def backend_time(url):
    endpoint=urlsplit(url)
    if endpoint.scheme!='redis' or endpoint.username or endpoint.password:
        raise ValueError('Private test Redis endpoint without embedded credentials required')
    with socket.create_connection((endpoint.hostname,endpoint.port or 6379),timeout=5) as sock:
        sock.sendall(b'*1\r\n$4\r\nTIME\r\n')
        with sock.makefile('rb') as stream:
            if stream.readline()!=b'*2\r\n':raise ValueError('Unexpected Redis TIME reply')
            values=[]
            for _ in range(2):
                size=stream.readline()
                if not size.startswith(b'$'):raise ValueError('Unexpected Redis TIME field')
                length=int(size[1:])
                if not 1<=length<=32:raise ValueError('Invalid Redis TIME length')
                raw=stream.read(length)
                if len(raw)!=length or stream.read(2)!=b'\r\n':
                    raise ValueError('Invalid Redis TIME field')
                values.append(int(raw))
    return values[0]*10**9+values[1]*1000


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--binary', type=Path, required=True)
    p.add_argument('--binary-sha256', required=True)
    p.add_argument('--services', type=Path, required=True)
    p.add_argument('--redis-url', default='redis://127.0.0.1:16379/0')
    p.add_argument('--s3-endpoint', default='http://127.0.0.1:19000')
    p.add_argument('--native-base', type=Path, default=Path(__file__).resolve().parents[1]/'profiles/native-base-p1.json')
    p.add_argument('--out', type=Path, required=True)
    a = p.parse_args()
    if os.geteuid() != 0 or not a.out.is_absolute() or digest(a.binary) != a.binary_sha256:
        raise ValueError('Root on the owned test host and exact built binary required')
    backend_time(a.redis_url)
    a.out.mkdir(parents=True, exist_ok=False, mode=0o700)
    env = dict(os.environ, AWS_REGION='us-east-1', AWS_EC2_METADATA_DISABLED='true', RUST_LOG='error', NO_COLOR='1')
    for line in (a.services/'rustfs.env').read_text().splitlines():
        key, value = line.split('=', 1)
        env[{'RUSTFS_ACCESS_KEY':'AWS_ACCESS_KEY_ID','RUSTFS_SECRET_KEY':'AWS_SECRET_ACCESS_KEY'}[key]] = value
    bucket = 'axp-fence-'+uuid.uuid4().hex[:12]
    catalog = 'ax-pilot-fence-'+uuid.uuid4().hex
    prefix = [str(a.binary),'workspace','--meta-backend','redis','--meta-url',a.redis_url,'--workspace-namespace',catalog]
    result = dict(passed=False, binary_sha256=digest(a.binary), bucket=bucket, catalog=catalog,
        scope='Actual FUSE/Redis/S3 correctness only; pause/expire/takeover is separate from clean handoff timing',
        automatic_retry=False, model_calls=0)
    processes = {}; source_stopped = False; writer = None; writer_streams = []; phase = 'initialize'

    def command(name, argv):
        directory = a.out/name;directory.mkdir()
        write_new(directory/'command.json', list(map(str, argv)))
        with (directory/'stdout').open('xb') as out, (directory/'stderr').open('xb') as err:
            process = subprocess.Popen(list(map(str,argv)), env=env, stdout=out, stderr=err)
            write_new(directory/'process.json', identity(process.pid))
            code = process.wait(timeout=60)
        write_new(directory/'exit.json', dict(exit_code=code))
        if code:raise RuntimeError('Command failed: '+name)
        return (directory/'stdout').read_text()

    def inspect(name):
        return json.loads(command(name, prefix+['inspect', result['workspace_id']]))

    def mount(name, port, expect_ready=True):
        directory = a.out/name;directory.mkdir()
        point = directory/'mnt';point.mkdir()
        config = dict(base, cache=dict(base['cache'],cache_root=str(directory/'cache')))
        write_new(directory/'config.json',config)
        argv = [str(a.binary),'mount','--config',str(directory/'config.json'),'--metrics',
            '--metrics-addr','127.0.0.1:'+str(port),'--s3-rustfs-ec-block-size-hint','auto',str(point)]
        write_new(directory/'command.json',argv)
        with (directory/'stdout').open('xb') as out, (directory/'stderr').open('xb') as err:
            process = subprocess.Popen(argv,env=env,stdout=out,stderr=err)
        processes[name] = dict(process=process, identity=identity(process.pid), point=point)
        write_new(directory/'process.json',processes[name]['identity'])
        deadline = time.monotonic()+60
        while time.monotonic()<deadline:
            if os.path.ismount(point):
                if not expect_ready:raise ValueError('Second live writer mounted')
                return point
            if process.poll() is not None:
                write_new(directory/'exit.json',dict(exit_code=process.returncode,mount_absent=True))
                if expect_ready:raise RuntimeError('Mount failed: '+name)
                if process.returncode==0 or 'workspace is busy' not in (directory/'stderr').read_text():
                    raise ValueError('Second mount failed for a reason other than writer exclusion')
                return point
            time.sleep(.05)
        raise TimeoutError('Inspect original mount process: '+name)

    def stop(name, clean):
        entry = processes[name]
        command('unmount-'+name,['fusermount3','-u',str(entry['point'])])
        code = entry['process'].wait(timeout=60)
        if os.path.ismount(entry['point']) or (clean and code):
            raise ValueError('Mount/process did not close: '+name)
        write_new(a.out/name/'exit.json',dict(exit_code=code,mount_absent=True,stale_writer=not clean))

    def write_file(path, raw):
        with path.open('wb') as stream:
            stream.write(raw);stream.flush();os.fsync(stream.fileno())
        fd = os.open(path.parent,os.O_RDONLY|os.O_DIRECTORY)
        try:os.fsync(fd)
        finally:os.close(fd)

    try:
        import boto3
        from botocore.config import Config
        client = boto3.client('s3',endpoint_url=a.s3_endpoint,region_name='us-east-1',
            aws_access_key_id=env['AWS_ACCESS_KEY_ID'],aws_secret_access_key=env['AWS_SECRET_ACCESS_KEY'],
            config=Config(signature_version='s3v4',retries={'max_attempts':0},s3={'addressing_style':'path'}))
        client.create_bucket(Bucket=bucket);client.close()
        data = dict(backend='s3',s3=dict(bucket=bucket,endpoint=a.s3_endpoint,region='us-east-1',force_path_style=True,rustfs_ec_block_size_hint='auto'))
        write_new(a.out/'data-config.json',dict(data=data))
        initialized = json.loads(command('init-volume',prefix+['init-volume','--data-config',str(a.out/'data-config.json')]))
        result['workspace_id'] = initialized['workspace_id']
        command('init-native',prefix+['init-native'])
        base = dict(volume_format='workspace-native-v2',workspace=result['workspace_id'],workspace_namespace=catalog,
            volume=dict(format='workspace-native-v2',schema_version=2,native_control_version=2,chunk_size=67108864,block_size=4194304),
            native_base=json.loads(a.native_base.read_text()),data=data,
            meta=dict(backend='redis',redis=dict(url=a.redis_url)),layout=dict(chunk_size=67108864,block_size=4194304),
            fuse=dict(workers=4,max_background=512,cache_ttl_ms=0,keep_cache=False),
            cache=dict(read_memory_bytes=0,read_ssd_bytes=0,write_memory_bytes=536870912,
                write_ssd_bytes=1073741824,memory_budget_bytes=536870912,writeback_mode='upload_before_commit',compression='none'))
        phase = 'live-writer-exclusion'
        source = mount('source',18921)
        write_file(source/'owner.txt',b'original-owner\n')
        original = inspect('inspect-source')
        if not original['lease_id'] or original['pending_writeback_bytes']!=0:
            raise ValueError('Missing actual source writer or outstanding debt')
        directory=a.out/'stale-write';directory.mkdir()
        child_code = """import json,os,sys
fd=os.open(sys.argv[1],os.O_RDWR)
print(json.dumps({'ready':True,'fd_opened_before_pause':True}),flush=True)
request=sys.stdin.readline().strip()
result={'write_bytes':0,'fsync_succeeded':False,'errno':None,'cancelled':request!='attempt'}
if request=='attempt':
 try:
  result['write_bytes']=os.pwrite(fd,b'STALE-WRITER!\\n',0)
  os.fsync(fd);result['fsync_succeeded']=True
 except OSError as error:result['errno']=error.errno
try:os.close(fd)
except OSError as error:result['close_errno']=error.errno
print(json.dumps(result),flush=True)
"""
        writer_stderr=(directory/'stderr').open('xb');writer_streams.append(writer_stderr)
        writer=subprocess.Popen([sys.executable,'-c',child_code,str(source/'owner.txt')],
            stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=writer_stderr)
        write_new(directory/'process.json',identity(writer.pid))
        ready=json.loads(writer.stdout.readline())
        if ready!={'ready':True,'fd_opened_before_pause':True}:raise ValueError('Original writer handle was not opened')
        write_new(directory/'ready.json',ready)
        mount('challenger',18922,expect_ready=False)
        unchanged = inspect('inspect-after-challenger')
        if (unchanged['lease_id'],unchanged['holder_generation']) != (original['lease_id'],original['holder_generation']):
            raise ValueError('Failed challenger changed original authority')
        result['live_second_writer_rejected'] = True
        phase = 'expire-source'
        if not alive(processes['source']['identity']):raise ValueError('Original source identity changed')
        os.kill(processes['source']['process'].pid,signal.SIGSTOP);source_stopped=True
        paused = inspect('inspect-paused-source')
        if (paused['lease_id'],paused['holder_generation']) != (original['lease_id'],original['holder_generation']):
            raise ValueError('Source authority changed before the controlled pause')
        # The expiry was read from the real backend. Redis TIME decides when
        # to attempt the one takeover; the local wall clock does not extend it.
        expiry = paused['lease_expires_at_ns'];deadline=time.monotonic()+60
        while True:
            now_ns = backend_time(a.redis_url)
            if now_ns>expiry+2*10**9:break
            if time.monotonic()>deadline:raise TimeoutError('Backend lease expiry was not reached')
            time.sleep(.25)
        write_new(a.out/'expiry.json',dict(original_expires_at_ns=expiry,redis_now_ns=now_ns,source_paused=True))
        phase = 'takeover'
        target = mount('target',18922)
        current = inspect('inspect-target')
        if (not current['lease_id'] or current['lease_id']==original['lease_id'] or
                current['holder_generation']==original['holder_generation']):
            raise ValueError('Takeover did not acquire a distinct actual writer')
        expected = b'current-owner\n'
        write_file(target/'owner.txt',expected)
        phase = 'old-writer-write'
        os.kill(processes['source']['process'].pid,signal.SIGCONT);source_stopped=False
        raw,_=writer.communicate(input=b'attempt\n',timeout=60)
        (directory/'stdout').write_bytes(raw)
        stale=json.loads(raw);write_new(directory/'result.json',stale)
        if writer.returncode or stale['fsync_succeeded'] or stale['errno'] is None:
            raise ValueError('Stale writer did not reject a confirmed write')
        phase = 'close-stale-writer'
        stop('source',False)
        still_current=inspect('inspect-target-after-stale-exit')
        if (still_current['lease_id'],still_current['holder_generation']) != (current['lease_id'],current['holder_generation']):
            raise ValueError('Stale shutdown changed the new writer lease')
        phase = 'cold-readback'
        stop('target',True)
        released=inspect('inspect-target-release')
        if released['lease_id'] is not None or released['pending_writeback_bytes']!=0:
            raise ValueError('Target did not release its authority and debt')
        cold=mount('cold',18923)
        if (cold/'owner.txt').read_bytes()!=expected or sorted(p.name for p in cold.iterdir())!=['owner.txt']:
            raise ValueError('Stale bytes published or namespace changed')
        stop('cold',True)
        result.update(passed=True,expired_writer_superseded=True,stale_confirmed_write_rejected=True,
            stale_write=stale,new_lease_survived_old_shutdown=True,cold_final_bytes_equal=True,
            expected_sha256=hashlib.sha256(expected).hexdigest())
    except BaseException as error:
        result.update(failed_phase=phase,error_type=type(error).__name__,error=str(error))
        raise
    finally:
        if source_stopped and alive(processes['source']['identity']):
            os.kill(processes['source']['process'].pid,signal.SIGCONT)
        if writer is not None and writer.poll() is None and writer.stdin and not writer.stdin.closed:
            writer.stdin.close()
        for stream in writer_streams:stream.close()
        result.update(at=datetime.now(timezone.utc).isoformat(),processes={name:dict(identity=entry['identity'],
            alive=alive(entry['identity']),exit_code=entry['process'].poll(),mount_present=os.path.ismount(entry['point']))
            for name,entry in processes.items()})
        if writer is not None:result['writer_helper_exit_code']=writer.poll()
        write_new(a.out/'result.json',result);print(json.dumps(result))


if __name__=='__main__':
    main()
