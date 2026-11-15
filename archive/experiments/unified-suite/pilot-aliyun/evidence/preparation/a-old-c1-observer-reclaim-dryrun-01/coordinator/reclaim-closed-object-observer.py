#!/usr/bin/env python3
"""Reclaim only historical Pool A C1 observer copies already covered by public archives.

The reviewed coordinator request pins the existing guard and its boundary inputs,
the canonical, original C1 and original fork plans, the tool, and each target's
role archive/check and OSS receipt.
For a nested publication, also pin the public outer archive/check and exact
public member name. Dry-run and apply use the same request and separate outputs.
"""

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import runpy
import shlex
import shutil
import socket
import stat
import subprocess
import sys
import tarfile


PILOT = Path('/srv/ax-pilot-control')
PREP = PILOT/'preparation'
CANONICAL = PILOT/'parallel-control-01'
ROOT = Path('/srv/ax-objects-0')
GUARD = PREP/'c123-old-c1-retention-guard-02/runner.py'
BUCKET = 'brewfs-agent-traces-20261023-4ccdae73'
HEX = re.compile(r'[0-9a-f]{64}')
TAG = re.compile(r'(?:cal|c1)-[a-z0-9-]{2,85}')


def require(ok, reason):
    if not ok:
        raise ValueError(reason)


def now():
    return datetime.now(timezone.utc)


def sha(data):
    return hashlib.sha256(data).hexdigest()


def file_sha(path):
    with path.open('rb') as src:
        return hashlib.file_digest(src, 'sha256').hexdigest()


def load(path):
    return json.loads(path.read_bytes())


def write_new(path, value):
    with path.open('x', encoding='utf-8') as dst:
        dst.write(json.dumps(value, indent=2, sort_keys=True)+'\n')


def pin(ref, expected=None, below=PILOT):
    p = Path(ref['path'])
    require(set(ref) == {'path', 'sha256'} and HEX.fullmatch(ref['sha256']) and p.is_relative_to(below)
            and p.resolve(strict=True) == p and p.is_file() and (expected is None or expected == p)
            and file_sha(p) == ref['sha256'], 'Wrong path or changed pinned file: '+str(p))
    return p


def rel(name):
    p = PurePosixPath(name)
    require(p.parts and str(p) == name and not p.is_absolute() and '..' not in p.parts
            and not any(c in name for c in ('\\', ':', '\x00')), 'Unsafe archive member')
    return p


def manifest(path):
    with tarfile.open(path, 'r|gz') as tar:
        item = tar.next()
        require(item and item.isfile() and item.name == '.suite-manifest.json' and item.size <= 16*1024**2,
                'Archive must start with a bounded manifest')
        body = json.load(tar.extractfile(item))
    require(body['schema'] == 'suite-archive-manifest-v1' and body['deferred'] == [] and body['files'],
            'Complete archived manifest required')
    return body


def public_readback(receipt, path, digest):
    up, prep = receipt['upload'], receipt['prepared']
    key = receipt['key']
    require(receipt['schema'] == 'pilot-cloud-oss-archive-v1' and receipt['bucket'] == BUCKET
            and re.fullmatch(r'pilot-results/20261111/[a-z0-9/-]+\.tar\.gz', key)
            and receipt['public_url'] == f'https://{BUCKET}.oss-cn-hangzhou.aliyuncs.com/{key}'
            and receipt['anonymous_public_head'] is True and up['full_internal_readback'] is True
            and up['overwrite'] is False and prep['passed'] is True and up['passed'] is True
            and prep['source'] == up['source'] == str(path)
            and prep['sha256'] == up['sha256'] == up['readback_sha256'] == digest
            and prep['bytes'] == up['bytes'] == path.stat().st_size, 'Exact public full readback required')


