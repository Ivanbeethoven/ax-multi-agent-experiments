#!/usr/bin/env python3
"""One publication diagnostic, with no Redis, replay, FUSE or performance claim."""
import argparse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import runpy
import signal
import socket
import stat
import subprocess
import sys
import threading
import time
import uuid

CONTROL = Path('/srv/ax-pilot-control')
CANON = CONTROL/'parallel-control-01'
CLIENT = Path('/srv/ax-client')
CASE = CLIENT/'trace-exact-02/cases/pytest-full-02'
PRODUCER = CLIENT/'build-ca07159-01/bin/packed_v3_snapshot_fixture'
PINS = {
    PRODUCER: 'aa45b1ee9640da92051ead72b17ed41836b57812e05623f860b963e09373b3ab',
    CASE/'initial-tree.json': 'c1b9b066e6660301ad944fff78de1910e6388021c6caa90923355848eb1c2847',
}
REFERENCE_SHA = '6b718199077ee17241131ed03d2da3c209e689f26a3ea0ce219da0a4568a77a8'
BUILD_MANIFEST_SHA = 'd4d00a6aab0887fe86c75646ef260594587e95fb2728daaa93d3ee5ccabf1b6e'
OBJECTS = 'http://10.20.2.163:9000'
SLOT = Path('/sys/fs/cgroup/ax-slot-1')
ROLE_NAMES = {'client': 'ax-pilot-20261111-client', 'objects': 'ax-pilot-20261111-objects',
              'metadata': 'ax-pilot-20261111-metadata'}
ROLES = ('client', 'objects')
FILES = ('initial.json', 'intent.json', 'process.ndjson', 'object-count.ndjson',
         'producer-exit.json', 'object-count-final.json', 'result.json')
MIB = 2**20
PRODUCER_SECONDS = 300


def need(ok, message):
    if not ok:
        raise ValueError(message)


def digest(path):
    with path.open('rb') as file:
        return hashlib.file_digest(file, 'sha256').hexdigest()


def save(path, data):
    # Publish only complete receipts without replacing an existing attempt.
    tmp = path.with_name('.'+path.name+'.'+str(os.getpid())+'.tmp')
    with tmp.open('x') as f:
        json.dump(data, f, indent=2)
        f.write('\n')
    try:
        os.link(tmp,path)
    finally:
        tmp.unlink()


def read(path):
    return json.loads(path.read_bytes())


def record(file, value):
    file.write(json.dumps(value, separators=(',', ':'))+'\n')
    file.flush()


def stat_fields(path):
    return path.read_text().rsplit(')', 1)[1].split()


def process_snapshot(pid, start_ticks, proc=Path('/proc')):
    p = proc/str(pid)
    s = stat_fields(p/'stat')
    need(int(s[19]) == start_ticks and int(s[2]) == pid and int(s[3]) == pid,
         'Original producer identity differs')
    io = {}
    for line in (p/'io').read_text().splitlines():
        key, value = line.split(': ', 1)
        if key in {'rchar', 'wchar', 'syscr', 'syscw', 'read_bytes', 'write_bytes'}:
            io[key] = int(value)
    waits = Counter()
    for thread in sorted((p/'task').iterdir(), key=lambda x: x.name)[:128]:
        try:
            value = (thread/'wchan').read_text().strip()
            waits[value if re.fullmatch(r'[A-Za-z0-9_.]+', value) else 'unknown'] += 1
        except FileNotFoundError:
            pass
    sockets = set()
    for fd in sorted((p/'fd').iterdir(), key=lambda x: x.name)[:1024]:
        try:
            target = os.readlink(fd)
        except FileNotFoundError:
            continue
        match = re.fullmatch(r'socket:\[([0-9]+)\]', target)
        if match:
            sockets.add(match[1])
    # Linux shows IPv4 octets in reverse order; IPv4-mapped TCP6 suffix is identical.
    rows = []
    other = Counter()
    for family in ('tcp', 'tcp6'):
        try:
            lines = (p/'net'/family).read_text().splitlines()[1:]
        except FileNotFoundError:
            continue
        for line in lines:
            cols = line.split()
            if len(cols) < 10 or cols[9] not in sockets:
                continue
            ip, port = cols[2].split(':', 1)
            match = port == '2328' and ip in {'A302140A', '0000000000000000FFFF0000A302140A'}
            if not match:
                other[cols[3]] += 1
                continue
            tx, rx = (int(value, 16) for value in cols[4].split(':'))
            rows.append(dict(state=cols[3], tx_queue_bytes=tx, rx_queue_bytes=rx))
    end = stat_fields(p/'stat')
    need(int(end[19]) == start_ticks, 'Producer changed during sampling')
    return dict(state=s[0], cpu_user_ticks=int(s[11]), cpu_system_ticks=int(s[12]),
                threads=int(s[17]), io=io, wchan_counts=dict(waits), rustfs_tcp=rows[:64],
                other_tcp_states=dict(other), socket_fds_seen=len(sockets))


