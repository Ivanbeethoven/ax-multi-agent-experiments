#!/usr/bin/env python3
"""Pair fresh workspace mounts on one authenticated Packed snapshot."""
import argparse
import hashlib
from pathlib import Path
import sys

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('result', type=Path)
parser.add_argument('--binary', type=Path, required=True)
parser.add_argument('--producer', type=Path, required=True)
parser.add_argument('--files', type=int, choices=(128, 10000), default=128)
parser.add_argument('--order', choices=('off-on', 'on-off'), required=True)
args = parser.parse_args()
here = Path(__file__).resolve().parent.parent / 'evidence/preparation'
source = here / 'c4-workspace-v1-packed-fork-01/smoke.py'
raw = source.read_bytes()
if hashlib.sha256(raw).hexdigest() != '5e0cb515cb07851e2f8713ce78395fbf2b47796ad7a7c534eaea828ecd066e2d':
    raise ValueError('Frozen fixture source changed')
text = raw.decode()

def replace(old, new):
    global text
    if text.count(old) != 1:
        raise ValueError(f'Unexpected fixture anchor: {old}')
    text = text.replace(old, new, 1)

replace('result_path = Path(sys.argv[1])', 'result_path = options.result.resolve()')
replace('no_model=True,no_cloud=True', 'no_model=True,no_cloud=False')
start = text.index('binary_root =')
stop = text.index('access, secret', start)
text = text[:start] + """binary = options.binary.resolve(strict=True)
producer = options.producer.resolve(strict=True)
pins = {}
for asset in (binary, producer):
    with asset.open('rb') as stream:
        pins[asset] = hashlib.file_digest(stream, 'sha256').hexdigest()
""" + text[stop:]
replace('942893356344d292d9fcccadfceb039467c589ba10b4045cb148c0a65d2d3fec',
        '9750c5eaaac37e2d6ebaa8aaf39a4aa98a35fca5fba8de5ade26585123058806')
replace("RUST_LOG='warn'", """RUST_LOG='warn,brewfs::workspace::packed::readonly=info',
    BREWFS_FUSE_READ_DIRECT_IO='1', BREWFS_PACKED_METADATA_CACHE_BYTES=str(256 << 20),
    BREWFS_PACKED_OUTPUT_BUDGET_BYTES=str(128 << 20),
    BREWFS_PACKED_CONTROL_BUDGET_BYTES=str(8 << 20)""")
replace("schema='c4-local-brewfs-packed-fork-smoke-v1',passed=False,files=2",
        "schema='packed-whole-object-cache-pair-v1',passed=False,files=options.files")
replace("scope='One WSL FUSE host, tiny nonempty packed-v3 lower, no performance timing.'",
        "scope='One cloud host; fresh mount per arm, shared RustFS cache; readdir then serial sorted paths; SHA-256 checked.', order=options.order, budgets=dict(packed_disk_bytes=256<<20, metadata_bytes=256<<20, payload_memory_bytes=512<<20, payload_ssd_bytes=0, output_bytes=128<<20, control_bytes=8<<20, process_memory_bytes=8<<30, cpu_cores=2)")
replace('deadline = time.monotonic() + 138', 'deadline = time.monotonic() + (600 if options.files == 128 else 1500)')
replace('signal.alarm(145)', 'signal.alarm(610 if options.files == 128 else 1510)')
replace('def visit(side, workspace, data, rurl, namespace):',
        'def visit(side, workspace, data, rurl, namespace, counters):')
replace('    proc=subprocess.Popen([binary,', """    mount_env = dict(env, BREWFS_PACKED_CACHE='on' if side.startswith('on') else 'off',
        BREWFS_PACKED_CACHE_BYTES=str(256 << 20))
    counters.reset_duplicates()
    before_mount = counters()
    begin_mount = time.monotonic_ns()
    proc=subprocess.Popen([binary,""")
