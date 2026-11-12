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
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[4]))
from bench.runtime.smoke_tree import file_tree
from bench.runtime.smoke_journal import SmokeJournal

p = argparse.ArgumentParser(description=__doc__)
for flag in ('brewfs', 'producer', 'juicefs', 'smallfiles', 'out'):
    p.add_argument('--'+flag, required=True, type=Path)
p.add_argument('--rustfs-image', required=True)
p.add_argument('--workload', choices=('c1','c2','c3','c4-stay'), default='c3')
p.add_argument('--engine', choices=('flat-v1','workspace-v1'), default='workspace-v1')
p.add_argument('--packed-metadata', choices=('on','off'), default='on')
p.add_argument('--eager-workspace', action='store_true', help='Import into workspace-v1 with no shared lower')
p.add_argument('--trace', type=Path)
p.add_argument('--replay', type=Path)
p.add_argument('--full-trace', action='store_true')
p.add_argument('--trace-timeout', type=int, default=300)
p.add_argument('--checkpoint',type=Path)
p.add_argument('--opencode',type=Path)
for flag in ('ssh-config','target-repo','target-tools'):
    p.add_argument('--'+flag,type=Path)
p.add_argument('--target-host')
p.add_argument('--target-check-timeout',type=int,default=300)
p.add_argument('--hint', default='auto')
p.add_argument('--layout', choices=('enabled','disabled','block-only'), default='enabled')
p.add_argument('--product', choices=('brewfs','juicefs'), required=True)
p.add_argument('--tiny', action='store_true', help='8 files and one worker; script checks only')
p.add_argument('--prepare-timeout',type=int,default=60)
a = p.parse_args()
if (a.packed_metadata == 'off' or a.eager_workspace) and (a.product != 'brewfs' or a.engine != 'workspace-v1'):
    p.error('Metadata and eager controls require BrewFS workspace-v1')
if a.eager_workspace and (a.packed_metadata != 'off' or a.layout != 'disabled' or a.hint != 'off'):
    p.error('Eager all-off requires metadata off, layout disabled and hint off')
if a.trace_timeout <= 0:
    p.error('--trace-timeout must be positive')
if a.prepare_timeout <= 0:
    p.error('--prepare-timeout must be positive')
if not 1<=a.target_check_timeout<=900:
    p.error('--target-check-timeout must be 1..900 seconds')
if a.workload=='c4-stay' and not a.checkpoint:
    p.error('C4 stay requires --checkpoint to locate the frozen workspace input')
if a.target_host and (a.workload!='c4-stay' or not all((a.ssh_config,a.target_repo,a.target_tools))):
    p.error('Move requires C4 state, SSH config and target repo/tools paths')
a.out.mkdir(mode=0o700)
result = {'formal_sample': False, 'workload': a.workload, 'engine': a.engine, 'hint': a.hint, 'layout': a.layout, 'arms': {}}
result.update(packed_metadata=a.packed_metadata, eager_workspace=a.eager_workspace)
run_id = 'ax-rbm-' + uuid.uuid4().hex[:12]
key, secret = secrets.token_hex(12), secrets.token_hex(24)
journal = SmokeJournal(a.out/'stages.jsonl', (key, secret))
env = dict(os.environ, AWS_ACCESS_KEY_ID=key, AWS_SECRET_ACCESS_KEY=secret,
           AWS_REGION='us-east-1', AWS_EC2_METADATA_DISABLED='true', NO_PROXY='127.0.0.1,localhost')
opts = ['--files','128','--hot-files','32','--workers','2','--read-ops','128',
        '--cow-files','16','--write-ops','32','--mixed-ops','64']
if a.tiny:
    opts = ['--files','8','--hot-files','4','--workers','1','--read-ops','8',
            '--cow-files','2','--write-ops','4','--mixed-ops','8']
result['smallfiles_options'] = opts

