#!/usr/bin/env python3
"""Publish the declared complete C3 packed base and verify every source file.

Fresh entity namespace is used only for this immutable-base check. It does not
prove reopening an upper, which requires the original-namespace reader.
"""
import argparse
from datetime import datetime,timezone
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import uuid

REPO=Path(__file__).resolve().parents[4]
sys.path.insert(0,str(REPO))
from bench.runtime.common import digest,load,write_new
from bench.suite_mount import overlay


def ref(p):return dict(path=str(p),sha256=digest(p))


def main():
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('asset','plan','source','source-proof','node-tool','out'):p.add_argument('--'+name,type=Path,required=True)
    p.add_argument('--node-tool-sha256',required=True)
    p.add_argument('--database',type=int,required=True)
    p.add_argument('--mount-state',type=Path,required=True)
    a=p.parse_args()
    if a.asset.exists() or a.out.exists() or a.mount_state.exists():raise ValueError('Fresh asset, output and supervisor required')
    if digest(a.node_tool)!=a.node_tool_sha256:raise ValueError('Helper changed')
    source=load(a.source_proof);plan=load(a.plan)
    cell=next(c for c in plan['cells'] if c['workload']=='smallfiles' and c['arm_id']=='packed')
    files=cell['params']['files'];logical_bytes=files*4096
    if files not in (100000,1000000):raise ValueError('Declared 100k or million-file base required')
    if source.get('passed') is not True or source.get('files')!=files or source.get('bytes_checked')!=logical_bytes:
        raise ValueError('Complete source qualification must match the declared plan size')
    if not 20<=a.database<256:raise ValueError('Explicit unused pilot database required')
    redis='redis://10.20.2.162:6379/'+str(a.database)
    if subprocess.check_output(['redis-cli','-u',redis,'DBSIZE'],text=True).strip()!='0':raise ValueError('Never overwrite metadata')
    a.out.mkdir(parents=True);a.asset.mkdir(parents=True)
    env=dict(os.environ,AWS_EC2_METADATA_DISABLED='true',AWS_REGION='us-east-1',RUST_LOG='error',NO_COLOR='1')
    values=dict(line.split('=',1) for line in Path('/srv/ax-client/private/rustfs.env').read_text().splitlines())
    env.update(AWS_ACCESS_KEY_ID=values['RUSTFS_ACCESS_KEY'],AWS_SECRET_ACCESS_KEY=values['RUSTFS_SECRET_KEY'])
    bucket='axp-'+uuid.uuid4().hex[:16];volume=str(uuid.uuid4());namespace=str(uuid.uuid4());catalog='pilot-'+uuid.uuid4().hex
    write_new(a.out/'started.json',dict(at=datetime.now(timezone.utc).isoformat(),cell_id=cell['id'],source=str(a.source),
              source_proof=ref(a.source_proof),node_tool=ref(a.node_tool),database=a.database,bucket=bucket,
              volume=volume,namespace=namespace,catalog=catalog,purpose='packed-base-preparation',automatic_retry=False))
    Path('/sys/fs/cgroup/ax-slot-1/cgroup.procs').write_text(str(os.getpid()))
    def run(name,argv):
        directory=a.out/name;directory.mkdir();begin=time.monotonic()
        write_new(directory/'command.json',dict(argv=list(map(str,argv)),automatic_retry=False))
        with (directory/'stdout').open('xb') as stdout,(directory/'stderr').open('xb') as stderr:
            child=subprocess.Popen(list(map(str,argv)),env=env,stdout=stdout,stderr=stderr)
            write_new(directory/'process.json',dict(pid=child.pid));code=child.wait()
        write_new(directory/'exit.json',dict(exit_code=code,wall_seconds=time.monotonic()-begin))
        if code:raise RuntimeError('Inspect '+str(directory))
    def helper(action,name,**fields):
        argv=['python3',a.node_tool,action,'--asset',a.asset]
        for k,v in fields.items():
            if v is True:argv+=['--'+k.replace('_','-')]
            elif v is not None:argv+=['--'+k.replace('_','-'),str(v)]
        run(name,argv)
    active=False;failure=None
    try:
        import boto3
        from botocore.config import Config
        client=boto3.client('s3',endpoint_url='http://10.20.2.163:9000',region_name='us-east-1',
            aws_access_key_id=env['AWS_ACCESS_KEY_ID'],aws_secret_access_key=env['AWS_SECRET_ACCESS_KEY'],
            config=Config(signature_version='s3v4',retries={'max_attempts':0},s3={'addressing_style':'path'}))
        try:client.create_bucket(Bucket=bucket)
        finally:client.close()
        temporary=a.asset/'producer-tmp';temporary.mkdir();env['TMPDIR']=str(temporary)
        if temporary.stat().st_dev!=a.source.stat().st_dev:raise ValueError('Producer temporary files must use the declared client data disk')
        producer='/srv/ax-client/assets/bin/packed_v3_snapshot_fixture';manifest=a.asset/'manifest-key.txt'
        run('producer',[producer,'--source-directory',a.source,'--source-consistency','best-effort-detected',
            '--source-hardlink-policy','visible-links','--output-dir',a.asset/'producer','--manifest-output',manifest,
            '--bucket',bucket,'--endpoint','http://10.20.2.163:9000','--region','us-east-1','--force-path-style','true',
            '--prefix','base','--metadata-codec','raw','--data-codec','raw','--inline-data','off',
            '--chunk-size','4194304','--block-size','4194304','--frame-policy','size-only',
            '--access-profile','random-small-file','--max-random-frame-raw-bytes','262144','--s3-rustfs-ec-block-size-hint','auto'])
        key=manifest.read_text().strip()
        if not key or '\n' in key:raise ValueError('Actual producer manifest key required')
        config=overlay(dict(meta={'backend':'redis','redis':{'url':redis}},
            data={'backend':'s3','s3':dict(bucket=bucket,endpoint='http://10.20.2.163:9000',region='us-east-1',force_path_style=True)},
            layout={'chunk_size':4194304,'block_size':4194304},fuse={'workers':4,'max_background':512},
            workspace=namespace,workspace_namespace=catalog,entity_volume_id=volume,packed_manifest_key=key),cell['client_settings'])
        point=a.asset/'mnt';point.mkdir()
        write_new(a.asset/'asset.json',dict(product='brewfs',arm='packed',bucket=bucket,database=a.database,
                  seed=plan['config']['seed'],config=config,mount_point=str(point),plan=ref(a.plan),
                  producer=ref(a.out/'producer/command.json'),manifest_key=key,source='Complete immutable C3 packed base'))
        helper('draft-mount','draft',cell=cell['id'],out=a.out/'mount')
        helper('start','mount-start',join_slot=True,spec=a.out/'mount/mount.reviewed.json',out=a.mount_state);active=True
        run('full-base-check',['/srv/ax-client/assets/bin/smallfiles','verify-base','--root',point,
                             '--output',a.out/'base-check','--files',str(files)])
        checked=load(a.out/'base-check/result.json')
        if checked.get('passed') is not True or checked.get('files')!=files or checked.get('bytes_checked')!=logical_bytes:
            raise ValueError('Complete packed base check failed')
        helper('stop','normal-stop',mount_run=a.mount_state,out=a.out/'normal-stop.json');active=False
        write_new(a.out/'result.json',dict(passed=True,cell_id=cell['id'],asset=ref(a.asset/'asset.json'),source=ref(a.source_proof),
            full_base_check=ref(a.out/'base-check/result.json'),normal_exit=ref(a.out/'normal-stop.json'),
            scope='Complete immutable packed base verified once; no upper reopening, original-namespace persistence or performance claim'))
    except Exception as error:
        failure=error
        write_new(a.out/'failed.json',dict(passed=False,error=str(error),mount_started=active,automatic_retry=False))
    finally:
        if failure and active:helper('stop','failure-normal-stop',mount_run=a.mount_state,out=a.out/'failure-normal-stop.json')
    if failure:raise failure


if __name__=='__main__':main()
