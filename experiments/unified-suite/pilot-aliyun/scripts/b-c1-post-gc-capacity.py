#!/usr/bin/env python3
"""Cold, read-only B objects disk snapshot; use unshare --mount --propagation private."""
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import socket
import subprocess
import tempfile

HOST = 'ax-pilot-20261111-b-objects'
INSTANCE = 'i-bp1bhdngko7nq3c3jrhp'
SERIALS = [f'bp1bhdngko7nq3c2i34{i}' for i in range(4)]


def require(ok, reason):
    if not ok:
        raise ValueError(reason)


def command(*argv):
    return subprocess.check_output(argv, text=True).strip()


def utc():
    return datetime.now(timezone.utc).isoformat()


def main():
    require(os.geteuid() == 0 and socket.gethostname() == HOST, 'Expected exact B objects host')
    require(os.readlink('/proc/self/ns/mnt') != os.readlink('/proc/1/ns/mnt'),
            'An isolated mount namespace is required')
    require(not command('docker', 'ps', '-q'), 'Object storage services must remain stopped')
    begun = utc()
    boot = Path('/proc/sys/kernel/random/boot_id').read_text().strip()
    rows = json.loads(command('lsblk', '--json', '--bytes', '--paths', '--output',
        'NAME,TYPE,SERIAL,SIZE,UUID,FSTYPE,MOUNTPOINTS'))['blockdevices']
    disks = []
    for serial in SERIALS:
        candidates = [row for row in rows if (row.get('serial') or '').strip() == serial]
        require(len(candidates) == 1, 'Missing or ambiguous attached disk: '+serial)
        row = candidates[0]
        require(row['type'] == 'disk' and row['size'] == 60*1024**3 and not row.get('children') and
                not any(row.get('mountpoints') or []) and row['fstype'] == 'ext4' and row['uuid'],
                'Expected an unmounted original 60 GiB ext4 data disk: '+serial)
        disks.append(row)
    require(len({d['uuid'] for d in disks}) == 4, 'Repeated filesystem UUID')
    results = []
    for index, row in enumerate(disks):
        with tempfile.TemporaryDirectory(prefix='ax-b-c1-statvfs-', dir='/run') as name:
            p = Path(name)
            command('mount', '-t', 'ext4', '-o', 'ro,noload,nodev,nosuid,noexec', row['name'], name)
            try:
                current = json.loads(command('findmnt', '--json', '--mountpoint', name,
                    '--output', 'SOURCE,UUID,OPTIONS'))['filesystems']
                require(len(current) == 1 and current[0]['source'] == row['name'] and current[0]['uuid'] == row['uuid']
                        and 'ro' in current[0]['options'].split(',') and
                        any(x in current[0]['options'].split(',') for x in ('norecovery','noload')) and
                        p.stat().st_dev == os.stat(row['name']).st_rdev, 'Unexpected read-only mount identity')
                s = os.statvfs(p)
                results.append(dict(index=index, serial=SERIALS[index], disk_id='d-'+SERIALS[index],
                    source=row['name'], uuid=row['uuid'], options=current[0]['options'],
                    total_bytes=s.f_blocks*s.f_frsize, available_bytes=s.f_bavail*s.f_frsize,
                    total_inodes=s.f_files, available_inodes=s.f_favail))
            finally:
                command('umount', name)
                require(not p.is_mount(), 'Diagnostic mount remained attached')
    require(not command('docker', 'ps', '-q'), 'Unexpected service startup while checking capacity')
    print(json.dumps(dict(schema='ax-c1-b-cold-capacity-v1',instance_id=INSTANCE, hostname=HOST, boot_id=boot,
        started_at=begun, finished_at=utc(), disks=results, all_unmounted=True,
        maintenance_only=True, benchmark_capacity_approved=False,
        scope='Cold native statvfs only; no service start, journal replay, deletes, or workload'), indent=2))


if __name__ == '__main__':
    main()