def call(*argv, timeout=60, check=True, umask=-1):
    return journal.run(argv, env=env, check=check, timeout=timeout,
                  capture_output=True, text=True, umask=umask)

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
    with journal.stage('workload:'+stage, timeout=a.trace_timeout+20):
        return run_workload(stage, root, out, *extra)


def run_workload(stage, root, out, *extra):
    if a.workload=='c4-stay':
        import runpy
        checkpoint=json.loads(a.checkpoint.read_text())
        if (checkpoint['source_process_exited'] is not True or checkpoint['sqlite_quick_check']!='ok'
                or checkpoint['contains_provider_master_key'] is not False):
            raise ValueError('Closed original checkpoint without provider keys required')
        original=a.checkpoint.parent/'workspace'
        workspace=root/'workspace'
        if stage=='prepare':
            root.mkdir(); call('cp','-a','--',original,workspace)
            result['checkpoint_sha256']=hashlib.sha256(a.checkpoint.read_bytes()).hexdigest()
            result['placement']='move' if a.target_host else 'stay'
            result['scope']='filesystem-only workspace reopen; agent state is opaque data'
            result['agent_process_started']=False
        if stage in ('prepare','verify-base'):
            if file_tree(workspace)!=file_tree(original):
                raise ValueError('Original workspace differs at '+stage)
            return {'passed':True,'entries':len(file_tree(workspace))}
        if stage=='verify-touched':
            expected=json.loads((out.parent/'run/closed-tree.json').read_text())
            if file_tree(workspace)!=expected:
                raise ValueError('Complete workspace differs on cold reopen')
        out.mkdir()
        proof={'passed':True,'agent_process_started':False,'native_model_calls':0,
               'scope':'filesystem tree and clean reopen; no native session interpretation'}
        sync=runpy.run_path(str(Path(__file__).with_name('prepare-session-input.py')))['sync_tree']
        proof['sync']=sync(workspace)
        if stage=='run':
            (out/'closed-tree.json').write_text(json.dumps(file_tree(workspace),sort_keys=True)+'\n')
        proof['cold_workspace_equal']=stage=='verify-touched'
        return proof
    if a.workload == 'c2':
        import shutil
        if not a.trace or not a.replay:
            raise ValueError('C2 requires --trace and --replay')
        if stage == 'prepare':
            events = a.trace/'capture/events.jsonl'
            with events.open() as stream:
                callbacks = sum(1 for _ in stream)
            if not a.full_trace and callbacks != 140:
                raise ValueError('this minimal C2 adapter requires the frozen 140-callback fixture')
            call('cp','-a','--',a.trace/'initial',root)
            with events.open('rb') as stream:
                result['trace_sha256'] = hashlib.file_digest(stream,'sha256').hexdigest()
            result['trace_kind'] = 'full-fuse-input' if a.full_trace else 'synthetic-140-callback-smoke-not-full-agent'
            result['trace_callbacks'] = callbacks
            if a.full_trace:
                metrics = json.loads((a.trace/'metrics.json').read_text())
                if callbacks != metrics['callbacks']:
                    raise ValueError('trace callbacks differ from metrics')
                for name in ('initial-tree.json','final-tree.json','hardlink-check.json'):
                    if not (a.trace/name).is_file():
                        raise ValueError('full trace input missing '+name)
        elif a.full_trace and stage in ('run','verify-touched'):
            command = ['timeout','--signal=TERM','--kill-after=5s',str(a.trace_timeout),
                       sys.executable,Path(__file__).with_name('qualify-trace.py'),
                       '--case',a.trace,'--binary',a.replay,'--root',root,'--out',out,
                       '--portable-terminal-probe','--cache-parent-fds']
            if a.product == 'juicefs':
                command.append('--juicefs-controls')
            if stage == 'run':
                command.append('--prepared-root')
            else:
                command += ['--verify-only','--replay-result',out.parent/'run/replay.json']
            call(*command,timeout=a.trace_timeout+15)
            proof = json.loads((out/'verification.json').read_text())
            if stage == 'run':
                result['replay'] = json.loads((out/'replay.json').read_text())
            return proof
        elif stage == 'run':
            out.mkdir()
            call(a.replay,a.trace/'capture/events.jsonl',a.trace/'capture/blobs',root,out/'replay.json',umask=0)
            report = json.loads((out/'replay.json').read_text())
            if not report.get('valid'):
                raise RuntimeError('Strict Rust replay did not validate')
            result['replay'] = report
        expected_path = a.trace/('initial-tree.json' if stage in ('prepare','verify-base') else 'final-tree.json')
        expected = json.loads(expected_path.read_text())
        actual = file_tree(root)
        if actual != expected:
            raise RuntimeError('trace tree mismatch at '+stage)
        return {'passed':True,'entries':len(actual)}
    if a.workload == 'c1' and stage in ('run','verify-touched'):
        if stage == 'run':
            path = root/'io.bin'
            expected = bytearray(bytes(range(256))*4096)
            with path.open('w+b', buffering=0) as f:
                f.write(expected); os.fsync(f.fileno())
                f.seek(0)
                assert f.read() == expected
                import random
                offsets = random.Random(9173).sample(range(256),16)
                for index in offsets:
                    f.seek(index*4096)
                    assert f.read(4096) == expected[index*4096:(index+1)*4096]
                for index in offsets:
                    payload = bytes([index])*4096
                    f.seek(index*4096); f.write(payload); os.fsync(f.fileno())
                    expected[index*4096:(index+1)*4096] = payload
                for ordinal, index in enumerate(offsets):
                    f.seek(index*4096)
                    if ordinal % 4:
                        assert f.read(4096) == expected[index*4096:(index+1)*4096]
                    else:
                        payload = bytes([255-index])*4096
                        f.write(payload); os.fsync(f.fileno())
                        expected[index*4096:(index+1)*4096] = payload
            out.mkdir()
            (out/'expected-sha256').write_text(hashlib.sha256(expected).hexdigest())
            return {'passed':True,'bytes':len(expected),'scope':'sequential write/read and random write/read/mixed functional probe'}
        digest = hashlib.sha256((root/'io.bin').read_bytes()).hexdigest()
        assert digest == (out.parent/'run/expected-sha256').read_text()
        return {'passed':True,'sha256':digest}
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
    mount_env = dict(env)
    if product == 'brewfs' and a.engine == 'workspace-v1':
        mount_env.update(BREWFS_WORKSPACE_PACKED_METADATA=a.packed_metadata,
                         BREWFS_WORKSPACE_NATIVE_IMPORT='0' if reopen else '1',
                         BREWFS_PACKED_V3_RETAINED_PAYLOAD_BYTES='0',
                         BREWFS_WORKSPACE_REQUIRE_NO_LOWER='1' if a.eager_workspace else '0')
    with (out/(label+'.log')).open('w') as log:
        proc = sp.Popen(list(map(str,cmd)), env=mount_env, stdout=log, stderr=sp.STDOUT)
        try:
            def ready():
                if proc.poll() is not None:
                    raise RuntimeError(f'{product} mount exited {proc.returncode}')
                return mounted(point)
            wait(ready)
            def stats(stage):
                if product == 'brewfs' and a.packed_metadata == 'off' and not a.eager_workspace:
                    (out/(label+'-'+stage+'.stats')).write_bytes((point/'.stats').read_bytes())
            stats('before')
            if reopen:
                arm['phase'] = 'reopen-verify'
                arm['verify_touched'] = workload('verify-touched',point,out/'verify-touched',
                                                  '--state',out/'run'/'expected-touched.tsv')
            else:
                arm['phase'] = 'prepare-and-verify-base'
                if product == 'juicefs' or a.engine == 'flat-v1' or a.eager_workspace:
                    import shutil
                    if a.workload in ('c2','c4-stay'):
                        with journal.stage('import-workspace',timeout_seconds=a.prepare_timeout):
                            copied=call('stdbuf','-oL','cp','-av','--',str(work/'source')+'/.',str(point)+'/',
                                        timeout=a.prepare_timeout)
                            arm['import_entries_reported']=len(copied.stdout.splitlines())
                    else:
                        shutil.copytree(work/'source'/'small',point/'small')
                arm['verify_base'] = workload('verify-base',point,out/'verify-base')
                arm['phase'] = 'run'
                arm['run'] = workload('run',point,out/'run')
            stats('after')
            if product == 'brewfs' and a.packed_metadata == 'off' and not a.eager_workspace:
                from bench.runtime.packed_metadata_probe import verify_off
                arm[label+'_metadata_path'] = verify_off(
                    (out/(label+'-before.stats')).read_text(),
                    (out/(label+'-after.stats')).read_text())
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
    with journal.stage('verify-tool-sha256'):
        for name in ('brewfs','producer','juicefs','smallfiles'):
            with getattr(a,name).open('rb') as f:
                result['binary_sha256'][name] = hashlib.file_digest(f,'sha256').hexdigest()
        if a.replay:
            with a.replay.open('rb') as f:
                result['binary_sha256']['replay'] = hashlib.file_digest(f,'sha256').hexdigest()
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
        if a.layout == 'disabled':
            for k in knobs:
                if k.startswith('RUSTFS_EC_'): knobs[k] = 'false'
        elif a.layout == 'block-only':
            knobs['RUSTFS_EC_READ_QUANTUM_ENABLE'] = 'false'
            knobs['RUSTFS_EC_READ_QUANTUM_FLEET_CONFIRMED'] = 'false'
        ef = work/'rustfs.env'
        ef.write_text(''.join(f'{k}={v}\n' for k,v in knobs.items()))
        ef.chmod(0o600)
        call('docker','run','--pull=never','-d','--name',run_id,'--cpus','2','--memory','2g',
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
        result['source'] = workload('prepare',work/'source',a.out/'prepare')
        for product, db in [(a.product, 0)]:
            arm = result['arms'][product] = {'phase':'prepare', 'passed':False}
            out = a.out/product
            out.mkdir()
            point = (Path(json.loads(a.checkpoint.read_text())['canonical_workspace']).parent
                     if a.workload=='c4-stay' else work/product)
            if a.workload=='c4-stay' and (str(point)!='/srv/ax-pilot-session' or point.is_symlink()
                    or mounted(point) or (point.exists() and any(point.iterdir()))):
                raise ValueError('Original canonical mount must be absent or empty')
            point.mkdir(exist_ok=a.workload=='c4-stay')
            points.append(point)
            try:
                bucket = run_id+'-'+product
                with journal.stage('create-bucket'):
                    s3.create_bucket(Bucket=bucket)
                url = f'redis://127.0.0.1:{rp}/{db}'
                if product == 'brewfs' and a.engine == 'workspace-v1':
                    manifest = work/'manifest'
                    producer_cmd = [a.producer,'--source-directory',work/'source','--source-consistency',
                         'best-effort-detected','--source-hardlink-policy','visible-links','--output-dir',
                         work/'objects','--manifest-output',manifest,'--bucket',bucket,'--endpoint',ep,
                         '--region','us-east-1','--force-path-style','true','--prefix','base',
                         '--metadata-codec','raw','--data-codec','raw','--inline-data','off',
                         '--chunk-size','67108864','--block-size','4194304',
                         '--s3-rustfs-ec-block-size-hint',a.hint]
                    if not a.eager_workspace:
                        call(*producer_cmd)
                        arm['manifest_key'] = manifest.read_text().strip()
                    data = dict(backend='s3',s3=dict(bucket=bucket,endpoint=ep,region='us-east-1',
                                force_path_style=True,rustfs_ec_block_size_hint=a.hint))
                    cfg = work/'data.json'
                    cfg.write_text(json.dumps({'data':data}))
                    arm['phase'] = 'init-volume' if a.eager_workspace else 'init-packed-volume'
                    init_cmd = [a.brewfs,'workspace','--meta-backend','redis',
                        '--meta-url',url,'--workspace-namespace',run_id,arm['phase'],'--data-config',cfg]
                    if not a.eager_workspace:
                        init_cmd += ['--manifest-key',manifest.read_text().strip()]
                    raw = call(*init_cmd).stdout
                    arm['lower_preparation'] = ('eager-private-workspace-no-packed-binding' if a.eager_workspace
                                                else 'packed-data-unchanged-kv-metadata' if a.packed_metadata=='off'
                                                else 'packed')
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
                elif product == 'brewfs':
                    def command(cache):
                        cfg = work/(cache+'.json')
                        cfg.write_text(json.dumps(dict(
                            volume_format='flat-v1', meta=dict(backend='redis',redis={'url':url}),
                            data=dict(backend='s3',s3=dict(bucket=bucket,endpoint=ep,
                                region='us-east-1',force_path_style=True,rustfs_ec_block_size_hint=a.hint)),
                            cache=dict(cache_root=str(work/cache),writeback_mode='upload_before_commit'))))
                        return [a.brewfs,'mount','--config',cfg,point]
                else:
                    call(a.juicefs,'format','--storage','s3','--bucket',ep+'/'+bucket,
                         '--access-key',key,'--secret-key',secret,url,'ax-smoke')
                    def command(cache):
                        return [a.juicefs,'mount','--enable-xattr','--enable-cap',
                                '--cache-dir',work/cache,url,point]
                def base_objects():
                    rows=[]
                    for page in s3.get_paginator('list_objects_v2').paginate(Bucket=bucket):
                        rows.extend((row['Key'],row['Size'],row['ETag']) for row in page.get('Contents',[]))
                    return sorted(rows)
                check_lower = product=='brewfs' and a.engine=='workspace-v1' and not a.eager_workspace
                immutable_before=base_objects() if check_lower else None
                visit(product,point,work,out,command('initial-'+product),arm)
                if a.target_host:
                    from bench.runtime.move_client import reopen
                    arm['phase']='target-reopen'
                    config=json.loads((work/('initial-'+product+'.json')).read_text()) if product=='brewfs' else None
                    arm['move']=reopen(ssh_config=a.ssh_config,host=a.target_host,
                        repo=a.target_repo,tools=a.target_tools,product=product,binary=getattr(a,product),
                        expected_tree=json.loads((out/'run/closed-tree.json').read_text()),
                        config=config,redis_url=url,endpoint=ep,env=env,out=out,
                        check_timeout=a.target_check_timeout)
                else:
                    visit(product,point,work,out,command('reopen-'+product),arm,reopen=True)
                if check_lower:
                    if base_objects()!=immutable_before:
                        raise ValueError('Immutable lower object identities changed')
                    arm['lower_objects']={'unchanged':True,'count':len(immutable_before),
                        'identity_sha256':hashlib.sha256(json.dumps(immutable_before).encode()).hexdigest()}
                arm.update(phase='complete',passed=True)
            except (Exception, KeyboardInterrupt) as e:
                arm['error'] = error(e)
                arm['log_tails'] = {p.name:error(RuntimeError(p.read_text(errors='replace')[-1500:]))
                                    for p in out.glob('*.log')}
    finally:
        result['mounts_absent'] = all(not mounted(point) for point in points)
        logs = call('docker','logs','--tail','25',run_id,check=False)
        (a.out/'rustfs.log').write_text((logs.stdout+logs.stderr).replace(secret,'[redacted]').replace(key,'[redacted]'))
        call('docker','rm','-fv',run_id,check=False)
        result['rustfs_removed'] = call('docker','inspect',run_id,check=False).returncode != 0
        redis.terminate()
        redis.wait(timeout=5)
        result["redis_exited"] = redis.poll() is not None

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
    result['passed'] = (len(result['arms']) == 1 and all(x['passed'] for x in result['arms'].values())
                        and result['scratch_removed'])
    (a.out/'receipt.json').write_text(json.dumps(result,indent=2)+'\n')
raise SystemExit(not result['passed'])
