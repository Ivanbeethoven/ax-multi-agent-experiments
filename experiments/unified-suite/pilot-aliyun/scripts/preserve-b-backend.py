#!/usr/bin/env python3
"""Cold inventory and private evidence pack for the original B backend only."""
import argparse
from contextlib import contextmanager, ExitStack
import gzip
import hashlib
import io
import json
import os
from pathlib import Path
import re
import socket
import stat
import subprocess
import tarfile
import tempfile
import time
import urllib.request

ROLES = {
    'objects': ('i-bp1bhdngko7nq3c3jrhp', 'bp1bhdngko7nq3c2i33z',
        {f'bp1bhdngko7nq3c2i34{i}': uuid for i, uuid in enumerate((
         '96ee330f-4aa4-499a-89ce-3e236e63a6ce', '43dd6857-2704-43cf-83d7-25271f91c12a',
         '282adf59-0059-4164-b424-39f905afe916', '9f11a3e6-3a71-40d7-8e30-a46c89cbdcdf'))}),
    'metadata': ('i-bp14nenf7osz9v1snsp2', 'bp14nenf7osz9v1rkk5k',
                 {'bp14nenf7osz9v1rkk5l': '24381dda-bee5-43bf-933b-fed798d36fbe'}),
    'client': ('i-bp1fmmfkcpr8w99j2bsy', 'bp1fmmfkcpr8w99pwgc4',
               {'bp1fmmfkcpr8w99pwgc5': 'e2cb49e2-f4d5-464c-889f-2bdfe29e6b43'}),
}
PENDING = {
    'objects': ['o/cal-pool-b-j-mx-mxb01-on1', 'q/cal-pool-b-j-mx-mxb01-on1-observer',
                'o/cal-pool-b-j-mx-mxb01-on2', 'q/cal-pool-b-j-mx-mxb01-on2-observer',
                'q/cal-pool-b-j-mx-mxb01-on1-collector'],
    'metadata': ['o/cal-pool-b-j-mx-mxb01-on1', 'q/cal-pool-b-j-mx-mxb01-on1-observer',
                 'o/cal-pool-b-j-mx-mxb01-on2', 'q/cal-pool-b-j-mx-mxb01-on2-observer'],
    'client': [
        f'{prefix}/cal-pool-b-j-mx-mxb01-{phase}{suffix}'
        for phase, items in {
            'off1': [('q',''),('m','-p'),('m',''),('w',''),('j',''),('m','-v')],
            'on1': [('q',''),('m','-p'),('m',''),('w',''),('j',''),('m','-v'),('o',''),('q','-observer')],
            'on2': [('q',''),('m','-p'),('m',''),('w',''),('j',''),('o',''),('q','-observer')],
        }.items() for prefix,suffix in items
    ],
}
EVIDENCE = {'o', 'q', 'archives', 'evidence', 'logs', 'preparation', 'qualification',
            'receipts', 'redis-setup-01', 'retained-calibrations', 'retained-evidence'}
BUCKETS = {'axp-392c124c743749b1', 'ax-pilot-layout-aecf2d28e0f44b468de7ab2793ea0e8f',
           'ax-pilot-juicefs-f0cfa2087dcb47118af2f034e904a051'}
AOF = re.compile(r'appendonly\.aof(?:\.manifest|\.\d+\.(?:base\.(?:rdb|aof)|incr\.aof))?')
REDIS_KEYS = {'bind', 'port', 'databases', 'dir', 'appendonly', 'appendfsync', 'appendfilename',
              'appenddirname', 'dbfilename', 'maxmemory', 'maxmemory-policy', 'protected-mode',
              'save', 'auto-aof-rewrite-percentage', 'auto-aof-rewrite-min-size'}
IDENT = re.compile(r'[a-z0-9][a-z0-9-]{7,53}')
MARKER = 'ax-b-backend-retirement-inventory-v1'

