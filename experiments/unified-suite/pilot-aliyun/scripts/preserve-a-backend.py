#!/usr/bin/env python3
"""Inventory and privately pack the original A pool; no service or cloud mutations."""

import argparse
from contextlib import ExitStack
import importlib.util
import json
import os
from pathlib import Path
import re
import socket
import stat
import subprocess


# Exact Git blob at 00779c70ec0d0a0a4fd1f392cc688cdd18dc6136. Reuse its
# read-only mount, no-follow walk, archive creation and independent verifier.
COMMON_SHA = '038b673b396109f6d72fb09721c2d2d81b793900f9887fb844b609943a70d8c8'
# Serial is the immutable cloud disk ID without "d-". Source: release-owned-sets-02.
ROLES = {
    'client': ('i-bp1809mw7rlujtj9f705', 'bp1809mw7rlujtjc8eph',
               {'bp1809mw7rlujtjc8epi': 100}),
    'metadata': ('i-bp1gy3cfuj9jik0y4oc3', 'bp1gy3cfuj9jik0upt3j',
                 {'bp1gy3cfuj9jik0upt3k': 60}),
    'objects': ('i-bp1h9kaoya0predubdkn', 'bp1h9kaoya0predx6kvc',
                {f'bp1h9kaoya0predx6kv{x}': 60 for x in 'defg'}),
}
EVIDENCE = {'archives', 'evidence', 'logs', 'o', 'preparation', 'q',
            'qualification', 'receipts', 'redis-setup-01', 'retained-calibrations',
            'retained-evidence', 'rustfs-logs'}
CONTAINER = 'ax-pilot-20261111-objects-01-rustfs-1'
REDIS_CONF = Path('/etc/redis/ax-pilot-20261111.conf')
REDIS_UNIT = Path('/etc/systemd/system/ax-pilot-redis.service')
SCHEMA = 'ax-a-backend-retirement-inventory-v1'


def need(condition, message):
    if not condition:
        raise ValueError(message)


def common(path):
    # Execute the bytes just checked, with no import cache write or second file read.
    import hashlib
    from types import ModuleType
    code = path.read_bytes()
    need(hashlib.sha256(code).hexdigest() == COMMON_SHA, 'Historical helper SHA differs')
    module = ModuleType('ax_retirement_common')
    exec(compile(code, str(path), 'exec'), module.__dict__)
    return module


def cold(core, role):
    need(os.geteuid() == 0 and socket.gethostname() == f'ax-pilot-20261111-{role}'
         and core.metadata_instance() == ROLES[role][0]
         and os.readlink('/proc/self/ns/mnt') != os.readlink('/proc/1/ns/mnt'),
         'Expected exact A host in a private root mount namespace')
    if role == 'objects':
        need(not core.command('docker', 'ps', '-q').strip(), 'Running object container present')
    elif role == 'metadata':
        result = subprocess.run(['systemctl', 'is-active', '--quiet', REDIS_UNIT.name], timeout=10)
        need(result.returncode in (3, 4), 'Campaign Redis unit is active or unverifiable')
        for path in Path('/proc').glob('[0-9]*/cmdline'):
            try:
                argv = path.read_bytes().split(b'\0')
            except (FileNotFoundError, ProcessLookupError):
                continue
            need(not (argv[0].split(b'/')[-1].startswith(b'redis-server') and any(
                b'ax-pilot-20261111.conf' in a or b'10.20.2.162:6379' in a for a in argv)),
                'Campaign Redis process present')
    else:
        slot = Path('/sys/fs/cgroup/ax-slot-1')
        need(not any(p.read_text().strip() for p in slot.rglob('cgroup.procs')),
             'Campaign client cgroup is occupied')
        need(not any(' - fuse' in row and '/srv/ax-' in row
                     for row in Path('/proc/1/mountinfo').read_text().splitlines()),
             'Campaign FUSE mount present')


