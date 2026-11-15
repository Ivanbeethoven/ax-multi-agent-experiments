"""Owned local scratch: preview first; delete only after a successful, closed smoke case.

Never stops services, deletes result directories, prunes Docker, or calls a cloud API.
"""
import argparse
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import tempfile
import uuid

from bench.experiment_io import digest, load, write_new
from bench.runtime.common import alive, identity
from bench.runtime.smoke_cleanup import mounts_below

MARKER = '.ax-scratch-owner.json'
SCHEMA = 'ax-owned-scratch-v1'


def capacity(parent):
    v = os.statvfs(parent)
    return dict(total_bytes=v.f_blocks*v.f_frsize, available_bytes=v.f_bavail*v.f_frsize,
                total_inodes=v.f_files, available_inodes=v.f_favail)


def admit(parent, peak_bytes=0, peak_inodes=0):
    if peak_bytes < 0 or peak_inodes < 0:
        raise ValueError('scratch peak estimates must not be negative')
    row = capacity(parent)
    for unit, peak in (('bytes', peak_bytes), ('inodes', peak_inodes)):
        if row['total_'+unit] <= 0 or (row['available_'+unit]-peak)*5 < row['total_'+unit]:
            raise ValueError('scratch '+unit+' would fall below the 20% reserve')
    return row


def create(directory, parent, receipt, peak_bytes=0, peak_inodes=0):
    if os.name != 'posix' or not Path('/proc/self/mountinfo').is_file():
        raise ValueError('managed scratch requires Linux')
    directory, parent, receipt = Path(directory).absolute(), Path(parent).resolve(strict=True), Path(receipt).absolute()
    if directory.resolve() != directory or parent == Path('/') or not parent.is_dir():
        raise ValueError('canonical non-root scratch parent and output directory required')
    before = admit(parent, peak_bytes, peak_inodes)
    directory.mkdir(mode=0o700)
    root = Path(tempfile.mkdtemp(prefix='ax-owned-', dir=parent))
    info = root.stat()
    owner = dict(schema=SCHEMA, token=uuid.uuid4().hex, root=str(root), parent=str(parent),
                 device=info.st_dev, inode=info.st_ino, uid=os.geteuid(),
                 creator=identity(os.getpid()), mount_namespace=os.readlink('/proc/self/ns/mnt'),
                 directory=str(directory), receipt=str(receipt), capacity_before=before,
                 peak_bytes=peak_bytes, peak_inodes=peak_inodes)
    write_new(directory/'owner.json', owner)
    write_new(root/MARKER, owner)
    return root


def footprint(root, device):
    rows, allocated, seen = [], 0, set()
    def add(path):
        nonlocal allocated
        s = path.lstat()
        if s.st_dev != device or not (stat.S_ISDIR(s.st_mode) or stat.S_ISREG(s.st_mode) or stat.S_ISLNK(s.st_mode)):
            raise ValueError('scratch contains another device or special file: '+str(path))
        rel = '.' if path == root else path.relative_to(root).as_posix()
        rows.append([rel, s.st_dev, s.st_ino, s.st_mode, s.st_size, s.st_mtime_ns,
                     os.readlink(path) if stat.S_ISLNK(s.st_mode) else None])
        if (s.st_dev, s.st_ino) not in seen:
            allocated += s.st_blocks*512
            seen.add((s.st_dev, s.st_ino))
    add(root)
    def walk_error(error):
        raise error
    for parent, dirs, files in os.walk(root, followlinks=False, onerror=walk_error):
        for name in sorted(dirs+files):
            add(Path(parent)/name)
    raw = json.dumps(sorted(rows), separators=(',', ':')).encode()
    return dict(entries=len(rows), candidate_allocated_bytes=allocated,
                metadata_sha256=hashlib.sha256(raw).hexdigest())


