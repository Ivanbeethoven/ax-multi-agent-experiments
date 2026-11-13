"""One C1 fixed-work trial against the four-host backend, using AX's fio verifier."""
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess as sp
import sys
import time
from urllib.request import ProxyHandler, build_opener

BASE = Path('/srv/ax-idcai-20261113')
REPO = BASE/'src/ax'
sys.path.insert(0, str(REPO))
from bench.fio_workload import run as fio_run
from bench.runtime.observe import prometheus
from bench.suite_mount import settings
from bench.suite_catalog import CLIENT_PROFILE
from boto3 import client
from botocore.config import Config

MIB = 1024**2
ENDPOINT = 'http://10.77.13.21:19000'


def main(out, product, mode, database, phase='qualification'):
    out = Path(out)
    if product not in ('brewfs','juicefs') or mode not in ('read','write','randread','randwrite','randrw'):
        raise ValueError('unsupported C1 product or mode')
    if product == 'juicefs' and mode == 'randrw':
        raise ValueError('JuiceFS mixed I/O is excluded by the current user-approved plan')
    if phase not in ('qualification','calibration-off','calibration-on','candidate'):
        raise ValueError('unknown trial phase')
    database = int(database)
    if not 0<=database<256:
        raise ValueError('exclusive Redis DB index required')
    out.mkdir(mode=0o700)
    creds = json.load(sys.stdin)
    result = {'purpose': phase, 'formal_sample':False, 'product':product, 'mode':mode,
              'events': [], 'passed':False, 'seed':9173, 'shared_s3_endpoint':ENDPOINT,
              'shared_redis_endpoint':'10.77.13.24:16379', 'redis_database':database}
    name = 'ax-fio-' + hashlib.sha256(str(out).encode()).hexdigest()[:14]
    env = dict(os.environ, AWS_ACCESS_KEY_ID=creds['key'], AWS_SECRET_ACCESS_KEY=creds['secret'],
               AWS_REGION='us-east-1', AWS_EC2_METADATA_DISABLED='true', SECRET_KEY=creds['secret'],
               NO_PROXY='127.0.0.1,localhost,10.77.13.21,10.77.13.24')
    url = 'redis://:'+creds['rpc']+f'@10.77.13.24:16379/{database}'
    tools = Path(os.environ.get('AX_IDCAI_TOOLS', str(BASE/'bin')))
    bins = {k:str(tools/k) for k in ('brewfs','juicefs')}
    current = (tools.parent/'build.json').is_file()
    if current:
        provenance = json.loads((tools.parent/'build.json').read_text())
        with (tools/'brewfs').open('rb') as f:
            if hashlib.file_digest(f,'sha256').hexdigest() != provenance['artifacts']['brewfs']['sha256']:
                raise ValueError('BrewFS differs from build receipt')
        result['brewfs_build'] = provenance
    metrics = 'http://127.0.0.1:18910/metrics'
    opener = build_opener(ProxyHandler({}))
    mnt = out/'mnt'
    mnt.mkdir()
    src = client('s3',endpoint_url=ENDPOINT,aws_access_key_id=creds['key'],aws_secret_access_key=creds['secret'],
                 region_name='us-east-1',config=Config(s3={'addressing_style':'path'},proxies={},
                 connect_timeout=4,read_timeout=15,retries={'total_max_attempts':2}))

    def scrub(text):
        for secret in creds.values():
            text = text.replace(secret, '[redacted]')
        return text

    def record(label, start, passed):
        result['events'].append(dict(stage=label, start_ns=start[0], end_ns=time.monotonic_ns(),
                                     utc_start_ns=start[1], utc_end_ns=time.time_ns(), passed=passed))

    def clock():
        return time.monotonic_ns(), time.time_ns()

    def capture(target):
        with opener.open(metrics, timeout=4) as response:
            data = response.read().decode()
        if not data:
            raise ValueError('empty client metrics')
        target.write_text(data)

    def drain(path):
        path.mkdir()
        names = (('brewfs_writeback_live_dirty_bytes','brewfs_writeback_recent_pending_upload_bytes',
                  'brewfs_writeback_stage_inflight_bytes','brewfs_writeback_remote_upload_inflight_bytes',
                  'brewfs_writeback_live_slices','brewfs_writeback_recent_pending_upload_slices',
                  'brewfs_writeback_metadata_inflight') if product=='brewfs' else
                 ('juicefs_staging_block_bytes','juicefs_staging_blocks','juicefs_staging_writing_blocks'))
        previous = 0
        zeros = []
        begin = time.monotonic()
        for index in range(25):
            if product=='brewfs':
                data = (mnt/'.stats').read_text()
                (path/f'{index:02}.prom').write_text(data)
            else:
                file = path/f'{index:02}.prom'
                capture(file)
                data = file.read_text()
            values = {k:v for k,v in prometheus(data).items() if k.split('{',1)[0] in names}
            if {k.split('{',1)[0] for k in values} != set(names):
                raise ValueError(f'Native debt gauges missing: {sorted(set(names)-{k.split(chr(123),1)[0] for k in values})}')
            now = time.monotonic_ns()
            if all(v==0 for v in values.values()):
                zeros.append(dict(index=index, at_ns=now, native_gauges=values))
                if previous and now-previous>=1_000_000_000:
                    (path/'result.json').write_text(json.dumps(dict(passed=True, zero_samples=zeros[-2:],seconds=time.monotonic()-begin)))
                    return
                previous = now
            else:
                previous = 0
                zeros.clear()
            time.sleep(1.05)
        raise TimeoutError('native upload debt did not drain')

    @contextmanager
    def mounted(label):
        cfg = out/f'{label}-mount-private.json'
        stdout = out/f'{label}-mount-private.log'
        start = clock()
        with stdout.open('wb') as log:
            if product=='brewfs':
                data = settings(dict(client_profile=CLIENT_PROFILE),dict(product='brewfs',engine='flat-v1',
                                payload_cache_bytes=512*MIB,hint='auto'))
                data['layout'] = dict(chunk_size=4*MIB,block_size=4*MIB)
                if current:
                    data['volume_format'] = 'flat'
                    data['volume'] = {'format': 'flat'}
                data['meta'] = dict(backend='redis',redis={'url':url})
                data['data'] = dict(backend='s3',s3=dict(bucket=name,endpoint=ENDPOINT,region='us-east-1',
                                    force_path_style=True,rustfs_ec_block_size_hint='auto'))
                data['cache']['cache_root']=str(out/f'{label}-cache')
                cfg.write_text(json.dumps(data))
                argv = [bins[product],'mount','--config',str(cfg),'--metrics','--metrics-addr','127.0.0.1:18910',str(mnt)]
            else:
                argv = [bins[product],'mount','--enable-xattr','--enable-cap','--cache-dir=memory','--cache-size=512',
                        '--buffer-size=512','--attr-cache=0','--entry-cache=0','--dir-entry-cache=0','--open-cache=0',
                        '--prefetch=1','--max-readahead=128','--cache-large-write=true','--verify-cache-checksum=full',
                        '--backup-meta=0','--no-usage-report','--metrics=127.0.0.1:18910',url,str(mnt)]
            (out/f'{label}-mount-command.json').write_text(json.dumps([scrub(a) for a in argv]))
            child = sp.Popen(argv, env=env, stdout=log, stderr=log)
            try:
                for _ in range(160):
                    if child.poll() is not None:
                        raise RuntimeError('client exited before readiness')
                    if os.path.ismount(mnt):
                        try:
                            capture(out/f'{label}-ready.prom')
                        except OSError:
                            pass
                        else:
                            break
                    time.sleep(.15)
                else:
                    raise TimeoutError('mount readiness')
                record(label+'-mount',start,True)
                (out/f'{label}-pid.txt').write_text(str(child.pid))
                yield
            finally:
                close = clock()
                if os.path.ismount(mnt):
                    sp.run(['fusermount3','-u',str(mnt)],timeout=30,check=True)
                elif child.poll() is None:
                    child.terminate()
                exit_code = child.wait(timeout=45)
                clean = exit_code==0 and not os.path.ismount(mnt)
                record(label+'-unmount',close,clean)
                (out/f'{label}-exit.json').write_text(json.dumps(dict(pid=child.pid,exit_code=exit_code,mount_absent=not os.path.ismount(mnt))))
                (out/f'{label}-mount.log').write_text(scrub(stdout.read_text(errors='replace')))
                stdout.unlink()
                if cfg.exists():
                    (out/f'{label}-mount-config.json').write_text(scrub(cfg.read_text()))
                    cfg.unlink()
                if not clean:
                    raise RuntimeError('mount cleanup incomplete')

    def fio(label):
        params=dict(rw=mode,file_bytes_per_job=64*MIB,io_bytes_per_job=64*MIB,
                    block_bytes=4096 if mode.startswith('rand') else MIB,numjobs=1,
                    ioengine='psync',iodepth=1,time_based=False,direct=False,fsync_every_write=True,
                    rwmixread=75,cache_state='client-cold')
        binary=Path('/usr/bin/fio')
        with binary.open('rb') as f:
            digest=hashlib.file_digest(f,'sha256').hexdigest()
        start=clock()
        result['fio_params'] = params
        r=fio_run(dict(params=params,root=str(mnt),binary=dict(path=str(binary),sha256=digest),seed=9173),label,out/label)
        record(label,start,r['passed'])
        if not r['passed']:
            raise RuntimeError('fio failed')

    try:
        src.create_bucket(Bucket=name)
        result['bucket']=name
        if product=='juicefs':
            p=sp.run([bins[product],'format','--storage','s3','--bucket',ENDPOINT+'/'+name,
                      '--access-key',creds['key'],'--block-size=4096','--compress=none',url,name],
                     env=env,capture_output=True,text=True,timeout=30)
            (out/'format.log').write_text(scrub(p.stdout+p.stderr))
            if p.returncode:
                raise RuntimeError('JuiceFS format failed')
        with mounted('prepare'):
            fio('prepare')
            drain(out/'prepare-drain')
        with mounted('measure'):
            capture(out/'measure-before.prom')
            fio('measure')
            capture(out/'measure-after.prom')
            drain(out/'measure-drain')
        with mounted('verify'):
            fio('verify')
            drain(out/'verify-drain')
        result['passed']=True
    except Exception as error:
        result.update(error=type(error).__name__,detail=scrub(str(error)))
    finally:
        (out/'trial.json').write_text(json.dumps(result,indent=2)+'\n')
    return 0 if result['passed'] else 2


if __name__=='__main__':
    sys.exit(main(*sys.argv[1:]))
