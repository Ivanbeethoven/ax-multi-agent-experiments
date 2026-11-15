#!/usr/bin/env python3
"""Qualify corpus inventory outside timing with a disk-backed uniqueness index.

Content is independently checked by the Rust scanner's verify pass. Symlinks,
unlisted files, duplicate manifest paths, and missing entries are rejected.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import sqlite3


def qualify(root, manifest, output):
    root = root.resolve(strict=True)
    manifest = manifest.resolve(strict=True)
    if manifest.is_relative_to(root) or output.resolve().is_relative_to(root):
        raise ValueError('inventory inputs/output must be outside corpus')
    output.mkdir(parents=True, exist_ok=False)
    report = {'passed': False, 'content_verified': False, 'files': 0, 'bytes': 0}
    db = sqlite3.connect(output/'inventory.sqlite')
    try:
        db.execute('CREATE TABLE files(path TEXT PRIMARY KEY, size INTEGER NOT NULL, seen INTEGER NOT NULL DEFAULT 0) WITHOUT ROWID')
        sha = hashlib.sha256()
        with manifest.open('rb') as source:
            for line in source:
                sha.update(line)
                fields = line.decode('utf-8').rstrip('\n').split('\t')
                if len(fields) != 3 or not re.fullmatch('[0-9a-f]{64}', fields[0]):
                    raise ValueError('invalid manifest digest/record')
                raw = fields[2]; path = PurePosixPath(raw)
                if not raw or path.is_absolute() or any(x in ('', '.', '..') for x in raw.split('/')) or '\\' in raw or '\r' in raw:
                    raise ValueError('noncanonical manifest path')
                size = int(fields[1])
                if size < 0:
                    raise ValueError('negative file length')
                try:
                    db.execute('INSERT INTO files(path,size) VALUES(?,?)', (raw, size))
                except sqlite3.IntegrityError:
                    raise ValueError('duplicate manifest path') from None
                report['files'] += 1; report['bytes'] += size
        db.commit()
        if not report['files']:
            raise ValueError('empty inventory')
        def onerror(error):
            raise error
        for directory, dirs, names in os.walk(root, followlinks=False, onerror=onerror):
            for name in dirs:
                if (Path(directory)/name).is_symlink():
                    raise ValueError('symlink directory in corpus')
            for name in names:
                path = Path(directory)/name
                if path.is_symlink() or not path.is_file():
                    raise ValueError('nonregular file in corpus')
                relative = path.relative_to(root).as_posix()
                found = db.execute('SELECT size,seen FROM files WHERE path=?', (relative,)).fetchone()
                if found != (path.stat().st_size, 0):
                    raise ValueError('unlisted, repeated, or wrong-size corpus file: '+relative)
                db.execute('UPDATE files SET seen=1 WHERE path=?', (relative,))
        db.commit()
        missing = db.execute('SELECT COUNT(*) FROM files WHERE seen=0').fetchone()[0]
        if missing:
            raise ValueError(f'{missing} listed files missing')
        report.update(passed=True, manifest_sha256=sha.hexdigest(), unique_paths=True,
                      exact_regular_file_inventory=True, symlinks=False)
    except Exception as error:
        report['error'] = str(error)
        raise
    finally:
        db.close()
        (output/'result.json').write_text(json.dumps(report, indent=2)+'\n')
    return report


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--root', type=Path, required=True)
    p.add_argument('--manifest', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    a = p.parse_args()
    print(json.dumps(qualify(a.root, a.manifest, a.output)))