def checked_manifest(path, digest, receipt, cell, kind=None):
    expected = manifest(path)
    require(receipt['schema'] == 'suite-archive-check-v1' and receipt['passed'] is True
            and receipt['archive'] == str(path) and receipt['sha256'] == digest
            and receipt['bytes'] == path.stat().st_size and receipt['deferred'] == []
            and receipt['cell_id'] == expected['cell_id'] == cell and receipt['kind'] == expected['kind']
            and (kind is None or receipt['kind'] == kind) and receipt['file_members'] == len(expected['files']),
            'Original full-member archive check differs')
    return expected


def source(item, allowed_cells):
    tag, cell = item['tag'], item['cell_id']
    require(TAG.fullmatch(tag) and cell in allowed_cells, 'Only explicit historical Pool A C1 tags are allowed')
    role = pin(item['role_archive'])
    require(role.name == 'objects.tar.gz', 'Expected an exact objects role archive')
    check = load(pin(item['role_check']))
    kind = f'calibration-{tag}-objects' if tag.startswith('cal-') else 'objects'
    expected = checked_manifest(role, item['role_archive']['sha256'], check, cell, kind)
    published = pin(item['public_archive'])
    public_readback(load(pin(item['public_receipt'])), published, item['public_archive']['sha256'])
    nested = item.get('public_member')
    if nested is None:
        require(item['public_archive'] == item['role_archive'], 'Direct publication must match the exact role archive')
    else:
        permitted = f'{tag}/objects.tar.gz' if tag.startswith('cal-') else 'archives/objects.tar.gz'
        outer = checked_manifest(published, item['public_archive']['sha256'], load(pin(item['public_check'])), cell,
                                 'six-calibration-trials' if tag.startswith('cal-') else None)
        require(nested == permitted and outer['files'].get(nested) == {
            'bytes': role.stat().st_size, 'sha256': item['role_archive']['sha256']}, 'Wrong published nested objects member')
    files = {}
    for name, evidence in expected['files'].items():
        parts = rel(name).parts
        if parts[0] == 'o':
            require(len(parts) >= 3 and parts[1] == tag, 'Objects archive contains a foreign observer tag')
            files['/'.join(parts[2:])] = evidence
    validate_members(files)
    return {'tag': tag, 'files': files, 'cell_id': cell,
            'role_sha256': item['role_archive']['sha256'], 'public_sha256': item['public_archive']['sha256']}


def validate_members(files):
    require({'ready.json', 'finished.json'} <= files.keys(), 'Observer lifecycle files missing from archive')
    require('observer.sock' not in files and any(k.startswith('sample-') for k in files), 'Missing raw observer samples')
    for name, row in files.items():
        rel(name)
        require(set(row) == {'bytes', 'sha256'} and type(row['bytes']) is int and row['bytes'] >= 0
                and HEX.fullmatch(row['sha256']), 'Invalid archived file entry')


def snapshot(info):
    return (info.st_dev, info.st_ino, info.st_mode, info.st_nlink, info.st_size,
            info.st_blocks, info.st_mtime_ns, info.st_ctime_ns)