def need(ok, reason):
    if not ok:
        raise ValueError(reason)

def command(*argv, timeout=20):
    return subprocess.check_output(argv, text=True, timeout=timeout)

def sha(path):
    with path.open('rb') as f:
        return hashlib.file_digest(f, 'sha256').hexdigest()

def encoded(value):
    return (json.dumps(value, indent=2, sort_keys=True) + '\n').encode()

def write(path, value):
    with path.open('xb') as f:
        f.write(encoded(value))

def metadata_instance():
    op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    base = 'http://100.100.100.200/latest/'
    with op.open(urllib.request.Request(base+'api/token', method='PUT',
         headers={'X-aliyun-ecs-metadata-token-ttl-seconds': '30'}), timeout=3) as r:
        token = r.read(2048).decode()
    with op.open(urllib.request.Request(base+'meta-data/instance-id',
         headers={'X-aliyun-ecs-metadata-token': token}), timeout=3) as r:
        return r.read(128).decode().strip()

def check_cold(role):
    need(os.geteuid() == 0 and socket.gethostname() == f'ax-pilot-20261111-b-{role}'
         and metadata_instance() == ROLES[role][0]
         and os.readlink('/proc/self/ns/mnt') != os.readlink('/proc/1/ns/mnt'),
         'Expected exact B backend in a private root mount namespace')
    if role == 'objects':
        need(not command('docker', 'ps', '-q').strip(), 'Unexpected running B Docker workload')
    elif role == 'metadata':
        p = subprocess.run(['systemctl', 'is-active', '--quiet', 'ax-pilot-redis.service'], timeout=10)
        need(p.returncode in (3, 4), 'Unexpected B campaign Redis process')
        # The image may run unrelated default Redis on the system disk.
        for path in Path('/proc').glob('[0-9]*/cmdline'):
            try:
                argv = path.read_bytes().split(b'\0')
            except FileNotFoundError:
                continue
            need(not (argv[0].split(b'/')[-1].startswith(b'redis-server')
                      and any(b'ax-pilot-20261111-b.conf' in a or b'10.20.2.169:6379' in a for a in argv)),
                 'Unexpected B Redis service process')
    else:
        slot = Path('/sys/fs/cgroup/ax-slot-1/cgroup.procs')
        need(not slot.exists() or not slot.read_text().strip(), 'B client executor still occupied')
        need(not any(' - fuse' in row and ('/srv/ax-client' in row or '/srv/ax-pilot-session' in row)
                     for row in Path('/proc/1/mountinfo').read_text().splitlines()),
             'B client still has a campaign FUSE mount')

def live_mount_devices():
    found, namespaces = set(), set()
    for p in Path('/proc').glob('[0-9]*'):
        try:
            ns = os.readlink(p/'ns/mnt')
            if ns not in namespaces:
                found.update(x.split()[2] for x in (p/'mountinfo').read_text().splitlines())
                namespaces.add(ns)
        except (FileNotFoundError, ProcessLookupError):
            continue
        except PermissionError:
            # Linux kernel threads may have no accessible mount namespace.
            status = (p/'status').read_text()
            need(re.search(r'^Kthread:\s+1$', status, re.M), 'Unverifiable live mount namespace')
    return found

