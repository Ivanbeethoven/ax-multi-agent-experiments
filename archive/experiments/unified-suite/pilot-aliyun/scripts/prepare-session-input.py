#!/usr/bin/env python3
"""Install one frozen OpenCode checkpoint on an already supervised C4 mount.

Preparation only: no model call, import, resume, mount stop or automatic retry.
Provider configuration is read from a private file and never copied to evidence.
"""
import argparse
from datetime import datetime, timezone
import importlib.util
import json
import os
from pathlib import Path
import shutil
import stat
import sys

REPO = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO/'bench'))
from bench.runtime.common import digest, load, pinned, write_new
from bench.runtime import node
from session_check import check_after


def tree(root):
    """Full contents and metadata; symlinks must remain inside this checkpoint."""
    root = root.resolve(strict=True)
    rows = {}
    for p in sorted(root.rglob('*')):
        s = p.lstat(); name = p.relative_to(root).as_posix()
        row = dict(mode=stat.S_IMODE(s.st_mode))
        if p.is_symlink():
            if not p.resolve(strict=True).is_relative_to(root):
                raise ValueError('Checkpoint link leaves workspace')
            row.update(type='symlink', target=os.readlink(p))
        elif p.is_dir():
            row.update(type='directory')
        elif p.is_file():
            row.update(type='file', bytes=s.st_size, sha256=digest(p))
        else:
            raise ValueError('Unexpected checkpoint file type')
        rows[name] = row
    return rows


