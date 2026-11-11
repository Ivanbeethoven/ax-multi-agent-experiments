#!/usr/bin/env python3
"""Restore the original Pool B service, run one guarded JuiceFS `gc`, then close.

The node performs only reviewed, local maintenance steps:

* mount the original whole-disk ext4 filesystems by serial/UUID (never mkfs),
* re-create the original RustFS bind layout,
* start the original RustFS container / Redis unit and verify their identity,
* capture the original Redis AOF identity, per-disk bytes/inodes and file counts,
* and - only on an explicit ``gc <outdir>`` stdin command - run the pinned
  JuiceFS garbage collector with a fixed argv (``juicefs gc --threads 1 <meta>``,
  ``--delete``/``--compact``/``--force`` are rejected) and keep its raw output.

The service stops only on the explicit ``close`` command. EOF no longer stops
the service immediately: the node waits ``--eof-grace`` seconds (default 60)
after EOF and then closes, so a dropped controller stdin cannot unmount a disk
while the coordinator still believes the service is up.

stdin protocol (one command per line)::

    snapshot
    verify [before|after]
    gc <outdir>          # objects role only, synchronous
    close

stdout is a JSON-lines protocol; JuiceFS output never reaches it.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import pwd
import re
import socket
import stat
import subprocess as sp
import sys
import time
from urllib.request import ProxyHandler, Request, build_opener

OBJECTS = (
    ('bp1bhdngko7nq3c2i340', '96ee330f-4aa4-499a-89ce-3e236e63a6ce'),
    ('bp1bhdngko7nq3c2i341', '43dd6857-2704-43cf-83d7-25271f91c12a'),
    ('bp1bhdngko7nq3c2i342', '282adf59-0059-4164-b424-39f905afe916'),
    ('bp1bhdngko7nq3c2i343', '9f11a3e6-3a71-40d7-8e30-a46c89cbdcdf'),
)
METADATA = (('bp14nenf7osz9v1rkk5l', '24381dda-bee5-43bf-933b-fed798d36fbe'),)
CID = 'abc0a432b493e0a820a380738e359532921693e82e015d5c9982868084e33893'
IMAGE = 'sha256:942893356344d292d9fcccadfceb039467c589ba10b4045cb148c0a65d2d3fec'
RUSTFS_IP = '10.20.2.170'
REDIS_IP = '10.20.2.169'
UNIT = 'ax-pilot-redis.service'
CONF = Path('/etc/redis/ax-pilot-20261111-b.conf')
OPENER = build_opener(ProxyHandler({}))

JUICEFS_SHA256 = '6d450d3c151b6affede364d9a198d2505111b0612a5fb4d31613444c635174cc'
DEFAULT_JUICEFS = '/usr/local/bin/juicefs'
GC_THREADS = '1'
GC_FORBIDDEN = ('--delete', '--compact', '--force')
META_URL = re.compile(r'^redis://10\.20\.2\.169:6379/([0-9]{1,3})$')
ATTEMPT = re.compile(r'^[a-z0-9][a-z0-9-]{2,40}$')
MARKER_DIR = Path('/run')
AOF_BASELINE = {}


def require(ok, reason):
    if not ok:
        raise ValueError(reason)


def get(*argv):
    return sp.check_output(argv, text=True, timeout=20).strip()


def run(*argv, timeout=40):
    sp.run(argv, check=True, timeout=timeout, stdout=sys.stderr)


def emit(**row):
    print(json.dumps(row), flush=True)


def utcnow():
    return datetime.now(timezone.utc).isoformat()


def sha256_file(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def write_new(path, value):
    with Path(path).open('x', encoding='utf-8') as stream:
        json.dump(value, stream, indent=2, allow_nan=False)
        stream.write('\n')


def instance():
    base = 'http://100.100.100.200/latest/'
    with OPENER.open(Request(base+'api/token', method='PUT',
                             headers={'X-aliyun-ecs-metadata-token-ttl-seconds': '60'}), timeout=3) as reply:
        token = reply.read().decode()
    with OPENER.open(Request(base+'meta-data/instance-id',
                             headers={'X-aliyun-ecs-metadata-token': token}), timeout=3) as reply:
        return reply.read().decode().strip()


def boot_id():
    return Path('/proc/sys/kernel/random/boot_id').read_text().strip()


def plain_dir(path):
    require(path.resolve(strict=True) == path and path.is_dir(), 'Invalid directory: '+str(path))


def empty_target(path):
    plain_dir(path)
    require(not path.is_mount() and not list(path.iterdir()), 'Mount target not empty and unmounted: '+str(path))


def get_disk(serial, uuid, blocks):
    matches = [row for row in blocks if row.get('serial') == serial]
    require(len(matches) == 1, 'Original whole disk not uniquely found: '+serial)
    row = matches[0]
    require(row['type'] == 'disk' and row['size'] == 60*1024**3 and not row.get('children')
            and not any(row['mountpoints'] or []), 'Wrong disk topology: '+serial)
    device = Path(row['name'])
    mode = device.stat()
    require(device.parent == Path('/dev') and not device.is_symlink() and stat.S_ISBLK(mode.st_mode)
            and mode.st_rdev != Path('/').stat().st_dev, 'Wrong block node: '+serial)
    keys = dict(line.split('=', 1) for line in get('blkid', '-p', '-o', 'export', str(device)).splitlines())
    require(keys.get('TYPE') == 'ext4' and keys.get('UUID') == uuid and 'PTTYPE' not in keys,
            'Original ext4 UUID or type differs: '+serial)
    require(sum(row.get('uuid') == uuid for row in blocks) == 1, 'Duplicate ext4 UUID: '+uuid)
    return device, serial, mode.st_rdev


def inspect_rustfs(targets):
    require(get('docker', 'ps', '-q') == '', 'Another Docker container is running')
    row, = json.loads(get('docker', 'inspect', CID))
    cfg = row['HostConfig']
    require(row['Id'] == CID and row['Image'] == IMAGE and row['State']['Status'] == 'exited'
            and cfg['RestartPolicy']['Name'] == 'no' and cfg['NetworkMode'] == 'host',
            'Original stopped RustFS container or policy differs')
    mapping = {str(p): '/data/rustfs'+str(i) for i, p in enumerate(targets)}
    mapping['/srv/ax-objects-0/rustfs-logs'] = '/var/log/rustfs'
    require(len(row['Mounts']) == len(mapping) and all(x['Type'] == 'bind' and x['RW']
            for x in row['Mounts']) and {x['Source']: x['Destination'] for x in row['Mounts']} == mapping,
            'Original RustFS bind specification differs')


def redis_processes():
    """Every process started as this exact redis-server configuration."""
    found = []
    for entry in Path('/proc').iterdir():
        if not entry.name.isdigit():
            continue
        try:
            argv = [part.decode() for part in (entry/'cmdline').read_bytes().split(b'\0') if part]
        except OSError:
            continue
        if argv[:2] == ['/usr/bin/redis-server', str(CONF)]:
            found.append(int(entry.name))
    return found


def redis_listening():
    return any(row.rstrip().endswith(':6379') for row in get('ss', '-H', '-ltn').splitlines())


def inspect_redis():
    conf = CONF.read_text()
    expected = '''bind 127.0.0.1 10.20.2.169
protected-mode no
port 6379
daemonize no
supervised no
databases 256
dir /srv/ax-metadata/redis
appendonly yes
appendfsync always
save ""
maxmemory 10737418240
maxmemory-policy noeviction
logfile ""
'''
    unit = Path('/etc/systemd/system') / UNIT
    require(CONF.resolve(strict=True) == CONF and conf == expected and unit.resolve(strict=True) == unit
            and 'ExecStart=/usr/bin/redis-server '+str(CONF)+'\n' in unit.read_text(),
            'Original Redis unit or configuration differs')
    props = dict(line.split('=', 1) for line in get('systemctl', 'show', UNIT,
        '--property=ActiveState,UnitFileState,FragmentPath,DropInPaths,ExecStart,Restart').splitlines())
    require(props['ActiveState'] == 'inactive' and props['UnitFileState'] == 'static'
            and props['FragmentPath'] == str(unit) and not props['DropInPaths'] and props['Restart'] == 'no'
            and 'argv[]=/usr/bin/redis-server '+str(CONF)+' ;' in props['ExecStart'],
            'Redis must be the original inactive unit without overrides')
    # A stopped-then-started node may already carry the original redis-server
    # started outside this unit. Accept only the exact original configuration,
    # never a foreign listener.
    pids = redis_processes()
    listening = redis_listening()
    require(not listening or pids, 'A foreign process listens on port 6379')
    require(not pids or listening, 'The original redis-server is not listening')
    return bool(pids)


def data_path(path, device, uid):
    plain_dir(path)
    s = path.stat()
    require(s.st_dev == device and s.st_uid == uid and not path.is_mount(),
            'Original data path identity differs: '+str(path))


def findmnt(path, field='TARGET'):
    result = sp.run(['findmnt', '-n', '-o', field, '--mountpoint', str(path)],
                    capture_output=True, text=True, timeout=20)
    return result.stdout.strip() if result.returncode == 0 else ''


def mount_register(argv, target, mounted):
    """Mount first, then always register, then let the caller verify.

    A timeout can fire after the kernel already created the mount, so the
    registration lives in ``finally``: no mount may stay unknown to the closer.
    """
    try:
        run(*argv)
    finally:
        if target.is_mount() and target not in mounted:
            mounted.append(target)


def check_bind(source_stat, target_stat):
    require((source_stat.st_dev, source_stat.st_ino) == (target_stat.st_dev, target_stat.st_ino),
            'RustFS bind inode differs')


def aof_delta(before, after):
    old = {part['name']: part for part in before['parts']}
    new = {part['name']: part for part in after['parts']}
    removed = sorted(set(old)-set(new))
    added = sorted(set(new)-set(old))
    shrunk = [name for name in old if name in new and new[name]['size'] < old[name]['size']]
    rewritten = [name for name in old if name in new and new[name]['size'] == old[name]['size']
                 and new[name]['sha256'] != old[name]['sha256']]
    touched = [name for name in old if name in new and new[name]['mtime_ns'] != old[name]['mtime_ns']]
    changed = bool(removed or added or shrunk or rewritten or touched or before['manifest'] != after['manifest']
                   or before['sha256'] != after['sha256'])
    return dict(changed=changed, removed=removed, added=added, shrunk=shrunk, rewritten=rewritten,
                touched=touched)


def aof_identity(root):
    """Full `{size, mtime_ns, sha256}` identity of the original Redis AOF."""
    root = Path(root)
    subdir = root/'appendonlydir'
    plain_dir(subdir)
    manifest = subdir/'appendonly.aof.manifest'
    require(manifest.resolve(strict=True) == manifest and manifest.is_file(), 'Original AOF manifest absent')
    entries = [line.split() for line in manifest.read_text().splitlines()]
    require(entries and any(len(row) == 6 and row[5] == 'b' for row in entries), 'AOF base absent')
    parts = []
    for row in entries:
        require(len(row) == 6 and row[0] == 'file' and Path(row[1]).name == row[1]
                and row[2] == 'seq' and row[3].isdigit() and row[4] == 'type' and row[5] in ('b', 'i', 'h'),
                'AOF manifest entry differs')
        part = subdir/row[1]
        require(part.resolve(strict=True) == part and part.is_file() and part.stat().st_dev == root.stat().st_dev,
                'Original AOF part absent or on wrong disk')
        info = part.stat()
        parts.append(dict(name=row[1], type=row[5], size=info.st_size, mtime_ns=info.st_mtime_ns,
                          sha256=sha256_file(part)))
    info = manifest.stat()
    combined = hashlib.sha256()
    for part in parts:
        combined.update(part['name'].encode())
        combined.update(part['sha256'].encode())
    return dict(path=str(root), device=root.stat().st_dev,
                manifest=dict(name=manifest.name, size=info.st_size, mtime_ns=info.st_mtime_ns,
                              sha256=sha256_file(manifest)),
                parts=parts, sha256=combined.hexdigest())


def verify_aof(root, stage='before'):
    """Compare the AOF ``{size, mtime_ns, sha256}`` identity with the baseline.

    ``before`` is strict: any change between the ready snapshot and the pre-GC
    check means somebody wrote while the maintenance window held the metadata
    service, so the node refuses to run ``gc``. ``after`` tolerates an append
    (JuiceFS may clean stale sessions) but rejects a rewrite/shrink/removal.
    """
    require(stage in ('baseline', 'before', 'after'), 'Unknown AOF stage: '+str(stage))
    after = aof_identity(root)
    key = str(root)
    before = AOF_BASELINE.get(key)
    if before is None:
        AOF_BASELINE[key] = after
        return dict(stage='baseline', before=after, after=after, delta=aof_delta(after, after), unchanged=True)
    delta = aof_delta(before, after)
    value = dict(stage=stage, before=before, after=after, delta=delta, unchanged=not delta['changed'])
    if stage == 'after':
        require(not delta['removed'] and not delta['added'] and not delta['shrunk'] and not delta['rewritten']
                and before['manifest']['name'] == after['manifest']['name'],
                'Original Redis AOF was rewritten, shrunk or replaced after gc')
    else:
        require(not delta['changed'],
                'Original Redis AOF changed while the metadata service was held; someone wrote - refuse gc')
    return value


def snapshots(roots, disks):
    rows = []
    for path, (device, serial, _rdev) in zip(roots, disks):
        s = os.statvfs(path)
        rows.append(dict(path=str(path), device=str(device), serial=serial,
                         f_bfree=s.f_bfree, f_ffree=s.f_ffree, block_bytes=s.f_frsize,
                         available_bytes=s.f_bavail*s.f_frsize, total_bytes=s.f_blocks*s.f_frsize,
                         available_inodes=s.f_favail, total_inodes=s.f_files,
                         st_dev=path.stat().st_dev))
    return rows


def disk_usage(root, timeout):
    """Actual per-disk bytes and file count; never infer them."""
    reading = sp.check_output(['du', '-sx', '-B1', str(root)], text=True, timeout=timeout).strip()
    require(reading and reading.split()[0].isdigit(), 'du reading missing for '+str(root))
    raw = sp.check_output(['find', str(root), '-xdev', '-type', 'f', '-print0'], timeout=timeout)
    require(isinstance(raw, (bytes, bytearray)), 'find file count reading missing for '+str(root))
    return dict(path=str(root), du_bytes=int(reading.split()[0]), files=raw.count(b'\0'))


def usage(roots, timeout):
    return [disk_usage(root, timeout) for root in roots]


def pin_meta_url(value):
    match = META_URL.match(value or '')
    require(match is not None and 20 <= int(match.group(1)) < 256,
            'Original Pool B metadata URL required: redis://10.20.2.169:6379/<20..255>')
    return value


def require_gc_argv(argv):
    require(isinstance(argv, list) and argv and all(isinstance(value, str) for value in argv),
            'JuiceFS gc argv must be a nonempty list of strings')
    require(Path(argv[0]).name == 'juicefs', 'JuiceFS gc argv must start with the pinned juicefs binary')
    forbidden = [value for value in argv[1:] if value.split('=', 1)[0] in GC_FORBIDDEN]
    require(not forbidden,
            'Forbidden destructive JuiceFS gc flags: '+', '.join(forbidden))
    require(len(argv) == 5 and argv[1:4] == ['gc', '--threads', GC_THREADS],
            'JuiceFS gc argv must be exactly: juicefs gc --threads '+GC_THREADS+' <meta-url>')
    pin_meta_url(argv[4])
    return argv


def gc_argv(meta_url, juicefs=DEFAULT_JUICEFS):
    return require_gc_argv([str(juicefs), 'gc', '--threads', GC_THREADS, pin_meta_url(meta_url)])


def juicefs_processes():
    result = sp.run(['pgrep', '-a', 'juicefs'], capture_output=True, text=True, timeout=20)
    if result.returncode == 1:
        return []
    require(result.returncode == 0, 'pgrep juicefs failed with exit '+str(result.returncode))
    return [line for line in result.stdout.splitlines() if line.strip()]


def established_redis_sockets():
    rows = get('ss', '-H', '-tn', 'state', 'established').splitlines()
    return [row for row in rows if REDIS_IP+':6379' in row]


def redis_info(section):
    text = get('redis-cli', '-h', '127.0.0.1', 'INFO', section)
    return dict(line.split(':', 1) for line in text.splitlines() if ':' in line)


def redis_clients():
    """CLIENT LIST proof that no JuiceFS/Go session survived the maintenance."""
    lines = get('redis-cli', '-h', '127.0.0.1', 'CLIENT', 'LIST').splitlines()
    probe = [line for line in lines if 'cmd=client' in line]
    peers = [line for line in lines if 'cmd=client' not in line]
    suspect = [line for line in peers if any(token in line for token in
               ('name=juicefs', 'lib-name=juicefs', 'lib-name=go-redis'))]
    return dict(probe_connections=len(probe), peers=len(peers), suspect=suspect,
                connected_clients=int(redis_info('clients').get('connected_clients', '0')))


def aof_files_bytes(root):
    """Authoritative on-disk AOF size, used when INFO omits aof_current_size."""
    total = 0
    for path in sorted(Path(root).rglob('*')):
        if path.is_file() and (path.suffix in ('.aof', '.rdb') or path.name.startswith('appendonly')):
            total += path.stat().st_size
    return total


def redis_aof(fallback=None, seconds=30):
    """AOF counters appear only once the original AOF is loaded and active.

    Redis 7 dropped ``aof_current_size`` from ``INFO persistence``; the file
    sizes under the original appendonly directory are the authoritative figure.
    """
    deadline = time.monotonic()+seconds
    info = redis_info('persistence')
    while 'aof_current_size' not in info and fallback is None:
        require(time.monotonic() < deadline,
                'Redis persistence info lacks aof_current_size after '+str(seconds)+'s: '
                +str(sorted(info))[:200])
        time.sleep(1)
        info = redis_info('persistence')
    if 'aof_current_size' in info:
        size, source = int(info['aof_current_size']), 'info'
    else:
        size, source = int(fallback), 'appendonly-files'
    return dict(aof_enabled=int(info.get('aof_enabled', '0')), aof_current_size=size,
                aof_current_size_source=source,
                aof_rewrite_in_progress=int(info.get('aof_rewrite_in_progress', '0')),
                aof_last_write_status=info.get('aof_last_write_status'),
                aof_base_size=int(info.get('aof_base_size', '0')))


def run_gc(outdir, meta_url, juicefs=DEFAULT_JUICEFS, timeout=14400):
    """Run exactly one pinned ``juicefs gc --threads 1 <meta>`` synchronously."""
    outdir = Path(outdir)
    require(outdir.is_absolute() and not outdir.is_symlink(), 'GC output must be an absolute plain path')
    require(not outdir.resolve().is_relative_to(Path('/srv')), 'GC evidence must never be written to a data disk')
    require(not outdir.exists(), 'GC output directory must be fresh: '+str(outdir))
    binary = Path(juicefs)
    require(binary.is_absolute() and binary.is_file() and not binary.is_symlink(),
            'Pinned JuiceFS binary path required: '+str(binary))
    digest = sha256_file(binary)
    require(digest == JUICEFS_SHA256, 'Pinned JuiceFS binary SHA256 differs: '+str(binary))
    argv = gc_argv(meta_url, str(binary))
    require(not juicefs_processes(), 'Another JuiceFS process runs on this node')
    require(not established_redis_sockets(), 'An established metadata Redis session exists before gc')
    outdir.mkdir(mode=0o700)
    started, timed_out, exit_code = utcnow(), False, None
    with (outdir/'gc.stdout').open('xb') as stdout, (outdir/'gc.stderr').open('xb') as stderr:
        process = sp.Popen(argv, stdin=sp.DEVNULL, stdout=stdout, stderr=stderr)
        try:
            exit_code = process.wait(timeout=timeout)
        except sp.TimeoutExpired:
            timed_out = True
            process.kill()
            process.wait()
            exit_code = process.returncode
    finished = utcnow()
    exit_doc = dict(schema='b-c1-juicefs-gc-v1', at=finished, argv=argv, threads=1,
                    juicefs=dict(path=str(binary), sha256=digest),
                    started_at=started, finished_at=finished, exit_code=exit_code, timed_out=timed_out,
                    stdout=dict(path=str(outdir/'gc.stdout'), sha256=sha256_file(outdir/'gc.stdout')),
                    stderr=dict(path=str(outdir/'gc.stderr'), sha256=sha256_file(outdir/'gc.stderr')),
                    forbidden_flags=[])
    write_new(outdir/'gc-exit.json', exit_doc)
    require(not timed_out, 'JuiceFS gc timed out; inspect gc-exit.json')
    require(exit_code == 0, 'JuiceFS gc failed with exit '+str(exit_code)+'; inspect gc-exit.json')
    deadline = time.monotonic()+60
    while True:
        processes, sockets = juicefs_processes(), established_redis_sockets()
        if not processes and not sockets:
            break
        require(time.monotonic() < deadline, 'A JuiceFS process or metadata Redis socket remains after gc')
        time.sleep(1)
    post = dict(schema='b-c1-juicefs-gc-post-v1', at=utcnow(), juicefs_processes=processes,
                established_redis_sockets=sockets, passed=True,
                scope='Objects node only: no JuiceFS process and no established metadata Redis socket remain. '
                      'Redis-side CLIENT LIST/AOF proof comes from the metadata node.')
    write_new(outdir/'gc-post.json', post)
    return dict(outdir=str(outdir), exit=exit_doc, post=post)


def marker_paths(attempt, directory=MARKER_DIR):
    require(ATTEMPT.match(attempt or ''), 'Explicit short attempt name required')
    return Path(directory)/('b-c1-gc-'+attempt+'.started'), Path(directory)/('b-c1-gc-'+attempt+'.done')


def begin_attempt(attempt, role, directory=MARKER_DIR):
    started, done = marker_paths(attempt, directory)
    require(not started.exists() and not done.exists(),
            'Attempt already started or complete; manual review required, never automatic retry')
    write_new(started, dict(at=utcnow(), attempt=attempt, role=role, pid=os.getpid(), boot_id=boot_id()))
    return started, done


def stop_service(objects, started):
    if objects:
        if started and get('docker', 'inspect', '-f', '{{.State.Running}}', CID) == 'true':
            run('docker', 'stop', '-t', '30', CID)
        require(get('docker', 'ps', '-q') == '', 'Original RustFS container did not close')
    else:
        if started:
            run('systemctl', 'stop', UNIT)
        require(get('systemctl', 'show', '-p', 'ActiveState', '--value', UNIT) == 'inactive',
                'Original Redis unit did not close')


def unmount_all(paths, errors):
    for path in reversed(paths):
        if path.is_mount():
            try:
                run('umount', str(path), timeout=120)
            except Exception as error:  # keep collecting; never leave silently
                errors.append('umount '+str(path)+': '+repr(error))


def close_node(objects, roots, targets, errors, started):
    """Stop the service, unmount everything and recheck; never swallow an error."""
    try:
        stop_service(objects, started)
    except Exception as error:
        errors.append('stop-service: '+repr(error))
    try:
        unmount_all([*roots, *targets], errors)
    except Exception as error:
        errors.append('umount: '+repr(error))
    try:
        mounted = [str(path) for path in [*roots, *targets] if findmnt(path)]
        require(not mounted, 'Mounts remain after close: '+', '.join(mounted))
    except Exception as error:
        errors.append('mount-recheck: '+repr(error))
    try:
        require(not juicefs_processes(), 'A JuiceFS process remains after close')
    except Exception as error:
        errors.append('juicefs-process: '+repr(error))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('role', choices=('objects', 'metadata'))
    parser.add_argument('--preflight', action='store_true')
    parser.add_argument('--attempt', help='Short attempt ID used for the /run markers')
    parser.add_argument('--meta-url', help='Original Pool B metadata URL for the objects role')
    parser.add_argument('--juicefs', default=DEFAULT_JUICEFS)
    parser.add_argument('--gc-timeout', type=float, default=14400)
    parser.add_argument('--du-timeout', type=float, default=1800)
    parser.add_argument('--eof-grace', type=float, default=60)
    args = parser.parse_args()
    objects = args.role == 'objects'
    if objects:
        pin_meta_url(args.meta_url)
    ip = RUSTFS_IP if objects else REDIS_IP
    expected_instance = 'i-bp1bhdngko7nq3c3jrhp' if objects else 'i-bp14nenf7osz9v1snsp2'
    require(os.geteuid() == 0 and socket.gethostname() == 'ax-pilot-20261111-b-'+args.role
            and instance() == expected_instance, 'Wrong B node identity')
    interfaces = json.loads(get('ip', '-j', '-4', 'addr', 'show'))
    require(sum(a['local'] == ip for nic in interfaces for a in nic['addr_info']) == 1, 'Wrong B private address')
    roots = [Path('/srv/ax-objects-'+str(i)) for i in range(4)] if objects else [Path('/srv/ax-metadata')]
    targets = [Path('/srv/ax-rustfs-data-'+str(i)) for i in range(4)] if objects else []
    blocks = json.loads(get('lsblk', '-Jbp', '-o', 'NAME,TYPE,SERIAL,SIZE,UUID,MOUNTPOINTS'))['blockdevices']
    disks = [get_disk(serial, uuid, blocks) for serial, uuid in (OBJECTS if objects else METADATA)]
    require(len(set(rdev for _, _, rdev in disks)) == len(roots), 'Expected distinct data devices')
    for path in [*roots, *targets]:
        empty_target(path)
    if objects:
        inspect_rustfs(targets)
    else:
        inspect_redis()
    if args.preflight:
        emit(status='preflight', role=args.role, instance=expected_instance,
             devices=[str(device) for device, _, _ in disks])
        return
    require(args.attempt, 'Explicit --attempt is required for a real maintenance window')
    started_marker, done_marker = begin_attempt(args.attempt, args.role)
    mounted = []
    errors = []
    started = False
    failure = None
    data_root = None if objects else roots[0]/'redis'
    aof_baseline = redis_baseline = None
    try:
        for root, (device, _serial, rdev) in zip(roots, disks):
            mount_register(['mount', '-t', 'ext4', '-o', 'rw', str(device), str(root)], root, mounted)
            require(findmnt(root) == str(root) and root.stat().st_dev == rdev
                    and 'rw' in findmnt(root, 'OPTIONS').split(','),
                    'Original writable filesystem not mounted: '+str(root))
        if objects:
            for root, target in zip(roots, targets):
                data = root/'rustfs-data'
                data_path(data, root.stat().st_dev, 10001)
                require(any(data.iterdir()), 'Original RustFS data directory is empty')
                mount_register(['mount', '--bind', str(data), str(target)], target, mounted)
                require(findmnt(target, 'FSROOT') == '/rustfs-data', 'RustFS bind root differs: '+str(target))
                check_bind(data.stat(), target.stat())
            data_path(roots[0]/'rustfs-logs', roots[0].stat().st_dev, 10001)
            inspect_rustfs(targets)
            started = True
            run('docker', 'start', CID)
        else:
            data_path(data_root, roots[0].stat().st_dev, pwd.getpwnam('redis').pw_uid)
            already = inspect_redis()
            started = True
            if not already:
                run('systemctl', 'start', UNIT)
        last = ''
        for _ in range(30):
            try:
                if objects:
                    with OPENER.open('http://'+ip+':9000/health', timeout=2) as reply:
                        ready = reply.status == 200
                        last = 'health '+str(reply.status)
                else:
                    # Try the private address first, then loopback: the original
                    # configuration binds both, but a start race can bind later.
                    answer = ''
                    for host in (ip, '127.0.0.1'):
                        try:
                            answer = get('redis-cli', '-h', host, 'PING').strip()
                        except Exception as error:
                            answer = 'error '+repr(error)
                        if answer == 'PONG':
                            break
                    ready = answer == 'PONG'
                    last = answer
                if ready:
                    break
            except (OSError, sp.SubprocessError) as error:
                last = repr(error)
            time.sleep(1)
        else:
            raise ValueError('Original service did not become ready: '+last[-300:])
        ready_row = dict(status='ready', role=args.role, instance=expected_instance, boot_id=boot_id(),
                         attempt=args.attempt, capacity=snapshots(roots, disks),
                         usage=usage(roots, args.du_timeout))
        if not objects:
            aof_baseline = verify_aof(data_root)
            redis_baseline = dict(clients=redis_clients(), aof=redis_aof(aof_files_bytes(data_root)))
            require(redis_baseline['aof']['aof_enabled'] == 1, 'Original Redis AOF must be enabled')
            require(redis_baseline['aof']['aof_rewrite_in_progress'] == 0, 'Redis AOF rewrite in progress')
            require(not redis_baseline['clients']['suspect'], 'A JuiceFS/Go Redis session exists at ready')
            ready_row.update(aof=aof_baseline, redis=redis_baseline)
        emit(**ready_row)
        for raw in sys.stdin:
            words = raw.split()
            require(words, 'Empty session command')
            command, options = words[0], words[1:]
            if command == 'close':
                require(not options, 'close takes no arguments')
                break
            if command == 'snapshot':
                require(not options, 'snapshot takes no arguments')
                emit(status='snapshot', role=args.role, capacity=snapshots(roots, disks))
                continue
            if command == 'verify':
                require(len(options) <= 1 and (not options or options[0] in ('before', 'after')),
                        'verify takes at most [before|after]')
                stage = options[0] if options else 'before'
                require(not juicefs_processes(), 'A JuiceFS process runs on this node')
                row = dict(status='verify', role=args.role, stage=stage)
                if objects:
                    sockets = established_redis_sockets()
                    require(not sockets, 'An established metadata Redis session remains on the objects node')
                    row.update(established_redis_sockets=sockets)
                else:
                    aof_row = verify_aof(data_root, stage)
                    clients, aof_info = redis_clients(), redis_aof(aof_files_bytes(data_root))
                    require(not clients['suspect'], 'A JuiceFS/Go Redis session is still connected')
                    require(aof_info['aof_rewrite_in_progress'] == 0, 'Redis AOF rewrite in progress')
                    require(clients['peers'] <= redis_baseline['clients']['peers'],
                            'A temporary Redis session remains')
                    if stage == 'before':
                        require(aof_info['aof_current_size'] == redis_baseline['aof']['aof_current_size'],
                                'Redis AOF grew before gc; someone wrote - refuse')
                    else:
                        require(aof_info['aof_current_size'] >= redis_baseline['aof']['aof_current_size'],
                                'Redis AOF shrank after gc')
                    row.update(aof=aof_row, redis_clients=clients,
                               redis_aof=dict(before=redis_baseline['aof'], after=aof_info,
                                              grew_bytes=aof_info['aof_current_size']
                                              - redis_baseline['aof']['aof_current_size']))
                row['passed'] = True
                emit(**row)
                continue
            if command == 'gc':
                require(objects, 'The JuiceFS collector runs on the objects node only')
                require(len(options) == 1, 'gc requires exactly one output directory')
                result = run_gc(options[0], args.meta_url, args.juicefs, args.gc_timeout)
                emit(status='gc', role=args.role, **result)
                emit(status='usage', phase='after', role=args.role,
                     usage=usage(roots, args.du_timeout))
                continue
            raise ValueError('Unsupported session command: '+command)
        else:
            emit(status='eof', role=args.role, grace_seconds=args.eof_grace,
                 note='EOF does not stop the service immediately; waiting the grace window for close')
            time.sleep(max(0.0, args.eof_grace))
    except Exception as error:
        failure = error
    finally:
        close_node(objects, roots, targets, errors, started)
    if failure is not None:
        if errors:
            print('close verification also failed: '+'; '.join(errors), file=sys.stderr, flush=True)
        raise failure
    require(not errors, 'Close verification failed: '+'; '.join(errors))
    write_new(done_marker, dict(at=utcnow(), attempt=args.attempt, role=args.role))
    emit(status='closed', role=args.role, instance=expected_instance, attempt=args.attempt,
         started_marker=str(started_marker), done_marker=str(done_marker))


if __name__ == '__main__':
    main()
