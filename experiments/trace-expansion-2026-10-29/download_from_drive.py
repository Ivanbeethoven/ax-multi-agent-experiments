#!/usr/bin/env python3
"""Download selected trace archives from Drive or the retained private OSS copy."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess


CATALOG = Path(__file__).with_name('drive-catalog.json')


def verify(path, entry):
    if path.stat().st_size != entry['bytes']:
        raise ValueError(f"{path}: expected {entry['bytes']} bytes, got {path.stat().st_size}")
    digest = hashlib.sha256()
    with path.open('rb') as source:
        for block in iter(lambda: source.read(8 * 1024 * 1024), b''):
            digest.update(block)
    actual = digest.hexdigest()
    if actual != entry['sha256']:
        raise ValueError(f"{path}: SHA256 mismatch: {actual}")
    return {'case': entry['case'], 'bytes': entry['bytes'], 'sha256': actual, 'verified': True}


def main():
    catalog = json.loads(CATALOG.read_text(encoding='utf-8'))
    entries = {entry['case']: entry for entry in catalog['archives']}
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, help='Directory outside the measured filesystem')
    parser.add_argument('--cases', nargs='+', choices=list(entries), help='Defaults to all 20 traces')
    parser.add_argument('--source', choices=('drive', 'oss'), default='drive',
                        help='Drive uses rclone; oss uses an authenticated Aliyun CLI profile')
    parser.add_argument('--remote', default=catalog['rclone_remote'], help='Configured rclone Drive remote')
    parser.add_argument('--list', action='store_true', help='List cases and hashes without downloading')
    args = parser.parse_args()
    selected = [entries[name] for name in args.cases] if args.cases else catalog['archives']
    if args.list:
        print(json.dumps(selected, ensure_ascii=False, indent=2))
        return
    if args.out is None:
        parser.error('Provide --out for downloads')
    if args.source == 'drive':
        downloader = shutil.which('rclone') or shutil.which('rclone.exe')
        if not downloader:
            parser.error('Install rclone and configure the authorized Drive remote first')
    else:
        downloader = shutil.which('aliyun') or shutil.which('aliyun.exe')
        if not downloader:
            parser.error('Install Aliyun CLI and configure an authorized identity first')
    args.out.mkdir(parents=True, exist_ok=True)
    for entry in selected:
        if Path(entry['file']).name != entry['file']:
            raise ValueError('Catalog filenames must be simple basenames')
        target = args.out / entry['file']
        if target.is_symlink():
            raise ValueError(f'Refusing symlink destination: {target}')
        if target.exists():
            print(json.dumps(verify(target, entry)), flush=True)
            continue
        partial = target.with_name(target.name + '.partial')
        if partial.exists() or partial.is_symlink():
            raise FileExistsError(f'Inspect unfinished download or use a new directory: {partial}')
        if args.source == 'drive':
            subprocess.run([downloader, 'copyto', f"{args.remote}:{entry['drive_path']}", str(partial)], check=True)
        else:
            if not entry.get('oss_uri'):
                raise ValueError(f"No retained OSS URI recorded for {entry['case']}")
            subprocess.run([downloader, 'ossutil', 'cp', entry['oss_uri'], str(partial)], check=True)
        receipt = verify(partial, entry)
        receipt['source'] = args.source
        partial.rename(target)
        print(json.dumps(receipt), flush=True)


if __name__ == '__main__':
    main()
