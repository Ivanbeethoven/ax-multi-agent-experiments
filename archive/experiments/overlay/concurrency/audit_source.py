#!/usr/bin/env python3
"""Compare the build tree with the reviewed Git working tree, without .git on host."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess


def digest(path):
    data = path.read_bytes()
    # Windows text checkout; binary assets remain byte-for-byte inputs.
    if b'\0' not in data:
        data = data.replace(b'\r\n', b'\n')
    return hashlib.sha256(data).hexdigest()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('mode', choices=('manifest', 'verify'))
    parser.add_argument('root', type=Path)
    parser.add_argument('manifest', type=Path)
    args = parser.parse_args()
    if args.mode == 'manifest':
        paths = subprocess.check_output(['git', 'ls-files', '-z'], cwd=args.root).decode().split('\0')
        record = {name: digest(args.root / name) for name in paths if name and (args.root / name).is_file()}
        with args.manifest.open('x', encoding='utf-8') as stream:
            json.dump(record, stream, indent=2, sort_keys=True)
        return
    record = json.loads(args.manifest.read_text(encoding='utf-8'))
    missing = [p for p in record if not (args.root / p).is_file()]
    different = [p for p, want in record.items() if (args.root / p).is_file() and digest(args.root / p) != want]
    passed = not missing and not different
    print(json.dumps(dict(passed=passed, files=len(record), missing=missing, different=different,
                         normalization='CRLF to LF for text files only',
                         manifest_sha256=hashlib.sha256(args.manifest.read_bytes()).hexdigest()), indent=2))
    raise SystemExit(0 if passed else 1)


if __name__ == '__main__':
    main()