def producer_argv(bucket, out):
    return list(map(str, [PRODUCER, '--source-directory', CASE/'initial',
        '--source-consistency', 'best-effort-detected', '--source-hardlink-policy', 'reject-external',
        '--output-dir', out/'producer', '--manifest-output', out/'manifest-key.txt',
        '--bucket', bucket, '--endpoint', OBJECTS, '--region', 'us-east-1',
        '--force-path-style', 'true', '--prefix', 'base', '--metadata-codec', 'raw',
        '--data-codec', 'raw', '--inline-data', 'off', '--chunk-size', 4*MIB,
        '--block-size', 4*MIB, '--frame-policy', 'size-only', '--access-profile',
        'random-small-file', '--max-random-frame-raw-bytes', 262144,
        '--s3-rustfs-ec-block-size-hint', 'auto']))


def object_snapshot(s3, bucket):
    t = time.monotonic_ns()
    try:
        rows = s3.list_objects_v2(Bucket=bucket, MaxKeys=1000)
        entries = rows.get('Contents', [])
        return dict(at_monotonic_ns=t, elapsed_ns=time.monotonic_ns()-t,
            objects=len(entries), bytes=sum(r['Size'] for r in entries),
            truncated=rows.get('IsTruncated', False),
            manifest_candidates=sum(bool(re.fullmatch(r'base/objects/0/[0-9a-f]{64}', r['Key']))
                                    for r in entries))
    except Exception as error:
        return dict(at_monotonic_ns=t, elapsed_ns=time.monotonic_ns()-t,
                    error_class=type(error).__name__)


def object_counts(s3, bucket, out, finished):
    # One page, one request every five seconds, run outside the producer slot.
    with (out/'object-count.ndjson').open('x') as f:
        while True:
            record(f, object_snapshot(s3,bucket))
            if finished.wait(5):
                return


def settle(child):
    # The parent remains our unreaped child until the signal is sent. Never
    # signal a reaped process's numeric process group.
    if child.poll() is not None:
        return child.returncode
    child.terminate()
    try:
        return child.wait(timeout=3)
    except subprocess.TimeoutExpired:
        child.kill()
        return child.wait(timeout=5)


def execute_producer(argv, env, out, epoch_deadline, start_count, finished):
    def join_original_slot():
        (SLOT/'cgroup.procs').write_text(str(os.getpid()))
    need(epoch_deadline-time.time() >= PRODUCER_SECONDS+15, 'Need the original full 300s publisher budget')
    started = time.monotonic()
    child = subprocess.Popen(argv, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL, start_new_session=True, preexec_fn=join_original_slot)
    # The producer is a pinned Rust binary; it uses threads, not worker subprocesses.
    # Independently prove the cgroup empty before stopping either host.
    code = None
    ticks = None
    outcome = 'failed'
    try:
        try:
            ticks = int(stat_fields(Path(f'/proc/{child.pid}/stat'))[19])
        except FileNotFoundError:
            pass
        start_count()
        with (out/'process.ndjson').open('x') as f:
            due = started
            while True:
                if time.monotonic() >= started+PRODUCER_SECONDS:
                    outcome = 'timeout'
                    break
                code = child.poll()
                if code is not None:
                    outcome = 'exited'
                    break
                if ticks is not None:
                    try:
                        row = process_snapshot(child.pid, ticks)
                    except (FileNotFoundError,ProcessLookupError):
                        row = {'exiting_during_sample': True}
                    record(f, dict(elapsed_ms=round((time.monotonic()-started)*1000),
                                   at_monotonic_ns=time.monotonic_ns(), **row))
                due += 1
                time.sleep(max(0, min(due, started+PRODUCER_SECONDS)-time.monotonic()))
    finally:
        finished.set()
        if code is None:
            code = settle(child)
        save(out/'producer-exit.json', dict(outcome=outcome, exit_code=code, pid=child.pid,
             pgid=child.pid, start_ticks=ticks, clock_ticks=os.sysconf('SC_CLK_TCK'),
             wall_seconds=time.monotonic()-started, budget_seconds=PRODUCER_SECONDS))
    return outcome == 'exited' and code == 0