start = text.index("    py = 'from pathlib import Path")
stop = text.index('\ntry:', start)
text = text[:start] + '''
    from statistics import median
    def delta(before, after):
        return {k: after[k]-before[k] for k in before}

    arm = result['readbacks'][side] = dict(passed=False, phases={})
    arm['mount_ns'] = time.monotonic_ns()-begin_mount
    arm['mount_s3'] = delta(before_mount,counters())
    for stage in ('cold', 'warm'):
        if stage == 'warm':
            drain_start = time.monotonic_ns()
            idle = None
            while True:
                now = time.monotonic()
                if now > deadline: raise TimeoutError('Background drain did not finish')
                if counters()['active'] == 0:
                    if idle is None: idle = now
                    if now-idle >= .5: break
                else: idle = None
                time.sleep(.01)
            arm['background_drain_ns'] = time.monotonic_ns()-drain_start
        stat = Path(f'/proc/{proc.pid}/stat').read_text().split()
        cpu_before = int(stat[13])+int(stat[14])
        begin = time.monotonic_ns()
        start_s3 = counters()
        begin_listing = time.monotonic_ns()
        listed = set()
        for directory, subdirs, filenames in os.walk(point):
            subdirs.sort()
            for filename in sorted(filenames):
                listed.add(str((Path(directory)/filename).relative_to(point)))
        listing_ns = time.monotonic_ns()-begin_listing
        if listed != set(expected): raise ValueError('Directory listing differs')
        rows = []
        for relative, wanted in expected.items():
            begin_open = time.monotonic_ns()
            fd = os.open(point/relative, os.O_RDONLY)
            begin_read = time.monotonic_ns()
            try:
                body = os.pread(fd, 4096, 0)
                end_read = time.monotonic_ns()
            finally:
                os.close(fd)
            if len(body) != wanted['bytes'] or hashlib.sha256(body).hexdigest() != wanted['sha256']:
                raise ValueError('Wrong content at ' + relative)
            rows.append([begin_read-begin_open,end_read-begin_read])
        stat = Path(f'/proc/{proc.pid}/stat').read_text().split()
        cpu_after = int(stat[13])+int(stat[14])
        arm['phases'][stage] = dict(cpu_seconds=(cpu_after-cpu_before)/os.sysconf('SC_CLK_TCK'),wall_ns=time.monotonic_ns()-begin,
            s3=delta(start_s3,counters()), samples_ns=rows, readdir_ns=listing_ns,
            median_open_ns=median(row[0] for row in rows),
            median_pread_ns=median(row[1] for row in rows))
    status = Path(f'/proc/{proc.pid}/status').read_text()
    arm['vm_hwm_kib'] = int(re.search(r'^VmHWM:\\s+(\\d+)',status,re.M)[1])
    start_s3 = counters()
    begin_drain = time.monotonic_ns()
    stop_mount(True)
    arm['unmount_ns'] = time.monotonic_ns()-begin_drain
    arm['unmount_s3'] = delta(start_s3,counters())
    arm['end_to_end_ns'] = time.monotonic_ns()-begin_mount
    arm['end_to_end_s3'] = delta(before_mount,counters())
    messages = log_file.read_text(errors='replace')
    metrics = re.findall(r'(hits|misses|ready_hits|inflight_hits|local_bytes|downloaded)\\s*[:=]\\s*(\\d+)', messages)
    arm['cache'] = {key:int(value) for key,value in metrics}
    arm['passed'] = (arm['cache'].get('downloaded',0) > 0 if side.startswith('on') else not arm['cache'])
    return arm['passed']

''' + text[stop:]
replace("    src=work/'src';src.mkdir();(src/'sub').mkdir()\n    payload={'a.txt':b'packed smoke\\n','sub/b.txt':bytes(range(64))}",
        """    src=work/'src';src.mkdir()
    payload={f'{i//32:04d}/{i:06d}.bin': hashlib.shake_256(i.to_bytes(8,'little')).digest(4096)
             for i in range(options.files)}
    for relative in payload:
        (src/relative).parent.mkdir(exist_ok=True)""")
replace("    result['expected_files']=expected", "    result['input_sha256']=hashlib.sha256(json.dumps(expected,sort_keys=True).encode()).hexdigest()")
replace("    result['manifest_key']=key", """    result['manifest_key']=key
    result['object_sizes'] = [dict(key=row['Key'],bytes=row['Size'])
        for page in s3.get_paginator('list_objects_v2').paginate(Bucket=bucket)
        for row in page.get('Contents',[])]""")
