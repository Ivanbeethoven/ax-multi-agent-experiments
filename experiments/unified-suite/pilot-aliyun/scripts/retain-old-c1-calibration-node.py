#!/usr/bin/env python3
"""Internal one-role C1 retention worker. Never mounts, measures or deletes."""
import argparse
import base64
from contextlib import redirect_stdout
import hashlib
import io
import json
import os
from pathlib import Path
import re
import runpy
import socket
import sys

sys.dont_write_bytecode = True
ROOTS = {'client': '/srv/ax-client', 'metadata': '/srv/ax-metadata', 'objects': '/srv/ax-objects-0'}
SCRATCH = Path('/var/tmp')
GIB = 1024 ** 3
MIB = 1024 ** 2
RUNTIME_FILES = frozenset(('bench/__init__.py', 'bench/archive_check.py', 'bench/experiment_io.py',
    'bench/experiment_step.py', 'bench/runtime/__init__.py', 'bench/runtime/common.py', 'bench/runtime/observe.py'))
TOOL = 'experiments/unified-suite/pilot-aliyun/scripts/retain-calibrations.py'


def require(ok, message):
    if not ok:
        raise ValueError(message)


def sha(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def file_pin(path, expected):
    require(path.is_absolute() and path.resolve(strict=True) == path and path.is_file() and
            sha(path) == expected, f'Changed original or retention runtime file: {path}')
    return path


def stable_stat(path):
    info = path.stat()
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def space(path):
    vfs = os.statvfs(path)
    return vfs.f_bavail * vfs.f_frsize, vfs.f_favail


def archive_bound(raw_bytes, files):
    # Includes per-member tar/PAX padding, compression expansion and manifest.
    return raw_bytes + raw_bytes // 50 + files * 8192 + 32 * MIB


def directory_names(role, tag, mode):
    names = ['o/'+tag, 'q/'+tag+'-observer'] if mode == 'on' else []
    if role == 'client':
        names += ['q/'+tag, 'm/'+tag+'-p', 'm/'+tag, 'm/'+tag+'-v', 'w/'+tag, 'j/'+tag]
    require(bool(names), 'Off trials have client evidence only')
    return names


def load_retainer(path, config):
    encoded = base64.b64encode(json.dumps(config).encode()).decode()
    original = sys.argv
    try:
        sys.argv = [str(path), '--node-config', encoded]
        return runpy.run_path(str(path))
    finally:
        sys.argv = original


def check_identity(root, expected):
    boot = Path('/proc/sys/kernel/random/boot_id').read_text().strip()
    require(root.resolve(strict=True) == root and os.path.ismount(root) and
            root.stat().st_dev == expected['data_device'] and boot == expected['boot_id'] and
            socket.gethostname() == expected['hostname'], 'Original role boot, host or mounted data device changed')
    require(SCRATCH.resolve(strict=True) == SCRATCH and SCRATCH.is_dir() and
            SCRATCH.stat().st_dev != root.stat().st_dev, 'Scratch must be on a different filesystem from the source')
    return {'hostname': expected['hostname'], 'boot_id': boot, 'data_device': root.stat().st_dev,
            'scratch_device': SCRATCH.stat().st_dev}


def client_idle(root):
    require(not Path('/sys/fs/cgroup/ax-slot-1/cgroup.procs').read_text().strip(), 'A client agent slot is occupied')
    for line in Path('/proc/self/mountinfo').read_text().splitlines():
        lhs, rhs = line.split(' - ', 1)
        point = lhs.split()[4]
        if rhs.split()[0].startswith('fuse') and (point == str(root) or point.startswith(str(root)+'/')):
            raise ValueError('A client FUSE mount remains present: '+point)


def source_probe(root, retainer, cfg):
    role, tag, mode = cfg['role'], cfg['tag'], cfg['mode']
    if role == 'client':
        spec = cfg['worker_spec']
        file_pin(root/'w'/tag/'worker.reviewed.json', spec['sha256'])
    if mode == 'on':
        file_pin(root/'q'/(tag+'-observer')/'observer.json', cfg['observer_sha256'])
    dirs = directory_names(role, tag, mode)
    files, skipped = retainer['file_list'](root, dirs)
    stat_rows = {name: stable_stat(root/name) for name in files}
    encoded = json.dumps([stat_rows, skipped], sort_keys=True, separators=(',', ':')).encode()
    return {'tag': tag, 'files': len(files), 'source_stat_sha256': hashlib.sha256(encoded).hexdigest(),
            'bound_bytes': archive_bound(sum(row[2] for row in stat_rows.values()), len(files))}


def scratch_room(root, planned_bytes, files):
    scratch_free, scratch_inodes = space(SCRATCH)
    role_free, role_inodes = space(root)
    require(scratch_free >= planned_bytes + GIB and scratch_inodes >= files + 16 and
            role_free >= 64 * MIB and role_inodes >= files * 2 + 16,
            'Worker lacks reviewed room for compressed scratch or tiny role receipts')
    return {'scratch_free_bytes': scratch_free, 'scratch_free_inodes': scratch_inodes,
            'role_free_bytes': role_free, 'role_free_inodes': role_inodes}


def work(request):
    phase, role, run = (request[k] for k in ('phase', 'role', 'run'))
    require(request['schema'] == 'c123-role-retention-dispatch-v1' and request['reviewed'] is True and
            phase in ('preflight', 'pack') and role in ROOTS and
            re.fullmatch(r'c123-old-c1-retention-[a-z0-9-]{2,45}', run), 'Invalid original role dispatch')
    root = Path(ROOTS[role]); scratch = SCRATCH/run
    require(not scratch.exists() and not scratch.is_symlink(), 'New private worker scratch path required')
    identity = check_identity(root, request['identity'])
    if role == 'client':
        client_idle(root)
    repo = root/request['retention_code_dir']
    require(re.fullmatch(r'code-[0-9]{2}', repo.name) and repo.parent == root and
            repo.resolve(strict=True) == repo, 'Pinned retention runtime must be on the original role')
    require(set(request['runtime_files']) == RUNTIME_FILES, 'Incomplete pinned runtime imports')
    for name, digest in request['runtime_files'].items():
        file_pin(repo/name, digest)
    retainer_path = file_pin(repo/TOOL, request['retainer_sha256'])
    configs = request['configs']
    require(len(configs) == (6 if role == 'client' else 3), 'Wrong number of original role trials')
    require(len({cfg['tag'] for cfg in configs}) == len(configs), 'Duplicate worker trial tag')
    checked_pins = set()
    for cfg in configs:
        tag = cfg['tag']
        require(cfg['role'] == role and cfg['root'] == str(root) and cfg['pool'] == 'pool-a' and
                cfg['cell_id'] == request['cell_id'] and cfg['bundle'] == 'c123-cal-recovery-'+request['cell_id'] and
                re.fullmatch(r'cal-[a-z0-9-]{3,28}', tag) and cfg['mode'] in ('off', 'on') and
                (role == 'client' or cfg['mode'] == 'on'), 'Changed original node pack payload')
        if role == 'client':
            require(cfg['worker_spec']['path'] == str(root/'w'/tag/'worker.reviewed.json'), 'Foreign worker spec')
        old_code = Path(cfg['code'])
        require(old_code.parent == root and old_code.resolve(strict=True) == old_code and
                re.fullmatch(r'code-[0-9]{2}', old_code.name) and cfg['code_pins'], 'Foreign original runtime')
        for pin in cfg['code_pins']:
            path = Path(pin['path'])
            require(path.is_relative_to(old_code) or path == Path('/usr/bin/python3.12'), 'Foreign original tool pin')
            key = (path, pin['sha256'])
            if key not in checked_pins:
                file_pin(*key)
                checked_pins.add(key)
        original_receipt = root/'retained-calibrations'/cfg['bundle']/tag
        require(not original_receipt.exists() and not original_receipt.is_symlink(), 'Worker output already exists')
        require(original_receipt.resolve() == original_receipt, 'Unsafe original role receipt path')
    # Only the helper runtime changes; original executable/code pins, specs, tags and marks remain exact.
    retainer = load_retainer(retainer_path, dict(configs[0], code=str(repo)))
    sources = [source_probe(root, retainer, cfg) for cfg in configs]
    total = sum(s['bound_bytes'] for s in sources)
    capacity = scratch_room(root, total, len(sources))
    if phase == 'preflight':
        return dict(passed=True, role=role, phase=phase, identity=identity, sources=sources, capacity=capacity,
                    scratch=str(scratch), bytes_written=0)
    preflight = request['preflight']
    require(preflight['passed'] is True and preflight['phase'] == 'preflight' and preflight['role'] == role and
            preflight['identity'] == identity and preflight['sources'] == sources and
            preflight['scratch'] == str(scratch), 'Original worker source changed after capacity preflight')
    scratch.mkdir(mode=0o700, exist_ok=False)
    archives = []
    for cfg, expected in zip(configs, sources, strict=True):
        if role == 'client':
            client_idle(root)
        require(source_probe(root, retainer, cfg) == expected, 'Worker evidence changed before packing')
        tag = cfg['tag']; archive = scratch/(tag+'.tar.gz')

        def direct_pack(original_root, files, role_out, cell_id, kind):
            require(original_root == root and kind == f'calibration-{tag}-{role}', 'Unexpected original source or archive kind')
            require(source_probe(root, retainer, cfg) == expected and len(files) == expected['files'],
                    'Worker evidence changed during original closure checks')
            scratch_room(root, expected['bound_bytes'], 1)
            result = retainer['archive_check'].pack(root, files, archive, cell_id, kind)
            require(result['bytes'] <= expected['bound_bytes'] and not (role_out/'raw').exists(),
                    'Unexpected archive size or raw staging copy')
            return result

        node_pack = retainer['node_pack']
        node_pack.__globals__['pack_node_files'] = direct_pack
        text = io.StringIO()
        with redirect_stdout(text):
            node_pack(dict(cfg, code=str(repo)))
        result = json.loads(text.getvalue())
        require(result['passed'] is True and result['archive']['archive'] == str(archive), 'Original node pack did not pass')
        archives.append(result)
    return dict(passed=True, role=role, phase=phase, identity=identity, sources=sources, capacity=capacity,
                scratch=str(scratch), raw_staging_created=False, archives=archives)


if __name__ == '__main__':
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dispatch', required=True)
    args = parser.parse_args()
    print(json.dumps(work(json.loads(base64.b64decode(args.dispatch, validate=True)))))
