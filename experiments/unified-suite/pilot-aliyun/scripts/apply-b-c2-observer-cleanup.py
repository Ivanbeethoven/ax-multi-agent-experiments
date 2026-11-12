#!/usr/bin/env python3
"""Apply a separately reviewed cleanup of the eight verified B C2 observer copies."""
import argparse
from contextlib import contextmanager, ExitStack
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import socket
import stat
import tempfile


HOST = 'ax-pilot-20261111-b-objects'
INSTANCE = 'i-bp1bhdngko7nq3c3jrhp'
SERIAL = 'bp1bhdngko7nq3c2i340'
UUID = '96ee330f-4aa4-499a-89ce-3e236e63a6ce'
IDENTITY = dict(host=HOST, instance_id=INSTANCE, disk_id='d-'+SERIAL, serial=SERIAL, filesystem_uuid=UUID)
MIN_FREE = 12_014_120_960
ARCHIVE_BYTES = 9_027_256_320
DRAFT_SHA = '1248d9eae023bfa6407bd568c68981496703afbfc70e42d4eabad53b8fd0b6e4'
ACTION = 'remove-verified-b-c2-observer-copies'
PIN = re.compile('[0-9a-f]{64}')


def require(ok, message):
    if not ok:
        raise ValueError(message)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def pin_fields(pin):
    require(isinstance(pin,dict) and set(pin)=={'path','sha256'} and
            isinstance(pin['sha256'],str) and PIN.fullmatch(pin['sha256']) and
            isinstance(pin['path'],str) and PurePosixPath(pin['path']).is_absolute() and
            str(PurePosixPath(pin['path']))==pin['path'] and '..' not in PurePosixPath(pin['path']).parts,
            'Canonical evidence path and SHA256 required')
    return pin['sha256']


def validate_request(draft, reviewed, approval, guard, pins, executor_sha):
    fields = {'schema','reviewed','automatic_retry','source_checker','archive_proof','readback',
              'targets','minimum_free_bytes',*IDENTITY}
    require(set(draft)==fields and draft['schema']=='b-c2-observer-cleanup-draft-v1' and
            draft['reviewed'] is False and draft['automatic_retry'] is False and
            reviewed==dict(draft,reviewed=True) and reviewed['reviewed'] is True and
            all(draft[key]==value for key,value in IDENTITY.items()) and draft['minimum_free_bytes']==MIN_FREE,
            'Reviewed request must change only the exact cleanup draft review flag')
    require(all(pin_fields(draft[key])==pins[key] for key in ('source_checker','archive_proof','readback')),
            'A reviewed source file differs from its pin')
    require(set(approval)=={'schema','approved','action','reviewer','user_message_id','draft',
                           'reviewed_request','executor'} and
            approval['schema']=='b-c2-observer-cleanup-approval-v1' and approval['approved'] is True and
            approval['action']==ACTION and
            all(isinstance(approval[key],str) and approval[key].strip() for key in ('reviewer','user_message_id')) and
            pin_fields(approval['draft'])==pins['draft'] and
            pin_fields(approval['reviewed_request'])==pins['reviewed_request'] and
            pin_fields(approval['executor'])==executor_sha,
            'Independent operator approval must bind the exact draft, reviewed request, and executor')
    require(set(guard)=={'schema','passed','locks_held','active_leases','at','reviewed_request','approval',*IDENTITY}
            and guard['schema']=='b-c2-observer-cleanup-guard-v1' and
            guard['passed'] is True and guard['locks_held'] is True and
            type(guard['active_leases']) is int and guard['active_leases']==0 and
            guard['reviewed_request']==approval['reviewed_request'] and
            pin_fields(guard['approval'])==pins['approval'] and all(guard[k]==v for k,v in IDENTITY.items()),
            'A held coordinator guard with zero active leases must bind this approval')


def current_guard(guard):
    issued = datetime.fromisoformat(guard['at'])
    require(issued.utcoffset() is not None and
            0 <= (datetime.now(timezone.utc)-issued).total_seconds() <= 900,
            'Coordinator guard outside the 900-second dispatch window')


