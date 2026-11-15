#!/usr/bin/env python3
"""Verify every deployment archive member, then install into a NEW directory."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path, PurePosixPath
import tarfile


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--archive', type=Path, required=True)
    p.add_argument('--sha256', required=True)
    p.add_argument('--destination', type=Path, required=True)
    p.add_argument('--out', type=Path, required=True)
    a = p.parse_args()
    if a.destination.exists() or a.destination.is_symlink() or not a.destination.is_absolute() or a.out.exists():
        raise ValueError('New absolute destination and new result path required')
    if digest(a.archive) != a.sha256:
        raise ValueError('Downloaded deployment archive SHA256 differs')
    found, seen, manifest = {}, set(), None
    with tarfile.open(a.archive, 'r:gz') as archive:
        for member in archive:
            name = member.name
            path = PurePosixPath(name)
            if not name or name.startswith('/') or '..' in path.parts or '\\' in name or ':' in name or str(path) != name:
                raise ValueError('Unsafe archive path')
            if name in seen or not member.isfile():
                raise ValueError('Duplicate, link or special deployment member')
            seen.add(name)
            with archive.extractfile(member) as stream:
                if name == '.suite-manifest.json':
                    if member.size > 16 * 1024**2:
                        raise ValueError('Internal manifest too large')
                    manifest = json.load(stream)
                else:
                    found[name] = dict(bytes=member.size, sha256=hashlib.file_digest(stream, 'sha256').hexdigest())
    if not manifest or manifest.get('schema') != 'suite-archive-manifest-v1' or manifest.get('deferred') != [] or \
            manifest.get('kind') != 'prepared-assets' or found != manifest['files']:
        raise ValueError('Deployment member hashes or manifest differ')
    a.destination.mkdir(parents=True, exist_ok=False)
    with tarfile.open(a.archive, 'r:gz') as archive:
        archive.extractall(a.destination, filter='data')
    for name, expected in found.items():
        path = a.destination / name
        if path.is_symlink() or digest(path) != expected['sha256']:
            raise ValueError('Installed deployment member differs')
    result = dict(at=datetime.now(timezone.utc).isoformat(), passed=True,
        archive_sha256=a.sha256, destination=str(a.destination), file_members=len(found),
        bytes=sum(v['bytes'] for v in found.values()), per_member_checked_before_and_after=True)
    with a.out.open('x') as stream:
        json.dump(result, stream, indent=2); stream.write('\n')
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
