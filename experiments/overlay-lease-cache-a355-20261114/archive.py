"""Archive verified receipts/raw results; preserve earlier failed checks."""
import argparse
import fcntl
import hashlib
import json
from pathlib import Path
import tarfile


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--campaign', type=Path, required=True)
    p.add_argument('--rejected', type=Path, action='append', required=True)
    p.add_argument('--out', type=Path, required=True)
    a = p.parse_args()
    assert not a.out.exists()
    assert (a.campaign / 'measure.exit').read_text().strip() == '0'
    assert (a.campaign / 'build-check.exit').read_text().strip() == '0'
    with open('/run/lock/ax-overlay-open-diagnostic.lock', 'a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        files = {}
        roots = [('accepted', a.campaign)] + [(f'prior-{i+1}', root) for i, root in enumerate(a.rejected)]
        for label, root in roots:
            for folder in ('results', 'checks', 'artifacts', 'harness'):
                for path in (root / folder).rglob('*'):
                    if path.is_file() and not path.is_symlink() and path.name not in ('brewfs', 'packed_snapshot_fixture') and '__pycache__' not in path.parts:
                        files[label + '/' + path.relative_to(root).as_posix()] = path
            for path in root.iterdir():
                if path.is_file() and path.suffix in ('.log', '.exit', '.sh', '.json', '.patch'):
                    files[label + '/' + path.name] = path
        a.out.mkdir()
        manifest = {name: dict(bytes=path.stat().st_size, sha256=digest(path)) for name, path in sorted(files.items())}
        manifest_path = a.out / 'inputs.json'
        manifest_path.write_text(json.dumps(manifest, indent=2))
        archive = a.out / 'results.tar.gz'
        with tarfile.open(archive, 'x:gz') as tar:
            tar.add(manifest_path, arcname='inputs.json')
            for name, path in sorted(files.items()):
                assert digest(path) == manifest[name]['sha256']
                tar.add(path, arcname=name, recursive=False)
        with tarfile.open(archive) as tar:
            for name, value in manifest.items():
                with tar.extractfile(name) as stream:
                    assert hashlib.file_digest(stream, 'sha256').hexdigest() == value['sha256'], name
        record = dict(passed=True, files=len(files), bytes=archive.stat().st_size, sha256=digest(archive),
                      excluded=['binaries', 'build/source trees', 'Python bytecode'])
        (a.out / 'archive.json').write_text(json.dumps(record, indent=2))
        print(json.dumps(record))


if __name__ == '__main__':
    main()
