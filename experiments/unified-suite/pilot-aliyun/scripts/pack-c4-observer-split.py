#!/usr/bin/env python3
"""Pack one closed C4 role into a raw-observation tar plus a small evidence tar.

The original single-package archive keeps one JSON manifest for every member,
which cannot survive a five-hour five-second observation series (132,944
members put the manifest at 19.5 MiB against a 16 MiB bound). This tool keeps
the same evidence but splits it:

  <tag>-<role>-observer.tar.gz  the closed ``o/<tag>`` tree, indexed by a
                                streamed NDJSON member list
  <tag>-<role>.tar.gz           the closure and other allowed directories,
                                still packaged and verified by the frozen
                                bench.archive_check helpers

Nothing is deleted or rewritten; every member is hashed before and after the
write. Run it on the worker that owns the closed observer tree.
"""
import argparse
import hashlib
import io
import json
import os
from pathlib import Path
import re
import stat
import sys
import tarfile

CODE_FILES = {
    'bench/__init__.py', 'bench/archive_check.py', 'bench/experiment_io.py', 'bench/experiment_step.py',
    'bench/runtime/__init__.py', 'bench/runtime/common.py', 'bench/runtime/observe.py',
}
INDEX = 'observer-index.ndjson'
INDEX_SCHEMA = 'suite-observer-index-v1'
CREDENTIAL = re.compile(r'(?i)(credential|secret|\.env$|id_(rsa|ed25519)|\.pem$)')


def require(ok, message):
    if not ok:
        raise ValueError(message)


