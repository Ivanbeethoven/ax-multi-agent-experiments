"""Verify every historical archive without extracting or executing it."""
from pathlib import Path, PurePosixPath
import hashlib
import io
import json
import re
import subprocess
import zipfile

def safe_path(value):
    path = PurePosixPath(value)
    if not value or value == '.' or path.is_absolute() or '..' in path.parts or any(c in value for c in '\\:\r\n'):
        raise ValueError('unsafe archived path')
    return value


def git_blobs(root, commit, paths):
    """Read pinned historical bytes in one Git process, without checkout."""
    if not re.fullmatch(r'[0-9a-f]{40}', commit):
        raise ValueError('history requires a full commit SHA')
    args = ['git']
    dotgit = root / '.git'
    if dotgit.is_file():
        gitdir = dotgit.read_text(encoding='utf-8').removeprefix('gitdir:').strip()
        if root.as_posix().startswith('/mnt/') and re.match(r'^[A-Za-z]:/', gitdir):
            gitdir = '/mnt/' + gitdir[0].lower() + gitdir[2:]
            args.extend(['--git-dir=' + gitdir, '--work-tree=' + str(root)])
    refs = [commit + ':' + safe_path(path) for path in paths]
    proc = subprocess.run([*args, 'cat-file', '--batch'], cwd=root,
                          input=('\n'.join(refs) + '\n').encode(), capture_output=True, check=True)
    stream = io.BytesIO(proc.stdout)
    found = {}
    for path in paths:
        header = stream.readline().split()
        if len(header) != 3 or header[1] != b'blob':
            raise ValueError(f'historical file unavailable: {commit}:{path}')
        size = int(header[2])
        data = stream.read(size)
        if len(data) != size or stream.read(1) != b'\n':
            raise ValueError('truncated Git blob response')
        found[path] = data
    return found


def verify_retired(root, manifest):
    entries = manifest['files']
    paths = [safe_path(entry['path']) for entry in entries]
    if len(paths) != len(set(paths)) or len(paths) != manifest['removed_files']:
        raise ValueError('retired file count mismatch')
    blobs = git_blobs(root, manifest['source_commit'], paths)
    total = 0
    for entry in entries:
        data = blobs[entry['path']]
        if len(data) != entry['bytes'] or hashlib.sha256(data).hexdigest() != entry['sha256']:
            raise ValueError(f'retired file mismatch: {entry["path"]}')
        if entry['disposition'] != 'removed' or (root / entry['path']).exists():
            raise ValueError(f'retired file returned to active tree: {entry["path"]}')
        total += len(data)
    if total != manifest['removed_bytes']:
        raise ValueError('retired byte count mismatch')
    return {'storage': 'git-history', 'source_commit': manifest['source_commit'], 'files_verified': len(entries)}


def verify(manifest_path):
    manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
    root = manifest_path.resolve().parents[1]
    if manifest.get('schema') == 'git-retirement-v1':
        return verify_retired(root, manifest)
    archive = manifest_path.parent / manifest['archive']
    if archive.resolve().parent != manifest_path.parent.resolve():
        raise ValueError('archive must be beside its manifest')
    if archive.is_file():
        raw = archive.read_bytes()
    else:
        history = json.loads((manifest_path.parent / 'history.json').read_text(encoding='utf-8'))
        source = history['archives'][archive.name]
        raw = git_blobs(root, source['commit'], [source['path']])[source['path']]
    if hashlib.sha256(raw).hexdigest() != manifest['archive_sha256']:
        raise ValueError(f'{archive.name}: archive SHA256 mismatch')
    with zipfile.ZipFile(io.BytesIO(raw)) as bundle:
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
