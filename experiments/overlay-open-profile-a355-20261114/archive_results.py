"""Archive completed campaigns, raw timings and failed attempts; exclude binaries.

Run only after the campaign exits. The host benchmark lock covers archiving.
The outer manifest hashes every archived input, independently of runner lists.
"""
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
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--rejected', type=Path, required=True)
    parser.add_argument('--accepted', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--accepted-completion', default='measure.exit')
    args = parser.parse_args()
    assert not args.out.exists()
    with open('/run/lock/ax-overlay-open-diagnostic.lock', 'a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        for root in (args.rejected, args.accepted):
            assert (root / 'measure.exit').is_file(), root
        assert Path(args.accepted_completion).name == args.accepted_completion
        assert (args.accepted / args.accepted_completion).read_text().strip() == '0'
        selected = {}
        for label, root in (('rejected', args.rejected), ('accepted', args.accepted)):
            for directory in ('profile-existing', 'results', 'fio-diagnostic', 'checks', 'checks2', 'checks3', 'artifacts'):
                for path in (root / directory).rglob('*'):
                    if not path.is_file() or path.is_symlink():
                        continue
                    if path.name in ('brewfs', 'packed_snapshot_fixture') or '__pycache__' in path.parts:
                        continue
                    selected[label + '/' + path.relative_to(root).as_posix()] = path
            for path in root.iterdir():
                if path.is_file() and path.suffix in ('.log', '.exit', '.sh'):
                    selected[label + '/' + path.name] = path
        args.out.mkdir(parents=True)
        manifest = {name: dict(bytes=path.stat().st_size, sha256=digest(path))
                    for name, path in sorted(selected.items())}
        manifest_path = args.out / 'inputs.json'
        manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True))
        archive = args.out / 'results.tar.gz'
        with tarfile.open(archive, 'x:gz', compresslevel=6) as tar:
            tar.add(manifest_path, arcname='inputs.json')
            for name, path in sorted(selected.items()):
                assert digest(path) == manifest[name]['sha256'], f'input changed: {name}'
                tar.add(path, arcname=name, recursive=False)
        # Re-read the archive and verify bytes, not merely tar member names.
        with tarfile.open(archive) as tar:
            for name, record in manifest.items():
                with tar.extractfile(name) as stream:
                    assert hashlib.file_digest(stream, 'sha256').hexdigest() == record['sha256'], name
        receipt = dict(passed=True, archive_bytes=archive.stat().st_size,
                       archive_sha256=digest(archive), input_files=len(manifest),
                       exclusions=['compiled binaries', 'build/source trees', 'Python bytecode'])
        (args.out / 'archive.json').write_text(json.dumps(receipt, indent=2))
        print(json.dumps(receipt))


if __name__ == '__main__':
    main()