def sync_tree(root):
    files = 0; total = 0
    directories = [root]
    for p in root.rglob('*'):
        if p.is_symlink():
            continue
        if p.is_dir():
            directories.append(p)
        else:
            fd = os.open(p, os.O_RDONLY | os.O_NOFOLLOW)
            try:
                os.fsync(fd); total += os.fstat(fd).st_size; files += 1
            finally:
                os.close(fd)
    for p in sorted(directories, key=lambda p: len(p.parts), reverse=True):
        fd = os.open(p, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    return dict(files=files, file_bytes=total, directories=len(directories))


def run(request, output):
    source = pinned(request['checkpoint_directory_receipt'])
    # The receipt is checkpoint.json, not a self-reported copied output tree.
    original = source.parent/'workspace'; checkpoint = load(source)
    if (checkpoint.get('source_process_exited') is not True or
            checkpoint.get('sqlite_quick_check') != 'ok' or
            checkpoint.get('contains_provider_master_key') is not False):
        raise ValueError('Closed, verified source checkpoint required')
    export_path = original/'session-export.json'
    if digest(export_path) != checkpoint['session_export_sha256']:
        raise ValueError('Original native export changed')
    check_after(checkpoint['session_prefix'], load(export_path), False)
    worker = pinned(request['session_worker'])
    binary = pinned(request['binary'])
    if digest(binary) != checkpoint['opencode_binary_sha256']:
        raise ValueError('OpenCode differs from source checkpoint')
    config = pinned(request['private_relay_config'])
    if stat.S_IMODE(config.stat().st_mode) != 0o600:
        raise ValueError('Relay configuration must be private')
    settings = load(config)
    options = settings['provider']['experiment']['options']
    if (options['baseURL'] != request['relay_url'] or
            request['relay_url'] != 'http://10.20.0.10:18443/v1' or
            settings['model'] != 'experiment/deepseek-v4-flash' or
            not options.get('apiKey')):
        raise ValueError('Exact private relay/model configuration required')
    mount_ref = request['mount']['spec']; mount_spec = load(pinned(mount_ref))
    ready = load(Path(request['mount']['directory'])/'ready.json')
    if ready['spec'] != mount_ref or ready['anchor'] != mount_spec['anchor']:
        raise ValueError('Wrong original mount instance')
    current = node.control(request['mount']['directory'], 'status')
    if current['anchor'] != ready['anchor'] or current['mount'] != ready['mount']:
        raise ValueError('Mount identity changed')
    workspace = Path(checkpoint['canonical_workspace'])
    point = Path(mount_spec['mount_point'])
    if workspace != point/'workspace' or workspace.exists():
        raise ValueError('New workspace at the original canonical path required')
    output = Path(output)
    if output.resolve().is_relative_to(point.resolve()):
        raise ValueError('Evidence must be outside tested filesystem')
    output.mkdir(parents=True, exist_ok=False, mode=0o700)
    write_new(output/'started.json', dict(at=datetime.now(timezone.utc).isoformat(),
        checkpoint=request['checkpoint_directory_receipt'], binary=request['binary'],
        session_worker=request['session_worker'], mount=request['mount'], automatic_retry=False,
        scope='Install frozen input and native export only; no model invocation'))
    before = tree(original)
    if before != load(pinned(request['source_tree'])):
        raise ValueError('Frozen complete input manifest differs')
    for name in before:
        if name.endswith(('.db-wal', '.db-journal')) and before[name].get('bytes', 0):
            raise ValueError('Original checkpoint has unclosed SQLite journal')
    write_new(output/'source-tree.json', before)
    shutil.copytree(original, workspace, symlinks=True)
    if tree(workspace) != before:
        raise ValueError('Full checkpoint copy differs')
    destination = workspace/'config/opencode.json'
    if destination.is_symlink() or not destination.is_file():
        raise ValueError('Expected a regular workspace provider configuration')
    with destination.open('wb') as f:
        os.fchmod(f.fileno(), 0o600); f.write(config.read_bytes()); f.flush(); os.fsync(f.fileno())
    sync = sync_tree(workspace)
    after = tree(workspace)
    if {k:v for k,v in after.items() if k != 'config/opencode.json'} != {
            k:v for k,v in before.items() if k != 'config/opencode.json'}:
        raise ValueError('Only provider relay configuration may change during preparation')
    write_new(output/'installed-tree.json', after)
    # Use the exact same phase implementation as the later supervised handoff.
    spec = importlib.util.spec_from_file_location('session_worker', worker)
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    phase = dict(action='export', binary=request['binary'], workspace=str(workspace),
        project=checkpoint['canonical_project'], config=str(destination),
        state={k:str(workspace/'source'/k) for k in ('data','state','cache','config')},
        relay_url=request['relay_url'], session_id=checkpoint['session_id'])
    write_new(output/'export-request.json', phase)
    exported = module.run(phase, output/'native-export')
    validation = check_after(checkpoint['session_prefix'], exported, False)
    canonical = output/'native-export/export.json'
    if digest(canonical) != checkpoint['session_export_sha256']:
        raise ValueError('Native cloud export differs; re-review outside measurement')
    sync_after = sync_tree(workspace)
    final = node.control(request['mount']['directory'], 'status')
    if final['anchor'] != ready['anchor'] or final['mount'] != ready['mount']:
        raise ValueError('Mount changed during checkpoint installation')
    result = dict(passed=True, session_id=checkpoint['session_id'], prefix_validation=validation,
        canonical_export=dict(path=str(canonical), sha256=digest(canonical)),
        checkpoint=request['checkpoint_directory_receipt'], mount=request['mount'],
        sync_before_export=sync, sync_after_export=sync_after,
        source_tree_sha256=digest(output/'source-tree.json'),
        installed_tree_sha256=digest(output/'installed-tree.json'),
        relay_configuration_sha256=digest(destination), empty_home=True,
        model_calls=0, automatic_retry=False,
        scope='Prepared workspace and exact native export; drain, normal exit, cold readback and actual resume remain separate')
    write_new(output/'result.json', result)
    return result


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--request', type=Path, required=True); p.add_argument('--sha256', required=True)
    p.add_argument('--out', type=Path, required=True)
    a = p.parse_args()
    result = run(load(pinned(dict(path=str(a.request), sha256=a.sha256))), a.out)
    print(json.dumps(result))