def closed_receipt(receipt):
    if receipt.get('status') != 'passed':
        raise ValueError('failed or interrupted case is retained')
    evidence = receipt.get('evidence')
    if not isinstance(evidence, dict) or evidence.get('passed') is not True:
        raise ValueError('native passed evidence required')
    closure = evidence.get('cleanup', evidence)
    for field in ('mounts_absent', 'rustfs_removed', 'redis_exited', 'filesystem_processes_exited'):
        if closure.get(field) is not True:
            raise ValueError('missing positive native closure: '+field)
    if closure.get('errors') or evidence.get('cleanup_error'):
        raise ValueError('native cleanup contains errors')


def preview(directory, *, _allow_current_owner=False):
    directory = Path(directory).absolute()
    plan = dict(schema='ax-scratch-cleanup-plan-v1', directory=str(directory),
                eligible=False, reasons=[], automatic_retry=False)
    try:
        if directory.resolve(strict=True) != directory or directory.is_symlink():
            raise ValueError('cleanup record path must be canonical')
        owner_path = directory/'owner.json'
        if owner_path.is_symlink():
            raise ValueError('owner record cannot be a symlink')
        owner = load(owner_path)
        if owner.get('schema') != SCHEMA or owner['directory'] != str(directory):
            raise ValueError('scratch ownership record differs')
        root, parent = Path(owner['root']), Path(owner['parent'])
        plan['root'] = str(root)
        if not root.is_absolute() or root.parent != parent or parent == Path('/') or not root.name.startswith('ax-owned-'):
            raise ValueError('scratch must be a directly owned child of its registered parent')
        # Read mountinfo before touching the tree, including a disconnected FUSE root.
        if mounts_below([root]):
            raise ValueError('scratch still contains a mount')
        if root.is_symlink() or root.resolve(strict=True) != root:
            raise ValueError('scratch root changed or was already removed')
        if directory.is_relative_to(root) or Path(owner['receipt']).is_relative_to(root):
            raise ValueError('result records must be outside scratch')
        if owner['creator']['boot_id'] != Path('/proc/sys/kernel/random/boot_id').read_text().strip():
            raise ValueError('host restarted; old closure evidence cannot authorize cleanup')
        if owner['mount_namespace'] != os.readlink('/proc/self/ns/mnt'):
            raise ValueError('mount namespace changed; cleanup requires a fresh audit')
        if alive(owner['creator']) and not (_allow_current_owner and owner['creator']['pid'] == os.getpid()):
            raise ValueError('scratch creator is still running')
        current = root.lstat()
        if (current.st_dev, current.st_ino, current.st_uid) != (owner['device'], owner['inode'], owner['uid']):
            raise ValueError('scratch inode, device or owner changed')
        marker = root/MARKER
        if marker.is_symlink() or load(marker) != owner:
            raise ValueError('scratch ownership marker differs')
        exit_path, receipt_path = directory/'child-exit.json', Path(owner['receipt'])
        if exit_path.is_symlink() or receipt_path.is_symlink() or receipt_path.resolve(strict=True) != receipt_path:
            raise ValueError('closure records must be original regular files')
        if load(exit_path).get('exit_code') != 0:
            raise ValueError('child did not exit successfully')
        closed_receipt(load(receipt_path))
        plan.update(owner_sha256=digest(owner_path), exit_sha256=digest(exit_path),
                    receipt_sha256=digest(receipt_path), footprint=footprint(root, owner['device']),
                    capacity_before=capacity(parent), eligible=True)
    except (OSError, ValueError, KeyError, TypeError) as error:
        plan['reasons'].append(str(error))
    return plan


@contextmanager
def cleanup_lock(directory):
    import fcntl
    path = directory/'cleanup.lock'
    fd = os.open(path, os.O_CREAT|os.O_WRONLY|os.O_NOFOLLOW, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX|fcntl.LOCK_NB)
        yield
    finally:
        os.close(fd)


