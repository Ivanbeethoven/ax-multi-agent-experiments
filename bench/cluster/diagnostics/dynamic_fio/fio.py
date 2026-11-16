import contextlib, json, os, pathlib, re, signal, subprocess, sys, time, urllib.request
import boto3
from botocore.config import Config

out = pathlib.Path(sys.argv[1]).resolve()
reference = pathlib.Path(os.environ.get('DIAG_FORMAL_RUN', '/srv/ax-cluster/runs/direct/run-002')).resolve()
jobs = json.loads((reference / 'status.json').read_text())['jobs']
binary = pathlib.Path('/srv/ax-cluster/direct/bin')
brewfs = pathlib.Path(os.environ.get('DIAG_BREWFS_BIN', binary/'brewfs')).resolve()
driver = '/srv/ax-cluster/direct/scripts/mount_driver.py'
volume_mode = os.environ.get('DIAG_VOLUME_MODE', 'workspace')
if volume_mode not in {'flat', 'workspace'}:
    raise ValueError('DIAG_VOLUME_MODE must be flat or workspace')
redis = 'redis://127.0.0.1:37379/' + ('1' if volume_mode == 'flat' else '0')
endpoint = 'http://127.0.0.1:39000'
auth = {'AWS_ACCESS_KEY_ID':'dynamicdiag', 'AWS_SECRET_ACCESS_KEY':'localdiagnostic-only'}
port = 39567
hint = sys.argv[2]
read_memory_mib = int(sys.argv[3])
policy = sys.argv[4]
operations = sys.argv[5:]
read_variants = json.loads(os.environ.get('DIAG_READ_BINARIES', '[]'))
input_write_memory_mib = int(os.environ.get('DIAG_INPUT_WRITE_MEMORY_MIB', '0'))
if not 0 <= input_write_memory_mib <= 4096:
    raise ValueError('Input write memory must be 0–4096 MiB; zero uses the recorded settings')
if not isinstance(read_variants, list) or not all(isinstance(v, str) and pathlib.Path(v).is_file() for v in read_variants):
    raise ValueError('DIAG_READ_BINARIES must contain existing BrewFS binary paths')
if read_variants and any(mode not in {'seqread', 'randread'} for mode in operations):
    raise ValueError('Shared-input comparisons support read operations only')
assert policy in {'backend-default','whole-object','sparse'}
assert hint in {'off', 'auto'} and read_memory_mib in {512,4096}
server_pid = subprocess.check_output(['docker','inspect','-f','{{.State.Pid}}',(out/'container').read_text().strip()],text=True).strip()
assert int(server_pid) > 0
s3 = boto3.client('s3', endpoint_url=endpoint, aws_access_key_id=auth['AWS_ACCESS_KEY_ID'], aws_secret_access_key=auth['AWS_SECRET_ACCESS_KEY'], region_name='us-east-1', config=Config(s3={'addressing_style':'path'}, request_checksum_calculation='when_required', response_checksum_validation='when_required'))

def run(*args, **kw):
    return subprocess.run(list(map(str, args)), check=True, **kw)

def decode_cli(output):
    decoder = json.JSONDecoder()
    for line in output.splitlines(keepends=True):
        if line.startswith('{'):
            return decoder.raw_decode(output[output.index(line):])[0]
    raise RuntimeError(f'No JSON in command output: {output}')

def write(path, value):
    path.write_text(json.dumps(value, indent=2) + '\n')