def context(raw, reviewed_sha, approval_sha, guard_sha, executor_sha):
    pins = {name:sha(body) for name,body in raw.items()}
    require(pins['draft']==DRAFT_SHA,'The original worker-verified B C2 cleanup draft is required')
    require((pins['reviewed_request'],pins['approval'],pins['guard'])==(reviewed_sha,approval_sha,guard_sha),
            'Apply transport changed the reviewed request, approval or coordinator guard')
    draft, reviewed, approval, guard = (json.loads(raw[k]) for k in ('draft','reviewed_request','approval','guard'))
    validate_request(draft,reviewed,approval,guard,pins,executor_sha)
    current_guard(guard)
    checker = {'__name__':'pinned_b_c2_readback_checker'}
    exec(compile(raw['source_checker'],'<pinned read-only B C2 checker>','exec'),checker)
    expected = checker['expected_maps'](json.loads(raw['archive_proof']))
    paths = {path:tag for tag in checker['TAGS'] for path in checker['candidate_paths'](tag)}
    require(reviewed['targets']==list(paths), 'Only the original eight ordered B C2 observer paths are allowed')
    readback = json.loads(raw['readback'])
    groups = readback['groups']
    require(readback['schema']=='b-c2-observer-copies-readback-v1' and
            readback['archive_proof_sha256']==pins['archive_proof'] and readback['serial']==SERIAL and
            readback['filesystem_uuid']==UUID and readback['all_unmounted'] is True and
            readback['deletion_performed'] is False and readback['cleanup_approved'] is False and
            [row['path'] for row in groups]==list(paths) and readback['checked_allocated_bytes']==ARCHIVE_BYTES and
            sum(row['bytes_allocated'] for row in groups)==ARCHIVE_BYTES and
            all(row['files']==len(expected[paths[row['path']]]) for row in groups),
            'Exact successful B C2 archive/worker readback is required')
    return expected, paths, groups, guard, checker['command'], pins


def identity(s):
    return (s.st_dev,s.st_ino,s.st_mode)


def stamp(s):
    return (*identity(s),s.st_nlink,s.st_size,s.st_blocks,s.st_mtime_ns,s.st_ctime_ns)


@contextmanager
def directory(name, parent=None, expected=None):
    fd = os.open(name,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=parent)
    try:
        require(expected is None or identity(os.fstat(fd))==expected,'Verified directory identity changed')
        yield fd
    finally:
        os.close(fd)


@contextmanager
def path_parent(rootfd, relative, device):
    dirs = PurePosixPath(relative).parts
    require(len(dirs)>=2 and not PurePosixPath(relative).is_absolute() and '..' not in dirs,
            'Only fixed nested relative paths are permitted')
    # Open each component separately; no symlink may redirect the walk.
    with ExitStack() as stack:
        fd = rootfd
        for part in dirs[:-1]:
            fd = stack.enter_context(directory(part,fd))
            require(os.fstat(fd).st_dev==device,'Parent crossed the reviewed data disk')
        yield fd, dirs[-1]