def apply(plan_path, expected_sha, output, *, _allow_current_owner=False):
    plan_path, output = Path(plan_path), Path(output).absolute()
    if plan_path.is_symlink() or digest(plan_path) != expected_sha:
        raise ValueError('cleanup preview SHA256 differs')
    old = load(plan_path)
    if old.get('schema') != 'ax-scratch-cleanup-plan-v1':
        raise ValueError('unknown cleanup plan schema')
    directory = Path(old['directory'])
    if directory.resolve(strict=True) != directory or directory.is_symlink():
        raise ValueError('cleanup record directory changed')
    with cleanup_lock(directory):
        fresh = preview(directory, _allow_current_owner=_allow_current_owner)
        if not old.get('eligible') or not fresh['eligible']:
            raise ValueError('cleanup is not eligible: '+str(fresh['reasons']))
        for key in ('root', 'owner_sha256', 'exit_sha256', 'receipt_sha256', 'footprint'):
            if fresh[key] != old[key]:
                raise ValueError('scratch or closure changed after preview: '+key)
        root = Path(fresh['root'])
        if (output.resolve().is_relative_to(root) or plan_path.resolve().is_relative_to(root)
                or output.exists() or output.is_symlink()):
            raise ValueError('new cleanup receipt outside scratch required')
        if not shutil.rmtree.avoids_symlink_attacks:
            raise ValueError('fd-safe Linux recursive deletion required')
        # Intent survives interruption. A partially deleted tree needs a new preview.
        write_new(output.with_name(output.name+'.intent.json'),
                  dict(root=str(root), plan_sha256=expected_sha, owner_sha256=fresh['owner_sha256']))
        parent_fd = os.open(root.parent, os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
        try:
            observed = os.stat(root.name, dir_fd=parent_fd, follow_symlinks=False)
            owner = load(directory/'owner.json')
            if (observed.st_dev, observed.st_ino) != (owner['device'], owner['inode']):
                raise ValueError('scratch root replaced before removal')
            if mounts_below([root]):
                raise ValueError('mount appeared after preview')
            shutil.rmtree(root.name, dir_fd=parent_fd)
        finally:
            os.close(parent_fd)
        after = capacity(root.parent)
        result = dict(passed=not root.exists(), removed_root=str(root),
                      before=fresh['capacity_before'], after=after,
                      available_bytes_delta=after['available_bytes']-fresh['capacity_before']['available_bytes'],
                      available_inodes_delta=after['available_inodes']-fresh['capacity_before']['available_inodes'],
                      plan_sha256=expected_sha, source_receipt_sha256=fresh['receipt_sha256'])
        write_new(output, result)
        return result


def finish(directory, exit_code):
    directory = Path(directory)
    write_new(directory/'child-exit.json', {'exit_code': exit_code})
    plan = preview(directory, _allow_current_owner=True)
    write_new(directory/'preview.json', plan)
    if not plan['eligible']:
        return dict(passed=False, retained=True, preview=str(directory/'preview.json'), reasons=plan['reasons'])
    try:
        return apply(directory/'preview.json', digest(directory/'preview.json'),
                     directory/'applied.json', _allow_current_owner=True)
    except (OSError, ValueError) as error:
        failure = dict(passed=False, retained=True, reasons=[str(error)])
        write_new(directory/'apply-error.json', failure)
        return failure


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='action', required=True)
    p = sub.add_parser('preview')
    p.add_argument('--directory', type=Path, required=True)
    p.add_argument('--out', type=Path, required=True)
    p = sub.add_parser('apply')
    p.add_argument('--plan', type=Path, required=True)
    p.add_argument('--sha256', required=True)
    p.add_argument('--out', type=Path, required=True)
    a = parser.parse_args()
    if a.action == 'preview':
        result = preview(a.directory)
        root = result.get('root')
        if root and a.out.resolve().is_relative_to(Path(root)):
            parser.error('preview output must be outside scratch')
        write_new(a.out, result)
        print(json.dumps(result, indent=2))
        return 0 if result['eligible'] else 2
    result = apply(a.plan, a.sha256, a.out)
    print(json.dumps(result, indent=2))
    return 0 if result['passed'] else 2


if __name__ == '__main__':
    raise SystemExit(main())
