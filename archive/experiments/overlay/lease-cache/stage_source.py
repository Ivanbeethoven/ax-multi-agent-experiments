"""Package tracked source plus reviewed edits without copying .git/build trees."""
import argparse
import hashlib
import io
import json
from pathlib import Path
import subprocess
import tarfile


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--source', type=Path, required=True)
    p.add_argument('--out', type=Path, required=True)
    a = p.parse_args()
    a.out.mkdir(parents=True, exist_ok=False)
    def git(*args):
        return subprocess.check_output(['git', '-C', str(a.source), *args])
    files = git('ls-files', '-z').decode().split('\0')
    manifest = {}
    with tarfile.open(a.out / 'source.tar.gz', 'w:gz') as archive:
        for name in sorted(filter(None, files)):
            path = a.source / name
            if not path.is_file():
                continue
            content = path.read_bytes()
            if path.suffix in {'.rs', '.sh', '.toml', '.md', '.yml', '.yaml', '.py', '.lock'}:
                content = content.replace(b'\r\n', b'\n')
            info = tarfile.TarInfo('candidate/' + name)
            info.size = len(content)
            info.mode = 0o755 if path.suffix == '.sh' else 0o644
            archive.addfile(info, io.BytesIO(content))
            manifest[name] = hashlib.sha256(content).hexdigest()
    (a.out / 'source-manifest.json').write_text(json.dumps(manifest, indent=2))
    (a.out / 'source.patch').write_bytes(git('diff', '--binary', 'origin/main').replace(b'\r\n', b'\n'))
    (a.out / 'provenance.json').write_text(json.dumps({
        'head': git('rev-parse', 'HEAD').decode().strip(),
        'main': git('rev-parse', 'origin/main').decode().strip(),
        'status': git('status', '--short').decode(),
        'archive_sha256': hashlib.sha256((a.out / 'source.tar.gz').read_bytes()).hexdigest(),
    }, indent=2))


if __name__ == '__main__':
    main()
