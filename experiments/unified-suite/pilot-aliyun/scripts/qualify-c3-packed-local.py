#!/usr/bin/env python3
"""Check a complete C3 Packed input on owned local RustFS/Redis services.

Runs the frozen eight-phase workload only with --run-workload. Reads the original
stopped namespace through the existing read-only verifier. No cloud API or model.
"""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import runpy
import secrets
import socket
import subprocess
import sys
import time
from urllib.request import urlopen
import uuid

REPO = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(REPO))
from bench.runtime.common import digest, load, write_new
from bench.runtime.workloads import counts
from bench.suite_mount import overlay


def main():
    p = argparse.ArgumentParser(description=__doc__)
    for name in ('build-root', 'services', 'manifest', 'latest', 'plan', 'source', 'source-proof',
                 'verifier', 'reader', 'out'):
        p.add_argument('--' + name, type=Path, required=True)
    for name in ('verifier-sha256', 'reader-sha256'):
        p.add_argument('--' + name, required=True)
    p.add_argument('--run-workload', action='store_true')
    a = p.parse_args()
    if os.geteuid() != 0 or not a.out.is_absolute() or a.out.exists():
        raise ValueError('Root and a new absolute local output directory required')
    manifest = load(a.manifest); latest = load(a.latest)
    if not latest['passed'] or latest['build_manifest_sha256'] != digest(a.manifest):
        raise ValueError('Matching successful branch check required')
    for name in ('brewfs', 'packed_v3_snapshot_fixture', 'smallfiles'):
        expected = next(x for x in manifest['files'] if x['file'] == name)
        if digest(a.build_root/'bin'/name) != expected['sha256']:
            raise ValueError('Build binary differs: ' + name)
    if digest(a.verifier) != a.verifier_sha256 or digest(a.reader) != a.reader_sha256:
        raise ValueError('Independent verification binary changed')
    plan = load(a.plan)
    cell = next(c for c in plan['cells'] if c['workload'] == 'smallfiles' and c['arm_id'] == 'packed')
    params = cell['params']; expected = counts(params); files = params['files']
    if files not in (100000, 1000000) or params['agents'] != 1 or params['workspaces'] != 1:
        raise ValueError('One complete declared C3 workspace required')
    source = load(a.source_proof)
    if not source['passed'] or source['files'] != files or source['bytes_checked'] != files*4096:
        raise ValueError('Full source proof differs from the declared plan')
    with socket.socket() as probe:
        probe.bind(('127.0.0.1', 18913))
    a.out.mkdir(parents=True); a.out.chmod(0o700)
    pins={str(x):digest(x) for x in [Path(__file__), a.manifest, a.latest, a.plan, a.source_proof,
        a.verifier, a.reader, Path(__file__).with_name('smallfiles-oracle.py'),
        Path(__file__).with_name('qualify-entity-local.py')]}
    write_new(a.out/'intent.json', dict(files=files, params=params, seed=plan['config']['seed'],
        run_workload=a.run_workload, pins=pins,
        automatic_retry=False, model_calls=0, cloud_instances_started=False))
    (a.out/'executed-source.py').write_bytes(Path(__file__).read_bytes())
    env = dict(os.environ, AWS_EC2_METADATA_DISABLED='true', AWS_REGION='us-east-1',
        RUST_LOG='warn', NO_COLOR='1', NO_PROXY='127.0.0.1,localhost', no_proxy='127.0.0.1,localhost')
    for line in (a.services/'rustfs.env').read_text().splitlines():
        key,value = line.split('=',1)
        env[{'RUSTFS_ACCESS_KEY':'AWS_ACCESS_KEY_ID','RUSTFS_SECRET_KEY':'AWS_SECRET_ACCESS_KEY'}[key]] = value
    import boto3
    from botocore.config import Config
    client = boto3.client('s3', endpoint_url='http://127.0.0.1:19000', region_name='us-east-1',
        aws_access_key_id=env['AWS_ACCESS_KEY_ID'], aws_secret_access_key=env['AWS_SECRET_ACCESS_KEY'],
        config=Config(signature_version='s3v4', retries={'max_attempts':0}, s3={'addressing_style':'path'}))
    bucket='axc3-'+uuid.uuid4().hex; volume=str(uuid.uuid4()); namespace=str(uuid.uuid4())
    catalog='axc3-'+uuid.uuid4().hex; point=a.out/'mount'; point.mkdir()
    process=None; logs=[]; result=dict(passed=False, files=files, bucket=bucket, volume=volume,
        namespace=namespace, catalog=catalog, run_workload=a.run_workload,
        scope='Local functional check, one shared physical disk. Initial full verification warms the cache; timings are excluded from performance. No observer qualification.')
    def run(name, argv, environment=env):
        d=a.out/name;d.mkdir();write_new(d/'command.json',dict(argv=list(map(str,argv)),automatic_retry=False))
        begin=time.monotonic()
        with (d/'stdout').open('xb') as stdout,(d/'stderr').open('xb') as stderr:
            child=subprocess.Popen(list(map(str,argv)),env=environment,stdout=stdout,stderr=stderr)
            write_new(d/'process.json',dict(pid=child.pid));code=child.wait()
        write_new(d/'exit.json',dict(exit_code=code,wall_seconds=time.monotonic()-begin))
        if code:raise RuntimeError('Inspect '+str(d))
    def stop():
        if process is None:return
        if os.path.ismount(point):run('normal-unmount',['fusermount3','-u',point])
        code=process.wait()
        for stream in logs:stream.close()
        write_new(a.out/'mount-exit.json',dict(exit_code=code,mount_absent=not os.path.ismount(point),pid=process.pid))
        if code or os.path.ismount(point):raise ValueError('Original mount did not stop normally')
    try:
        client.create_bucket(Bucket=bucket)
        run('source-before',[a.verifier,a.source,'base',a.out/'source-before.json',str(files),'8','1'])
        key=a.out/'manifest-key.txt'
        run('producer',[a.build_root/'bin/packed_v3_snapshot_fixture','--source-directory',a.source,
            '--source-consistency','best-effort-detected','--source-hardlink-policy','visible-links',
            '--output-dir',a.out/'producer-output','--manifest-output',key,'--bucket',bucket,
            '--endpoint','http://127.0.0.1:19000','--region','us-east-1','--force-path-style','true',
            '--prefix','base','--metadata-codec','raw','--data-codec','raw','--inline-data','off',
            '--chunk-size','4194304','--block-size','4194304','--frame-policy','size-only',
            '--access-profile','random-small-file','--max-random-frame-raw-bytes','262144',
            '--s3-rustfs-ec-block-size-hint','auto'])
        config=dict(volume_format='workspace-entity-v1',volume={'format':'workspace-entity-v1'},
            workspace=namespace,workspace_namespace=catalog,
            data={'backend':'s3','s3':dict(bucket=bucket,endpoint='http://127.0.0.1:19000',region='us-east-1',
                force_path_style=True,rustfs_ec_block_size_hint='auto')},
            meta={'backend':'redis','redis':{'url':'redis://127.0.0.1:16379/0'}},
            layout=dict(chunk_size=4194304,block_size=4194304),
            fuse=dict(workers=4,max_background=512,cache_ttl_ms=0,keep_cache=False),
            cache=dict(cache_root=str(a.out/'cache'),writeback_mode='upload_before_commit',
                read_memory_bytes=0,read_ssd_bytes=0,compression='none'))
        config=overlay(config,cell['client_settings'])
        write_new(a.out/'brewfs.json',config)
        argv=[a.build_root/'bin/brewfs','mount','--config',a.out/'brewfs.json','--volume-format','workspace-entity-v1',
            '--experimental-entity-mount','--entity-volume-id',volume,'--workspace',namespace,
            '--workspace-namespace',catalog,'--entity-read-cache-bytes',str(config['entity_read_cache_bytes']),'--packed-manifest-key',
            key.read_text().strip(),'--s3-rustfs-ec-block-size-hint','auto','--metrics','--metrics-addr','127.0.0.1:18913',point]
        write_new(a.out/'mount-command.json',dict(argv=list(map(str,argv))))
        logs=[(a.out/'mount.stdout').open('xb'),(a.out/'mount.stderr').open('xb')]
        process=subprocess.Popen(list(map(str,argv)),env=env,stdout=logs[0],stderr=logs[1])
        write_new(a.out/'mount-started.json',dict(pid=process.pid,
            start_ticks=Path(f'/proc/{process.pid}/stat').read_text().rsplit(')',1)[1].split()[19]))
        while not os.path.ismount(point):
            if process.poll() is not None:raise RuntimeError('Mount exited before ready')
            time.sleep(.1)
        run('full-mounted-base',[a.verifier,point,'base',a.out/'mounted-base.json',str(files),'8','1'])
        expected_dir=a.out/'expected'
        oracle=['python3',Path(__file__).with_name('smallfiles-oracle.py'),'--source',a.source,
            '--source-proof',a.source_proof,'--files',str(files),'--out',expected_dir]
        if a.run_workload:
            argv=[a.build_root/'bin/smallfiles','run','--root',point,'--output',a.out/'workload']
            for flag,keyname in [('files','files'),('hot-files','hot_files'),('workers','workers'),('read-ops','read_ops'),
                    ('cow-files','cow_files'),('write-ops','write_ops'),('mixed-ops','mixed_ops')]:
                argv+=['--'+flag,str(params[keyname])]
            argv+=['--seed',str(plan['config']['seed']),'--workspace','1']
            run('eight-phases',argv)
            measured=load(a.out/'workload/result.json')
            if any(measured.get(k)!=v for k,v in expected.items()):raise ValueError('Fixed workload counts differ')
            oracle+=['--state',a.out/'workload/expected-touched.tsv']
        run('expected-state',oracle)
        with urlopen('http://127.0.0.1:18913/metrics',timeout=5) as response:
            (a.out/'native-metrics.prom').write_bytes(response.read())
        stop()
        helpers=runpy.run_path(str(Path(__file__).with_name('qualify-entity-local.py')))
        redis=helpers['redis_command'];username='verify-'+uuid.uuid4().hex;password=secrets.token_hex(24)
        redis(['ACL','SETUSER',username,'on','>'+password,'~{brewfs-entity:'+volume+'}/*',
            '~{brewfs-ws-v1}:'+catalog+':ws:v1/*','+type','+get','+mget','+exists','+zscore','+zrangebylex',
            '+strlen','+time','+evalsha','+script|load','+ping','+client|setinfo'])
        try:redis(['SET','{brewfs-entity:'+volume+'}/must-not-write','invalid'],username,password)
        except RuntimeError as error:
            if not str(error).startswith('NOPERM'):raise
        else:raise ValueError('Read-only verifier account allowed a write')
        verify_env=dict(env,AX_VERIFY_REDIS_URL=f'redis://{username}:{password}@127.0.0.1:16379/0')
        run('original-cold-readback',[a.reader,'packed',volume,namespace,catalog,bucket,'http://127.0.0.1:19000',
            a.out/'cold-proof',expected_dir/'expected-tree.json',expected_dir/'expected-hardlinks.json'],verify_env)
        proof=load(a.out/'cold-proof/verify.json')
        if not all(proof.get(k) is True for k in ('passed','expected_tree_equal','expected_hardlinks_equal',
                'namespace_unchanged','authority_unchanged','volume_rows_unchanged')):
            raise ValueError('Original namespace proof incomplete')
        run('source-after',[a.verifier,a.source,'base',a.out/'source-after.json',str(files),'8','1'])
        if any(digest(Path(path))!=sha for path,sha in pins.items()):raise ValueError('Pinned preparation input or helper changed')
        result.update(passed=True,full_base_verified=True,readonly_acl_write_rejected=True,
            original_namespace_readback=proof,immutable_source_unchanged=True)
    except BaseException as error:
        result['error']=type(error).__name__+': '+str(error)
        raise
    finally:
        if process is not None and process.poll() is None:
            try:stop()
            except BaseException as error:result.update(passed=False,cleanup_error=str(error))
        client.close()
        result.update(at=datetime.now(timezone.utc).isoformat(),mount_present=os.path.ismount(point),
            process_alive=process is not None and process.poll() is None)
        write_new(a.out/'result.json',result)
        print(json.dumps(result,indent=2))


if __name__=='__main__':main()