def digest(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def safe(name):
    p = Path(name)
    require(name and not p.is_absolute() and '..' not in p.parts and '\\' not in name and ':' not in name,
            'unsafe archive member: '+name)
    require(not CREDENTIAL.search(name), 'credential-like evidence excluded: '+name)
    return name


def members(base, relative_to=None):
    """Every regular file under one directory, named relative to ``relative_to``."""
    base = Path(base)
    require(base.is_dir() and not base.is_symlink(), 'evidence directory missing: '+str(base))
    anchor = Path(relative_to) if relative_to is not None else base
    found = []
    for current, folders, files in os.walk(base, followlinks=False):
        folders[:] = sorted(name for name in folders if name != 'cache')
        for name in sorted(files):
            path = Path(current)/name
            mode = path.lstat().st_mode
            require(not stat.S_ISLNK(mode), 'evidence symlink refused: '+str(path))
            if not stat.S_ISREG(mode):
                continue
            relative = path.relative_to(anchor).as_posix()
            safe(relative)
            found.append(relative)
    return sorted(found), base


def write_observer(root, names, output, header):
    """Index first, then the bytes; the index is NDJSON so it never grows into one JSON blob."""
    entries = []
    with output.open('xb') as sink:
        os.fchmod(sink.fileno(), 0o600)
        with tarfile.open(fileobj=sink, mode='w:gz') as archive:
            index_lines = [json.dumps(header, sort_keys=True)]
            for name in names:
                path = root/name
                size = path.stat().st_size
                checksum = digest(path)
                entries.append(dict(name=name, bytes=size, sha256=checksum))
                index_lines.append(json.dumps(entries[-1], sort_keys=True))
            raw = ('\n'.join(index_lines)+'\n').encode()
            head = tarfile.TarInfo(INDEX)
            head.size, head.mode = len(raw), 0o600
            archive.addfile(head, io.BytesIO(raw))
            for name, entry in zip(names, entries):
                info = tarfile.TarInfo(name)
                info.size = entry['bytes']
                info.mode = stat.S_IMODE((root/name).stat().st_mode)
                with (root/name).open('rb') as stream:
                    archive.addfile(info, stream)
        sink.flush()
        os.fsync(sink.fileno())
    return entries


def verify_observer(path, expected_entries):
    found = {}
    index = None
    with tarfile.open(path, 'r:gz') as archive:
        for member in archive:
            require(member.isfile(), 'observer tar holds only regular files: '+member.name)
            with archive.extractfile(member) as stream:
                if member.name == INDEX:
                    # Stream the index: at 132,944 members it is about 16 MiB,
                    # which must not be materialised as one JSON document.
                    header = None
                    index = []
                    for raw_line in io.TextIOWrapper(stream, encoding='utf-8'):
                        line = raw_line.strip()
                        if not line:
                            continue
                        row = json.loads(line)
                        if header is None:
                            header = row
                            require(header.get('schema') == INDEX_SCHEMA, 'observer index schema differs')
                        else:
                            index.append(row)
                    require(header is not None, 'empty observer index')
                    require(len(index) == header.get('members'), 'observer index member count differs')
                else:
                    found[member.name] = dict(name=member.name, bytes=member.size,
                                              sha256=hashlib.file_digest(stream, 'sha256').hexdigest())
    require(index is not None, 'observer index missing')
    require(len({row['name'] for row in index}) == len(index), 'duplicate observer index member')
    require(found == {row['name']: row for row in index}, 'observer tar does not match its index')
    require(sorted(row['name'] for row in index) == sorted(expected_entries),
            'observer tar member list differs from the packed list')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, required=True, help='Worker root owning o/<tag>')
    parser.add_argument('--tag', required=True)
    parser.add_argument('--role', required=True, choices=('client', 'metadata', 'objects'))
    parser.add_argument('--cell-id', required=True)
    parser.add_argument('--include', action='append', default=[],
                        help='Extra relative evidence directory for the small tar (repeatable)')
    parser.add_argument('--out-dir', type=Path, required=True)
    parser.add_argument('--code', type=Path, required=True, help='Worker CODE tree for the frozen checker')
    args = parser.parse_args()

    root = args.root.resolve(strict=True)
    require(root == args.root and not args.root.is_symlink(), 'canonical worker root required')
    require((root/'o'/args.tag).is_dir(), 'closed observer tree missing: o/'+args.tag)
    require(not re.search(r'[^a-z0-9-]', args.tag), 'plain tag required')
    out = args.out_dir
    require(out.is_absolute() and out.resolve() == out and not out.is_symlink(), 'canonical output directory required')
    require(not out.is_relative_to(root/'o'/args.tag), 'output must stay outside the observer tree')
    out.mkdir(parents=True, exist_ok=True)
    observer_tar = out/(args.tag+'-'+args.role+'-observer.tar.gz')
    evidence_tar = out/(args.tag+'-'+args.role+'.tar.gz')
    receipt = out/(args.tag+'-'+args.role+'-split.json')
    for path in (observer_tar, evidence_tar, receipt):
        require(not path.exists(), 'refuse to overwrite existing output: '+str(path))

    observer_root = root/'o'/args.tag
    names, _ = members(observer_root)
    header = dict(schema=INDEX_SCHEMA, tag=args.tag, role=args.role, cell_id=args.cell_id,
                  members=len(names), scope='every regular file under o/<tag>, hashed before packing')
    entries = write_observer(observer_root, names, observer_tar, header)
    verify_observer(observer_tar, [row['name'] for row in entries])

    sys.path.insert(0, str(args.code))
    from bench import archive_check
    extra = list(args.include)
    evidence_names = []
    for name in extra:
        found, _ = members(root/name, relative_to=root)
        evidence_names.extend(found)
    evidence = archive_check.pack(root, evidence_names, evidence_tar, args.cell_id, args.role) if evidence_names else None
    if evidence is not None:
        archive_check.verify(evidence_tar, digest(evidence_tar))

    result = dict(schema='c4-observer-split-v1', passed=True, tag=args.tag, role=args.role,
                  cell_id=args.cell_id, root=str(root),
                  observer=dict(path=str(observer_tar), bytes=observer_tar.stat().st_size,
                                sha256=digest(observer_tar), members=len(entries),
                                index_schema=INDEX_SCHEMA),
                  evidence=dict(path=str(evidence_tar), bytes=evidence_tar.stat().st_size,
                                sha256=digest(evidence_tar),
                                members=evidence['file_members'] if evidence else 0),
                  includes=extra,
                  scope='split packaging only: the original observer bytes are unchanged and no evidence was deleted')
    with receipt.open('x', encoding='utf-8') as stream:
        json.dump(result, stream, indent=2)
        stream.write('\n')
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
