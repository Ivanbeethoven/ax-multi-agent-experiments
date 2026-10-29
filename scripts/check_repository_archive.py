"""Verify every historical archive without extracting or executing it."""
from pathlib import Path, PurePosixPath
import hashlib
import json
import zipfile

def verify(manifest_path):
    manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
    archive = manifest_path.parent / manifest['archive']
    if archive.resolve().parent != manifest_path.parent.resolve():
        raise ValueError('archive must be beside its manifest')
    if hashlib.sha256(archive.read_bytes()).hexdigest() != manifest['archive_sha256']:
        raise ValueError(f'{archive.name}: archive SHA256 mismatch')
    with zipfile.ZipFile(archive) as bundle:
        names = bundle.namelist()
        entries = manifest['files']
        if bundle.testzip() is not None or len(names) != len(set(names)):
            raise ValueError(f'{archive.name}: corrupt or duplicate members')
        if len(names) != manifest['archived_files'] or len(entries) != len(names):
            raise ValueError(f'{archive.name}: file count mismatch')
        if set(names) != {f['path'] for f in entries}:
            raise ValueError(f'{archive.name}: member list mismatch')
        for entry in entries:
            path = PurePosixPath(entry['path'])
            if path.is_absolute() or '..' in path.parts or '\\' in entry['path'] or ':' in entry['path']:
                raise ValueError('unsafe archived path')
            data = bundle.read(entry['path'])
            if len(data) != entry['bytes'] or hashlib.sha256(data).hexdigest() != entry['sha256']:
                raise ValueError(f'{archive.name}: {entry["path"]}: member mismatch')
    return {'archive': archive.name, 'files_verified': len(entries)}


if __name__ == '__main__':
    directory = Path(__file__).resolve().parents[1] / 'archive'
    manifests = [directory / 'manifest.json', *sorted(directory.glob('*.manifest.json'))]
    print(json.dumps({'passed': True, 'archives': [verify(p) for p in manifests]}))