def scan(rootfd, relative, files, device, seen):
    wanted_dirs = {p.as_posix() for name in files for p in PurePosixPath(name).parents}
    dirs, members, life = {}, {}, {}
    amount = 0
    inactive_socket = None

    def count(info):
        nonlocal amount
        key = (info.st_dev,info.st_ino)
        require(info.st_dev==device and key not in seen,'Cross-device file or shared inode')
        seen.add(key)
        amount += info.st_blocks*512

    def walk(fd, base):
        nonlocal inactive_socket
        here = os.fstat(fd)
        require(base.as_posix() in wanted_dirs,'Unarchived directory: '+base.as_posix())
        count(here)
        dirs[base.as_posix()] = stamp(here)
        for name in sorted(os.listdir(fd)):
            child = (base/name).as_posix()
            info = os.stat(name,dir_fd=fd,follow_symlinks=False)
            if stat.S_ISDIR(info.st_mode):
                with directory(name,fd,identity(info)) as sub:
                    walk(sub,base/name)
                continue
            count(info)
            if child=='observer.sock' and stat.S_ISSOCK(info.st_mode) and info.st_nlink==1 and info.st_blocks==0:
                inactive_socket = stamp(info)
                continue
            require(child in files and stat.S_ISREG(info.st_mode) and info.st_nlink==1 and
                    info.st_size==files[child]['bytes'],'Unarchived or changed file: '+child)
            initial = stamp(info)
            with os.fdopen(os.open(name,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK,dir_fd=fd),'rb') as stream:
                require(stamp(os.fstat(stream.fileno()))==initial and
                        hashlib.file_digest(stream,'sha256').hexdigest()==files[child]['sha256'],
                        'Archived file bytes differ: '+child)
                if child in ('ready.json','finished.json'):
                    require(info.st_size<=4*1024**2,'Oversized archived observer lifecycle record')
                    stream.seek(0)
                    life[child] = json.load(stream)
                require(stamp(os.fstat(stream.fileno()))==initial,'Archive file changed during read: '+child)
            members[child] = initial
        require(stamp(os.fstat(fd))==dirs[base.as_posix()],'Directory changed during byte verification')

    with path_parent(rootfd,relative,device) as (parent,name):
        with directory(name,parent) as fd:
            walk(fd,PurePosixPath('.'))
    require(members.keys()==files.keys() and dirs.keys()==wanted_dirs,'Incomplete verified observer tree')
    ready, finished = life['ready.json'],life['finished.json']
    require(ready['passed'] is True and finished['passed'] is True and
            ready['spec']['sha256']==finished['spec_sha256'],'Closed original observer identity differs')
    group = dict(path=relative,bytes_allocated=amount,files=len(members),directories=len(dirs),
                 observer_boot_id=ready['process']['boot_id'],stale_socket_not_archived=inactive_socket is not None)
    return group, dirs, members, inactive_socket


def verify_or_remove(rootfd, device, verified, remove=False):
    group, dirs, members, inactive_socket = verified
    children = {name:set() for name in dirs}
    for path in (*dirs,*members,*(['observer.sock'] if inactive_socket else [])):
        if path!='.':
            relative = PurePosixPath(path)
            children[relative.parent.as_posix()].add(relative.name)

    def walk(fd, base):
        subdirs = []
        current = dirs[base.as_posix()]
        require(stamp(os.fstat(fd))==current,'Verified directory metadata changed: '+str(base))
        names = sorted(os.listdir(fd))
        require(set(names)==children[base.as_posix()],'Verified directory membership changed: '+str(base))
        for name in names:
            child = (base/name).as_posix()
            actual = stamp(os.stat(name,dir_fd=fd,follow_symlinks=False))
            if child in dirs:
                require(actual==dirs[child],'Verified subdirectory changed: '+child)
                subdirs.append((name,actual))
            elif child=='observer.sock' and inactive_socket is not None:
                require(actual==inactive_socket,'Unarchived socket changed')
            else:
                require(child in members and actual==members[child],'Verified file changed: '+child)
                if remove:
                    os.unlink(name,dir_fd=fd)
        for name, actual in subdirs:
            with directory(name,fd,actual[:3]) as nested:
                walk(nested,base/name)
                if remove:
                    require(identity(os.stat(name,dir_fd=fd,follow_symlinks=False))==actual[:3],
                            'Verified subdirectory moved')
                    os.rmdir(name,dir_fd=fd)
        if remove:
            os.fsync(fd)

    with path_parent(rootfd,group['path'],device) as (parent,name):
        with directory(name,parent,dirs['.'][:3]) as fd:
            walk(fd,PurePosixPath('.'))
            if remove and inactive_socket is None:
                require(identity(os.stat(name,dir_fd=parent,follow_symlinks=False))==dirs['.'][:3],
                        'Verified observer directory moved')
                os.rmdir(name,dir_fd=parent)
        if remove:
            os.fsync(parent)