replace("'--s3-rustfs-ec-block-size-hint','auto'],limit=25)", "'--s3-rustfs-ec-block-size-hint','auto'],limit=120)")
start = text.index("    phase='parent-readback'")
stop = text.index('except Exception as error:', start)
text = text[:start] + """    from whole_pack_meter import meter
    with meter(sp) as (measured_endpoint,counters):
        observed_data = dict(data,s3=dict(data['s3'],endpoint=measured_endpoint))
        for i, mode in enumerate(options.order.split('-') * 2):
            phase = mode + '-' + str(i)
            if not visit(phase,parent['workspace_id'],observed_data,rurl,namespace,counters):
                raise ValueError('Prefetch mode failed its trace check')
    result['passed'] = bool(result['parent_binding'])
""" + text[stop:]
replace("    phase='start-private-services'", """    result['rustfs_revision'] = docker('image','inspect','--format',
        '{{ index .Config.Labels "org.opencontainers.image.revision" }}',images[rustfs]).stdout.strip()
    if result['rustfs_revision'] != 'f4ed202508f2153afb7956bae6f7799b60eccd00':
        raise ValueError('RustFS revision differs')
    phase='start-private-services'""")
replace("    phase='create-base-bucket'", """    phase='create-base-bucket'
    from botocore.exceptions import ClientError, EndpointConnectionError, ConnectionClosedError, ReadTimeoutError
    def s3_ready():
        try:
            s3.list_buckets()
            return True
        except ClientError as error:
            if error.response.get('ResponseMetadata',{}).get('HTTPStatusCode') not in (500,502,503,504):
                raise
            return False
        except (EndpointConnectionError, ConnectionClosedError, ReadTimeoutError):
            return False
    wait_for(s3_ready,'authenticated RustFS S3 readiness',30)""")
replace("    for image in images.values():", "    for image in (images[rustfs],):")
replace("    docker(*common,'--name',redis,'-p','127.0.0.1::6379','--memory','256m','--cpus','1',\n           images[redis],'redis-server','--appendonly','no','--save','')", """    with socket.socket() as probe:
        probe.bind(('127.0.0.1', 0))
        rp = probe.getsockname()[1]
    redis_process = subprocess.Popen(['/usr/bin/redis-server','--bind','127.0.0.1',
        '--port',str(rp),'--dir',str(work),'--appendonly','no','--save','',
        '--maxmemory','256mb'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    result['redis_version'] = invoke(['/usr/bin/redis-server','--version']).stdout.strip()
    del result['image_sha256']['redis']""")
replace("    rp,sp=port(redis,'6379/tcp'),port(rustfs,'9000/tcp')","    sp=port(rustfs,'9000/tcp')")
replace("    wait_for(lambda:docker('exec',redis,'redis-cli','ping',check=False,limit=3).stdout.strip()=='PONG','Redis')",
        """    def redis_ready():
        if redis_process.poll() is not None:
            raise RuntimeError('Private Redis exited')
        return invoke(['/usr/bin/redis-cli','-p',str(rp),'ping'],check=False,limit=3).stdout.strip()=='PONG'
    wait_for(redis_ready,'Redis')""")
replace("    lines=docker('exec',redis,'redis-cli','--scan','--pattern','*packed/v3/current/'+workspace).stdout.splitlines()",
        "    lines=invoke(['/usr/bin/redis-cli','-p',str(rp),'--scan','--pattern','*packed/v3/current/'+workspace]).stdout.splitlines()")
replace("    for container in (rustfs,redis):", """    private_redis = globals().get('redis_process')
    if private_redis is not None and private_redis.poll() is None:
        private_redis.terminate()
        try: private_redis.wait(timeout=5)
        except subprocess.TimeoutExpired:
            private_redis.kill()
            private_redis.wait(timeout=5)
    cleanup['owned_containers_absent']['redis'] = private_redis is None or private_redis.poll() is not None
    for container in (rustfs,):""")
replace("    source_commit='ca07159f3da4b670e4aeb878faef928e69bc1634',",
        "    base_commit='ca07159f3da4b670e4aeb878faef928e69bc1634',")
replace("def visit(side, workspace, data, rurl, namespace, counters):",
        "def visit(side, workspace, data, rurl, namespace, counters, mutate=None):")
replace("str(work/('cache-'+side))", "str(work/('cache-'+side.removesuffix('-remount')))")
replace("    from statistics import median", """    if mutate is not None:
        relative, content = mutate
        fd = os.open(point/relative, os.O_WRONLY)
        try:
            if os.pwrite(fd, content, 0) != len(content):
                raise ValueError('Short private write')
            os.fsync(fd)
        finally:
            os.close(fd)
    from statistics import median""")
