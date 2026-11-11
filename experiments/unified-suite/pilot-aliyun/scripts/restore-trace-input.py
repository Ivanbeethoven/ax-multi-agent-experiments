#!/usr/bin/env python3
"""Copy every declared replay input and restore initial file/directory modes.

Original case stays unchanged. No replay, mount, service or cloud resources.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import stat
import subprocess
import time

REQUIRED = {'metrics.json', 'initial-tree.json', 'final-tree.json', 'hardlink-check.json', 'capture/events.jsonl'}


def load(path): return json.loads(Path(path).read_text())


def digest(path):
    with Path(path).open('rb') as stream: return hashlib.file_digest(stream, 'sha256').hexdigest()


def ref(path): return dict(path=str(path), sha256=digest(path))


def write_new(path, value):
    with Path(path).open('x') as stream: json.dump(value, stream, indent=2); stream.write('\n')


def relative(name):
    path = PurePosixPath(name)
    if not name or path.is_absolute() or '..' in path.parts or '\\' in name or str(path)!=name:
        raise ValueError('Canonical relative input path required')
    return name


def tree(root):
    entries = {}
    for path in sorted(root.rglob('*')):
        entry = path.lstat(); name = path.relative_to(root).as_posix()
        if stat.S_ISLNK(entry.st_mode): value = dict(type='link', target=os.readlink(path))
        elif stat.S_ISDIR(entry.st_mode): value = dict(type='directory')
        elif stat.S_ISREG(entry.st_mode): value = dict(type='file', bytes=entry.st_size, sha256=digest(path))
        else: raise ValueError('Special input inode rejected: '+name)
        value['mode'] = stat.S_IMODE(entry.st_mode); entries[name] = value
    return entries


def hardlinks(root):
    groups = {}
    for path in root.rglob('*'):
        entry = path.lstat()
        if stat.S_ISREG(entry.st_mode):
            groups.setdefault((entry.st_dev, entry.st_ino), []).append(path.relative_to(root).as_posix())
    return sorted(sorted(names) for names in groups.values() if len(names)>1)


def inputs(case, manifest, checksum, expected_files):
    if digest(manifest)!=checksum: raise ValueError('Original manifest checksum differs')
    inventory = load(manifest)
    if len(inventory)!=expected_files or not REQUIRED.issubset(inventory):
        raise ValueError('Complete declared replay inventory required')
    for name, expected in inventory.items():
        relative(name); path = case/name
        if name not in REQUIRED and not name.startswith(('initial/', 'capture/blobs/')):
            raise ValueError('Unrecognized required input: '+name)
        if path.is_symlink() or not path.is_file() or not path.resolve(strict=True).is_relative_to(case) or digest(path)!=expected:
            raise ValueError('Original required payload differs: '+name)
    for folder in (case/'initial', case/'capture/blobs'):
        if folder.is_symlink() or not folder.is_dir(): raise ValueError('Original input directory missing')
        for path in folder.rglob('*'):
            if path.is_symlink():
                if not path.resolve(strict=True).is_relative_to(case): raise ValueError('Input link escapes case')
            elif path.is_file() and path.relative_to(case).as_posix() not in inventory:
                raise ValueError('Unlisted original payload')
    return inventory


def restore(a):
    source = a.source_case; destination = a.destination_case; out = a.out
    if (not source.is_absolute() or source.is_symlink() or source.resolve(strict=True)!=source or
            not destination.is_absolute() or destination.exists() or destination.is_symlink() or
            destination.name!=source.name or not out.is_absolute() or out.exists() or out.is_symlink()):
        raise ValueError('Canonical source, new case with the same name, and new output required')
    paths = [source, destination, out]
    if any(left.is_relative_to(right) or right.is_relative_to(left) for i,left in enumerate(paths) for right in paths[i+1:]):
        raise ValueError('Source, destination and evidence must be disjoint')
    out.mkdir(parents=True, exist_ok=False); begin = time.monotonic()
    write_new(out/'started.json', dict(at=datetime.now(timezone.utc).isoformat(), source=str(source),
        destination=str(destination), manifest=ref(a.manifest), expected_manifest_sha256=a.manifest_sha256,
        expected_files=a.expected_files, wrapper=ref(Path(__file__)), automatic_retry=False))
    try:
        inputs(source, a.manifest, a.manifest_sha256, a.expected_files)
        expected = load(source/'initial-tree.json')
        for name, value in expected.items():
            relative(name)
            if value['type'] not in ('file', 'directory', 'link') or type(value['mode']) is not int or not 0<=value['mode']<=0o7777:
                raise ValueError('Declared initial inode or mode invalid')
        before = tree(source/'initial'); links = hardlinks(source/'initial')
        differences = {name:dict(expected=expected.get(name), actual=before.get(name))
            for name in sorted(expected.keys()|before.keys()) if expected.get(name)!=before.get(name)}
        write_new(out/'source-initial-differences.json', differences)
        for value in differences.values():
            want, actual = value['expected'], value['actual']
            if (want is None or actual is None or want['type']=='link' or
                    {k:v for k,v in want.items() if k!='mode'}!={k:v for k,v in actual.items() if k!='mode'}):
                raise ValueError('Only initial file/directory mode differences can be restored')
        destination.mkdir(parents=True, exist_ok=False); (destination/'capture').mkdir()
        # Copy the entire declared input inventory, excluding historical grading,
        # backing and replay outputs. cp -a preserves original hardlink groups.
        for index, name in enumerate(('initial', 'capture/blobs', *sorted(REQUIRED))):
            stage = out/('copy-'+str(index).zfill(2)); stage.mkdir()
            argv = ['/usr/bin/cp', '-a', '--reflink=never', '--', str(source/name), str(destination/name)]
            write_new(stage/'command.json', dict(argv=argv, binary=ref(Path(argv[0])), automatic_retry=False))
            with (stage/'stdout').open('xb') as stdout, (stage/'stderr').open('xb') as stderr:
                result = subprocess.run(argv, stdout=stdout, stderr=stderr)
            write_new(stage/'exit.json', dict(exit_code=result.returncode)); result.check_returncode()
        changed = []
        for name in sorted(expected, key=lambda value:(-len(PurePosixPath(value).parts), value)):
            value = expected[name]; path = destination/'initial'/name
            if value['type']=='link': continue
            if path.is_symlink() or any(parent.is_symlink() for parent in path.parents if parent.is_relative_to(destination)):
                raise ValueError('Never change modes through an input link')
            old = stat.S_IMODE(path.lstat().st_mode)
            if old!=value['mode']:
                os.chmod(path, value['mode'], follow_symlinks=False)
                changed.append(dict(path=name, previous_mode=old, restored_mode=value['mode']))
        write_new(out/'mode-changes.json', changed)
        inputs(destination, a.manifest, a.manifest_sha256, a.expected_files)
        if tree(destination/'initial')!=expected or hardlinks(destination/'initial')!=links:
            raise ValueError('Restored initial tree or hardlink topology differs')
        inputs(source, a.manifest, a.manifest_sha256, a.expected_files)
        if tree(source/'initial')!=before or hardlinks(source/'initial')!=links:
            raise ValueError('Original source changed during restoration')
        write_new(out/'result.json', dict(passed=True, source=str(source), destination_case=str(destination),
            manifest=ref(a.manifest), regular_files=a.expected_files, source_mode_differences=len(differences),
            destination_initial_tree_equal=True, initial_hardlink_groups=links, source_unchanged=True,
            source_byte_inventory_unchanged=True, wall_seconds=time.monotonic()-begin,
            scope='Complete replay inventory with restored initial modes; no replay, mount or performance sample'))
        print(json.dumps(load(out/'result.json')))
    except Exception as error:
        write_new(out/'failed.json', dict(passed=False, error_type=type(error).__name__, error=str(error), automatic_retry=False))
        raise


def main():
    p = argparse.ArgumentParser(description=__doc__)
    for name in ('source-case', 'destination-case', 'manifest', 'out'): p.add_argument('--'+name, type=Path, required=True)
    p.add_argument('--manifest-sha256', required=True)
    p.add_argument('--expected-files', type=int, required=True)
    restore(p.parse_args())


if __name__=='__main__': main()