def write_new(out, name, value):
    with (out/name).open('x',encoding='utf-8',newline='\n') as stream:
        json.dump(value,stream,indent=2)
        stream.write('\n')
        stream.flush()
        os.fsync(stream.fileno())
    with directory(out) as fd:
        os.fsync(fd)


def boundary(command, root=None):
    require(not command('docker','ps','-q'),'B object services must remain stopped')
    for path in Path('/proc').glob('[0-9]*/cmdline'):
        try:
            argv = path.read_bytes().split(b'\0')
        except (FileNotFoundError, ProcessLookupError):
            continue
        require(not (b'bench.runtime.observe' in argv or b'bench.runtime.observe_ndjson' in argv or
                     os.path.basename(argv[0]) in (b'rustfs',b'juicefs',b'brewfs')),
                'A storage service or observer remains active')
    prefixes = [Path('/srv/ax-objects-0')]
    if root:
        prefixes.append(root)
        for line in Path('/proc/self/mountinfo').read_text().splitlines():
            target = Path(re.sub(r'\\([0-7]{3})',lambda m:chr(int(m[1],8)),line.split()[4]))
            require(target==root or not target.is_relative_to(root),'Nested mount on cleanup disk')
    for row in Path('/proc/net/unix').read_text().splitlines()[1:]:
        parts = row.split(maxsplit=7)
        require(len(parts)!=8 or not any(Path(parts[7]).is_relative_to(base) for base in prefixes),
                'Live socket in object disk cleanup scope')


@contextmanager
def mount_owned_disk(command, out):
    require(os.geteuid()==0 and socket.gethostname()==HOST and
            Path('/var/lib/cloud/data/instance-id').read_text().strip()==INSTANCE and
            os.readlink('/proc/self/ns/mnt')!=os.readlink('/proc/1/ns/mnt'),
            'Original B objects host and private root mount namespace required')
    boundary(command)
    rows = json.loads(command('lsblk','--json','--bytes','--paths','--output',
        'PATH,TYPE,SERIAL,SIZE,UUID,FSTYPE,MAJ:MIN,MOUNTPOINTS'))['blockdevices']
    disks = [row for row in rows if (row.get('serial') or '').strip()==SERIAL]
    require(len(disks)==1,'Expected exactly one original object disk')
    row = disks[0]
    device = os.stat(row['path'])
    host_mounts = {line.split()[2] for line in Path('/proc/1/mountinfo').read_text().splitlines()}
    require(row['type']=='disk' and row['size']==60*1024**3 and row['fstype']=='ext4' and row['uuid']==UUID and
            not row.get('children') and not any(row.get('mountpoints') or []) and row['maj:min'] not in host_mounts and
            stat.S_ISBLK(device.st_mode) and row['maj:min']==f'{os.major(device.st_rdev)}:{os.minor(device.st_rdev)}'
            and out.stat().st_dev!=device.st_rdev,'Owned ext4 disk must be inactive and distinct from the receipt disk')
    with tempfile.TemporaryDirectory(prefix='ax-b-c2-cleanup-',dir='/run') as name:
        root = Path(name)
        command('mount','-t','ext4','-o','rw,nodev,nosuid,noexec',row['path'],name)
        try:
            mounts = json.loads(command('findmnt','--json','--mountpoint',name,
                '--output','SOURCE,MAJ:MIN,UUID,OPTIONS'))['filesystems']
            require(len(mounts)==1,'Unexpected cleanup mount count')
            m = mounts[0]
            require(m['source']==row['path'] and m['uuid']==UUID and m['maj:min']==row['maj:min'] and
                    {'rw','nodev','nosuid','noexec'}<=set(m['options'].split(',')) and
                    root.stat().st_dev==device.st_rdev,'Mounted cleanup device differs from approved identity')
            yield root, device.st_rdev
        finally:
            command('umount',name)
            require(not root.is_mount(),'Cleanup device still mounted')


