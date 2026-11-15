#!/usr/bin/env python3
"""Inspect, format NEW owned data disks, or install a role's dependencies.

Run as root on the exact ECS host named in an actual deployment spec. Formatting
is a separate explicit step and refuses signatures, children, holders or mounts.
It does not format a system disk, infer a disk by /dev/vdb, or delete anything.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import socket
import subprocess
from urllib.request import Request, build_opener, ProxyHandler

PACKAGES = {
    'coordinator': ['python3','python3-venv','python3-yaml','python3-boto3','sqlite3','pigz','rsync','git','squid','docker.io','docker-compose-v2'],
    'client': ['python3','python3-yaml','python3-boto3','fio','fuse3','libfuse3-3','libssl3t64','redis-tools','rsync','curl','jq'],
    'target': ['python3','python3-yaml','python3-boto3','fio','fuse3','libfuse3-3','libssl3t64','redis-tools','rsync','curl','jq'],
    'metadata': ['python3','redis-server','redis-tools','rsync','curl'],
    'objects': ['python3','python3-yaml','python3-boto3','python3-msgpack','python3-xxhash','docker.io','docker-compose-v2','rsync','curl','jq'],
}


def capture(argv):
    return subprocess.check_output(argv, text=True).strip()


def instance_id():
    opener = build_opener(ProxyHandler({}))
    # ECS metadata v2, with no credential/instance-role retrieval.
    with opener.open(Request('http://100.100.100.200/latest/api/token', method='PUT',
            headers={'X-aliyun-ecs-metadata-token-ttl-seconds':'60'}), timeout=3) as response:
        token = response.read().decode()
    with opener.open(Request('http://100.100.100.200/latest/meta-data/instance-id',
            headers={'X-aliyun-ecs-metadata-token':token}), timeout=3) as response:
        return response.read().decode().strip()


def flatten(devices):
    for item in devices:
        yield item
        yield from flatten(item.get('children', []))


def select_disks(spec, devices):
    """Pure validation used before any format, independent of kernel device order."""
    disks = [d for d in flatten(devices) if d['type']=='disk']
    selected = []
    if not spec.get('created_for_campaign') or not re.fullmatch(r'ax-pilot-[0-9]{8}(-[a-z0-9]+)?', spec['campaign']):
        raise ValueError('Only explicitly owned new campaign disks may be initialized')
    for expected in spec['data_disks']:
        if not expected['id'].startswith('d-') or not expected['serial'] or expected['serial']==spec['system_disk_serial']:
            raise ValueError('Actual new data disk ID/serial required; never system disk')
        found = [d for d in disks if (d.get('serial') or '').strip()==expected['serial']]
        if len(found)!=1:
            raise ValueError('Exact API disk serial not uniquely present: '+expected['id'])
        disk = found[0]
        if int(disk['size']) != expected['gib']*1024**3 or disk.get('children') or disk.get('fstype') or any(disk.get('mountpoints') or []):
            raise ValueError('Wrong size or already initialized data disk: '+expected['id'])
        path = Path(expected['mount'])
        if not str(path).startswith('/srv/ax-') or '..' in path.parts or path.is_symlink():
            raise ValueError('Dedicated absolute data mount required')
        selected.append((expected,disk))
    if len({d['path'] for _,d in selected})!=len(selected) or len({e['mount'] for e,_ in selected})!=len(selected):
        raise ValueError('Duplicate disk or mount')
    mounts=[Path(e['mount']) for e,_ in selected]
    if any(left in right.parents or right in left.parents for i,left in enumerate(mounts) for right in mounts[i+1:]):
        raise ValueError('Overlapping data mounts')
    return selected


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('action',choices=['inspect','format','packages'])
    p.add_argument('--spec',type=Path,required=True)
    p.add_argument('--out',type=Path,required=True)
    p.add_argument('--apt-proxy',help='Private jump HTTP proxy, no credentials')
    a=p.parse_args()
    if os.geteuid()!=0: raise PermissionError('root required on owned ECS node')
    raw=a.spec.read_bytes(); spec=json.loads(raw)
    if spec['role'] not in PACKAGES or instance_id()!=spec['instance_id'] or socket.gethostname()!=spec['hostname']:
        raise ValueError('Deployment spec does not name this exact ECS node')
    a.out.mkdir(parents=True,exist_ok=False)
    (a.out/'spec.json').write_bytes(raw)
    def run(argv,name,**kw):
        with (a.out/(name+'.log')).open('xb') as log:
            subprocess.run(argv,stdout=log,stderr=subprocess.STDOUT,check=True,**kw)
    lsblk=json.loads(capture(['lsblk','--json','--bytes','--paths','--output','PATH,KNAME,TYPE,SERIAL,SIZE,FSTYPE,MOUNTPOINTS,PKNAME']))
    (a.out/'lsblk-before.json').write_text(json.dumps(lsblk,indent=2)+'\n')
    (a.out/'mountinfo-before.txt').write_text(Path('/proc/self/mountinfo').read_text())
    if a.action=='format':
        selected=select_disks(spec,lsblk['blockdevices'])
        root=json.loads(capture(['findmnt','--json','--target','/','--output','SOURCE,MAJ:MIN']))['filesystems'][0]
        ancestors=json.loads(capture(['lsblk','--inverse','--json','--paths','--output','PATH','/dev/block/'+root['maj:min']]))
        root_paths={d['path'] for d in flatten(ancestors['blockdevices'])}
        # Check ALL disks before formatting the first one.
        for expected,disk in selected:
            if disk['path'] in root_paths: raise ValueError('data candidate backs root filesystem')
            if list((Path('/sys/class/block')/Path(disk['kname']).name/'holders').iterdir()):
                raise ValueError('data disk has holders')
            signatures=json.loads(capture(['wipefs','--no-act','--json',disk['path']]))
            if signatures.get('signatures'): raise ValueError('existing disk signature; preserve it')
            mount=Path(expected['mount'])
            if mount.exists() and (not mount.is_dir() or any(mount.iterdir()) or os.path.ismount(mount)):
                raise ValueError('data mount is not an empty unmounted directory')
        for index,(expected,disk) in enumerate(selected):
            run(['mkfs.ext4','-i','4096','-m','0','-L',f'ax-{spec["role"]}-{index}',disk['path']],f'mkfs-{index}')
            mount=Path(expected['mount']); mount.mkdir(parents=True,exist_ok=True)
            run(['mount','-o','noatime',disk['path'],str(mount)],f'mount-{index}')
            expected['filesystem_uuid']=capture(['blkid','-s','UUID','-o','value',disk['path']])
        (a.out/'formatted-disks.json').write_text(json.dumps(spec['data_disks'],indent=2)+'\n')
        # Mounts are deliberately not made persistent; this pilot has no reboot/replay.
    elif a.action=='packages':
        options=[]
        if a.apt_proxy:
            if not re.fullmatch(r'http://10\.20\.0\.10:[0-9]{4,5}',a.apt_proxy):
                raise ValueError('Only this pilot private jump proxy is permitted')
            options=['-o','Acquire::http::Proxy='+a.apt_proxy,'-o','Acquire::https::Proxy='+a.apt_proxy]
        env=dict(os.environ,DEBIAN_FRONTEND='noninteractive')
        run(['apt-get',*options,'update'],'apt-update',env=env)
        run(['apt-get',*options,'install','--no-install-recommends','-y',*PACKAGES[spec['role']]],'apt-install',env=env)
        (a.out/'package-versions.txt').write_text(capture(['dpkg-query','-W',*PACKAGES[spec['role']]])+'\n')
    report=dict(at=datetime.now(timezone.utc).isoformat(),action=a.action,instance_id=spec['instance_id'],
        spec_sha256=hashlib.sha256(raw).hexdigest(),passed=True,
        kernel=capture(['uname','-a']),boot_id=Path('/proc/sys/kernel/random/boot_id').read_text().strip())
    (a.out/'result.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps(report))


if __name__=='__main__': main()