def disk_rows(role):
    _, system_serial, data = ROLES[role]
    rows = json.loads(command('lsblk', '--tree', '--json', '--bytes', '--paths', '-o',
                              'PATH,TYPE,SERIAL,SIZE,UUID,FSTYPE,MAJ:MIN,MOUNTPOINTS'))['blockdevices']
    disks = [x for x in rows if x['type'] == 'disk']
    by_serial = {str(x.get('serial') or '').strip(): x for x in disks}
    need(len(disks) == len(by_serial) and set(by_serial) == {system_serial, *data}, 'Exact B disk set differs')
    root_dev = command('findmnt', '-n', '-o', 'MAJ:MIN', '/').strip()
    need(by_serial[system_serial]['size'] == 40*1024**3 and any(
         '/' in (c.get('mountpoints') or []) and c['maj:min'] == root_dev
         for c in by_serial[system_serial].get('children', [])),
         'Expected B system disk mounted as root')
    host_mounted = live_mount_devices()
    for serial, uuid in data.items():
        d = by_serial[serial]
        actual = os.stat(d['path'], follow_symlinks=False)
        need(d['size'] == (100 if role == 'client' else 60)*1024**3 and d['fstype'] == 'ext4' and d['uuid']
             and d['uuid'] == uuid and not d.get('children')
             and not any(d.get('mountpoints') or []) and d['maj:min'] not in host_mounted
             and stat.S_ISBLK(actual.st_mode)
             and d['maj:min'] == f'{os.major(actual.st_rdev)}:{os.minor(actual.st_rdev)}',
             'Original data disk is mounted or has unexpected identity')
    return {k: by_serial[k] for k in data}

@contextmanager
def mount(row):
    with tempfile.TemporaryDirectory(prefix='ax-b-retire-', dir='/run') as name:
        command('mount', '-t', 'ext4', '-o', 'ro,noload,nodev,nosuid,noexec', row['path'], name)
        try:
            root = Path(name)
            checks = json.loads(command('findmnt', '--json', '--mountpoint', name,
              '--output', 'SOURCE,MAJ:MIN,UUID,OPTIONS'))['filesystems']
            need(len(checks) == 1, 'Unexpected mount count')
            current = checks[0]
            options = set(current['options'].split(','))
            need(current['source'] == row['path'] and current['maj:min'] == row['maj:min']
                 and current['uuid'] == row['uuid'] and {'ro','nodev','nosuid','noexec'} <= options
                 and {'noload','norecovery'} & options, 'Mount was not cold read only')
            yield root
        finally:
            command('umount', name)
            need(not Path(name).is_mount(), 'Diagnostic mount did not close')

def safe_name(relative):
    return not any(ch in relative for ch in ('\\', ':', '\x00', '\r', '\n')) and all(
        part not in {'', '.', '..'} for part in relative.split('/'))

def walk(root, prefix, dev, files, unknown, skipped):
    st = root.lstat()
    if not safe_name(prefix) or st.st_dev != dev:
        unknown.append(prefix)
    elif stat.S_ISDIR(st.st_mode):
        for child in sorted(root.iterdir()):
            walk(child, prefix+'/'+child.name, dev, files, unknown, skipped)
    elif stat.S_ISREG(st.st_mode):
        files.append((prefix, root, st))
    elif stat.S_ISSOCK(st.st_mode) and (root.name == 'observer.sock' or (
          root.name == 'control.sock' and prefix.split('/')[1:2] == ['m'])):
        skipped.append(prefix)
    elif stat.S_ISLNK(st.st_mode) and prefix.endswith(
          '/qualification/parallel-juicefs-regular-01/oracle/symbolic'):
        skipped.append(prefix+' -> '+os.readlink(root))
    else:
        unknown.append(prefix)

def excluded_store(role, path, prefix, generated, unknown, buckets):
    if not stat.S_ISDIR(path.lstat().st_mode):
        unknown.append(prefix)
        return
    for child in sorted(path.iterdir()):
        name = child.name
        if role == 'objects':
            good = name in buckets | {'.rustfs.sys', '.minio.sys'} and stat.S_ISDIR(child.lstat().st_mode)
        else:
            mode = child.lstat().st_mode
            good = ((name == 'appendonlydir' and stat.S_ISDIR(mode)) or
                    ((name == 'dump.rdb' or AOF.fullmatch(name)) and stat.S_ISREG(mode)))
        target = prefix+'/'+name
        if not good:
            unknown.append(target)
            continue
        if role == 'metadata' and name == 'appendonlydir':
            for segment in sorted(child.iterdir()):
                rel = target+'/'+segment.name
                if AOF.fullmatch(segment.name) and stat.S_ISREG(segment.lstat().st_mode):
                    generated.append(rel)
                else:
                    unknown.append(rel)
        else:
            generated.append(target)

