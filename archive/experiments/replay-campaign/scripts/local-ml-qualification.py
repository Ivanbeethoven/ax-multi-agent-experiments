#!/usr/bin/env python3
"""Owned local ML path qualification; simulated EC slots, never cloud perf."""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import secrets
import socket
import subprocess
import sys
import time
import urllib.request

AX=Path(__file__).resolve().parents[3]
sys.path.insert(0,str(AX/'bench'))
sys.path.insert(0, str(AX/'bench/reference'))
from aliyun_suite import digest, save, load
from ml_readonly_matrix import run
from ml_readonly_end_to_end import run as run_p2

SOURCE='a87e8f94a50b9fb6e3da74fed45fe37d4cb969ed'
CLIENT='80ddaf739e163c8933b0bb84390c4aa559bd2b40'


def docker(*args):
    return subprocess.check_output(['docker',*args],text=True).strip()


def main(a):
    if os.geteuid()!=0:
        raise RuntimeError('run qualification as uid 0; fresh BrewFS root inodes are owned by root')
    if subprocess.run(['pgrep','-x','rustc'],stdout=subprocess.DEVNULL).returncode==0:
        raise RuntimeError('compilation must finish before qualification')
    for port in range(19930,19936):
        with socket.socket() as sock:sock.bind(('127.0.0.1',port))
    a.output.mkdir(parents=True,exist_ok=False)
    token=secrets.token_hex(6);prefix='ax-ml-'+token
    record={'scope':'local qualification, four directories on one filesystem; no physical-disk/performance claim',
            'containers':[],'status':'running'}
    images={}
    for key,tag in [('rustfs','ax-retest/rustfs:a87e8f94'),
                    ('pd','pingcap/pd@sha256:5f6dfb1645769bebc5c0e983cf46842709cfe2fb910d91fd039e68810a09d555'),
                    ('tikv','pingcap/tikv@sha256:7f607b81bc623ed4336ac551d8174abe17329a9d260da2178fff1689ffc7f68f')]:
        identity=json.loads(docker('image','inspect',tag))[0]
        if key=='rustfs' and identity['Config']['Labels'].get('org.opencontainers.image.revision')!=SOURCE:
            raise ValueError('wrong RustFS source')
        candidates=identity.get('RepoDigests',[])
        if not candidates:raise ValueError('image has no immutable digest: '+key)
        images[key]={'reference':candidates[0],'id':identity['Id']}
        if key!='rustfs':
            images[key]['binary_version']=docker('run','--rm','--network','none','--memory','256m',candidates[0],'--version')
    record['images']=images;save(a.output/'runtime.json',record)
    remote=subprocess.check_output(['git','ls-remote','https://github.com/Ivanbeethoven/rustfs',
                    'refs/heads/feat/dynamic-block-size-rebased'],text=True).split()
    if not remote or remote[0]!=SOURCE:raise ValueError('RustFS branch changed; freeze a new batch')
    pin={'repository':'https://github.com/Ivanbeethoven/rustfs','branch':'feat/dynamic-block-size-rebased',
         'frozen_commit':SOURCE,'built_from_commit':SOURCE,'image_reference':images['rustfs']['reference'],
         'image_digest':images['rustfs']['reference'].split('@')[1],
         'brewfs_client_sha':CLIENT,'resolved_at':datetime.now(timezone.utc).isoformat(),
         'resolved_by':'git ls-remote plus runtime OCI revision label; retained image config equivalence receipt'}
    save(a.output/'backend-pin.json',pin)
    access=secrets.token_hex(12);secret=secrets.token_hex(24)
    envfile=a.output/'backend.env'
    with envfile.open('x') as stream:
        os.chmod(envfile,0o600)
        stream.write(f'RUSTFS_ACCESS_KEY={access}\nRUSTFS_SECRET_KEY={secret}\n')
        stream.write('RUSTFS_VOLUMES=/data/disk{1...4}\nRUSTFS_ADDRESS=127.0.0.1:19930\nRUSTFS_CONSOLE_ENABLE=false\nRUSTFS_UNSAFE_BYPASS_DISK_CHECK=true\nRUSTFS_OBS_LOG_DIRECTORY=/data/logs\n')
        # Isolate metadata placement: default layout in this P group. D group
        # separately qualifies dynamic layout; no unqualified producer hint.
        for flag in ('RUSTFS_EC_BLOCK_SIZE_HINT_ENABLE','RUSTFS_EC_BLOCK_SIZE_HINT_FLEET_CONFIRMED',
                     'RUSTFS_EC_READ_QUANTUM_ENABLE','RUSTFS_EC_READ_QUANTUM_FLEET_CONFIRMED'):
            stream.write(flag+'=false\n')
    def start(kind,extra,args,memory):
        directory=a.output/(kind+'-data');directory.mkdir(mode=0o777);directory.chmod(0o777)
        if kind=='rustfs':
            for n in range(1,5):(directory/f'disk{n}').mkdir(mode=0o777);(directory/f'disk{n}').chmod(0o777)
        name=prefix+'-'+kind
        cid=docker('run','-d','--name',name,'--label','ax.owner='+token,'--network','host',
                   '--memory',memory,'--cpus','2','-v',str(directory)+':/data',*extra,images[kind]['reference'],*args)
        record['containers'].append({'name':name,'id':cid,'image':images[kind]['id'],'removed':False})
        save(a.output/'runtime.json',record)
        return name
    old={k:os.environ.get(k) for k in ('AWS_ACCESS_KEY_ID','AWS_SECRET_ACCESS_KEY')}
    try:
        rustfs=start('rustfs',['--env-file',str(envfile)],[],'3g')
        start('pd',[],['--name=pd','--data-dir=/data/pd','--client-urls=http://127.0.0.1:19932',
              '--advertise-client-urls=http://127.0.0.1:19932','--peer-urls=http://127.0.0.1:19933',
              '--advertise-peer-urls=http://127.0.0.1:19933','--initial-cluster=pd=http://127.0.0.1:19933',
              '--log-level=warn'],'512m')
        start('tikv',['--ulimit','nofile=1048576:1048576'],['--addr=127.0.0.1:19934',
              '--advertise-addr=127.0.0.1:19934','--status-addr=127.0.0.1:19935','--pd=127.0.0.1:19932',
              '--data-dir=/data/tikv','--log-level=warn'],'3g')
        opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
        for _ in range(180):
            try:
                with opener.open('http://127.0.0.1:19932/pd/api/v1/stores',timeout=3) as response:
                    value=json.load(response)
                if any(s.get('store',{}).get('state_name')=='Up' for s in value.get('stores',[])):break
            except Exception:pass
            time.sleep(1)
        else:raise RuntimeError('owned TiKV did not become ready')
        os.environ.update(AWS_ACCESS_KEY_ID=access,AWS_SECRET_ACCESS_KEY=secret)
        import boto3
        from botocore.config import Config
        client=boto3.client('s3',endpoint_url='http://127.0.0.1:19930',region_name='us-east-1',
                           config=Config(connect_timeout=2,read_timeout=3,retries={'max_attempts':0}))
        for _ in range(60):
            try:client.list_buckets();break
            except Exception:time.sleep(1)
        else:raise RuntimeError('owned RustFS did not become ready')
        client.close()
        spec={'scope':'local-qualification','source':str(a.source),'manifest':str(a.manifest),
              'inventory_receipt':str(a.inventory),'files':load(a.inventory)['files'],
              'metadata_cache_bytes':67108864,'workers':a.workers,'repeats':a.repeats,
              'decode_workspace_bytes':a.decode_workspace_mib*1024*1024,
              'endpoint':'http://127.0.0.1:19930','tikv_pd':'127.0.0.1:19932',
              'rustfs_data_root':str(a.output/'rustfs-data'),
              'backend_pin':str(a.output/'backend-pin.json'),'rustfs_container':rustfs}
        for name in ('brewfs','fixture','scanner'):
            path=getattr(a,name).resolve(strict=True)
            spec[name]={'path':str(path),'sha256':digest(path)}
            if name!='scanner':spec[name]['source_sha']=CLIENT
        (run_p2 if a.group=='P2' else run)(spec,a.output/'matrix');record['status']='qualified'
    except Exception as error:
        record.update(status='failed',error=type(error).__name__+': '+str(error));raise
    finally:
        for row in reversed(record['containers']):
            # Refuse to touch a container whose identity/ownership changed.
            identity=json.loads(docker('inspect',row['id']))[0]
            if identity['Config']['Labels'].get('ax.owner')!=token or identity['Image']!=row['image']:
                raise RuntimeError('container identity changed; preserving resources')
            log=subprocess.run(['docker','logs',row['id']],capture_output=True,text=True)
            (a.output/(row['name']+'.log')).write_text((log.stdout+log.stderr).replace(access,'[redacted]').replace(secret,'[redacted]'))
            docker('stop',row['id']);docker('rm',row['id']);row['removed']=True
            save(a.output/'runtime.json',record)
        envfile.unlink()
        for key,value in old.items():
            if value is None:os.environ.pop(key,None)
            else:os.environ[key]=value
        save(a.output/'runtime.json',record)
    print(json.dumps({'status':record['status'],'all_owned_containers_removed':all(r['removed'] for r in record['containers'])}))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('source','manifest','inventory','brewfs','fixture','scanner','output'):
        p.add_argument('--'+name,type=Path,required=True)
    p.add_argument('--workers',type=int,nargs='+',default=[1,16,64]);p.add_argument('--repeats',type=int,default=3)
    p.add_argument('--group',choices=['P1','P2'],default='P1')
    p.add_argument('--decode-workspace-mib',type=int,choices=[8,32],default=32,
                   help='32 MiB explicit qualification budget; original 8 MiB failure retained separately')
    main(p.parse_args())
