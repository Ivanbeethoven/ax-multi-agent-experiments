#!/usr/bin/env python3
"""Verify or remount only the existing AX coordinator disk on its jump host."""
import argparse
import json
import os
from pathlib import Path
import re
import socket
import subprocess


POINT = Path('/srv/ax-pilot-control')
SERIAL = 'bp166nue4ac1q7e5oar2'
LABEL = 'ax-coordinator-0'


def capture(*args):
    return subprocess.check_output(args, text=True, timeout=15).strip()


def need(ok, reason):
    if not ok:
        raise ValueError(reason)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--mount', action='store_true', help='Remount the exact existing disk if needed')
    args = parser.parse_args()
    need(os.geteuid()==0 and socket.gethostname()=='ax-c1-jump', 'Expected the existing AX coordinator')
    rows=json.loads(capture('lsblk','--json','--bytes','--paths','--output',
                            'PATH,TYPE,SERIAL,SIZE,FSTYPE,LABEL,UUID,MAJ:MIN,MOUNTPOINTS'))['blockdevices']
    owned=[row for row in rows if (row.get('serial') or '').strip()==SERIAL]
    need(len(owned)==1, 'Existing coordinator disk serial is absent or ambiguous')
    [disk]=owned
    need(disk['type']=='disk' and disk['size']==100*1024**3 and disk['fstype']=='ext4'
         and disk['label']==LABEL and not disk.get('children')
         and re.fullmatch(r'[0-9a-f-]{36}',disk['uuid'] or ''),
         'Existing coordinator device, filesystem or label differs')
    root=capture('findmnt','-n','-o','MAJ:MIN','--target','/')
    need(root!=disk['maj:min'] and POINT.resolve()==POINT and POINT.is_dir(),
         'Existing coordinator path or device is unsafe')
    mounts=[path for path in disk['mountpoints'] if path]
    mounted_before=POINT.is_mount()
    if not mounted_before:
        need(args.mount and not mounts and not any(POINT.iterdir()),
             'Need explicit remount and an empty coordinator directory')
        subprocess.run(['mount','-t','ext4','-o','noatime',disk['path'],str(POINT)],check=True,timeout=35)
    actual=capture('findmnt','-n','-o','MAJ:MIN,FSTYPE','--mountpoint',str(POINT)).split()
    need(actual==[disk['maj:min'],'ext4'] and (not mounted_before or mounts==[str(POINT)]),
         'Mounted coordinator path differs from the exact existing disk')
    controls=sorted(p.parent.name for p in POINT.glob('*/state.sqlite') if p.is_file())
    need('parallel-control-01' in controls and len(controls)>=5 and
         (POINT/'parallel-control-01/suite/plan.json').is_file() and
         (POINT/'parallel-control-01/runner-locks').is_dir() and
         (POINT/'preparation').is_dir(), 'Original coordinator control files not present')
    print(json.dumps(dict(host='ax-c1-jump',disk='d-'+SERIAL,filesystem_uuid=disk['uuid'],
                          mount=str(POINT),mounted_before=mounted_before,control_roots=controls,passed=True)))


if __name__=='__main__':
    main()
