#!/usr/bin/env python3
"""Read the existing 100k Packed base through a fresh workspace-v1 mount in pool A."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import socket
import stat
import struct
import subprocess
import sys
import threading
import time
import uuid


CLIENT = Path('/srv/ax-client')
ASSET = CLIENT/'a/parallel-c3-packed-base-01/asset.json'
BREWFS = CLIENT/'build-ca07159-01/bin/brewfs'
ARTIFACTS = {
    ASSET: 'c344b42810042f2b64eb243a436e6a129dd0e59d07e43242a4e04d33bdaf6f45',
    BREWFS: '814499855a949cad7a8752c04f272fa1b02524b36b7ea77b0e4b3bcc6031c5c0',
    CLIENT/'assets/bin/smallfiles': '8c45bca1348e936e0b8e5d01e7e8ee11f1db72120dc8aa7800aa72e1e0bcde96',
}
EXPECTED = dict(files=100000,bytes_checked=409600000)
PARALLEL = CLIENT/'assets/bin/smallfiles-verify-c3-01'
PARALLEL_SHA = '843e4137ed6692f7b7ca9ee7961964733cfd5ebca7f75aff6d70486c646ecf95'
REDIS = 'redis://10.20.2.162:6379/'
OBJECTS = 'http://10.20.2.163:9000'


def write(path, value):
    with path.open('x') as stream:
        json.dump(value,stream,indent=2)
        stream.write('\n')


def capture(argv, *, timeout=10):
    return subprocess.check_output(argv,text=True,timeout=timeout).strip()


def active(path):
    for row in Path('/proc/self/mountinfo').read_text().splitlines():
        left,right = row.split(' - ',1)
        if left.split()[4] == str(path):
            return dict(id=int(left.split()[0]),type=right.split()[0])
    return None


def require(ok, reason):
    if not ok:
        raise ValueError(reason)


def select_database(requested, count=None):
    """Called while the coordinator owns the pool locks; never deletes keys."""
    require(requested == 0 or 20 <= requested < 256 and requested != 70,
            'Dedicated database required')
    count = count or (lambda db: int(capture(['redis-cli','-u',REDIS+str(db),'DBSIZE'])))
    candidates = [requested] if requested else [*range(240,256),*range(20,70),*range(71,240)]
    observations = []
    for db in candidates:
        keys = count(db)
        require(type(keys) is int and keys >= 0, 'Invalid Redis DBSIZE response')
        observations.append(dict(database=db,keys=keys))
        if keys == 0:
            return db,dict(requested=requested,selected=db,observations=observations,no_deletions=True)
    raise ValueError('No empty dedicated Redis DB; existing data was preserved')


def cohort_indices(offset):
    return [32*i + offset + 1024*(3*i) for i in range(32)]


def base_payload(index):
    """Same immutable version-zero SplitMix64 payload as smallfiles-verify."""
    mask = (1 << 64) - 1
    state = index
    result = bytearray()
    for _ in range(512):
        state = (state + 0x9e3779b97f4a7c15) & mask
        z = ((state ^ (state >> 30)) * 0xbf58476d1ce4e5b9) & mask
        z = ((z ^ (z >> 27)) * 0x94d049bb133111eb) & mask
        result.extend(struct.pack('<Q', z ^ (z >> 31)))
    return bytes(result)


def probe_cohort(mount, out):
    # Do not enumerate directories or pretend a 32-file sample is the whole
    # 100k tree. Cross the concurrency/order labels on two interleaved sets.
    phases = (('serial-first-a', 1, 0), ('parallel-first-b', 32, 16),
              ('serial-repeat-b', 1, 16), ('parallel-repeat-a', 32, 0))
    rows = []
    lock = threading.Lock()
    with (out/'cohort-events.ndjson').open('x', buffering=1) as log:
        def event(**fields):
            with lock:
                log.write(json.dumps(dict(monotonic_ns=time.monotonic_ns(), **fields))+'\n')

        def check(phase, index):
            relative = f'small/d{index % 1024:04d}/f{index:07d}'
            path = mount/relative

            def timed(name, operation):
                event(event='start', phase=phase, path=relative, operation=name)
                begin = time.monotonic_ns()
                try:
                    value = operation()
                except OSError as exc:
                    event(event='error', phase=phase, path=relative, operation=name,
                          errno=exc.errno, elapsed_ns=time.monotonic_ns()-begin)
                    raise
                event(event='end', phase=phase, path=relative, operation=name,
                      elapsed_ns=time.monotonic_ns()-begin)
                return value

            m = timed('lstat', path.lstat)
            require(stat.S_ISREG(m.st_mode) and stat.S_IMODE(m.st_mode) == 0o644 and
                    (m.st_size, m.st_nlink, m.st_uid, m.st_gid) == (4096, 1, 0, 0),
                    'File metadata differs: '+relative)
            fd = timed('open', lambda: os.open(path, os.O_RDONLY | os.O_NOFOLLOW))
            try:
                body = timed('pread', lambda: os.pread(fd, 4097, 0))
            finally:
                timed('close', lambda: os.close(fd))
            require(body == base_payload(index), 'File bytes differ: '+relative)
            event(event='verified', phase=phase, path=relative, bytes=len(body),
                  sha256=hashlib.sha256(body).hexdigest())
            return len(body)

        for phase, workers, offset in phases:
            event(event='phase-start', phase=phase, workers=workers)
            begin = time.monotonic_ns()
            indices = cohort_indices(offset)
            if workers == 1:
                total = sum(check(phase, i) for i in indices)
            else:
                barrier = threading.Barrier(workers, timeout=8)
                def simultaneous(i):
                    barrier.wait()
                    return check(phase, i)
                with ThreadPoolExecutor(max_workers=workers) as pool:
                    total = sum(pool.map(simultaneous, indices))
            row = dict(phase=phase, workers=workers, files=len(indices), bytes=total,
                       elapsed_ns=time.monotonic_ns()-begin)
            event(event='phase-end', **row)
            rows.append(row)
    write(out/'cohort-result.json', dict(passed=True, formal_sample=False,
          scope='64 unique pinned files, 4 passes; same mount and shared metadata caches; no full tree qualification',
          phases=rows))


def wait_verifier(command, log, abort_path, seconds, detect_worker_failure):
    with log.open('xb') as output:
        if not detect_worker_failure:
            subprocess.run(command,stdout=output,stderr=subprocess.STDOUT,check=True,timeout=seconds)
            return
        proc = subprocess.Popen(command,stdout=output,stderr=subprocess.STDOUT)
        deadline = time.monotonic() + seconds
        try:
            while proc.poll() is None:
                if log.stat().st_size:
                    errors = [x for x in log.read_text(errors='replace').splitlines()
                              if re.match(r'^C3 verifier worker [0-9]+ failed: ',x)]
                    if errors:
                        write(abort_path,dict(reason='A verifier worker already failed',first_error=errors[0],
                                              worker_errors_observed=len(errors),pid=proc.pid))
                        raise ValueError(errors[0])
                if time.monotonic() >= deadline:
                    raise subprocess.TimeoutExpired(command,seconds)
                time.sleep(1)
            if proc.returncode:
                raise subprocess.CalledProcessError(proc.returncode,command)
        finally:
            if proc.poll() is None:
                proc.terminate()
                try: proc.wait(timeout=4)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=4)


def run(tag, db, workers, verify_seconds, parallel_sha256=None, trace_packed_budget=False,
        packed_output_mib=0, packed_control_mib=0, diagnostic_cohort=False):
    require(re.fullmatch(r'c3-current-base-[0-9]{8}t[0-9]{6}z',tag) and (db == 0 or 20 <= db < 256 and db != 70),
            'Exact new tag and dedicated Redis DB required')
    require(socket.gethostname()=='ax-pilot-20261111-client' and active(CLIENT) is not None,
            'Pool A client data disk must be mounted')
    require(os.statvfs(CLIENT).f_bavail * os.statvfs(CLIENT).f_frsize >= 2*1024**3,
            'Client disk below this probe reserve')
    require(0 <= workers <= 32 and 60 <= verify_seconds <= 900,'Bounded verifier workers and duration required')
    require(not parallel_sha256 or (workers and re.fullmatch(r'[a-f0-9]{64}',parallel_sha256)),
            'Custom verifier requires a valid SHA and positive worker count')
    require(packed_output_mib in (0,128) and
            (not packed_output_mib or (trace_packed_budget and workers == 32)),
            'The 128 MiB probe requires budget tracing and 32 workers')
    require(not diagnostic_cohort or (not parallel_sha256 and workers == 32 and verify_seconds in (120,240)
            and trace_packed_budget and packed_output_mib == 128 and packed_control_mib == 8),
            'Cohort requires a 120- or 240-second diagnostic window, both raised budgets, and no separate verifier')
    require(packed_control_mib in (0,8) and
            (not packed_control_mib or ((parallel_sha256 or diagnostic_cohort) and packed_output_mib == 128)),
            'The 8 MiB control probe requires the diagnostic verifier and 128 MiB output')
    parallel = Path(__file__).resolve().with_name('smallfiles-verify') if parallel_sha256 else PARALLEL
    pins = {**ARTIFACTS,**({parallel:parallel_sha256 or PARALLEL_SHA} if workers and not diagnostic_cohort else {})}
    for path,expected in pins.items():
        require(path.resolve(strict=True)==path,'Unexpected artifact path: '+str(path))
        with path.open('rb') as stream:
            require(hashlib.file_digest(stream,'sha256').hexdigest()==expected,'Artifact digest mismatch: '+str(path))
    asset = json.loads(ASSET.read_text())
    data = {'backend':'s3','s3':dict(bucket='axp-f50629e4f0f74d62',endpoint=OBJECTS,
                  region='us-east-1',force_path_style=True)}
    require(asset['config']['data']['backend']=='s3' and
            all(asset['config']['data']['s3'].get(k)==v for k,v in data['s3'].items()) and
            re.fullmatch(r'base/objects/[0-9]+/[a-f0-9]{64}',asset['manifest_key']),
            'Unexpected immutable Packed source')
    db, database_selection = select_database(db)
    rurl = REDIS+str(db)
    require(capture(['redis-cli','-u',rurl,'DBSIZE'])=='0','Refusing to use a populated Redis DB')
    slot = Path('/sys/fs/cgroup/ax-slot-1')
    require((slot/'cgroup.procs').read_text()=='' and (slot/'cpu.max').read_text().strip()=='200000 100000'
            and (slot/'memory.max').read_text().strip()=='8589934592', 'Expected a free 2-CPU/8-GiB slot')
    out = CLIENT/'preparation'/tag
    out.mkdir(mode=0o700)
    mount = out/'mnt'; mount.mkdir()
    namespace = str(uuid.uuid4())
    write(out/'intent.json',dict(schema='c3-workspace-base-qualification-v1',tag=tag,namespace=namespace,
          redis_db=db,bucket=data['s3']['bucket'],manifest_key=asset['manifest_key'],
          database_selection=database_selection,
          pins={str(p):h for p,h in pins.items()},formal_sample=False,automatic_retry=False,
          verifier_workers=workers or 1,verify_timeout_seconds=verify_seconds,trace_packed_budget=trace_packed_budget,
          packed_output_budget_bytes=(packed_output_mib or 32)*1024**2,
          packed_control_budget_bytes=(packed_control_mib or 1)*1024**2,
          **({'diagnostic_cohort': True} if diagnostic_cohort else {}),
          boot_id=Path('/proc/sys/kernel/random/boot_id').read_text().strip()))
    (slot/'cgroup.procs').write_text(str(os.getpid()))
    auth = dict(line.split('=',1) for line in (CLIENT/'private/rustfs.env').read_text().splitlines() if '=' in line)
    env = dict(os.environ,AWS_ACCESS_KEY_ID=auth['RUSTFS_ACCESS_KEY'],AWS_SECRET_ACCESS_KEY=auth['RUSTFS_SECRET_KEY'],
            AWS_EC2_METADATA_DISABLED='true',NO_COLOR='1',
            RUST_LOG='error,brewfs::packed_v3_budget=debug' if trace_packed_budget else 'error',
            NO_PROXY='10.20.2.162,10.20.2.163',
        no_proxy='10.20.2.162,10.20.2.163')
    for name,budget in (('OUTPUT',packed_output_mib),('CONTROL',packed_control_mib)):
        key = 'BREWFS_PACKED_V3_'+name+'_BUDGET_BYTES'
        env.pop(key,None)
        if budget:
            env[key] = str(budget*1024**2)
    data_cfg = out/'data.json'; write(data_cfg,dict(data=data))
    brewfs = BREWFS
    with (out/'init.stdout').open('xb') as stdout,(out/'init.stderr').open('xb') as stderr:
        subprocess.run([brewfs,'workspace','--meta-backend','redis','--meta-url',rurl,'--workspace-namespace',namespace,
            'init-packed-volume','--manifest-key',asset['manifest_key'],'--data-config',data_cfg],
            env=env,stdout=stdout,stderr=stderr,check=True,timeout=30)
    parent = json.loads((out/'init.stdout').read_text())
    workspace = str(uuid.UUID(parent['workspace_id']))
    cfg = out/'mount.json'
    write(cfg,dict(volume_format='workspace-v1',volume={'format':'workspace-v1'},workspace=workspace,
        workspace_namespace=namespace,data=data,meta={'backend':'redis','redis':{'url':rurl}},
        layout=dict(chunk_size=67108864,block_size=4194304),fuse=dict(workers=4,max_background=128),
        cache=dict(cache_root=str(out/'cache'),writeback_mode='upload_before_commit',
                   read_memory_bytes=536870912,read_ssd_bytes=0,compression='none')))
    with (out/'mount.log').open('xb') as log:
        proc = subprocess.Popen([brewfs,'mount','--config',cfg,mount],stdout=log,stderr=subprocess.STDOUT,env=env)
        try:
            deadline = time.monotonic()+45
            while not active(mount) and proc.poll() is None and time.monotonic()<deadline:
                time.sleep(.2)
            identity = active(mount)
            require(identity and identity['type'].startswith('fuse') and proc.poll() is None,'Current FUSE mount did not start')
            write(out/'ready.json',dict(mount=identity,pid=proc.pid,workspace_id=workspace))
            if diagnostic_cohort:
                cmd = ['python3', '-B', Path(__file__).resolve(), '--cohort-worker', str(out)]
                wait_verifier(cmd,out/'verify.log',out/'verify-aborted.json',verify_seconds,False)
                require(active(mount)==identity and proc.poll() is None,'Mount changed during cohort readback')
                return
            if workers:
                (out/'verify').mkdir()
                verify_cmd = [parallel,mount,'base',out/'verify/result.json',str(EXPECTED['files']),str(workers),'1']
            else:
                verify_cmd = [CLIENT/'assets/bin/smallfiles','verify-base','--root',mount,
                    '--output',out/'verify','--files',str(EXPECTED['files'])]
            wait_verifier(verify_cmd,out/'verify.log',out/'verify-aborted.json',verify_seconds,bool(packed_control_mib))
            summary = json.loads((out/'verify/result.json').read_text())
            require(summary.get('passed') is True and all(summary.get(k)==v for k,v in EXPECTED.items()),
                    'Full base result mismatch')
            require(active(mount)==identity and proc.poll() is None,'Mount changed during readback')
        finally:
            code = None
            if active(mount):
                code = subprocess.run(['fusermount3','-u',mount],timeout=15).returncode
            if not active(mount) and proc.poll() is None:
                try: proc.wait(timeout=12)
                except subprocess.TimeoutExpired:
                    proc.send_signal(signal.SIGINT)
            exit_code = proc.wait(timeout=12)
            write(out/'mount-exit.json',dict(unmount_exit=code,exit_code=exit_code,mount_absent=active(mount) is None))
            require(code==0 and exit_code==0 and active(mount) is None,'Mount did not close normally')
    result = dict(passed=True,**EXPECTED,workspace_id=workspace,namespace=namespace,formal_sample=False,
        scope='Stock workspace-v1 reads the complete immutable base; no child, mutation or performance claim')
    write(out/'result.json',result)
    print(json.dumps(result))


if __name__=='__main__':
    # Invoked only by the owned mount wrapper. No mount, backend, or setup
    # operation in this child; a timeout terminates all its probe threads.
    if len(sys.argv) == 3 and sys.argv[1] == '--cohort-worker':
        out = Path(sys.argv[2])
        require(out.parent == CLIENT/'preparation' and out.resolve(strict=True) == out
                and re.fullmatch(r'c3-current-base-[0-9]{8}t[0-9]{6}z',out.name)
                and active(out/'mnt') is not None, 'Owned active C3 preparation mount required')
        probe_cohort(out/'mnt', out)
        raise SystemExit(0)
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--tag',required=True)
    parser.add_argument('--database',type=int,required=True)
    parser.add_argument('--workers',type=int,default=0)
    parser.add_argument('--verify-seconds',type=int,default=480)
    parser.add_argument('--parallel-sha256')
    parser.add_argument('--trace-packed-budget',action='store_true')
    parser.add_argument('--packed-output-mib',type=int,choices=(0,128),default=0)
    parser.add_argument('--packed-control-mib',type=int,choices=(0,8),default=0)
    parser.add_argument('--diagnostic-cohort',action='store_true')
    args=parser.parse_args()
    def terminate(_signum, _frame):
        raise TimeoutError('Qualification interrupted')
    signal.signal(signal.SIGTERM,terminate)
    run(args.tag,args.database,args.workers,args.verify_seconds,args.parallel_sha256,
        args.trace_packed_budget,args.packed_output_mib,args.packed_control_mib,args.diagnostic_cohort)
