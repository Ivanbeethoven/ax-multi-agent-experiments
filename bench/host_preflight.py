#!/usr/bin/env python3
"""Read-only Linux host evidence. Never formats, mounts, installs or stops services."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
from experiment_io import load, write_new, digest
from experiment_inventory import validate


def command(argv):
    return subprocess.check_output(argv, text=True, timeout=20)


def backing_disks(tree):
    disks = set()
    def visit(items):
        for item in items:
            if item['type'] == 'disk': disks.add(item['maj:min'])
            visit(item.get('children', []))
    visit(tree['blockdevices'])
    if not disks: raise ValueError('cannot establish backing physical/virtual disk identity')
    return disks


def distinct_data_disks(root_disks, disk_sets):
    used = set(root_disks)
    for disks in disk_sets:
        if len(disks) != 1 or disks & used:
            raise ValueError('data volumes must be independent disks, excluding the OS disk')
        used |= disks


def inspect(node):
    if platform.system() != 'Linux': raise ValueError('run on the actual Linux test host')
    addresses = json.loads(command(['ip', '-j', 'addr', 'show']))
    if node['private_ip'] not in {a['local'] for n in addresses for a in n.get('addr_info', [])}:
        raise ValueError('inventory IP does not belong to this host')
    def mount(path, exact):
        opts = ['--mountpoint' if exact else '--target', path]
        fs = json.loads(command(['findmnt', '--json', *opts, '--output', 'TARGET,SOURCE,FSTYPE,OPTIONS,MAJ:MIN']))['filesystems'][0]
        tree = json.loads(command(['lsblk', '--inverse', '--json', '--paths', '--output', 'NAME,TYPE,MAJ:MIN', '/dev/block/'+fs['maj:min']]))
        return fs, backing_disks(tree)
    _, root_disks = mount('/', False)
    observations = []; disk_sets = []
    for name in node['data_mounts']:
        p = Path(name)
        if not p.is_dir() or str(p.resolve()) != name:
            raise ValueError('data mount must exist without symlinks or aliases')
        fs, disks = mount(name, True)
        if fs['fstype'] not in ('xfs', 'ext4') or 'rw' not in fs['options'].split(','):
            raise ValueError('mounted read/write XFS/ext4 data filesystem required')
        stat = os.statvfs(p); free_bytes = stat.f_bavail * stat.f_frsize
        if free_bytes < node['min_free_bytes_per_disk'] or stat.f_favail < node['min_free_inodes_per_disk']:
            raise ValueError('insufficient approved data capacity/inodes')
        observations.append({'mount': name, 'filesystem': fs, 'backing_disks': sorted(disks),
                             'free_bytes': free_bytes, 'free_inodes': stat.f_favail})
        disk_sets.append(disks)
    distinct_data_disks(root_disks, disk_sets)
    cores = len(os.sched_getaffinity(0))
    memory = int(next(x.split()[1] for x in Path('/proc/meminfo').read_text().splitlines() if x.startswith('MemTotal:'))) * 1024
    if cores < 8 or memory < 30 * 1024**3:
        raise ValueError('requires at least 8 available CPU and nominal 32 GiB RAM')
    work = Path(node['work_root'])
    if not work.is_dir() or str(work.resolve()) != str(work):
        raise ValueError('approved dedicated work root must already exist without symlinks')
    return {'node_id': node['id'], 'resource_id': node['resource_id'], 'timestamp': datetime.now(timezone.utc).isoformat(),
            'machine_id_sha256': hashlib.sha256(Path('/etc/machine-id').read_bytes()).hexdigest(),
            'hostname': platform.node(), 'kernel': platform.release(), 'cpu': json.loads(command(['lscpu', '-J'])),
            'available_cpu_count': cores, 'memory_bytes': memory, 'data': observations,
            'tools': {k: shutil.which(k) for k in ('docker','fio','fusermount3','python3','git','systemd-run')},
            'mounts': json.loads(command(['findmnt', '--json', '--output', 'TARGET,FSTYPE'])),
            'passed': True, 'scope': 'host storage/capacity evidence only; AI verifies idle state, network, quotas and artifacts separately'}


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--inventory', required=True); p.add_argument('--node', required=True); p.add_argument('--output', required=True)
    a = p.parse_args(); data = load(a.inventory); validate(data)
    node = next(n for n in data['nodes'] if n['id'] == a.node)
    try:
        result = inspect(node)
    except Exception as error:
        write_new(a.output, {'passed': False, 'error': str(error), 'inventory_sha256': digest(a.inventory)})
        raise
    result['inventory_sha256'] = digest(a.inventory); write_new(a.output, result)