def selection(role, roots, buckets=BUCKETS, extra_evidence=(), system=Path('/srv'), root_home=Path('/root')):
    files, unknown, skipped, generated, pending = [], [], [], [], {}
    for serial, root in roots.items():
        dev = root.stat().st_dev
        for child in sorted(root.iterdir()):
            rel = serial+'/'+child.name
            if child.name in EVIDENCE or rel in extra_evidence or (role == 'client' and child.name in {'m','w','j'}):
                walk(child, rel, dev, files, unknown, skipped)
            elif child.name == 'lost+found' and child.is_dir() and not child.is_symlink() and not any(child.iterdir()):
                skipped.append(rel)
            elif child.name == {'objects':'rustfs-data','metadata':'redis'}.get(role):
                excluded_store(role, child, rel, generated, unknown, buckets)
            elif role == 'client' and child.name == 'trace-exact-02' and child.is_dir() and not child.is_symlink():
                generated.append(rel)
            else:
                unknown.append(rel)
    first = next(iter(roots.values()))
    for rel in PENDING[role]:
        p = first/rel
        pending[rel] = p.exists() and not p.is_symlink()
    # Empty former mountpoints are normal. Anything left underneath must be resolved.
    system_allowed = {'metadata': {'ax-metadata'}, 'client': {'ax-client','ax-pilot-session'},
        'objects': {*(f'ax-objects-{i}' for i in range(4)), *(f'ax-rustfs-data-{i}' for i in range(4))}}
    for path in sorted(system.glob('ax-*')):
        rel = 'system/srv/'+path.name
        if path.name in {'ax-receipts', 'ax-logs'} or rel in extra_evidence:
            walk(path, rel, system.stat().st_dev, files, unknown, skipped)
        elif path.name in system_allowed[role] and path.is_dir() and not path.is_symlink() and not any(path.iterdir()):
            skipped.append('system/srv/'+path.name)
        else:
            unknown.append('system/srv/'+path.name)
    for path in sorted(root_home.glob('ax-*')):
        rel = 'system/root/'+path.name
        if path.name == 'ax-pool-b-bootstrap-01' or rel in extra_evidence:
            walk(path, rel, root_home.stat().st_dev, files, unknown, skipped)
        else:
            unknown.append(rel)
    return files, sorted(unknown), sorted(skipped), sorted(generated), pending

def service_config(role):
    if role == 'client':
        return {'role': role, 'binaries': {
            path: sha(Path(path)) for path in ('/usr/local/bin/juicefs','/usr/local/bin/opencode')
            if Path(path).is_file()}}
    if role == 'objects':
        name = 'ax-pilot-20261111-b-pool-b-objects-01-rustfs-1'
        x = json.loads(command('docker','inspect',name))[0]
        mounts = sorted((m for m in x['Mounts'] if m.get('Type') == 'bind'),
                        key=lambda m: (m['Source'], m['Destination']))
        return {'container': name, 'image': x['Image'],
                'image_reference': x['Config']['Image'],
                'mounts': [{'source': m['Source'], 'destination': m['Destination']} for m in mounts],
                'switches': sorted(v for v in x['Config']['Env'] if re.fullmatch(
                 r'RUSTFS_EC_[A-Z_]+=(?:true|false|[0-9]+)', v))}
    path = Path('/etc/redis/ax-pilot-20261111-b.conf')
    values = []
    for line in path.read_text().splitlines():
        fields = line.strip().split(maxsplit=1)
        if fields and fields[0] in REDIS_KEYS:
            values.append(fields)
    need(any(x == ['dir','/srv/ax-metadata/redis'] for x in values), 'Redis test directory differs')
    return {'configuration_sha256': sha(path), 'settings': values, 'unit': 'ax-pilot-redis.service'}

