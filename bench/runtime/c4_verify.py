"""C4 cold checks. Reduced checks are valid only for overhead calibration."""
import hashlib
import json
import os
from pathlib import Path
import stat

FULL = 'full-cold-v1'
REFERENCE = 'full-reference-v1'
TOUCHED = 'touched-cold-v1'
CAPTURE = 'recovered-parent-stat-v1'


def scan(root, content_paths=None, *, parent_identity=False):
    """Visit the same namespace in either mode; read data only where requested.

    The parent digest includes native inode, owner, mode, size, links, ctime
    and mtime for every entry. Device and atime may change on a fresh mount.
    """
    root = Path(root)
    s = root.lstat()
    if not stat.S_ISDIR(s.st_mode):
        raise ValueError('Original fixture root is not a directory')
    attrs = {}

    def save(relative, info):
        if parent_identity:
            attrs[relative] = [info.st_ino, info.st_mode, info.st_uid, info.st_gid, info.st_size,
                               info.st_nlink, info.st_mtime_ns, info.st_ctime_ns]

    save('.', s)
    entries = {}
    stack = [(root, '')]
    read_files = read_bytes = 0
    while stack:
        path, prefix = stack.pop()
        with os.scandir(path) as children:
            for entry in children:
                relative = prefix + entry.name
                info = entry.stat(follow_symlinks=False)
                value = {'mode': stat.S_IMODE(info.st_mode)}
                save(relative, info)
                if stat.S_ISDIR(info.st_mode):
                    value['type'] = 'directory'
                    stack.append((entry.path, relative + '/'))
                elif stat.S_ISREG(info.st_mode):
                    value.update(type='file', bytes=info.st_size)
                    if content_paths is None or relative in content_paths:
                        data = Path(entry.path).read_bytes()
                        if len(data) != info.st_size:
                            raise ValueError('File size changed while reading: ' + relative)
                        value['sha256'] = hashlib.sha256(data).hexdigest()
                        read_files += 1
                        read_bytes += len(data)
                else:
                    raise ValueError('Unexpected inode in fixed C4 fixture: ' + relative)
                entries[relative] = value
    identity = None
    if parent_identity:
        encoded = json.dumps(attrs, sort_keys=True, separators=(',', ':')).encode()
        identity = dict(schema='c4-parent-stat-v1', entries=len(attrs), sha256=hashlib.sha256(encoded).hexdigest())
    return entries, identity, dict(files=read_files, bytes=read_bytes)


def touched_paths(original, child, first_read):
    """Derive all changes from independent expected trees; also check first read."""
    if first_read not in original or first_read not in child:
        raise ValueError('Original first-read path missing from fixed oracles')
    return {p for p in original.keys() | child.keys() if original.get(p) != child.get(p)} | {first_read}


def expected_projection(tree, touched):
    return {p: {k: v for k, v in row.items() if k != 'sha256' or p in touched}
            for p, row in tree.items()}


def scope(policy, purpose, reference):
    if policy not in (FULL, REFERENCE, TOUCHED):
        raise ValueError('Unknown C4 verification policy')
    if policy != FULL and purpose not in ('observer-calibration-on', 'observer-calibration-off'):
        raise ValueError('Reduced checks or calibration references are only for overhead trials')
    if bool(reference) != (policy == TOUCHED):
        raise ValueError('Touched calibration requires exactly one original full reference')


def reference_identity(value, binding, current_child, original=None):
    identity = value.get('parent_identity') or {}
    policy=value.get('verification_policy')
    full=value if policy==REFERENCE else original
    if (policy not in (REFERENCE,CAPTURE) or value.get('passed') is not True or not full or
            full.get('full_cold_child') is not True or full.get('parent_unchanged') is not True or
            full.get('child_name')!=value.get('child_name') or
            value.get('child_name') in (None, current_child) or value.get('binding') != binding or
            identity.get('schema') != 'c4-parent-stat-v1' or type(identity.get('entries')) is not int or
            identity['entries'] <= 1 or len(identity.get('sha256', '')) != 64):
        raise ValueError('Different original full reference, fixture, asset, arm or parent identity')
    if policy==CAPTURE and (value.get('parent_shape_matches') is not True or full.get('passed') is not True or
            full.get('cell_id')!=binding['cell_id'] or full.get('expected_child')!=binding['expected_child'] or
            full.get('files')!=binding['files'] or full.get('work_tag')!=binding['work_tag']):
        raise ValueError('Recovered metadata reference does not bind the actual original full cold trial')
    return identity


def check(base, child, original, expected, *, first_read, reference=None):
    # None means both complete data trees. A reference means every metadata
    # entry and only touched bytes; the caller separately pins full provenance.
    paths = touched_paths(original, expected, first_read) if reference is not None else None
    parent, current, parent_io = scan(base, paths, parent_identity=True)
    observed, _, child_io = scan(child, paths)
    parent_ok = parent == (original if paths is None else expected_projection(original, paths))
    child_ok = observed == (expected if paths is None else expected_projection(expected, paths))
    return dict(passed=parent_ok and child_ok and (reference is None or reference == current),
                parent_matches=parent_ok, child_matches=child_ok, parent_identity=current,
                parent_identity_unchanged=None if reference is None else current == reference,
                touched_paths=None if paths is None else sorted(paths),
                content_read=dict(parent=parent_io, child=child_io),
                namespace_entries=dict(parent=len(parent), child=len(observed)))
