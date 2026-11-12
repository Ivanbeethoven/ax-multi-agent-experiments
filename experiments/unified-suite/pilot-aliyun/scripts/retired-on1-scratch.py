#!/usr/bin/env python3
"""Read-only staging of the original failed On1 archive from its exact scratch disk."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import socket
import stat
import subprocess
import urllib.request


HOST = 'ax-pilot-20261111-objects'
INSTANCE = 'i-bp1h9kaoya0predubdkn'
SERIAL = 'bp16jaiu2fcmbij8mg7i'
UUID = '91d693ad-0c15-4e6e-b843-73e581aec594'
SIZE = 107374182400
REJECTED = 'archives/c4-a-flat-on1-objects.tar.gz'
REJECTED_SIZE = 379864420
REJECTED_SHA256 = 'b9362d9f84d724d7afdeeaefbf82b032a772c1dfad456c015c47c806e818ffde'
RUN = Path('/run')
RECEIPTS = Path('/srv/ax-receipts')
PROC = Path('/proc')
TAG = re.compile(r'[a-z0-9-]{8,40}')
DIR_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW


def need(ok, reason):
    if not ok:
        raise ValueError(reason)


def command(*args):
    return subprocess.check_output(args, text=True, timeout=20)


def host():
    need(os.geteuid() == 0 and socket.gethostname() == HOST, 'Exact root A objects host required')
    meta = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    base = 'http://100.100.100.200/latest/'
    with meta.open(urllib.request.Request(base+'api/token',method='PUT',
                 headers={'X-aliyun-ecs-metadata-token-ttl-seconds':'30'}),timeout=3) as response:
        token = response.read(2048).decode()
    with meta.open(urllib.request.Request(base+'meta-data/instance-id',
                 headers={'X-aliyun-ecs-metadata-token':token}),timeout=3) as response:
        need(response.read(128).decode().strip()==INSTANCE, 'A objects instance identity differs')
    return (PROC/'sys/kernel/random/boot_id').read_text().strip()


def mounts():
    for line in (PROC/'self/mountinfo').read_text().splitlines():
        left,right = line.split(' - ',1)
        fields, details = left.split(),right.split()
        target = re.sub(r'\\([0-7]{3})',lambda m:chr(int(m[1],8)),fields[4])
        yield dict(id=int(fields[0]),device=fields[2],root=fields[3],target=target,
                   options=sorted(fields[5].split(',')),type=details[0],
                   source=details[1],super_options=sorted(details[2].split(',')))


def disk():
    rows = json.loads(command('lsblk','--json','--bytes','--paths','-o',
                     'NAME,TYPE,SERIAL,SIZE,UUID,FSTYPE,MAJ:MIN,MOUNTPOINTS'))['blockdevices']
    candidates = [x for x in rows if (x.get('serial') or '').strip()==SERIAL]
    need(len(candidates)==1, 'Missing or ambiguous exact scratch serial')
    d = candidates[0]
    need(d['type']=='disk' and not d.get('children') and d['size']==SIZE and
         d['uuid']==UUID and d['fstype']=='ext4' and re.fullmatch(r'/dev/[a-z0-9]+',d['name']),
         'Unexpected scratch device type, size, filesystem or UUID')
    actual = os.stat(d['name'],follow_symlinks=False)
    need(stat.S_ISBLK(actual.st_mode) and d['maj:min']==f'{os.major(actual.st_rdev)}:{os.minor(actual.st_rdev)}',
         'Scratch device node identity differs')
    return d


def mount_at(target, d, readonly=True):
    rows = list(mounts())
    related = [x for x in rows if x['device']==d['maj:min'] or
               x['target']==str(target) or x['target'].startswith(str(target)+'/')]
    need(len(related)==1, 'Unexpected mounts or aliases for the scratch device')
    [row] = related
    need(row['root']=='/' and row['target']==str(target) and row['device']==d['maj:min'] and
         row['type']=='ext4' and row['source']==d['name'], 'Scratch mount identity differs')
    if readonly:
        need(set(row['options']) >= {'ro','nodev','nosuid','noexec'} and
             set(row['super_options']) & {'noload','norecovery'} and
             'ro' in row['super_options'] and 'rw' not in row['options'],
             'Scratch mount is not read-only without journal recovery')
    return row


def inventory(target):
    st_dev = target.stat().st_dev
    regular, special = [], []
    for parent, dirs, files, fd in os.fwalk(target, topdown=True, follow_symlinks=False):
        relative = Path(parent).relative_to(target)
        need(os.fstat(fd).st_dev==st_dev, 'Unexpected filesystem in inventory')
        for name in sorted(set(dirs+files)):
            info = os.stat(name,dir_fd=fd,follow_symlinks=False)
            path = (relative/name).as_posix()
            need(info.st_dev==st_dev, 'Unexpected device in inventory: '+path)
            if stat.S_ISREG(info.st_mode):
                regular.append(dict(path=path,bytes=info.st_size,links=info.st_nlink))
            elif not stat.S_ISDIR(info.st_mode):
                kind = ('symlink' if stat.S_ISLNK(info.st_mode) else
                        'socket' if stat.S_ISSOCK(info.st_mode) else 'other')
                special.append(dict(path=path,kind=kind))
                if name in dirs:
                    dirs.remove(name)
            need(len(regular)+len(special)<=200000, 'Unexpected scratch inventory size')
        dirs.sort()
    return sorted(regular,key=lambda row:row['path']),sorted(special,key=lambda row:row['path'])


def archive_sha(target, relative=REJECTED, size=REJECTED_SIZE):
    base = os.open(target,DIR_FLAGS)
    handles = [base]
    try:
        st_dev = os.fstat(base).st_dev
        parts = Path(relative).parts
        need(parts and not Path(relative).is_absolute() and '..' not in parts, 'Unsafe archive path')
        for part in parts[:-1]:
            fd = os.open(part,DIR_FLAGS,dir_fd=handles[-1])
            handles.append(fd)
            need(os.fstat(fd).st_dev==st_dev,'Archive path crosses a device')
        fd = os.open(parts[-1],os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,dir_fd=handles[-1])
        with os.fdopen(fd,'rb') as stream:
            info = os.fstat(stream.fileno())
            need(stat.S_ISREG(info.st_mode) and info.st_dev==st_dev and info.st_size==size,
                 'Original rejected archive shape differs')
            return hashlib.file_digest(stream,'sha256').hexdigest()
    finally:
        for fd in reversed(handles):
            os.close(fd)


def checked_path(path):
    need(path.is_absolute() and RECEIPTS in path.parents and path.parent.resolve(strict=True)==path.parent,
         'Canonical /srv/ax-receipts output required')
    return path


def write(path, obj):
    with path.open('x',encoding='utf-8') as stream:
        os.fchmod(stream.fileno(),0o600)
        json.dump(obj,stream,sort_keys=True,indent=2)
        stream.write('\n')
        stream.flush()
        os.fsync(stream.fileno())


def target_for(tag):
    need(TAG.fullmatch(tag),'Dedicated retirement tag required')
    return RUN/('ax-retired-on1-scratch-'+tag)


def interrupted(_signal, _frame):
    raise InterruptedError('Readonly scratch capture interrupted')


def mount_readonly(tag, out):
    boot = host()
    d = disk()
    out = checked_path(out)
    need(not os.path.lexists(out),'New inventory receipt required')
    target = target_for(tag)
    need(not any(d['mountpoints']) and not any(x['device']==d['maj:min'] for x in mounts()),
         'Scratch device is already mounted')
    target.mkdir(mode=0o700)
    owned = None
    try:
        command('mount','-t','ext4','-o','ro,noload,nodev,nosuid,noexec','--',d['name'],str(target))
        owned = mount_at(target,d,readonly=False)
        row = mount_at(target,d)
        files, special = inventory(target)
        need(archive_sha(target)==REJECTED_SHA256,'Exact rejected archive digest differs')
        need(mount_at(target,d)==row,'Mount changed during read-only inventory')
        receipt = dict(schema='ax-retired-on1-scratch-inventory-v1',tag=tag,instance=INSTANCE,
                       boot=boot,serial=SERIAL,uuid=UUID,disk_id='d-'+SERIAL,
                       mount=row,regular=files,special=special,
                       rejected_archive=dict(path=str(target/REJECTED),bytes=REJECTED_SIZE,sha256=REJECTED_SHA256))
        write(out,receipt)
        print(json.dumps(dict(ready=True,inventory=str(out),sha256=hashlib.sha256(out.read_bytes()).hexdigest(),
                              mount=str(target),rejected_archive=receipt['rejected_archive'])),flush=True)
    except BaseException:
        # Only undo our own exact temporary mount; never unmount another device.
        if owned is None and any(x['target']==str(target) for x in mounts()):
            owned = mount_at(target,d,readonly=False)
        if owned is not None and mount_at(target,d,readonly=False)==owned:
            command('umount','--',str(target))
        if not any(x['target']==str(target) for x in mounts()):
            target.rmdir()
        raise


def unmount(receipt, sha256):
    boot = host()
    d = disk()
    receipt = checked_path(receipt)
    fd = os.open(receipt,os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd,'rb') as stream:
        info = os.fstat(stream.fileno())
        need(stat.S_ISREG(info.st_mode) and info.st_size<=64*1024**2,'Invalid inventory receipt size or type')
        raw = stream.read()
    need(hashlib.sha256(raw).hexdigest()==sha256,'Inventory receipt SHA differs')
    value = json.loads(raw)
    need(value['schema']=='ax-retired-on1-scratch-inventory-v1' and
         (value['instance'],value['boot'],value['serial'],value['uuid'])==(INSTANCE,boot,SERIAL,UUID),
         'Receipt is not this exact disk and current boot')
    target = target_for(value['tag'])
    need(mount_at(target,d)==value['mount'],'Current mount differs from receipt')
    command('umount','--',str(target))
    need(not any(x['target']==str(target) or x['device']==d['maj:min'] for x in mounts()),
         'Scratch device remains mounted')
    target.rmdir()
    print(json.dumps(dict(unmounted=True,tag=value['tag'],device=d['maj:min'],uuid=UUID)),flush=True)


if __name__=='__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='operation',required=True)
    m = sub.add_parser('mount')
    m.add_argument('--tag',required=True)
    m.add_argument('--out',type=Path,required=True)
    u = sub.add_parser('unmount')
    u.add_argument('--receipt',type=Path,required=True)
    u.add_argument('--sha256',required=True)
    args = parser.parse_args()
    if args.operation=='mount':
        signal.signal(signal.SIGTERM,interrupted)
        mount_readonly(args.tag,args.out)
    else:
        unmount(args.receipt,args.sha256)