@contextmanager
def directory_fd(path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        yield fd
    finally:
        os.close(fd)


def node_boundary(root, tags):
    mounts = Path('/proc/self/mountinfo').read_text().splitlines()
    targets = [root/'o'/tag for tag in tags]
    for row in mounts:
        name = re.sub(r'\\([0-7]{3})', lambda m: chr(int(m[1], 8)), row.split()[4])
        require(not any(Path(name).is_relative_to(p) for p in targets), 'Nested mount in reclaim scope')
    for row in Path('/proc/net/unix').read_text().splitlines()[1:]:
        parts = row.split(maxsplit=7)
        if len(parts) == 8:
            require(not any(Path(parts[7]).is_relative_to(p) for p in targets), 'Live observer socket in reclaim scope')
    for proc in Path('/proc').glob('[0-9]*/cmdline'):
        try:
            argv = proc.read_bytes().split(b'\0')
        except (FileNotFoundError, ProcessLookupError):
            continue
        require(not (b'bench.runtime.observe' in argv and b'serve' in argv), 'Object observer still active')


def old_observer_exited(ready, stopped, boot):
    ident = ready['process']
    pid, ticks = ident['pid'], ident['start_ticks']
    require(ready['passed'] is True and stopped['passed'] is True and ident['boot_id'] == boot
            and type(pid) is int and pid > 0 and type(ticks) is int and ticks > 0
            and ready['spec']['sha256'] == stopped['spec_sha256'], 'Archived observer identity is incomplete or foreign')
    try:
        status = Path(f'/proc/{pid}/stat').read_text().rsplit(')', 1)[1].split()
    except (FileNotFoundError, ProcessLookupError):
        return
    require(int(status[19]) != ticks, 'Original observer process still exists')


def observe_copy(root, item, boot, device):
    path = root/'o'/item['tag']
    require(path.resolve(strict=True) == path and path.is_dir(), 'Observer root missing or redirected')
    files = item['files']
    validate_members(files)
    expected_dirs = {str(p) for name in files for p in rel(name).parents}
    seen = set()
    entries = []
    lifecycle = {}
    total = 0
    for folder, dirs, names, fd in os.fwalk(path, follow_symlinks=False):
        base = Path(folder).relative_to(path)
        current = os.fstat(fd)
        require(base.as_posix() in expected_dirs and current.st_dev == device, 'Unexpected observer directory or filesystem')
        total += current.st_blocks*512
        entries.append((base.as_posix(), snapshot(current)))
        for name in dirs + names:
            relative = (base/name).as_posix()
            initial = os.stat(name, dir_fd=fd, follow_symlinks=False)
            require(initial.st_dev == device, 'Observer path crosses filesystem')
            if stat.S_ISDIR(initial.st_mode):
                require(relative in expected_dirs, 'Unexpected observer directory')
                continue
            if relative == 'observer.sock' and stat.S_ISSOCK(initial.st_mode):
                require(initial.st_nlink == 1, 'Unexpected observer socket link')
                entries.append((relative, snapshot(initial)))
                total += initial.st_blocks*512
                continue
            require(relative in files and stat.S_ISREG(initial.st_mode) and initial.st_nlink == 1,
                    'Unexpected observer file, symlink or hardlink: '+relative)
            with os.fdopen(os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd), 'rb') as stream:
                require(snapshot(os.fstat(stream.fileno())) == snapshot(initial), 'Observer file changed before reading')
                actual = {'bytes': initial.st_size, 'sha256': hashlib.file_digest(stream, 'sha256').hexdigest()}
                if relative in {'ready.json', 'finished.json'}:
                    require(initial.st_size <= 4*1024**2, 'Unexpected observer lifecycle file size')
                    stream.seek(0)
                    lifecycle[relative] = json.load(stream)
                require(snapshot(os.fstat(stream.fileno())) == snapshot(initial) and actual == files[relative],
                        'Observer member bytes differ: '+relative)
            seen.add(relative)
            total += initial.st_blocks*512
            entries.append((relative, snapshot(initial)))
    require(seen == files.keys(), 'Missing original observer members')
    old_observer_exited(lifecycle['ready.json'], lifecycle['finished.json'], boot)
    initial_root = next(value for name, value in entries if name == '.')
    require(snapshot(path.lstat()) == initial_root, 'Observer directory changed')
    return {'tag': item['tag'], 'allocated_bytes': total, 'files': len(seen), 'root': list(initial_root),
            'fingerprint': sha(json.dumps(sorted(entries), separators=(',', ':')).encode())}


