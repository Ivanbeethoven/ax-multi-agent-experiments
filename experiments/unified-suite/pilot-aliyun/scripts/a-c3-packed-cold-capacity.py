#!/usr/bin/env python3
"""Inspect only the four original A objects disks with the service stopped."""
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import socket
import stat
import subprocess
import tempfile


INSTANCE = 'i-bp1h9kaoya0predubdkn'
HOSTNAME = 'ax-pilot-20261111-objects'
SERIALS = tuple('bp1h9kaoya0predx6kv' + suffix for suffix in 'defg')
DEVICE_BYTES = 60 * 1024**3
HISTORICAL_SHA256 = '24d56451e8aed8d99c2bedc98fd7e8e7c359d4052206101612a1c69b1046eba5'
HISTORICAL = ((1147904000, 14270), (351277056, 8929), (351272960, 8929), (351277056, 8929))


def require(ok, reason):
    if not ok:
        raise ValueError(reason)


def command(*argv):
    return subprocess.check_output(argv, text=True, timeout=15).strip()


def utc():
    return datetime.now(timezone.utc).isoformat()


def select_disks(rows, mounted_devices):
    result = []
    for serial in SERIALS:
        matches = [row for row in rows if (row.get('serial') or '').strip() == serial]
        require(len(matches) == 1, 'Missing or ambiguous original serial: ' + serial)
        row = matches[0]
        path, device, uuid = row['path'], row['maj:min'], row.get('uuid')
        require(row['type'] == 'disk' and row['size'] == DEVICE_BYTES
                and path.startswith('/dev/') and uuid and row['fstype'] == 'ext4'
                and not row.get('children') and not any(row.get('mountpoints') or [])
                and device not in mounted_devices, 'Expected unmounted whole original ext4 disk: ' + serial)
        result.append(row)
    for field in ('path', 'maj:min', 'uuid'):
        require(len({row[field] for row in result}) == len(SERIALS), 'Repeated disk ' + field)
    return result


def diagnostic(index, totals, available):
    values = {}
    for count, name in ((7, 'seven_more'), (8, 'new_window_plus_seven_more')):
        quantities = {}
        for key, total, free, growth in zip(('bytes', 'inodes'), totals, available, HISTORICAL[index]):
            reserve = (total + 4) // 5
            minimum = reserve + count * growth
            quantities[key] = dict(reserve=reserve, historical_increment=growth, minimum=minimum, margin=free-minimum)
        values[name] = quantities
    return values


def disk_snapshot(index, row):
    path = row['path']
    device = os.stat(path)
    majmin = f'{os.major(device.st_rdev)}:{os.minor(device.st_rdev)}'
    require(stat.S_ISBLK(device.st_mode) and majmin == row['maj:min'], 'Unexpected kernel block device: ' + path)
    with tempfile.TemporaryDirectory(prefix='ax-a-c3-statvfs-', dir='/run') as name:
        command('mount', '-t', 'ext4', '-o', 'ro,noload,nodev,nosuid,noexec', path, name)
        try:
            current = json.loads(command('findmnt', '--json', '--mountpoint', name, '--output',
                                         'SOURCE,MAJ:MIN,FSTYPE,UUID,OPTIONS'))['filesystems']
            require(len(current) == 1, 'Expected one private diagnostic mount')
            mount = current[0]
            options = set(mount['options'].split(','))
            require(mount['source'] == path and mount['maj:min'] == majmin and mount['uuid'] == row['uuid']
                    and mount['fstype'] == 'ext4' and {'ro', 'nodev', 'nosuid', 'noexec'} <= options
                    and bool({'norecovery', 'noload'} & options) and Path(name).stat().st_dev == device.st_rdev,
                    'Diagnostic mount is not the original read-only ext4 with journal replay disabled')
            usage = os.statvfs(name)
        finally:
            command('umount', name)
            require(not Path(name).is_mount(), 'Diagnostic mount remained attached')
    total = (usage.f_blocks * usage.f_frsize, usage.f_files)
    available = (usage.f_bavail * usage.f_frsize, usage.f_favail)
    return dict(index=index, serial=SERIALS[index], disk_id='d-' + SERIALS[index], source=path, device=majmin,
                uuid=row['uuid'], options=mount['options'], total_bytes=total[0], available_bytes=available[0],
                total_inodes=total[1], available_inodes=available[1], advisory=diagnostic(index, total, available))


def main():
    require(os.geteuid() == 0 and socket.gethostname() == HOSTNAME, 'Expected exact A objects host as root')
    require(os.readlink('/proc/self/ns/mnt') != os.readlink('/proc/1/ns/mnt'),
            'Run under unshare --mount --propagation private')
    require(command('findmnt', '-n', '--target', '/run', '--output', 'PROPAGATION') == 'private',
            'Diagnostic mount parent must have private propagation')
    require(not command('docker', 'ps', '-q'), 'All Docker services must remain stopped')
    started = utc()
    boot = Path('/proc/sys/kernel/random/boot_id').read_text().strip()
    system_mounts = {line.split()[2] for line in Path('/proc/1/mountinfo').read_text().splitlines()}
    rows = json.loads(command('lsblk', '--json', '--bytes', '--paths', '--output',
                              'PATH,TYPE,SERIAL,SIZE,UUID,FSTYPE,MAJ:MIN,MOUNTPOINTS'))['blockdevices']
    disks = [disk_snapshot(i, row) for i, row in enumerate(select_disks(rows, system_mounts))]
    require(not command('docker', 'ps', '-q'), 'Unexpected Docker service startup during capacity check')
    print(json.dumps(dict(schema='ax-c3-a-objects-cold-capacity-v1', instance_id=INSTANCE, hostname=HOSTNAME,
                          boot_id=boot, started_at=started, finished_at=utc(), disks=disks,
                          historical_packed_capacity_sha256=HISTORICAL_SHA256,
                          all_unmounted=True, maintenance_only=True, benchmark_capacity_approved=False,
                          scope='Cold object-device statvfs; Redis, client, journal replay and workload excluded'),
                     indent=2))


if __name__ == '__main__':
    main()