for number, operation in [(n, mode) for n, mode in [(4,'randwrite'), (2,'seqwrite'), (1,'seqread'), (3,'randread')] if mode in operations]:
    work = out / operation
    work.mkdir()
    source = pathlib.Path(jobs[f'01-io-{number:02d}-r1']['output'])
    audit = json.loads(next(source.glob('*-m-audit.json')).read_text())
    reference_result = json.loads((source/'result.json').read_text())
    if volume_mode == 'flat':
        audit['environment'] = {k: v for k, v in audit['environment'].items()
            if not k.startswith(('BREWFS_PACKED_', 'BREWFS_WORKSPACE_'))}
    ns = f'dynio{time.time_ns()}'
    bucket = f'{ns}-{operation}'
    s3.create_bucket(Bucket=bucket)
    admin = [binary/'ax-brewfs-admin', '--meta-url',redis,'--workspace-namespace',ns]
    env = {**os.environ, **auth, **audit['environment']}
    if volume_mode == 'flat':
        run('redis-cli', '-h', '127.0.0.1', '-p', '37379', '-n', '1', 'FLUSHDB', 'SYNC')
        fork = {'workspaces': ['flat']}
    else:
        init = decode_cli(run(*admin,'init-shared-volume',env=env,capture_output=True,text=True).stdout)
        fork = decode_cli(run(*admin,'fork-shared-bucket','--revision',init['fork_base'],'--count','1',env=env,capture_output=True,text=True).stdout)
    mount = work / 'mount'
    mount.mkdir()

    @contextlib.contextmanager
    def mounted(stage):
        unit = f"{ns}-{work.name}-{stage}.service"
        cache = work / f'cache-{stage}'
        cache.mkdir()
        config = work / f'{stage}-config.json'
        input_cache = ({'write_memory_bytes': input_write_memory_mib * 1048576,
                        'memory_budget_bytes': input_write_memory_mib * 1048576}
                       if stage == 'input' and input_write_memory_mib else {})
        write(config, {
            'mount_point': str(mount), 'volume_format':volume_mode,
            **({} if volume_mode == 'flat' else {'workspace':fork['workspaces'][0],
                'workspace_namespace':ns, 'workspace_operator_managed':True}),
            'layout':{'chunk_size':4194304,'block_size':4194304},
            'data':{'backend':'s3','s3':{'bucket':bucket,'endpoint':endpoint,
                    'region':'us-east-1','max_concurrency':16,'force_path_style':True,
                    'disable_payload_checksum':True}},
            'meta':{'backend':'redis','redis':{'url':redis}},
            'cache':{**audit['cache'], **input_cache, 'cache_root':str(cache), 'read_memory_bytes':read_memory_mib * 1048576, 'read_fetch_policy':policy}})
        env_options = [f'--setenv={k}={v}' for k,v in {**auth,**audit['environment']}.items()]
        run('systemd-run',f'--unit={unit}','--property=MemoryMax=16G','--property=MemorySwapMax=0',*env_options,
            brewfs,'mount','--config',config,'--metrics','--metrics-addr',f'127.0.0.1:{port}',
            '--s3-rustfs-ec-block-size-hint',hint,'--read-fetch-policy',policy,mount)
        try:
            for _ in range(300):
                if os.path.ismount(mount):
                    break
                time.sleep(.1)
            else:
                raise RuntimeError(f'{stage}: mount failed')
            cg = run('systemctl','show','-p','ControlGroup','--value',unit,capture_output=True,text=True).stdout.strip()
            yield pathlib.Path('/sys/fs/cgroup' + cg)
        finally:
            stop_path = work / f'{stage}-stop.json'
            run('python3',driver,'stop','--id',f'{ns}-{stage}','--unit',unit,'--engine','brewfs-native-overlay',
                '--mount-dir',mount,'--port',port,'--out',stop_path)
            if not json.loads(stop_path.read_text())['unmounted_cleanly']:
                raise RuntimeError(f'{stage}: normal unmount failed')
            with (work/f'{stage}.log').open('w') as log:
                run('journalctl','--no-pager','-u',unit,stdout=log)

    def in_mount(cg, *args, **kw):
        return run('python3',driver,'exec-in-cgroup','--cgroup',cg,'--',*args,**kw)

    def host_snapshot(stage):
        for entry in ('diskstats','loadavg','pressure/io','pressure/cpu'):
            (work/f"{stage}-{entry.replace('/', '-')}").write_bytes(pathlib.Path('/proc',entry).read_bytes())

    def drain(stage):
        with (work/f'{stage}-drain.json').open('w') as report:
            run('python3',driver,'wait-upload-zero','--engine','brewfs-native-overlay','--port',port,stdout=report)

    with mounted('input') as cg:
        with (work/'input.json').open('w') as report:
            in_mount(cg,binary/'c1-io-helper','init-files','--root',mount,'--jobs',8,'--bytes-per-job',268435456,stdout=report)
        drain('input')
        (work/'input-metrics.prom').write_bytes(urllib.request.urlopen(f'http://127.0.0.1:{port}/metrics').read())
    objects = [item for page in s3.get_paginator('list_objects_v2').paginate(Bucket=bucket)
               for item in page.get('Contents', [])]
    write(work/'input-objects.json', {'count':len(objects), 'bytes':sum(item['Size'] for item in objects),
          'objects':[{'key':item['Key'], 'bytes':item['Size']} for item in objects]})
    prepared_work = work
    input_binary = brewfs
    for variant_index, variant_binary in enumerate(read_variants or [str(input_binary)]):
        brewfs = pathlib.Path(variant_binary).resolve()
        if read_variants:
            work = prepared_work / f'variant-{variant_index:02d}'
            work.mkdir()
        fio = work/'workload.fio'
        fio.write_text(re.sub(r'(?m)^filename=.*/(job-\d+\.dat)$',lambda match: f'filename={mount}/{match[1]}',(source/'workload.fio').read_text()))
        with mounted('measure') as cg:
            for name in ('memory.max','memory.events','memory.stat'):
                (work/f'before-{name}').write_bytes((cg/name).read_bytes())
            passes = ['cold','hot'] if operation.endswith('read') else ['write']
            results = {}
            def metrics():
                return urllib.request.urlopen(f'http://127.0.0.1:{port}/metrics', timeout=10).read()
            for stage in passes:
                (work/f'{stage}-before.prom').write_bytes(metrics())
                host_snapshot(stage+'-before')
                for counter in ('cpu.stat', 'io.stat'):
                    (work/f'{stage}-before-{counter}').write_bytes((cg/counter).read_bytes())
                collecting = os.environ.get('DIAG_OTEL') == '1'
                def server_metrics():
                    return urllib.request.urlopen('http://127.0.0.1:39464/metrics', timeout=10).read()
                if collecting:
                    time.sleep(2.1)
                    (work/f'{stage}-server-before.prom').write_bytes(server_metrics())
                path = work/f'{stage}-fio.json'
                profiling = os.environ.get('DIAG_PERF') == '1'
                profiler = None
                if profiling:
                    pid = (cg/'cgroup.procs').read_text().splitlines()[0]
                    perf_log = (work/f'{stage}-perf.log').open('wb')
                    profiler = subprocess.Popen(['perf','record','-q','-F','99','--call-graph','dwarf,8192',
                        '-p',pid,'-o',str(work/f'{stage}-perf.data')], stdout=subprocess.DEVNULL, stderr=perf_log)
                try:
                    start = time.monotonic()
                    in_mount(cg,'fio','--output-format=json+',f'--output={path}',fio)
                    elapsed = time.monotonic()-start
                finally:
                    if profiler is not None:
                        if profiler.poll() is None:
                            profiler.send_signal(signal.SIGINT)
                        profiler.wait(timeout=60)
                        perf_log.close()
                        if profiler.returncode not in (0, -signal.SIGINT, 128 + signal.SIGINT):
                            raise RuntimeError(f'CPU profiling exited {profiler.returncode}; see {stage}-perf.log')
                        with (work/f'{stage}-perf.txt').open('w') as report:
                            run('perf','report','-i',work/f'{stage}-perf.data','--stdio','--no-children',
                                '--sort','symbol','--percent-limit','0.5',stdout=report)
                if stage == 'write':
                    drain(stage)
                host_snapshot(stage+'-after')
                for counter in ('cpu.stat', 'io.stat'):
                    (work/f'{stage}-after-{counter}').write_bytes((cg/counter).read_bytes())
                (work/f'{stage}-after.prom').write_bytes(metrics())
                if collecting:
                    time.sleep(2.1)
                    (work/f'{stage}-server-after.prom').write_bytes(server_metrics())
                f = json.loads(path.read_text())
                direction = 'read' if operation.endswith('read') else 'write'
                stats = f['jobs'][0][direction]
                assert all(job['error'] == 0 for job in f['jobs'])
                assert stats['io_bytes'] == reference_result['application'][direction+'_bytes']
                results[stage] = dict(seconds=elapsed, fio_seconds=stats['runtime']/1000,
                    MiB_per_second=stats['io_bytes']/elapsed/1048576, bytes=stats['io_bytes'],
                    p99_ms=stats['clat_ns']['percentile']['99.000000']/1000000)
            for name in ('memory.events','memory.stat'):
                (work/f'after-{name}').write_bytes((cg/name).read_bytes())
            (work/'metrics.prom').write_bytes(urllib.request.urlopen(f'http://127.0.0.1:{port}/metrics').read())
        with mounted('verify') as cg:
            options = ['--seq-written-per-job',268435456] if operation=='seqwrite' else []
            with (work/'verify.json').open('w') as report:
                in_mount(cg,binary/'c1-io-helper','verify-files','--root',mount,'--jobs',8,'--bytes-per-job',268435456,
                         '--mode',operation,'--seed',reference_result['seed'],*options,stdout=report)
        assert json.loads((work/'verify.json').read_text())['passed'] is True
        write(work/'summary.json', dict(passes=results, verified=True,
              cpu_profiling=os.environ.get('DIAG_PERF') == '1',
              input_write_memory_mib=input_write_memory_mib,
              hint=hint, policy=policy, volume_format=volume_mode, brewfs_binary=str(brewfs), read_memory_mib=read_memory_mib, reference=str(source),
              input_binary=str(input_binary), input_directory=str(prepared_work), workspace=fork['workspaces'][0]))
    work = prepared_work
    brewfs = input_binary