def node(envelope, apply=False):
    boot = Path('/proc/sys/kernel/random/boot_id').read_text().strip()
    device = envelope['identity']['data_device']
    require(ROOT.resolve(strict=True) == ROOT and ROOT.stat().st_dev == device
            and socket.gethostname() == envelope['identity']['hostname'] and boot == envelope['identity']['boot_id'],
            'Original object host, boot and device required')
    require(type(envelope['minimum_after_bytes']) is int and envelope['minimum_after_bytes'] > 0,
            'Positive reviewed space floor required')
    require(envelope['mode'] == ('apply' if apply else 'dry-run'), 'Worker action differs from reviewed mode')
    targets = envelope['targets']
    tags = [t['tag'] for t in targets]
    require(1 <= len(tags) <= 16 and len(tags) == len(set(tags)) and all(TAG.fullmatch(tag) for tag in tags),
            'Unique explicit historical tags required')
    with directory_fd(ROOT/'o') as fd:
        require(os.fstat(fd).st_dev == device, 'Observer parent on another device')
        node_boundary(ROOT, tags)
        groups = [observe_copy(ROOT, item, boot, device) for item in targets]
        node_boundary(ROOT, tags)
        v = os.statvfs(ROOT)
        before = v.f_bavail*v.f_frsize
        amount = sum(g['allocated_bytes'] for g in groups)
        require(before+amount >= envelope['minimum_after_bytes'], 'Verified allocation does not meet reviewed free-space floor')
        if apply:
            require(groups == envelope['dry_run_groups'] and shutil.rmtree.avoids_symlink_attacks,
                    'Apply needs identical verified dry-run groups and fd-safe removal')
            for g in groups:
                require(list(snapshot(os.stat(g['tag'], dir_fd=fd, follow_symlinks=False))) == g['root'],
                        'Observer root changed before removal')
            for g in groups:
                node_boundary(ROOT, tags)
                shutil.rmtree(g['tag'], dir_fd=fd)
                print(json.dumps({'removed': g['tag'], 'fingerprint': g['fingerprint']}), file=sys.stderr, flush=True)
        v = os.statvfs(ROOT)
        after = v.f_bavail*v.f_frsize
    return {'passed': not apply or after >= envelope['minimum_after_bytes'], 'mode': envelope['mode'],
            'groups': groups, 'observed_allocated_bytes': amount, 'initial_free_bytes': before,
            'final_free_bytes': after, 'applied': apply, 'boot_id': boot, 'device': device}


def lock_context(s):
    plan = load(pin(s['canonical_plan'], CANONICAL/'suite/plan.json'))
    lock_plan = load(pin(s['lock_plan'], PILOT/'fork-control-01/suite/plan.json'))
    selected = [c for c in lock_plan['cells'] if c['id'] == s['lock_cell']]
    require(len(selected) == 1, 'One original fork lock cell required')
    cell = selected[0]
    require(cell['pool'] == 'pool-a' and cell['suite'] == 'C4' and cell['case'] == 'fork-1',
            'Original A physical lock cell required')
    resources = set(cell['resource_ids'])
    canonical_a = [c['resource_ids'] for c in plan['cells'] if c['pool'] == 'pool-a']
    clusters = [c['resource_ids'] for c in plan['config']['clusters'] if c['id'] == 'pool-a']
    require(resources and len(resources) == len(cell['resource_ids']) and canonical_a and len(clusters) == 1
            and all(set(ids) == resources and len(ids) == len(resources) for ids in canonical_a + clusters),
            'Original fork and canonical A physical resources differ')
    old_plan = load(pin(s['original_plan'], PILOT/'c123-control-01/suite/plan.json'))
    eligible = {c['id'] for c in old_plan['cells'] if c['pool'] == 'pool-a' and c['suite'] == 'C1'
                and set(c['resource_ids']) == resources and len(c['resource_ids']) == len(resources)}
    require(eligible, 'No original C1 cell matches the locked A physical resources')
    return plan, cell, eligible


