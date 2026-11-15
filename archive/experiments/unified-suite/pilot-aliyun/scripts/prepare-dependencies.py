#!/usr/bin/env python3
"""Fetch the current stable JuiceFS release with its publisher digest; pin OpenCode."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import tarfile
import urllib.request


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--build-root', type=Path, required=True)
    parser.add_argument('--opencode', type=Path, required=True, help='Previously qualified Linux executable')
    args = parser.parse_args()
    root = args.build_root.resolve(strict=True)
    out = root/'dependencies'
    out.mkdir(exist_ok=False)
    request = urllib.request.Request('https://api.github.com/repos/juicedata/juicefs/releases/latest',
                                     headers={'Accept':'application/vnd.github+json', 'User-Agent':'ax-pilot'})
    with urllib.request.urlopen(request, timeout=60) as response:
        raw = response.read()
    (out/'juicefs-release.json').write_bytes(raw)
    release = json.loads(raw)
    assets = [a for a in release['assets'] if a['name'].endswith('-linux-amd64.tar.gz')]
    if len(assets) != 1 or release['prerelease'] or release['draft']:
        raise ValueError('Expected one Linux amd64 stable release')
    asset = assets[0]
    expected = asset.get('digest', '')
    if not expected.startswith('sha256:') or len(expected) != 71:
        raise ValueError('Publisher SHA256 missing; do not run unverified release')
    archive = out/asset['name']
    with urllib.request.urlopen(asset['browser_download_url'], timeout=120) as response, archive.open('xb') as stream:
        shutil.copyfileobj(response, stream, length=8*1024*1024)
    if digest(archive) != expected.split(':',1)[1] or archive.stat().st_size != asset['size']:
        raise ValueError('Publisher digest/size mismatch')
    destination = root/'bin/juicefs'
    if destination.exists() or (root/'bin/opencode').exists():
        raise FileExistsError('Inspect existing binaries; do not overwrite frozen assets')
    with tarfile.open(archive) as tar:
        matches = [m for m in tar.getmembers() if Path(m.name).name=='juicefs' and m.isfile()]
        if len(matches) != 1:
            raise ValueError('Expected one juicefs binary')
        with tar.extractfile(matches[0]) as source, destination.open('xb') as stream:
            shutil.copyfileobj(source, stream)
    destination.chmod(0o755)
    shutil.copyfile(args.opencode, root/'bin/opencode')
    (root/'bin/opencode').chmod(0o755)
    receipt = dict(at=datetime.now(timezone.utc).isoformat(), juicefs_release=release['tag_name'],
                   juicefs_download=asset['browser_download_url'], juicefs_archive_sha256=digest(archive),
                   juicefs_binary_sha256=digest(destination),
                   juicefs_version=subprocess.check_output([str(destination),'version'], text=True).strip(),
                   opencode_sha256=digest(root/'bin/opencode'),
                   opencode_version=subprocess.check_output([str(root/'bin/opencode'),'--version'], text=True).strip(),
                   opencode_note='Existing Linux executable pinned for native session qualification; not a BrewFS/RustFS branch')
    (out/'receipt.json').write_text(json.dumps(receipt, indent=2)+'\n')
    print(json.dumps(receipt, indent=2))


if __name__ == '__main__':
    main()
