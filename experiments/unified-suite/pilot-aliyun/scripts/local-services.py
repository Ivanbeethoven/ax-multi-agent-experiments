#!/usr/bin/env python3
"""Explicit local functional services; four directories share ONE physical disk.

No hardware/performance qualification and no cloud API. Use only after the
RustFS build and image finish; retain identities, configuration, and logs.
"""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import secrets
import subprocess

REDIS='redis@sha256:63218503f6461303e502355b8b0359a1194a190341f954e7fc7e8f92cd382a43'


def capture(argv):return subprocess.check_output(argv,text=True).strip()


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('action',choices=['start','stop'])
    p.add_argument('--build-root',type=Path,required=True)
    p.add_argument('--image-receipt',type=Path)
    p.add_argument('--name',required=True)
    p.add_argument('--out',type=Path,required=True)
    p.add_argument('--closure',type=Path)
    p.add_argument('--allow-local-shared-disk',action='store_true',
        help='Enable RustFS explicit CI-only shared-disk bypass; never a cloud hardware qualification')
    a=p.parse_args()
    if not re.fullmatch(r'ax-pilot-[a-z0-9-]+',a.name):raise ValueError('dedicated local service name required')
    names=[a.name+'-redis',a.name+'-rustfs']
    if a.action=='stop':
        closure=json.loads(a.closure.read_text())
        if not all(closure.get(k) is True for k in ('workloads_stopped','mounts_absent')):
            raise ValueError('close owned local workloads/mounts before services')
        for name in names:
            row=json.loads(capture(['docker','inspect',name]))[0]
            if row['Config']['Labels'].get('ax.pilot.owner')!=a.name:raise ValueError('container owner mismatch')
        for name in names:
            subprocess.run(['docker','stop','--timeout','-1',name],check=True)
            (a.out/(name+'.log')).write_text(subprocess.check_output(['docker','logs',name],stderr=subprocess.STDOUT,text=True))
        (a.out/'stopped.json').write_text(json.dumps(dict(at=datetime.now(timezone.utc).isoformat(),containers=names,passed=True))+'\n')
        return
    for component in ['rustfs']:
        attempt=Path((a.build_root/'receipts'/(component+'.last-attempt')).read_text().strip())
        if json.loads((attempt/'attestation-finished.json').read_text()).get('passed') is not True:
            raise ValueError('finish RustFS build before starting local storage services')
    image=json.loads(a.image_receipt.read_text())
    for name in names:
        if subprocess.run(['docker','inspect',name],capture_output=True).returncode==0:
            raise ValueError('preserve existing container: '+name)
    a.out.mkdir(parents=True,exist_ok=False);a.out.chmod(0o700)
    data=[a.out/f'volume-{i}' for i in range(4)];logs=a.out/'rustfs-logs'
    for directory in [*data,logs]:directory.mkdir()
    subprocess.run(['sudo','chown','10001:10001',*[str(p) for p in [*data,logs]]],check=True)
    redis_data=a.out/'redis-data';redis_data.mkdir()
    secret=a.out/'rustfs.env'
    secret.write_text('RUSTFS_ACCESS_KEY='+secrets.token_hex(12)+'\nRUSTFS_SECRET_KEY='+secrets.token_hex(24)+'\n')
    secret.chmod(0o600)
    label=['--label','ax.pilot.owner='+a.name]
    common=['docker','run','--pull','never','-d',*label]
    redis_argv=[*common,'--name',names[0],'-p','127.0.0.1:16379:6379','--memory','512m','--cpus','1',
        '-v',str(redis_data)+':/data',REDIS,'redis-server','--appendonly','yes','--appendfsync','always','--save','']
    rustfs_argv=[*common,'--name',names[1],'-p','127.0.0.1:19000:9000','--memory','4g','--cpus','2','--env-file',str(secret)]
    config=dict(RUSTFS_ADDRESS='0.0.0.0:9000',RUSTFS_CONSOLE_ENABLE='false',RUSTFS_VOLUMES='/data/rustfs{0...3}',
        RUSTFS_OBS_LOGGER_LEVEL='warn',RUSTFS_OBS_LOG_DIRECTORY='/var/log/rustfs',
        RUSTFS_OBS_METRIC_ENDPOINT='http://127.0.0.1:4318/v1/metrics',
        RUSTFS_OBS_METRICS_EXPORT_ENABLED='true',RUSTFS_OBS_METER_INTERVAL='1',
        RUSTFS_OBS_TRACES_EXPORT_ENABLED='false',RUSTFS_OBS_LOGS_EXPORT_ENABLED='false',
        RUSTFS_OBS_PROFILING_EXPORT_ENABLED='false',RUSTFS_OBS_USE_STDOUT='false',
        OTEL_RESOURCE_ATTRIBUTES='rustfs.cluster.id='+a.name,
        RUSTFS_EC_BLOCK_SIZE_HINT_ENABLE='true',RUSTFS_EC_BLOCK_SIZE_HINT_FLEET_CONFIRMED='true',
        RUSTFS_EC_READ_QUANTUM_ENABLE='true',RUSTFS_EC_READ_QUANTUM_FLEET_CONFIRMED='true')
    if a.allow_local_shared_disk:
        config['RUSTFS_UNSAFE_BYPASS_DISK_CHECK']='true'
    for k,v in config.items():rustfs_argv+=['-e',k+'='+v]
    for i,directory in enumerate(data):rustfs_argv+=['-v',str(directory)+f':/data/rustfs{i}']
    rustfs_argv+=['-v',str(logs)+':/var/log/rustfs',image['image_reference']]
    started=[]
    for name,argv in zip(names,[redis_argv,rustfs_argv]):
        (a.out/(name+'.intent.json')).write_text(json.dumps(dict(argv=argv,at=datetime.now(timezone.utc).isoformat()),indent=2)+'\n')
        container=capture(argv)
        row=dict(name=name,container_id=container,argv=argv)
        (a.out/(name+'.started.json')).write_text(json.dumps(row,indent=2)+'\n');started.append(row)
    receipt=dict(at=datetime.now(timezone.utc).isoformat(),containers=started,redis_image=REDIS,
        rustfs_image=image['image_reference'],layout_flags=config,
        scope='Local API/mount/remote-state qualification ONLY; four directories on the same local device, not four independent disks',
        performance_qualified=False,cloud_instances_started=False)
    (a.out/'receipt.json').write_text(json.dumps(receipt,indent=2)+'\n')
    print(json.dumps(receipt,indent=2))


if __name__=='__main__':main()