def coordinator(args):
    request = {'path': str(args.request), 'sha256': args.sha256}
    s = load(pin(request, below=PREP))
    require(s['schema'] == 'c1-closed-object-observer-reclaim-v1' and s['reviewed'] is True
            and args.out.is_relative_to(PREP) and args.out.parent.resolve(strict=True) == args.out.parent,
            'Reviewed request and new owned output required')
    require(bool(args.apply) == bool(args.dry_run) == bool(args.dry_run_sha256), 'Apply requires exact dry-run receipt pin')
    tool = Path(__file__).resolve(strict=True)
    code = pin(s['tool'], tool, PREP).read_text()
    guard = runpy.run_path(str(pin(s['guard'], GUARD)))
    plan, cell, eligible = lock_context(s)
    fleet = load(pin(s['fleet'], CANONICAL/'fleet.json'))
    identity = guard['identities'](s, cell, fleet)['objects']
    ssh = pin(s['ssh_config'], CANONICAL/'ssh.conf')
    tags = [item['tag'] for item in s['targets']]
    require(1 <= len(tags) <= 16 and len(set(tags)) == len(tags), 'Unique explicit target scope required')
    mode = 'apply' if args.apply else 'dry-run'
    expected_groups = None
    if args.apply:
        dry = load(pin({'path': str(args.dry_run), 'sha256': args.dry_run_sha256}, below=PREP))
        stamp = datetime.fromisoformat(dry['at'])
        require(dry['passed'] is True and dry['mode'] == 'dry-run' and dry['applied'] is False
                and dry['request'] == request and dry['tool'] == s['tool']
                and 0 <= (now()-stamp).total_seconds() <= 1800
                and dry['boot_id'] == identity['boot_id'] and dry['device'] == identity['data_device'],
                'Exact successful recent dry-run required')
        expected_groups = dry['groups']
    args.out.mkdir(mode=0o700)
    write_new(args.out/'intent.json', {'request': request, 'mode': mode, 'automatic_retry': False})
    with guard['two_pool_guard'](CANONICAL/'runner-locks', guard['lock_resources'](plan)):
        boundary = guard['boundary'](s, cell)
        targets = [source(item, eligible) for item in s['targets']]
        payload = {'identity': identity, 'targets': targets, 'mode': mode, 'dry_run_groups': expected_groups,
                   'minimum_after_bytes': s['minimum_after_bytes']}
        remote = shlex.join(['python3', '-B', '-c', code, '--node-'+mode])
        with (args.out/'stdout').open('xb') as stdout, (args.out/'stderr').open('xb') as stderr:
            subprocess.run(['ssh', '-F', str(ssh), '-oBatchMode=yes', 'pool-a-objects', remote],
                           input=json.dumps(payload, separators=(',', ':')).encode(), stdout=stdout, stderr=stderr, check=True)
        result = load(args.out/'stdout')
        require(result['mode'] == mode and result['applied'] == args.apply
                and [g['tag'] for g in result['groups']] == [x['tag'] for x in targets], 'Unexpected original node receipt')
        write_new(args.out/'result.json', dict(result, request=request, tool=s['tool'], at=now().isoformat(),
                  boundary=boundary, automatic_retry=False))
        require(result['passed'] is True, 'Actual final available bytes did not meet the reviewed floor')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--node-dry-run', action='store_true', help=argparse.SUPPRESS)
    parser.add_argument('--node-apply', action='store_true', help=argparse.SUPPRESS)
    parser.add_argument('--request', type=Path)
    parser.add_argument('--sha256')
    parser.add_argument('--out', type=Path)
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--dry-run', type=Path)
    parser.add_argument('--dry-run-sha256')
    args = parser.parse_args()
    if args.node_dry_run != args.node_apply and args.request is None and args.out is None and not args.apply:
        print(json.dumps(node(json.load(sys.stdin), args.node_apply)))
    elif not args.node_dry_run and not args.node_apply and args.request and args.sha256 and args.out:
        coordinator(args)
    else:
        parser.error('provide --request, --sha256 and --out; apply separately with a pinned --dry-run')
