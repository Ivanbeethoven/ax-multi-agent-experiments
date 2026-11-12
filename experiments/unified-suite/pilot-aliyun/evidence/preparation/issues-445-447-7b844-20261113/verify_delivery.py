"""Verify the frozen delivery and every archived file without extracting it."""
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import sys
import tarfile

MANIFEST_SHA256 = '38ff2b887811aab1af0fd6075f3dfc06696918a2d3f3936bfeab04c3444ecf27'
SECRET = re.compile(
    rb'-----BEGIN (?:RSA |EC |DSA |OPENSSH )?PRIVATE KEY-----'
    rb'|\bAKIA[A-Z0-9]{16}\b|\bLTAI[A-Za-z0-9]{12,32}\b'
)


def safe_path(name):
    path = PurePosixPath(name)
    if path.is_absolute() or '..' in path.parts or '\\' in name or ':' in name:
        raise ValueError('Unsafe archive/manifest path: ' + name)
    return path


def check_bytes(data, record):
    if len(data) != record['bytes'] or hashlib.sha256(data).hexdigest() != record['sha256']:
        raise ValueError('Length or SHA mismatch: ' + record['path'])
    if SECRET.search(data):
        raise ValueError('Credential pattern found in: ' + record['path'])


def verify(root):
    root = Path(root).resolve()
    if os.name == 'nt' and not str(root).startswith('\\\\?\\'):
        root = Path('\\\\?\\' + str(root))
    manifest_bytes = (root / 'manifest.json').read_bytes()
    if hashlib.sha256(manifest_bytes).hexdigest() != MANIFEST_SHA256:
        raise ValueError('Frozen package manifest SHA differs')
    manifest = json.loads(manifest_bytes)
    files = manifest['files']
    if len(files) != manifest['file_count'] or len({r['path'] for r in files}) != len(files):
        raise ValueError('Outer manifest count or duplicate path')
    for record in files:
        check_bytes((root / safe_path(record['path'])).read_bytes(), record)
    if sum(r['bytes'] for r in files) != manifest['total_bytes']:
        raise ValueError('Outer manifest byte total differs')

    archives = []
    for record in files:
        path = safe_path(record['path'])
        if path.parent != PurePosixPath('archives') or not path.name.endswith('.tar.gz'):
            continue
        name = path.name.removesuffix('.tar.gz')
        inventory_bytes = (root / 'receipts' / name / 'archive-manifest.json').read_bytes()
        inventory = json.loads(inventory_bytes)
        rows = inventory.get('files', inventory.get('members'))
        expected = {r['path']: r for r in rows}
        if len(expected) != len(rows):
            raise ValueError('Duplicate inventory path: ' + name)
        if 'archive-manifest.json' in expected:
            raise ValueError('Inventory lists its own digest: ' + name)
        expected['archive-manifest.json'] = {
            'path': 'archive-manifest.json', 'bytes': len(inventory_bytes),
            'sha256': hashlib.sha256(inventory_bytes).hexdigest()
        }
        seen = set()
        with tarfile.open(root / path, 'r:gz') as archive:
            for member in archive:
                safe_path(member.name)
                if member.isdir():
                    continue
                if not member.isfile() or member.name in seen or member.name not in expected:
                    raise ValueError('Unexpected archive member: ' + member.name)
                seen.add(member.name)
                stream = archive.extractfile(member)
                with stream:
                    check_bytes(stream.read(), expected[member.name])
        if seen != set(expected):
            raise ValueError('Missing archive members: ' + name)
        archives.append({'name': name, 'files': len(seen), 'payload_files': len(rows),
                         'bytes': sum(r['bytes'] for r in expected.values()),
                         'sha256': record['sha256']})
    return {'passed': True, 'manifest_sha256': MANIFEST_SHA256,
            'delivery_files': len(files), 'delivery_bytes': manifest['total_bytes'],
            'archived_files': sum(a['files'] for a in archives), 'archives': archives,
            'credential_pattern_matches': 0,
            'scope': 'Byte identity, archive membership/path boundaries and explicit credential patterns; independent semantic reviews remain separate.'}


if __name__ == '__main__':
    root = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parent
    print(json.dumps(verify(root), indent=2))
