#!/usr/bin/env python3
"""Download verified trace archives from public OSS or the Drive backup."""
import argparse
import json
from pathlib import Path
import shutil
import subprocess
import urllib.request

from check_archives import verify_archive
from restore_parts import restore_parts


def main():
    manifest = json.loads((Path(__file__).resolve().parents[1] / 'archives.json').read_text(encoding='utf-8'))
    entries = {row['case']: row for row in manifest['archives']}
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path)
    parser.add_argument('--cases', nargs='+', choices=list(entries))
    parser.add_argument('--include-metadata', action='store_true')
    parser.add_argument('--metadata-only', action='store_true')
    parser.add_argument('--remote', default=manifest.get('drive', {}).get('rclone_remote', 'axdata'),
                        help='rclone remote rooted at the backup folder')
    parser.add_argument('--source', choices=('oss', 'drive'), default='oss')
    parser.add_argument('--internal', action='store_true', help='OSS internal endpoint; use inside Aliyun cn-hangzhou')
    parser.add_argument('--via-oss-cli', action='store_true', help=argparse.SUPPRESS)
    parser.add_argument('--list', action='store_true', help='Print selections without downloading')
    args = parser.parse_args()
    if args.metadata_only and args.cases:
        parser.error('--metadata-only cannot be combined with --cases')
    if args.internal and args.source != 'oss':
        parser.error('--internal requires --source oss')
    selected = manifest.get('metadata', []) if args.metadata_only else [entries[name] for name in args.cases or entries]
    if args.include_metadata and not args.metadata_only:
        selected += manifest.get('metadata', [])
    if args.list:
        print(json.dumps(selected, ensure_ascii=False, indent=2))
        return
    if args.out is None:
        parser.error('Provide --out for downloads')
    args.out.mkdir(parents=True, exist_ok=True)
    rclone = shutil.which('rclone') or shutil.which('rclone.exe')
    for entry in selected:
        if Path(entry['file']).name != entry['file']:
            raise ValueError('Catalog filenames must be simple basenames')
        target = args.out / entry['file']
        if target.is_symlink():
            raise ValueError('Refusing a symlink destination')
        if target.exists():
            print(json.dumps(verify_archive(target, entry)))
            continue
        partial = target.with_name(target.name + '.partial')
        if partial.exists() or partial.is_symlink():
            raise FileExistsError(f'Inspect unfinished download or use a new directory: {partial}')
        if args.source == 'oss':
            if args.via_oss_cli:
                cli = shutil.which('aliyun') or shutil.which('aliyun.exe')
                if not cli:
                    parser.error('--via-oss-cli requires an authorized Aliyun CLI')
                subprocess.run([cli, 'ossutil', 'cp', entry['uri'], str(partial)], check=True)
            else:
                url = entry.get('url') or entry['https_url']
                if args.internal:
                    url = url.replace('.oss-cn-hangzhou.aliyuncs.com/', '.oss-cn-hangzhou-internal.aliyuncs.com/')
                request = urllib.request.Request(url, headers={'Accept-Encoding': 'identity'})
                with urllib.request.urlopen(request, timeout=120) as source, partial.open('xb') as dest:
                    shutil.copyfileobj(source, dest, length=8 * 1024 * 1024)
        elif entry.get('drive_path'):
            if not rclone:
                parser.error('Install rclone and configure the Drive remote described in data-catalog.md')
            if entry.get('drive_parts_path'):
                staging = args.out / (entry['case'] + '.parts')
                if staging.exists() or staging.is_symlink():
                    raise FileExistsError(f'Inspect or move existing part directory: {staging}')
                subprocess.run([rclone, 'copy', f"{args.remote}:{entry['drive_parts_path']}", str(staging)], check=True)
                receipt = restore_parts(staging, target, entry)
                print(json.dumps(receipt), flush=True)
                continue
            subprocess.run([rclone, 'copyto', f"{args.remote}:{entry['drive_path']}", str(partial)], check=True)
        else:
            raise ValueError(f"No Drive path recorded for {entry['file']}")
        receipt = verify_archive(partial, entry)
        partial.rename(target)
        print(json.dumps(receipt), flush=True)


if __name__ == '__main__':
    main()
