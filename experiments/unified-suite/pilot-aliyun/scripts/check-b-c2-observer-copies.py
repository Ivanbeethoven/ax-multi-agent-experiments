#!/usr/bin/env python3
"""Read-only byte comparison of explicit old B C2 observer copies."""
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import socket
import stat
import subprocess
import sys
import tempfile

HOST = 'ax-pilot-20261111-b-objects'
SERIAL = 'bp1bhdngko7nq3c2i340'
CAL = tuple(f'cal-pool-b-j-tr-c201-on{i}' for i in (1, 2, 3))
FORMAL = 'c2-fa630431ea12-05357b5089cd679c-r1-juicefs'
TAGS = (*CAL, FORMAL)
LIFECYCLE = {'ready.json', 'finished.json'}


def require(ok, message):
    if not ok:
        raise ValueError(message)


def safe_relative(name):
    p = PurePosixPath(name)
    require(p.parts and not p.is_absolute() and str(p) == name and '..' not in p.parts
            and not any(x in name for x in ('\\', ':', '\x00')), 'Unsafe archived member: '+name)
    return p


def expected_maps(value):
    tables = value.get('files_by_tag')
    require(value.get('schema') == 'b-c2-observer-archive-proof-v1'
            and isinstance(tables, dict) and set(tables) == set(TAGS),
            'Exact four archived B C2 observer maps required')
    for files in tables.values():
        require(isinstance(files, dict) and LIFECYCLE <= files.keys() and len(files) <= 15000
                and any(x.startswith('sample-') for x in files) and 'observer.sock' not in files,
                'Expected complete observer lifecycle and samples')
        for name, item in files.items():
            safe_relative(name)
            require(set(item) == {'bytes', 'sha256'} and type(item['bytes']) is int and item['bytes'] >= 0
                    and re.fullmatch(r'[0-9a-f]{64}', item['sha256']), 'Invalid archive file proof: '+name)
    return tables


def candidate_paths(tag):
    second = (f'retained-calibrations/pool-b-juicefs-c2-01/{tag}/raw/o/{tag}'
              if tag in CAL else f'evidence/{tag}/o/{tag}')
    return (f'o/{tag}', second)


def check_copy(root, relative, files, device, seen):
    path = root/safe_relative(relative)
    require(path.resolve(strict=True) == path and path.is_dir(), 'Noncanonical observer root: '+relative)
    expected_dirs = {str(p) for name in files for p in PurePosixPath(name).parents}
    names_seen = set()
    dir_seen = set()
    lifecycle = {}
    amount = 0
    socket_present = False

    def count(s):
        key = (s.st_dev, s.st_ino)
        require(s.st_dev == device and key not in seen, 'Unexpected device or shared inode: '+relative)
        seen.add(key)
        return s.st_blocks*512

    for directory, dirs, names, fd in os.fwalk(path, follow_symlinks=False):
        base = Path(directory).relative_to(path)
        require(base.as_posix() in expected_dirs, 'Unexpected directory: '+str(base))
        dir_seen.add(base.as_posix())
        amount += count(os.fstat(fd))
        for name in dirs:
            child = (base/name).as_posix()
            info = os.stat(name, dir_fd=fd, follow_symlinks=False)
            require(stat.S_ISDIR(info.st_mode) and info.st_dev == device and child in expected_dirs,
                    'Unexpected, linked or foreign directory: '+child)
        for name in names:
            child = (base/name).as_posix()
            info = os.stat(name, dir_fd=fd, follow_symlinks=False)
            if child == 'observer.sock' and stat.S_ISSOCK(info.st_mode) and info.st_nlink == 1:
                require(count(info) == 0, 'Unexpected allocated data for socket')
                socket_present = True
                continue
            require(child in files and stat.S_ISREG(info.st_mode) and info.st_nlink == 1
                    and info.st_size == files[child]['bytes'], 'Unexpected or changed file: '+child)
            amount += count(info)
            with os.fdopen(os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd), 'rb') as stream:
                read_stat = os.fstat(stream.fileno())
                require((read_stat.st_dev, read_stat.st_ino, read_stat.st_size) ==
                        (info.st_dev, info.st_ino, info.st_size)
                        and hashlib.file_digest(stream, 'sha256').hexdigest() == files[child]['sha256'],
                        'Archive bytes differ: '+child)
                if child in LIFECYCLE:
                    require(info.st_size <= 4*1024**2, 'Unexpected observer lifecycle record size')
                    stream.seek(0)
                    lifecycle[child] = json.load(stream)
            names_seen.add(child)
    require(names_seen == files.keys() and dir_seen == expected_dirs, 'Missing archived members: '+relative)
    ready, finished = lifecycle['ready.json'], lifecycle['finished.json']
    require(ready['passed'] is True and finished['passed'] is True and
            ready['spec']['sha256'] == finished['spec_sha256'], 'Original observer close differs: '+relative)
    return dict(path=relative, bytes_allocated=amount, files=len(names_seen), directories=len(dir_seen),
                observer_boot_id=ready['process']['boot_id'], stale_socket_not_archived=socket_present)


