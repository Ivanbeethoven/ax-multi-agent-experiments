"""Standard-library-only SSH probe; reads resources and hashes, changes nothing."""
import hashlib
import os
from pathlib import Path
import platform
import shutil
import stat
import sys


def limits(group=None):
    root = Path('/sys/fs/cgroup')
    relative = next((x[3:] for x in Path('/proc/self/cgroup').read_text().splitlines() if x.startswith('0::')), None)
    if relative is None:
        return {'version': 'not-v2', 'observed': []}
    current = Path(group) if group else root / relative.lstrip('/')
    if not group and not (current/'cpu.max').exists():
        current = root
    current = current.resolve(strict=True)
    if not current.is_relative_to(root):
        raise ValueError('cgroup outside cgroupfs')
    rows = []
    while current.is_relative_to(root):
        row = {'path': str(current)}
        for key in ('cpu.max', 'cpuset.cpus.effective', 'memory.max', 'memory.swap.max', 'pids.max'):
            if (current/key).is_file(): row[key] = (current/key).read_text().strip()
        rows.append(row)
        if current == root: break
        current = current.parent
    return {'version': 2, 'observed': rows,
            'scope': 'visible limits; host qualification checks hidden ancestors and shared capacity'}


def inspect(request):
    if platform.system() != 'Linux' or sys.version_info < (3, 11):
        raise ValueError('Linux and Python >=3.11 required')
    repo = Path(request['repo']).resolve(strict=True)
    pins = []; mismatches = []
    for relative, expected in request['runtime_files'].items():
        path = repo/relative
        if path.is_symlink() or not path.resolve().is_relative_to(repo):
            raise ValueError('runtime file outside source tree')
        if not path.is_file():
            mismatches.append(relative); continue
        with path.open('rb') as stream: actual = hashlib.file_digest(stream, 'sha256').hexdigest()
        pins.append({'path': str(path), 'sha256': actual})
        if actual != expected: mismatches.append(relative)
    python = Path(sys.executable).resolve(strict=True)
    with python.open('rb') as stream:
        pins.append({'path': str(python), 'sha256': hashlib.file_digest(stream, 'sha256').hexdigest()})
    checks = {'runtime_matches': not mismatches}
    work = Path(request['work_root'])
    checks['work_root'] = work.is_dir() and work.resolve() == work and os.access(work, os.W_OK | os.X_OK)
    paths = []
    for d in request.get('data_paths', []):
        path = Path(d['path'])
        if not path.is_dir() or path.resolve() != path:
            paths.append({'path': str(path), 'passed': False}); continue
        v = os.statvfs(path)
        free, inodes = v.f_bavail*v.f_frsize, v.f_favail
        paths.append({'path': str(path), 'free_bytes': free, 'free_inodes': inodes,
                      'device': path.stat().st_dev,
                      'passed': free >= d['min_free_bytes'] and inodes >= d['min_free_inodes']})
    checks['data_capacity'] = all(d['passed'] for d in paths)
    fuse = Path('/dev/fuse')
    fuse_ok = fuse.exists() and stat.S_ISCHR(fuse.stat().st_mode) and os.access(fuse, os.R_OK | os.W_OK)
    status = dict(line.split(':', 1) for line in Path('/proc/self/status').read_text().splitlines() if ':' in line)
    caps = int(status['CapEff'].strip(), 16)
    helper = shutil.which('fusermount3')
    if request.get('requires_fuse'):
        checks['fuse_device'] = fuse_ok
        checks['mount_route'] = bool(caps & (1 << 21)) or bool(helper and Path(helper).stat().st_mode & stat.S_ISUID)
    cgroups = limits(request.get('cgroup'))
    if request.get('executor'): checks['cgroup_v2'] = cgroups['version'] == 2
    namespaces = {key: os.readlink('/proc/self/ns/'+key) for key in ('mnt','pid','user','net','cgroup')}
    return {'passed': all(checks.values()), 'checks': checks, 'runtime_mismatches': mismatches,
            'pins': pins, 'python': str(python), 'kernel': platform.release(),
            'boot_id': Path('/proc/sys/kernel/random/boot_id').read_text().strip(),
            'namespaces': namespaces, 'affinity_cpu_count': len(os.sched_getaffinity(0)),
            'cgroups': cgroups, 'data': paths, 'fuse_device': fuse_ok, 'cap_sys_admin': bool(caps & (1 << 21)),
            'fusermount3': helper, 'read_only': True,
            'pending': ['native mount and content checks', 'effective Agent budget',
                        'shared host capacity and telemetry', 'backend network', 'observer overhead'],
            'scope': 'SSH capability probe; native FUSE and performance qualification are separate steps'}