def disks(core, role):
    _, system_serial, sizes = ROLES[role]
    rows = json.loads(core.command('lsblk', '--tree', '--json', '--bytes', '--paths', '-o',
                     'PATH,TYPE,SERIAL,SIZE,UUID,FSTYPE,MAJ:MIN,MOUNTPOINTS'))['blockdevices']
    whole = [r for r in rows if r['type'] == 'disk']
    by_serial = {str(r.get('serial') or '').strip(): r for r in whole}
    need(len(whole) == len(by_serial) and set(by_serial) == {system_serial, *sizes},
         'Exact A system/data disk serial set differs')
    system = by_serial[system_serial]
    root_dev = core.command('findmnt', '-n', '-o', 'MAJ:MIN', '/').strip()
    root_parts = [p for p in system.get('children', [])
                  if p['maj:min'] == root_dev and '/' in (p.get('mountpoints') or [])]
    need(system['size'] == 40*1024**3 and len(root_parts) == 1
         and root_parts[0]['type'] == 'part' and root_parts[0]['fstype'] == 'ext4'
         and root_parts[0]['uuid'], 'Root does not match the original A system disk')
    mounted = core.live_mount_devices()
    for serial, size in sizes.items():
        row = by_serial[serial]
        st = os.stat(row['path'], follow_symlinks=False)
        need(row['size'] == size*1024**3 and row['fstype'] == 'ext4' and row['uuid']
             and not row.get('children') and not any(row.get('mountpoints') or [])
             and row['maj:min'] not in mounted and stat.S_ISBLK(st.st_mode)
             and row['maj:min'] == f'{os.major(st.st_rdev)}:{os.minor(st.st_rdev)}',
             'Data disk mounted or identity differs: '+serial)
    return {serial: by_serial[serial] for serial in sizes}, {
        'serial': system_serial, 'disk_id': 'd-'+system_serial, 'size': system['size'],
        'root_uuid': root_parts[0]['uuid'], 'root_major_minor': root_dev,
    }


