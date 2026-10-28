"""Verify the historical archive without extracting or launching experiments."""
from pathlib import Path, PurePosixPath
import hashlib
import json
import zipfile

root = Path(__file__).resolve().parents[1]
manifest = json.loads((root / 'archive/manifest.json').read_text(encoding='utf-8'))
archive = root / 'archive' / manifest['archive']
assert hashlib.sha256(archive.read_bytes()).hexdigest() == manifest['archive_sha256']
with zipfile.ZipFile(archive) as bundle:
    assert bundle.testzip() is None
    assert len(bundle.namelist()) == len(set(bundle.namelist())) == manifest['archived_files']
    assert set(bundle.namelist()) == {f['path'] for f in manifest['files']}
    for entry in manifest['files']:
        path = PurePosixPath(entry['path'])
        assert not path.is_absolute() and '..' not in path.parts
        data = bundle.read(entry['path'])
        assert len(data) == entry['bytes']
        assert hashlib.sha256(data).hexdigest() == entry['sha256'], entry['path']
print(json.dumps({'passed': True, 'archived_files_verified': manifest['archived_files']}))