def run_worker(tag, epoch_deadline):
    need(os.geteuid() == 0 and socket.gethostname() == ROLE_NAMES['client'] and CLIENT.is_mount(),
         'Use the restored original A client')
    out = CLIENT/'preparation'/tag
    out.mkdir(mode=0o700)
    summary = dict(tag=tag, diagnostic_only=True, formal_sample=False, passed=False,
        automatic_retry=False, boot_id=Path('/proc/sys/kernel/random/boot_id').read_text().strip(),
        bucket_disposition='retained; no deletion attempted')
    phase = 'input'
    finished = threading.Event()
    counter = None
    try:
        for path, sha in PINS.items():
            need(path.resolve(strict=True) == path and digest(path) == sha, 'Input artifact differs')
        expected = read(CASE/'initial-tree.json')
        files = [x for x in expected.values() if x.get('type') == 'file']
        need(len(expected) == 714 and len(files) == 614 and
             all(x.get('type') in ('file', 'directory') for x in expected.values()) and
             sum(x['bytes'] for x in files) == 35526497, 'Unexpected initial tree size')
        subprocess.run([sys.executable, '-B', CLIENT/'code-12/bench/trace_checks.py', 'tree',
            '--root', CASE/'initial', '--expected', CASE/'initial-tree.json', '--output', out/'initial.json'],
            check=True, timeout=30, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        need(all(p.lstat().st_nlink == 1 for p in (CASE/'initial').rglob('*')
                 if stat.S_ISREG(p.lstat().st_mode)), 'Unexpected source hardlink topology')
        for file, value in {'cpu.max':'200000 100000', 'memory.max':'8589934592',
                            'memory.swap.max':'0', 'cgroup.procs':''}.items():
            need((SLOT/file).read_text().strip() == value, 'Original producer cgroup differs or is busy')
        phase = 'fresh-bucket'
        need(epoch_deadline-time.time() >= PRODUCER_SECONDS+27, 'No full producer window remains')
        secret = CLIENT/'private/rustfs.env'
        need(secret.is_file() and not secret.is_symlink() and secret.stat().st_mode & 0o077 == 0,
             'Original credential file must be owner-only')
        auth = dict(line.split('=', 1) for line in secret.read_text().splitlines() if '=' in line)
        tmp = out/'producer-tmp'
        tmp.mkdir(mode=0o700)
        env = dict(os.environ, AWS_ACCESS_KEY_ID=auth['RUSTFS_ACCESS_KEY'],
            AWS_SECRET_ACCESS_KEY=auth['RUSTFS_SECRET_KEY'], AWS_EC2_METADATA_DISABLED='true',
            AWS_REGION='us-east-1', RUST_LOG='error', NO_COLOR='1', TMPDIR=str(tmp),
            NO_PROXY='10.20.2.162,10.20.2.163', no_proxy='10.20.2.162,10.20.2.163',
            BREWFS_WORKSPACE_ROW_CACHE_TTL_MS='60000')
        for suffix, budget in dict(ROOTS=16,METADATA_OWNED=256,PLANS=32,CONTROL=1,
            STORED=32,RAW=32,DECODE_WORKSPACE=8,OUTPUT=32).items():
            env['BREWFS_PACKED_V3_'+suffix+'_BUDGET_BYTES'] = str(budget*MIB)
        import boto3
        from botocore.config import Config
        s3 = boto3.client('s3', endpoint_url=OBJECTS, region_name='us-east-1',
            aws_access_key_id=env['AWS_ACCESS_KEY_ID'], aws_secret_access_key=env['AWS_SECRET_ACCESS_KEY'],
            config=Config(signature_version='s3v4', connect_timeout=2, read_timeout=3,
                retries={'total_max_attempts':1}, s3={'addressing_style':'path'}, proxies={}))
        bucket = 'axc2diag-'+uuid.uuid4().hex
        argv = producer_argv(bucket, out)
        summary.update(bucket=bucket, source_files=614, source_bytes=35526497, bucket_created=False)
        save(out/'intent.json', dict(**summary, producer_argv=argv,
             producer_sha256=PINS[PRODUCER], initial_tree_sha256=PINS[CASE/'initial-tree.json']))
        from botocore.exceptions import ClientError
        try:
            s3.head_bucket(Bucket=bucket)
        except ClientError as error:
            need(error.response['ResponseMetadata']['HTTPStatusCode']==404,
                 'New bucket name absence could not be proved')
        else:
            raise ValueError('Refusing to reuse an existing bucket')
        s3.create_bucket(Bucket=bucket)
        summary['bucket_created'] = True
        counter = threading.Thread(target=object_counts, args=(s3,bucket,out,finished), daemon=True)
        phase = 'publish'
        need(execute_producer(argv,env,out,epoch_deadline,counter.start,finished), 'Producer did not complete')
        phase = 'manifest-readback'
        key = (out/'manifest-key.txt').read_text().strip()
        need(re.fullmatch(r'base/objects/0/[0-9a-f]{64}',key), 'Unexpected manifest identity')
        obj = s3.get_object(Bucket=bucket, Key=key)['Body']
        try:
            raw = obj.read(16*MIB+1)
        finally:
            obj.close()
        manifest_sha = hashlib.sha256(raw).hexdigest()
        need(8<len(raw)<=16*MIB and raw[:8]==b'BRFPM005' and key.endswith('/'+manifest_sha),
             'Remote manifest wire or content address differs')
        source = read(out/'manifest-key.source.json')
        need(source['manifest_key']==key and source['manifest_digest']==manifest_sha,
             'Producer source report disagrees with remote manifest')
        summary.update(passed=True, manifest_key=key, manifest_sha256=manifest_sha)
    except (Exception,KeyboardInterrupt) as error:
        summary.update(phase=phase, error_class=type(error).__name__)
    finally:
        finished.set()
        if counter is not None and counter.ident is not None:
            counter.join(timeout=6)
            if not counter.is_alive() and epoch_deadline-time.time() >= 6:
                save(out/'object-count-final.json',object_snapshot(s3,bucket))
        summary['slot_empty'] = not (SLOT/'cgroup.procs').read_text().strip()
        summary['passed'] = summary['passed'] and summary['slot_empty']
        save(out/'result.json',summary)
    return 0 if summary['passed'] else 1


def data_binds(mounts):
    expected = {f'/data/rustfs{i}':f'/srv/ax-rustfs-data-{i}' for i in range(4)}
    rows = [r for r in mounts if r['Destination']=='/data' or r['Destination'].startswith('/data/')]
    need(len(rows)==4 and {r['Destination']:r['Source'] for r in rows}==expected and
         all(r['Type']=='bind' and r['RW'] is True for r in rows),
         'RustFS container must use four exact writable data binds')
    return expected


def exact_status(ref, data, stopped):
    rows = data.get('Instances', {}).get('Instance', [])
    return (bool(data.get('RequestId')) and data.get('TotalCount') == 3 and len(rows) == 3 and
        {r.get('InstanceId') for r in rows} == set(ref['IDS'].values()) and
        all(r['ZoneId']=='cn-hangzhou-j' and r['InstanceName']==ROLE_NAMES[role]
            and (r['Status']=='Stopped' and r['StoppedMode']=='StopCharging'
                 if role in stopped else r['Status']=='Running')
            for role, instance in ref['IDS'].items() for r in rows if r['InstanceId']==instance))


def coordinator(tag, epoch_deadline):
    me = Path(__file__).resolve(strict=True)
    out = CONTROL/'preparation'/tag
    need(me.parent==out and CONTROL.is_mount() and socket.gethostname()=='ax-c1-jump'
         and os.geteuid()==0, 'Expected original mounted coordinator')
    helper = out/'reference.py'
    need(digest(helper)==REFERENCE_SHA, 'Original reusable controller primitives differ')
    ref = runpy.run_path(str(helper))
    latest = read(out/'latest.json')
    need(latest.get('build_manifest_sha256')==BUILD_MANIFEST_SHA and
         ref['latest_ok'](latest,datetime.now(timezone.utc)), 'Fresh pinned branch tips required')
    before = out/'instances-before.json'
    need(exact_status(ref,read(before),set(ROLE_NAMES)) and 0<=time.time()-before.stat().st_mtime<=300,
         'Fresh exact stopped A roles required')
    packet = read(out/'staged-shas.json')
    need(packet['worker']==digest(me) and packet['latest']==digest(out/'latest.json'),
         'Staging input differs')
    restore = ref['restore_payload'](out/'a-pool-up.py')
    join = ref['shell_join']
    sys.path.insert(0,str(CONTROL/'code-12'))
    from bench.runtime.pilot_pools import active_conflicts, cell_resources, resource_guard
    plan = read(CANON/'suite/plan.json')
    cell = next(c for c in plan['cells'] if c['pool']=='pool-a' and c['workload']=='trace')
    expected = {'host:'+i for i in ref['IDS'].values()} | {'disk:'+d for g in ref['DISK_IDS'].values() for d in g}
    need(set(cell_resources(plan,cell))==expected,'Canonical A lock identity differs')
    controls = lambda: sorted(p.parent for p in CONTROL.glob('*/state.sqlite'))
    active = controls()
    need(CANON in active and len(active)>=5,'Known controls missing')

    def ssh(role, cmd, timeout=8, **kw):
        need(role in ROLES,'Metadata must remain off')
        return subprocess.run(['ssh','-F',str(CANON/'ssh.conf'),'-oBatchMode=yes',
            '-oConnectTimeout=5','-oServerAliveInterval=4','-oServerAliveCountMax=2','pool-a-'+role,
            cmd if isinstance(cmd,str) else join(cmd)],check=True,timeout=timeout,**kw)

    def wait_workers():
        started = out/'instances-running.json'
        limit = min(time.time()+110, epoch_deadline-PRODUCER_SECONDS-60)
        while time.time()<limit:
            try:
                if exact_status(ref,read(started),{'metadata'}):
                    return
            except (FileNotFoundError,json.JSONDecodeError):
                pass
            time.sleep(1)
        raise TimeoutError('Exact authorized workers did not reach running state')

    remote = Path('/srv/ax-receipts')/tag
    worker_out = CLIENT/'preparation'/tag

    def bring(role):
        until = min(time.time()+28, epoch_deadline-PRODUCER_SECONDS-52)
        while time.time()<until:
            try:
                ssh(role,['true'],timeout=5,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
                break
            except (subprocess.CalledProcessError,subprocess.TimeoutExpired):
                time.sleep(2)
        else:
            raise TimeoutError('A node unavailable for restore')
        stage = str(remote)+'-restore'
        command = ('set -euC; umask 077; '+join(['mkdir','-m','700',stage])
            +'; cat > '+join([stage+'/a-pool-up.py'])
            +'; '+join(['printf','%s  %s\n',ref['RESTORE_SHA'],stage+'/a-pool-up.py'])
            +' | sha256sum -c --status; exec '+join(['timeout','--kill-after=5s','35s','python3','-B',
                stage+'/a-pool-up.py',role,'--out','/srv/ax-receipts/a-pool-up/'+role+'-'+tag,
                *(['--without-legacy-observer-scratch'] if role=='objects' else [])]))
        with (out/(role+'-restore.log')).open('xb') as f:
            ssh(role,command,input=restore,stdout=f,stderr=subprocess.STDOUT,timeout=42)
        paths = ref['DRIVES'][role]
        snapshot = json.loads(ssh(role,['python3','-B','-c',ref['CHECK_DRIVES'],*paths],
                                    capture_output=True,timeout=10).stdout)
        rows = snapshot['rows']
        need(snapshot['hostname']==ROLE_NAMES[role] and bool(snapshot['boot_id']) and
             [r['path'] for r in rows]==paths and len({r['device'] for r in rows})==len(paths) and
             ['d-'+r['serial'] for r in rows]==ref['DISK_IDS'][role] and
             all(r['available_bytes']>=r['total_bytes']//5+2*1024**3 and
                 r['available_inodes']>=r['total_inodes']//5+10000 for r in rows),
             'Original data disk identity or 20% + 2GiB capacity reserve differs')
        if role=='objects':
            script = ('import os,sys; '
                      'assert all(os.path.ismount(dst) and os.path.samefile(src,dst) '
                      'for src,dst in zip(sys.argv[1::2],sys.argv[2::2]))')
            binds = [x for i,p in enumerate(paths) for x in (p+'/rustfs-data','/srv/ax-rustfs-data-'+str(i))]
            ssh(role,['python3','-B','-c',script,*binds],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
            snapshot['binds_exact'] = True
        save(out/(role+'-capacity.json'),snapshot)

    def object_identity():
        result = ssh('objects',['docker','inspect','-f','{{.Image}} {{.State.Running}} {{.State.Pid}}',
            ref['CONTAINER']],capture_output=True,text=True).stdout.strip().split()
        revision = ssh('objects',['docker','image','inspect','-f',
            '{{index .Config.Labels "org.opencontainers.image.revision"}}',ref['IMAGE']],
            capture_output=True,text=True).stdout.strip()
        command = join(['docker','inspect','-f','{{range .Config.Env}}{{println .}}{{end}}',ref['CONTAINER']])
        switches = ssh('objects',command+' | '+join(['grep','-E',
            '^RUSTFS_EC_('+'|'.join(ref['SWITCHES'])+')=']),capture_output=True,text=True).stdout.splitlines()
        need(len(result)==3 and result[:2]==[ref['IMAGE'],'true'] and int(result[2])>1
             and revision==ref['RUSTFS'] and sorted(switches)==sorted('RUSTFS_EC_'+k+'=true' for k in ref['SWITCHES']),
             'Original RustFS identity or four layout switches differ')
        pid = int(result[2])
        mounts = json.loads(ssh('objects',['docker','inspect','-f','{{json .Mounts}}',ref['CONTAINER']],
                                capture_output=True,text=True).stdout)
        binds = data_binds(mounts)
        volume = ssh('objects',command+' | '+join(['grep','-E','^RUSTFS_VOLUMES=']),
                     capture_output=True,text=True).stdout.strip()
        need(volume=='RUSTFS_VOLUMES=/data/rustfs{0...3}', 'RustFS volume layout differs')
        probe = ('import os,sys; from pathlib import Path\n'
                 'p=Path("/proc")/sys.argv[1]\n'
                 'def tick(): return (p/"stat").read_text().rsplit(")",1)[1].split()[19]\n'
                 'a=tick()\n'
                 'assert all(os.path.samefile(src,p/"root"/dst.lstrip("/")) '
                 'for dst,src in zip(sys.argv[2::2],sys.argv[3::2]))\n'
                 'assert tick()==a\nprint(a)')
        ticks = ssh('objects',['python3','-B','-c',probe,pid,*[v for row in binds.items() for v in row]],
                    capture_output=True,text=True).stdout.strip()
        again = ssh('objects',['docker','inspect','-f','{{.State.Pid}}',ref['CONTAINER']],
                    capture_output=True,text=True).stdout.strip()
        need(ticks.isdecimal() and again==str(pid), 'RustFS running mount namespace changed')
        save(out/'rustfs.json',dict(image=ref['IMAGE'],revision=revision,pid=pid,
             start_ticks=int(ticks),switches=switches,volumes=volume.split('=',1)[1],
             data_binds=binds,process_mount_namespace_verified=True))

    need(epoch_deadline-time.time()>=PRODUCER_SECONDS+95,'Insufficient owned window')
    with ExitStack() as stack:
        roots = set()
        for ctl in active:
            locks = (ctl/'runner-locks').resolve(strict=True)
            if locks not in roots:
                stack.enter_context(resource_guard(ctl,cell,plan))
                roots.add(locks)

        def idle():
            need(controls()==active and not any(active_conflicts(ctl,cell,plan) for ctl in active),
                 'Shared physical lease appeared')

        idle()
        save(out/'locks-held.json',dict(tag=tag,work_deadline_epoch=epoch_deadline,
             controls=list(map(str,active)), resources=sorted(expected), formal_sample=False))
        phase = 'await-start'
        launched = passed = closure = False
        try:
            wait_workers()
            phase = 'restore'
            with ThreadPoolExecutor(max_workers=2) as executor:
                list(executor.map(bring,ROLES))
            phase = 'preflight'
            sv = os.statvfs(out)
            need(sv.f_bavail>=sv.f_blocks//5+(2*1024**3)//sv.f_frsize and
                 sv.f_favail>=sv.f_files//5+10000,'Coordinator capacity below the normal reserve')
            object_identity()
            idle()
            phase = 'stage-client'
            slot = (CANON/'hardware-inputs-01/data/pool-a-client-spec.json').read_bytes()
            need(hashlib.sha256(slot).hexdigest()==ref['SLOT_SHA'],'Original slot spec differs')
            ssh('client',['mkdir','-m','700',remote])
            for name, content in ((me.name,me.read_bytes()),('slot.json',slot)):
                ssh('client','set -euC; umask 077; cat > '+join([remote/name]),input=content)
            found = ssh('client',['sha256sum',remote/me.name],capture_output=True,text=True).stdout
            need(found.split()[0]==packet['worker'],'Client worker SHA differs')
            ssh('client',ref['client_slot_command'](remote),timeout=15)
            need(epoch_deadline-time.time()>=PRODUCER_SECONDS+58,'Insufficient window before starting work')
            phase = 'publish'
            launched = True
            result = ssh('client',['timeout','--kill-after=12s',str(max(1,int(epoch_deadline-time.time())))+'s',
                'python3','-B',remote/me.name,'worker','--tag',tag,'--deadline-epoch',epoch_deadline],
                stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=max(1,epoch_deadline-time.time()+15))
            passed = result.returncode==0
        except (Exception,KeyboardInterrupt) as error:
            save(out/'controller-failure.json',dict(phase=phase,**ref['failure_location'](error)))
        finally:
            if launched:
                try:
                    payload = ssh('client',['python3','-B','-c',ref['READ_FILES'],worker_out,*FILES],
                                  capture_output=True,timeout=12)
                    data = json.loads(payload.stdout)
                    ref['persist_payload'].__globals__['FILES'] = FILES
                    ref['persist_payload'](out,data)
                    got = ssh('client',['cat',SLOT/'cgroup.procs'],capture_output=True,text=True)
                    closure = not got.stdout.strip()
                    passed = passed and closure and all(key in data for key in FILES) and \
                        read(out/'worker/result.json').get('passed') is True
                except (Exception,KeyboardInterrupt) as error:
                    passed = False
                    save(out/'closure-failure.json',ref['failure_location'](error))
            save(out/'ready-for-stop.json',dict(passed=passed,launched=launched,
                 producer_slot_empty=closure,formal_sample=False,diagnostic_only=True,
                 remote_bucket_disposition='retained; no deletion attempted'))
            # Keep every physical lock until exact fresh, externally checked StopCharging proof.
            stop = out/'instances-after-stop.json'
            while True:
                try:
                    if stop.stat().st_mtime >= (out/'ready-for-stop.json').stat().st_mtime and \
                       exact_status(ref,read(stop),set(ROLE_NAMES)):
                        break
                except (FileNotFoundError,json.JSONDecodeError):
                    pass
                time.sleep(2)
            save(out/'closed.json',dict(passed=passed,diagnostic_only=True,formal_sample=False,
                 stop_evidence_sha256=digest(stop)))
    return 0 if passed else 1


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('role',choices=('coordinator','worker'))
    p.add_argument('--tag',required=True)
    p.add_argument('--deadline-epoch',required=True,type=int)
    a = p.parse_args()
    need(re.fullmatch(r'c2-pubdiag-[0-9]{8}t[0-9]{6}z',a.tag),'Expected a fresh diagnostic tag')
    return (coordinator if a.role=='coordinator' else run_worker)(a.tag,a.deadline_epoch)


if __name__=='__main__':
    raise SystemExit(main())