def selection(core, roots, include, exclude, *, system=Path('/srv'), home=Path('/root'),
              confs=()):
    files, unknown, skipped, classified = [], [], [], set()

    def consider(path, rel, dev, default=False, *, expand=False):
        st = path.lstat()
        if not core.safe_name(rel) or st.st_dev != dev:
            unknown.append(rel)
        elif rel in exclude:
            classified.add(rel)
        elif default or rel in include:
            if rel in include:
                classified.add(rel)
            core.walk(path, rel, dev, files, unknown, skipped)
        elif stat.S_ISDIR(st.st_mode) and path.name == 'lost+found' and not any(path.iterdir()):
            skipped.append(rel)
        elif expand and stat.S_ISDIR(st.st_mode):
            for child in sorted(path.iterdir()):
                consider(child, f'{rel}/{child.name}', dev)
        else:
            unknown.append(rel)

    for serial, root in roots.items():
        dev = root.stat().st_dev
        for path in sorted(root.iterdir()):
            consider(path, serial+'/'+path.name, dev, path.name in EVIDENCE,
                     expand=path.name in {'rustfs-data', 'redis'})

    root_dev = Path('/').stat().st_dev
    for directory, label in ((system, 'srv'), (home, 'root')):
        need(directory.stat().st_dev == root_dev, 'Unexpected system evidence filesystem')
        for path in sorted(directory.glob('ax-*')):
            rel = f'system/{label}/{path.name}'
            st = path.lstat()
            if stat.S_ISDIR(st.st_mode) and st.st_dev == root_dev and not any(path.iterdir()):
                skipped.append(rel)
                continue
            consider(path, rel, root_dev, default=path.name in {
                'ax-receipts', 'ax-logs', 'ax-pool-a-bootstrap-01'})
    for path in confs:
        if path.exists() or path.is_symlink():
            consider(path, 'system'+str(path), root_dev, default=True)

    need(classified == include | set(exclude), 'Explicit archive/exclusion path not found')
    return files, sorted(unknown), sorted(skipped)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('action', choices=('scan', 'pack', 'verify'))
    p.add_argument('role', choices=ROLES)
    p.add_argument('--common', type=Path, required=True)
    p.add_argument('--tag', required=True)
    p.add_argument('--include', action='append', default=[], help='Exact private archive path from scan')
    p.add_argument('--exclude', action='append', default=[], metavar='PATH=SHA256',
                   help='Exact excluded path and independently verified rebuild/copy receipt SHA')
    p.add_argument('--scan-sha256')
    p.add_argument('--max-source-bytes', type=int, default=16*1024**3)
    p.add_argument('--archive', type=Path)
    args = p.parse_args()
    core = common(args.common)
    need(core.IDENT.fullmatch(args.tag), 'Use a unique lowercase tag')
    if args.action == 'verify':
        need(args.archive is not None, 'Archive required')
        print(json.dumps({'verified_members': core.check_pack(args.archive),
                          'sha256': core.sha(args.archive)}), flush=True)
        return

    os.umask(0o077)
    include = set(args.include)
    exclude = dict(x.rsplit('=', 1) for x in args.exclude)
    need(not include & set(exclude) and len(exclude) == len(args.exclude)
         and all(core.safe_name(n) for n in include | set(exclude))
         and all(re.fullmatch('[a-f0-9]{64}', h) for h in exclude.values()),
         'Invalid explicit path classification')
    cold(core, args.role)
    out = Path('/var/tmp')/f'ax-a-retire-{args.tag}-{args.role}'
    inventory = out/'inventory.json'
    if args.action == 'scan':
        out.mkdir(mode=0o700)
    else:
        st = out.lstat()
        need(stat.S_ISDIR(st.st_mode) and st.st_uid == 0 and stat.S_IMODE(st.st_mode) == 0o700
             and args.scan_sha256 == core.sha(inventory), 'Original reviewed scan SHA required')

    data, system = disks(core, args.role)
    with ExitStack() as stack:
        roots = {serial: stack.enter_context(core.mount(row)) for serial, row in data.items()}
        files, unknown, skipped = selection(core, roots, include, exclude,
            confs=(REDIS_CONF, REDIS_UNIT) if args.role == 'metadata' else ())
        total = sum(st.st_size for _, _, st in files)
        value = {
            'schema': SCHEMA, 'tag': args.tag, 'role': args.role, 'instance': ROLES[args.role][0],
            'boot_id': Path('/proc/sys/kernel/random/boot_id').read_text().strip(),
            'system_disk': system, 'data_disks': {
                serial: {'disk_id': 'd-'+serial, 'uuid': row['uuid'], 'size': row['size']}
                for serial, row in data.items()},
            'evidence_files': len(files), 'evidence_bytes': total,
            'inventory_digest': core.manifest(files), 'unknown': unknown, 'nondata': skipped,
            'included_roots': sorted(include), 'excluded_roots_with_external_proof': exclude,
        }
        if args.action == 'scan':
            core.write(inventory, value)
        else:
            need(json.loads(inventory.read_bytes()) == value and not unknown
                 and 0 < total <= args.max_source_bytes,
                 'Cold pack refused: inventory drift, unknown paths or source budget')
            if args.role == 'objects':
                logs = subprocess.check_output(['docker', 'logs', '--tail', '4000', CONTAINER],
                                               stderr=subprocess.STDOUT, timeout=25)
            else:
                logs = core.command('journalctl', '-u',
                    REDIS_UNIT.name if args.role == 'metadata' else 'ax-*',
                    '--no-pager', '-o', 'short-iso', '-n', '4000', timeout=25).encode()
            proof = core.pack(out/'evidence.tar.gz', files, value, logs)
    result = {'role': args.role, 'instance': ROLES[args.role][0], 'out': str(out),
              'all_data_disks_unmounted': True, 'inventory_sha256': core.sha(inventory),
              'unknown_count': len(unknown), 'evidence_bytes': total, 'evidence_files': len(files)}
    if args.action == 'pack':
        result['archive'] = proof
        core.write(out/'packed.json', result)
    print(json.dumps(result), flush=True)


if __name__ == '__main__':
    main()
