#!/usr/bin/env python3
"""Download selected private OSS archives using an existing Aliyun CLI identity."""
import argparse
import json
from pathlib import Path
import shutil
import subprocess
from check_archives import verify_archive


def main():
    manifest = json.loads((Path(__file__).resolve().parents[1] / 'archives.json').read_text(encoding='utf-8'))
    entries = {row['case']: row for row in manifest['archives']}
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path)
    parser.add_argument('--cases', nargs='+', choices=list(entries))
    parser.add_argument('--include-metadata', action='store_true')
    parser.add_argument('--endpoint', default=manifest['endpoint'])
    parser.add_argument('--list', action='store_true', help='Print selections without downloading')
    args = parser.parse_args()
    selected = [entries[name] for name in args.cases or entries]
    if args.include_metadata:
        selected += manifest.get('metadata', [])
    if args.list:
        print(json.dumps(selected, ensure_ascii=False, indent=2))
        return
    if args.out is None:
        parser.error('Provide --out for downloads')
    cli = shutil.which('aliyun') or shutil.which('aliyun.exe')
    if not cli:
        parser.error('Aliyun CLI with an authorized OSS identity is required')
    args.out.mkdir(parents=True, exist_ok=True)
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
        subprocess.run([cli, 'oss', 'cp', entry['uri'], str(partial),
                        '--endpoint', args.endpoint, '--cli-non-interactive'], check=True)
        receipt = verify_archive(partial, entry)
        partial.rename(target)
        print(json.dumps(receipt), flush=True)


if __name__ == '__main__':
    main()
