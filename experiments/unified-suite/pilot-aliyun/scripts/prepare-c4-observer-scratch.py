#!/usr/bin/env python3
"""Inspect/setup one new owned A observer-only disk; default is inspect only.

Run on the A objects host under the coordinator's canonical pool/resource guard.
--apply formats only a completely unsigned, unmounted newly created owned disk.
It never infers a disk from /dev/vdb and never resumes a partial setup.
A --apply that fails after touching the disk releases its own mount and leaves a
passed:false receipt instead of a half-formatted or half-mounted scratch root.
--snapshot-tag only reads; --not-before dates the ownership receipt that is trusted.
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
import time

ROOT = Path('/srv/ax-c4-observer-scratch')
INSTANCE = 'i-bp1h9kaoya0predubdkn'
BOOT = '5a984d75-dca0-4921-92fa-b021d1f799e5'
ORIGINAL = {'d-bp1h9kaoya0predx6kv'+c for c in 'defg'}
SYSTEM = 'd-bp1h9kaoya0predx6kvc'
TAG = re.compile(r'c4-a-flat-(?:on[123]|formal-01)')
MKFS = ['mkfs.ext4','-q','-m','0','-i','4096','-L','ax-c4-observer']


class CommandError(RuntimeError):
    """One failed command, keeping its stderr for the failure receipt."""

    def __init__(self, argv, code, stderr):
        self.argv = list(argv); self.code = code; self.stderr = (stderr or '').strip()
        super().__init__('Command failed (%s, exit %d): %s' % (argv[0], code, self.stderr or 'no output'))


def require(ok, message):
    if not ok:
        raise ValueError(message)


def run(argv, codes=(0,)):
    p = subprocess.run(argv, capture_output=True, text=True)
    if p.returncode not in codes:
        raise CommandError(argv, p.returncode, p.stderr or p.stdout)
    return p.stdout


def instant(text):
    """Parse one ISO8601 timestamp; a naive value is UTC."""
    require(isinstance(text, str) and text.strip(), 'ISO8601 timestamp required')
    try:
        value = datetime.fromisoformat(text)
    except ValueError:
        raise ValueError('ISO8601 timestamp required: '+text) from None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def pin(path):
    return dict(path=str(path), sha256=digest(path))


def pinned(entry):
    """Recheck one producer source pin: absolute, not a symlink, and unchanged."""
    require(isinstance(entry, dict) and isinstance(entry.get('path'), str) and isinstance(entry.get('sha256'), str),
            'Malformed source pin')
    path = Path(entry['path'])
    require(path.is_absolute() and path.is_file() and not path.is_symlink() and digest(path)==entry['sha256'],
            'Changed source pin: '+entry['path'])


def write_new(path, value):
    with path.open('x', encoding='utf-8') as stream:
        json.dump(value, stream, indent=2)
        stream.write('\n'); stream.flush(); os.fsync(stream.fileno())


def sync_dir(path):
    """Flush one directory entry (the new storage receipt) to its device."""
    fd = os.open(str(path), os.O_RDONLY | getattr(os, 'O_DIRECTORY', 0))
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def ownership(path, sha, not_before=None, verify_pins=True):
    """Check the exact ownership receipt the cloud producer actually writes.

    observer-scratch-cloud.py emits delete_with_instance and original_disk_ids
    (all five owned disks, including the system disk); the older
    DeleteWithInstance/original_four_ids spelling is still accepted.
    The producer's source pins are cloud-receipt files that live on the
    coordinator, not on this object host, so verify_pins=False is only allowed
    for an explicitly coordinator-verified byte identity.
    """
    require(Path(path).is_file() and not Path(path).is_symlink() and digest(path)==sha, 'Changed ownership receipt')
    value = json.loads(Path(path).read_bytes())
    exact = dict(schema='c4-observer-scratch-ownership-v1', pool='pool-a', role='objects', instance_id=INSTANCE,
                 size_gib=100, category='cloud_essd', performance_level='PL0', zone='cn-hangzhou-j', status='In_use')
    require(all(value.get(k)==v for k,v in exact.items()) and value.get('passed') is True and
            value.get('applied') is True and value.get('disk_id')=='d-'+str(value.get('serial')) and
            value.get('delete_with_instance', value.get('DeleteWithInstance')) is True,
            'Exact new 100GiB PL0 scratch ownership required')
    if 'original_disk_ids' in value:
        owned = value['original_disk_ids']
        require(isinstance(owned, list) and all(isinstance(x, str) for x in owned) and len(owned)==len(set(owned)),
                'Original disk list required')
        ids = set(owned)
        require(SYSTEM in ids and ids-{SYSTEM}==ORIGINAL,
                'Original four 60GiB data disks plus the system disk required')
    else:
        owned = value.get('original_four_ids')
        require(isinstance(owned, list) and all(isinstance(x, str) for x in owned) and len(owned)==len(set(owned)),
                'Original four disk list required')
        ids = set(owned)
        require(ids==ORIGINAL, 'Original four 60GiB data disks required')
    require(value.get('disk_id') not in ids and value.get('disk_id')!=SYSTEM, 'New scratch is one of the original disks')
    pins = value.get('source_pins')
    require(isinstance(pins, list) and pins, 'Source pins required')
    if verify_pins:
        for entry in pins:
            pinned(entry)
    require('at' in value, 'Ownership receipt timestamp required')
    at = instant(value['at'])
    require(not_before is None or at >= not_before,
            'Ownership receipt predates the required start: '+value['at'])
    return value


def disks():
    return json.loads(run(['lsblk','--json','--bytes','--paths','--output',
                          'NAME,TYPE,SIZE,SERIAL,UUID,FSTYPE,MOUNTPOINTS,PKNAME,MAJ:MIN']))['blockdevices']


def choose_disk(owner, rows, unsigned):
    found = [r for r in rows if (r.get('serial') or '').strip()==owner['serial']]
    require(len(found)==1, 'New scratch serial must identify one actual whole disk')
    row = found[0]
    require(row.get('type')=='disk' and not row.get('children') and not row.get('pkname') and
            row.get('size')==100*1024**3 and str(row.get('name') or '').startswith('/dev/'),
            'Original whole-device 100GiB scratch required')
    actual = {(r.get('serial') or '').strip():r for r in rows if r.get('type')=='disk'}
    require(all(d[2:] in actual and actual[d[2:]].get('size')==60*1024**3 and
                actual[d[2:]].get('fstype')=='ext4' for d in ORIGINAL), 'Four original 60GiB data disks must remain unchanged')
    require(SYSTEM[2:] in actual and row['name']!=actual[SYSTEM[2:]]['name'], 'Never use the system disk')
    if unsigned:
        require(not row.get('uuid') and not row.get('fstype') and not any(row.get('mountpoints') or []),
                'Scratch already has a filesystem or mount; inspect partial setup, never reformat')
    return row


def host():
    require(socket.gethostname()=='ax-pilot-20261111-objects' and
            Path('/proc/sys/kernel/random/boot_id').read_text().strip()==BOOT, 'Original A objects host/boot required')


def inspect(owner, unsigned=True):
    host()
    rows = disks()
    row = choose_disk(owner, rows, unsigned)
    if unsigned:
        signs = json.loads(run(['wipefs','--no-act','--json',row['name']]))
        require(signs.get('signatures')==[], 'New disk is not blank; no formatting allowed')
        require(not run(['blkid','-p','-o','export',row['name']], codes=(2,)).strip(), 'Existing block signature')
        require(not ROOT.exists() and not ROOT.is_symlink(), 'Fresh scratch mount path required')
    return row, rows


def device_uuid(name):
    """Read the on-disk UUID directly; lsblk/udev may lag right after mkfs."""
    out = run(['blkid','-p','-o','export',name], codes=(0,2))
    values = dict(line.split('=',1) for line in out.splitlines() if '=' in line)
    return (values.get('UUID') or '').strip() or None


def wait_identity(row, seconds=15):
    """Wait until the freshly formatted whole device reports its ext4 identity."""
    try:
        run(['udevadm','settle'])
    except Exception:
        pass
    deadline = time.monotonic()+seconds
    while True:
        uuid = device_uuid(row['name'])
        found = [r for r in disks() if r['name']==row['name']]
        if uuid and found and found[0].get('fstype')=='ext4' and found[0].get('uuid')==uuid:
            return found[0]
        require(time.monotonic()<deadline, 'New ext4 identity did not appear: '+row['name'])
        time.sleep(0.5)


def mounted(owner):
    row = choose_disk(owner, disks(), False)
    require(ROOT.is_dir() and ROOT.resolve(strict=True)==ROOT and os.path.ismount(ROOT), 'Canonical scratch mount missing')
    fs = json.loads(run(['findmnt','--json','--mountpoint',str(ROOT),'--output','SOURCE,TARGET,FSTYPE,OPTIONS,UUID,MAJ:MIN']))['filesystems']
    uuid = device_uuid(row['name'])
    require(len(fs)==1 and fs[0]['target']==str(ROOT) and fs[0]['source']==row['name'] and
            fs[0]['fstype']=='ext4' and uuid and fs[0]['uuid']==uuid and
            row.get('uuid') in (None,uuid) and row.get('maj:min')==fs[0]['maj:min'] and
            'rw' in fs[0]['options'].split(',') and 'noatime' in fs[0]['options'].split(',') and
            row['fstype']=='ext4', 'Scratch mount/UUID/options differ from its whole device')
    return row, fs[0]


def unmount():
    """Release a mount this attempt may have made; findmnt is the authority.

    Only called after this attempt actually ran mount, because the mount
    command's exit status cannot be trusted. Idempotent: a second umount of an
    already released path fails harmlessly and findmnt then reports no mount.
    """
    try:
        run(['umount',str(ROOT)])
    except Exception:
        pass
    try:
        rows = json.loads(run(['findmnt','--json','--mountpoint',str(ROOT),'--output','TARGET']))['filesystems']
    except Exception:
        rows = []
    return not rows


def install(owner, out, owner_sha):
    """Format and mount the new disk once; any later failure is undone and receipted."""
    commands, applied, attempted, row = [], False, False, None

    def step(argv, codes=(0,)):
        commands.append(list(argv))
        return run(argv, codes)

    try:
        require(os.geteuid()==0, 'Root required to format the new owned scratch disk')
        row, original = inspect(owner)
        write_new(out/'before.json', dict(disk=row, all_disks=original))
        # Re-read identities and blank signatures directly before mkfs.
        checked, _ = inspect(owner)
        require(checked==row, 'Scratch identity changed before format')
        step(MKFS+[row['name']])
        applied = True
        # udev/lsblk can lag behind mkfs; never judge the new identity from a stale row.
        row = wait_identity(row)
        ROOT.mkdir(mode=0o700)
        attempted = True
        step(['mount','-t','ext4','-o','rw,noatime,nosuid,nodev',row['name'],str(ROOT)])
        row, fs = mounted(owner)
        for name in ('o','q','archives','evidence'):
            (ROOT/name).mkdir(mode=0o700)
        result = dict(schema='c4-observer-scratch-storage-v1',passed=True,applied=True,
            at=datetime.now(timezone.utc).isoformat(),root=str(ROOT),ownership_sha256=owner_sha,
            disk_id=owner['disk_id'],serial=owner['serial'],uuid=device_uuid(row['name']),device=ROOT.stat().st_dev,
            boot_id=BOOT,source=row['name'],automatic_retry=False,original_four_ids=sorted(ORIGINAL))
        os.sync()
        write_new(ROOT/'storage.json',result)
        sync_dir(ROOT)
        return result
    except Exception as error:
        # Only an attempt that reached mount can have left anything mounted; a
        # refusal before that must not touch a scratch root someone else owns.
        released = unmount() if attempted else None
        record = dict(schema='c4-observer-scratch-storage-v1',passed=False,applied=applied,
            at=datetime.now(timezone.utc).isoformat(),root=str(ROOT),ownership_sha256=owner_sha,
            disk_id=owner.get('disk_id'),serial=owner.get('serial'),uuid=(row or {}).get('uuid'),
            device=None,source=(row or {}).get('name'),boot_id=BOOT,error=str(error),
            stderr=getattr(error,'stderr',''),commands=commands,unmounted=released,automatic_retry=False)
        write_new(out/'result.json',record)
        raise


def snapshot(owner, owner_sha, tag):
    host(); require(TAG.fullmatch(tag), 'One original observed C4 tag required')
    row, fs = mounted(owner)
    receipt = ROOT/'storage.json'
    stored = json.loads(receipt.read_bytes())
    uuid = device_uuid(row['name'])
    require(uuid and stored['schema']=='c4-observer-scratch-storage-v1' and stored['passed'] is True and
            stored['applied'] is True and stored['ownership_sha256']==owner_sha and
            stored['disk_id']==owner['disk_id'] and
            stored['serial']==owner['serial'] and stored['uuid']==uuid==fs['uuid'] and
            stored['boot_id']==BOOT and stored['device']==ROOT.stat().st_dev, 'Original setup receipt changed')
    for name in ('o','q','archives','evidence'):
        p = ROOT/name
        require(p.is_dir() and p.resolve()==p and p.stat().st_dev==ROOT.stat().st_dev, 'Scratch subtree escaped its disk')
    require(not (ROOT/'o'/tag).exists() and not (ROOT/'q'/(tag+'-observer')).exists() and
            not (ROOT/'evidence'/tag).exists() and not (ROOT/'archives'/(tag+'-objects.tar.gz')).exists(),
            'Original next scratch attempt already exists')
    s = os.statvfs(ROOT)
    return dict(schema='c4-observer-scratch-snapshot-v1', passed=True, at=datetime.now(timezone.utc).isoformat(),
        pool='pool-a', role='objects', tag=tag, root=str(ROOT), boot_id=BOOT, ownership_sha256=owner_sha,
        disk_id=owner['disk_id'], serial=owner['serial'], uuid=uuid, device=ROOT.stat().st_dev,
        worker_receipt=pin(receipt), disk=dict(path=str(ROOT), device=ROOT.stat().st_dev, serial=owner['serial'],
            disk_id=owner['disk_id'], uuid=uuid, device_bytes=row['size'], source=row['name'],fstype='ext4',
            total_bytes=s.f_blocks*s.f_frsize, used_bytes=(s.f_blocks-s.f_bfree)*s.f_frsize, available_bytes=s.f_bavail*s.f_frsize,
            total_inodes=s.f_files, used_inodes=s.f_files-s.f_ffree, available_inodes=s.f_favail),
        targets={str(ROOT/n):ROOT.stat().st_dev for n in ('o','q','archives','evidence')})


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--ownership', type=Path, required=True); p.add_argument('--sha256', required=True)
    p.add_argument('--out', type=Path, required=True); p.add_argument('--apply', action='store_true')
    p.add_argument('--snapshot-tag'); p.add_argument('--not-before')
    p.add_argument('--source-pins-verified-offhost', action='store_true',
                   help='The coordinator already rechecked the producer pins; this host cannot see them')
    a = p.parse_args()
    require(a.out.is_absolute() and a.out.resolve()==a.out and not a.out.is_relative_to(ROOT), 'New independent receipt directory required')
    not_before = instant(a.not_before) if a.not_before else None
    a.out.mkdir(exist_ok=False)
    write_new(a.out/'intent.json', dict(at=datetime.now(timezone.utc).isoformat(), apply=a.apply,
        snapshot_tag=a.snapshot_tag, not_before=a.not_before,
        source_pins_verified_offhost=a.source_pins_verified_offhost,
        ownership=dict(path=str(a.ownership),sha256=a.sha256),automatic_retry=False))
    owner = ownership(a.ownership,a.sha256,not_before,verify_pins=not a.source_pins_verified_offhost)
    if a.snapshot_tag:
        require(not a.apply, 'Snapshot never applies setup')
        result = snapshot(owner,a.sha256,a.snapshot_tag)
    elif not a.apply:
        row, original = inspect(owner)
        write_new(a.out/'before.json', dict(disk=row, all_disks=original))
        result = dict(passed=True,applied=False,blank_disk=row,root=str(ROOT),automatic_retry=False)
    else:
        result = install(owner,a.out,a.sha256)
    write_new(a.out/'result.json',result);print(json.dumps(result))


if __name__=='__main__':
    main()