def command(*argv):
    return subprocess.check_output(argv, text=True, timeout=15).strip()


def main():
    require(len(sys.argv) == 2 and re.fullmatch(r'[0-9a-f]{64}', sys.argv[1]), 'Archive proof SHA required')
    raw = sys.stdin.buffer.read(12*1024**2+1)
    require(len(raw) <= 12*1024**2 and hashlib.sha256(raw).hexdigest() == sys.argv[1], 'Archive proof digest differs')
    tables = expected_maps(json.loads(raw))
    require(os.geteuid() == 0 and socket.gethostname() == HOST
            and os.readlink('/proc/self/ns/mnt') != os.readlink('/proc/1/ns/mnt'),
            'Expected B objects in a private root mount namespace')
    require(not command('docker', 'ps', '-q'), 'B object services must remain stopped')
    rows = json.loads(command('lsblk', '--json', '--bytes', '--paths', '--output',
        'PATH,TYPE,SERIAL,SIZE,UUID,FSTYPE,MAJ:MIN,MOUNTPOINTS'))['blockdevices']
    disks = [d for d in rows if (d.get('serial') or '').strip() == SERIAL]
    require(len(disks) == 1, 'Expected exactly one B data disk 0')
    row = disks[0]
    device = os.stat(row['path'])
    mount_ids = {x.split()[2] for x in Path('/proc/1/mountinfo').read_text().splitlines()}
    require(row['type'] == 'disk' and row['size'] == 60*1024**3 and row['fstype'] == 'ext4'
            and row['uuid'] and not row.get('children') and not any(row.get('mountpoints') or [])
            and row['maj:min'] not in mount_ids and stat.S_ISBLK(device.st_mode)
            and row['maj:min'] == f'{os.major(device.st_rdev)}:{os.minor(device.st_rdev)}',
            'Expected original unmounted 60 GiB ext4 disk')
    with tempfile.TemporaryDirectory(prefix='ax-b-c2-archive-compare-', dir='/run') as name:
        root = Path(name)
        command('mount','-t','ext4','-o','ro,noload,nodev,nosuid,noexec',row['path'],name)
        try:
            mounts = json.loads(command('findmnt','--json','--mountpoint',name,
                '--output','SOURCE,MAJ:MIN,UUID,OPTIONS'))['filesystems']
            require(len(mounts) == 1, 'Unexpected mount count')
            m = mounts[0]
            options = set(m['options'].split(','))
            require(m['source'] == row['path'] and m['uuid'] == row['uuid'] and m['maj:min'] == row['maj:min']
                    and {'ro','nodev','nosuid','noexec'} <= options and {'norecovery','noload'} & options
                    and root.stat().st_dev == device.st_rdev, 'Unexpected read-only mount identity')
            seen = set()
            groups = [check_copy(root, p, tables[tag], device.st_rdev, seen)
                      for tag in TAGS for p in candidate_paths(tag)]
            s = os.statvfs(root)
        finally:
            command('umount',name)
            require(not root.is_mount(), 'Diagnostic mount still attached')
    require(not command('docker', 'ps', '-q'), 'B object services unexpectedly started')
    print(json.dumps(dict(schema='b-c2-observer-copies-readback-v1', archive_proof_sha256=sys.argv[1],
        serial=SERIAL, filesystem_uuid=row['uuid'], boot_id=Path('/proc/sys/kernel/random/boot_id').read_text().strip(),
        groups=groups, checked_allocated_bytes=sum(g['bytes_allocated'] for g in groups),
        free_bytes=s.f_bavail*s.f_frsize, free_inodes=s.f_favail, all_unmounted=True,
        deletion_performed=False, cleanup_approved=False), indent=2))


if __name__ == '__main__':
    main()