replace("    arm['passed'] = (arm['cache'].get('downloaded',0) > 0 if side.startswith('on') else not arm['cache'])",
        """    arm['passed'] = (bool(arm['cache']) if side.startswith('on') else not arm['cache'])
    if side.endswith('-remount'):
        arm['passed'] = arm['passed'] and arm['cache'].get('downloaded',-1) == 0""")
replace("    result['passed'] = bool(result['parent_binding'])", """        on_side = next(side for side in result['readbacks'] if side.startswith('on'))
        phase = 'retained-remount'
        if not visit(on_side+'-remount',parent['workspace_id'],observed_data,rurl,namespace,counters):
            raise ValueError('Retained cache remount fetched new runtime objects')
        phase = 'private-write'
        private_rurl = rurl.rsplit('/',1)[0]+'/1'
        private_namespace = namespace+'-private'
        private_prefix = [binary,'workspace','--meta-backend','redis','--meta-url',private_rurl,
            '--workspace-namespace',private_namespace]
        private = json_call(private_prefix,'init-packed-volume','--manifest-key',key,'--data-config',config)
        original = expected
        relative = next(iter(expected))
        content = bytes([payload[relative][0] ^ 255]) + payload[relative][1:]
        expected = dict(original)
        expected[relative] = dict(bytes=len(content),sha256=hashlib.sha256(content).hexdigest())
        if not visit('on-private',private['workspace_id'],data,private_rurl,private_namespace,counters,(relative,content)):
            raise ValueError('Private write verification failed')
        # A different cache root forces an empty local cache on this remount.
        if not visit('on-private-cold',private['workspace_id'],data,private_rurl,private_namespace,counters):
            raise ValueError('Private write cold remount failed')
        expected = original
        if not visit('on-parent-isolation',parent['workspace_id'],observed_data,rurl,namespace,counters):
            raise ValueError('Private write changed parent snapshot')
        result['private_write_isolation'] = True
    result['passed'] = bool(result['parent_binding'])""")
replace("    proc=subprocess.Popen([binary,'mount','--config',cfg,point],stdout=log,stderr=subprocess.STDOUT,env=env)",
        """    cgroup = Path('/sys/fs/cgroup')/(name+'-'+side)
    cgroup.mkdir()
    (cgroup/'memory.max').write_text(str(8 << 30))
    (cgroup/'cpu.max').write_text('200000 100000')
    def join_cgroup():
        (cgroup/'cgroup.procs').write_text(str(os.getpid()))
    proc=subprocess.Popen([binary,'mount','--config',cfg,point],stdout=log,stderr=subprocess.STDOUT,env=mount_env,preexec_fn=join_cgroup)""")
replace("    arm['unmount_ns'] = time.monotonic_ns()-begin_drain",
        """    arm['unmount_ns'] = time.monotonic_ns()-begin_drain
    arm['cgroup_memory_peak_bytes'] = int((cgroup/'memory.peak').read_text())
    arm['cgroup_memory_stat'] = (cgroup/'memory.stat').read_text()
    cgroup.rmdir()""")
replace("    cleanup={}", """    raw_output = result_path.parent/(result_path.stem+'-raw')
    raw_output.mkdir(exist_ok=False)
    for artifact in work.glob('*.log'):
        body = artifact.read_text(errors='replace').replace(access,'[redacted]').replace(secret,'[redacted]')
        (raw_output/artifact.name).write_text(body)
    result['raw_logs_directory'] = raw_output.name
    cleanup={}""")
# Attempt cleanup of this fixture's empty measurement cgroups after any failure.
replace("    cleanup['scratch_removed']=not work.exists()", """    cleanup['scratch_removed']=not work.exists()
    cleanup['cgroups_absent'] = True
    for group in Path('/sys/fs/cgroup').glob(name+'-*'):
        try: group.rmdir()
        except OSError: cleanup['cgroups_absent'] = False""")
# Translate only the generated program; the frozen historical fixture stays intact.
for old, new in [('workspace-v1', 'workspace'), ('packed/v3/', 'packed/'),
                 ('packed_v3', 'packed'), ('packed-v3', 'packed'), ('wire005', 'snapshot')]:
    text = text.replace(old, new)
sys.path.insert(0, str(Path(__file__).resolve().parent))
exec(compile(text, str(source), 'exec'), {'__name__': '__main__', 'options': args})