def diagnostics(role):
    if role == 'objects':
        return subprocess.check_output(['docker', 'logs', '--tail', '4000',
             'ax-pilot-20261111-b-pool-b-objects-01-rustfs-1'], stderr=subprocess.STDOUT, timeout=25)
    unit = 'ax-pilot-redis.service' if role == 'metadata' else 'ax-c4*'
    return command('journalctl', '-u', unit, '--no-pager', '-o',
                   'short-iso', '-n', '4000', timeout=25).encode()

def manifest(files):
    h = hashlib.sha256()
    for path, _, st in files:
        h.update(f'{path}\0{st.st_size}\0{st.st_mtime_ns}\0{st.st_ino}\n'.encode())
    return h.hexdigest()

def pack(archive, files, inventory, logs):
    rows = []
    with archive.open('xb') as raw, gzip.GzipFile(fileobj=raw, mode='wb', compresslevel=1, mtime=0) as gz, tarfile.open(
        fileobj=gz, mode='w|', format=tarfile.PAX_FORMAT) as out:
        def add_data(name, data):
            entry = tarfile.TarInfo(name)
            entry.mode, entry.size = 0o600, len(data)
            out.addfile(entry, io.BytesIO(data))
            rows.append({'path': name, 'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest()})
        add_data('retirement-inventory.json', encoded(inventory))
        add_data('private-service-log.txt', logs)
        for name, path, before in files:
            with os.fdopen(os.open(path, os.O_RDONLY | os.O_NOFOLLOW), 'rb') as f:
                st = os.fstat(f.fileno())
                identity = lambda v: (v.st_dev, v.st_ino, v.st_mode, v.st_size, v.st_mtime_ns)
                need(identity(st) == identity(before), 'Evidence member changed before pack')
                h = hashlib.sha256()
                class Reader:
                    def read(self, size):
                        data = f.read(size)
                        h.update(data)
                        return data
                e = tarfile.TarInfo('files/'+name)
                e.mode, e.size, e.mtime = st.st_mode & 0o777, st.st_size, int(st.st_mtime)
                out.addfile(e, Reader())
                need(identity(os.fstat(f.fileno())) == identity(before), 'Evidence member changed during pack')
                rows.append({'path': e.name, 'bytes': e.size, 'sha256': h.hexdigest()})
        entry = tarfile.TarInfo('MANIFEST.ndjson')
        data = ''.join(json.dumps(r, separators=(',', ':'))+'\n' for r in rows).encode()
        entry.mode, entry.size = 0o600, len(data)
        out.addfile(entry, io.BytesIO(data))
    check_pack(archive)
    return {'bytes': archive.stat().st_size, 'sha256': sha(archive), 'verified_members': len(rows),
            'manifest_sha256': hashlib.sha256(data).hexdigest()}

def check_pack(path):
    seen = []
    with tarfile.open(path, 'r|gz') as bundle:
        for member in bundle:
            need(member.isfile() and safe_name(member.name), 'Unexpected archive type/path')
            f = bundle.extractfile(member)
            if member.name == 'MANIFEST.ndjson':
                data = f.read()
                need([json.loads(r) for r in data.splitlines()] == seen
                     and len({x['path'] for x in seen}) == len(seen) and bundle.next() is None,
                     'Archive member SHA or manifest differs')
                return len(seen)
            h = hashlib.sha256()
            while chunk := f.read(1024*1024):
                h.update(chunk)
            seen.append({'path': member.name, 'bytes': member.size, 'sha256': h.hexdigest()})
    raise ValueError('Archive lacks manifest')

def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('action', choices=('scan', 'pack', 'verify'))
    p.add_argument('role', choices=ROLES)
    p.add_argument('--tag', required=True)
    p.add_argument('--scan-sha256')
    p.add_argument('--max-source-bytes', type=int, default=32*1024**3)
    p.add_argument('--generated-bucket', action='append', default=[],
                   help='An exact B-only RustFS bucket after proving its inputs/rebuild route')
    p.add_argument('--extra-evidence', action='append', default=[],
                   help='An exact unknown evidence root now added to the private archive')
    p.add_argument('--archive', type=Path, help='For verify only; archive path on the independent reader')
    args = p.parse_args()
    need(IDENT.fullmatch(args.tag), 'Use a unique lowercase tag')
    if args.action == 'verify':
        need(args.archive is not None, 'Archive required')
        print(json.dumps({'verified_members': check_pack(args.archive), 'sha256': sha(args.archive)}))
        return
    os.umask(0o077)
    need(all(re.fullmatch(r'[a-z0-9][a-z0-9-]{2,61}', n) for n in args.generated_bucket)
         and (not args.generated_bucket or args.role == 'objects'), 'Invalid generated bucket classification')
    need(all(safe_name(n) and len(n.split('/')) in (2,3) for n in args.extra_evidence),
         'Invalid added evidence root')
    check_cold(args.role)
    started = time.monotonic()
    out = Path('/var/tmp')/f'ax-b-retire-{args.tag}-{args.role}'
    if args.action == 'scan':
        out.mkdir(mode=0o700)
    else:
        st = out.lstat()
        need(stat.S_ISDIR(st.st_mode) and st.st_uid == 0 and stat.S_IMODE(st.st_mode) == 0o700
             and args.scan_sha256 == sha(out/'inventory.json'), 'Original scan digest required')
    data = disk_rows(args.role)
    with ExitStack() as stack:
        roots = {serial: stack.enter_context(mount(row)) for serial, row in data.items()}
        files, unknown, skipped, generated, pending = selection(args.role, roots,
            BUCKETS | set(args.generated_bucket), args.extra_evidence)
        total = sum(st.st_size for _, _, st in files)
        value = {'schema': MARKER, 'tag': args.tag, 'role': args.role,
                 'instance': ROLES[args.role][0], 'boot_id': Path('/proc/sys/kernel/random/boot_id').read_text().strip(),
                 'disks': {k: {'uuid': v['uuid'], 'size': v['size']} for k,v in data.items()},
                 'evidence_files': len(files), 'evidence_bytes': total, 'inventory_digest': manifest(files),
                 'unknown': unknown, 'excluded_nondata': skipped, 'generated_store_paths': generated,
                 'original_pending': pending, 'service_config': service_config(args.role),
                 'reviewed_generated_buckets': sorted(args.generated_bucket),
                 'additional_archive_roots': sorted(args.extra_evidence)}
        if args.action == 'scan':
            write(out/'inventory.json', value)
        else:
            original = json.loads((out/'inventory.json').read_bytes())
            changed = sorted(key for key in set(original) | set(value) if original.get(key) != value.get(key))
            need(not changed and not unknown and total <= args.max_source_bytes,
                 'Cold pack refused; changed fields: '+','.join(changed)
                 +f'; unknown={len(unknown)}; bytes={total}; budget={args.max_source_bytes}')
            need(not (out/'evidence.tar.gz').exists(), 'Archive already exists; never overwrite')
            proof = pack(out/'evidence.tar.gz', files, value, diagnostics(args.role))
    result = {'role': args.role, 'tag': args.tag, 'out': str(out), 'all_data_disks_unmounted': True,
              'inventory_sha256': sha(out/'inventory.json'), 'evidence_files': len(files),
              'evidence_bytes': total, 'unknown_count': len(unknown), 'elapsed_seconds': time.monotonic()-started}
    if args.action == 'pack':
        result['archive'] = proof
        write(out/'packed.json', result)
    print(json.dumps(result), flush=True)

if __name__ == '__main__':
    main()