def bytes_free(fd):
    fs = os.fstatvfs(fd)
    return fs.f_bavail*fs.f_frsize


def execute(rootfd, device, expected, paths, groups, guard_check, emit):
    seen = set()
    verified = []
    for relative,tag in paths.items():
        verified.append(scan(rootfd,relative,expected[tag],device,seen))
    require([group for group,*_ in verified]==groups,'Fresh byte verification differs from approved readback')
    before = bytes_free(rootfd)
    require(before+sum(row['bytes_allocated'] for row in groups)>=MIN_FREE,'Insufficient space after planned cleanup')
    # Check every tree once more before removing the first member.
    guard_check()
    for entry in verified:
        verify_or_remove(rootfd,device,entry)
    emit('all-verified.json',dict(groups=groups,before_bytes=before,deletion_performed=False))
    receipts = []
    for index,entry in enumerate(verified,1):
        guard_check()
        group = entry[0]
        emit(f'{index:02d}-intent.json',dict(group,automatic_retry=False))
        verify_or_remove(rootfd,device,entry,remove=True)
        row = dict(group,regular_files_removed=True,after_bytes=bytes_free(rootfd))
        emit(f'{index:02d}-finished.json',row)
        receipts.append(row)
    after = bytes_free(rootfd)
    return dict(passed=after>=MIN_FREE,before_bytes=before,after_bytes=after,groups=receipts,
                minimum_free_bytes=MIN_FREE,automatic_retry=False)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    mode = p.add_mutually_exclusive_group(required=True)
    mode.add_argument('--apply',action='store_true')
    mode.add_argument('--validate-only',action='store_true')
    inputs = ('draft','reviewed_request','approval','guard','source_checker','archive_proof','readback')
    for field in inputs:
        p.add_argument('--'+field.replace('_','-'),type=Path,required=True)
    for field in ('reviewed-request','approval','guard'):
        p.add_argument('--'+field+'-sha256',required=True)
    p.add_argument('--out',type=Path)
    a = p.parse_args()
    require(a.apply==bool(a.out),'Only explicit apply may create a new receipt directory')
    raw = {}
    for field in inputs:
        with getattr(a,field).open('rb') as stream:
            raw[field] = stream.read(12*1024**2+1)
            require(len(raw[field])<=12*1024**2,'Staged input exceeds the proof size limit')
    executor_sha = sha(Path(__file__).read_bytes())
    expected, paths, groups, guard, command, pins = context(
        raw,a.reviewed_request_sha256,a.approval_sha256,a.guard_sha256,executor_sha)
    if a.validate_only:
        print(json.dumps(dict(passed=True,validation_only=True,deletion_performed=False,targets=list(paths))))
        return
    require(a.out.is_absolute() and a.out.resolve()==a.out,'Canonical new receipt directory required')
    a.out.mkdir(mode=0o700)
    emit = lambda name,value: write_new(a.out,name,value)
    emit('intent.json',dict(schema='b-c2-observer-cleanup-intent-v1',inputs=pins,executor_sha256=executor_sha,
                            targets=list(paths),automatic_retry=False))
    try:
        with mount_owned_disk(command,a.out) as (root,device):
            def held_and_idle():
                current_guard(guard)
                boundary(command,root)
            with directory(root) as rootfd:
                result = execute(rootfd,device,expected,paths,groups,held_and_idle,emit)
        emit('result.json',dict(result,schema='b-c2-observer-cleanup-result-v1',all_unmounted=True,
                                deletion_performed=True,inputs=pins))
        require(result['passed'],'Actual remaining space did not meet the approved floor')
    except BaseException as exc:
        emit('failed.json',dict(passed=False,error=str(exc),automatic_retry=False,
                                instruction='Keep all receipts; inspect any unmatched per-tree intent.'))
        raise


if __name__=='__main__':
    main()
