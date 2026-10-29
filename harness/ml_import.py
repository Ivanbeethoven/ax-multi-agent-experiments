#!/usr/bin/env python3
"""Import one frozen corpus through an already mounted native FS, then exit."""
import argparse
import hashlib
import os
from pathlib import Path, PurePosixPath
import time
from experiment_io import digest, write_new


def import_files(source, manifest, target, expected_files, expected_bytes):
    source = Path(source).resolve(strict=True); target = Path(target).resolve(strict=True)
    if source == target or source in target.parents or target in source.parents:
        raise ValueError('source and target must be disjoint')
    if any(target.iterdir()): raise FileExistsError('fresh empty target directory required')
    count = total = 0; started = time.monotonic()
    with Path(manifest).open(encoding='utf-8') as stream:
        for line in stream:
            expected, size, name = line.rstrip('\n').split('\t')
            relative = PurePosixPath(name)
            if relative.is_absolute() or '\\' in name or any(p in ('','..','.') for p in name.split('/')):
                raise ValueError('unsafe manifest path')
            src = source/name; dst = target/name
            if src.is_symlink() or not src.is_file() or not src.resolve().is_relative_to(source):
                raise ValueError('source type/path changed')
            dst.parent.mkdir(parents=True, exist_ok=True)
            if not dst.parent.resolve().is_relative_to(target): raise ValueError('destination escapes target')
            h = hashlib.sha256(); actual = 0
            with src.open('rb') as read, dst.open('xb') as write:
                while data := read.read(1024*1024):
                    write.write(data); h.update(data); actual += len(data)
                write.flush(); os.fsync(write.fileno())
            if actual != int(size) or h.hexdigest() != expected: raise ValueError('source changed during import')
            count += 1; total += actual
    if (count,total) != (expected_files,expected_bytes): raise ValueError('incomplete native import')
    return {'files': count, 'bytes': total, 'seconds': time.monotonic()-started, 'file_fsync': True,
            'source_hashes_verified': True, 'scope': 'preparation writes only; remote drain, remount and verification are separate'}


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    for key in ('source','manifest','target','output'): p.add_argument('--'+key,type=Path,required=True)
    p.add_argument('--manifest-sha256',required=True); p.add_argument('--files',type=int,required=True)
    p.add_argument('--bytes',type=int,required=True); a=p.parse_args()
    if a.output.exists() or digest(a.manifest) != a.manifest_sha256: raise ValueError('new output and pinned manifest required')
    try: result = import_files(a.source,a.manifest,a.target,a.files,a.bytes)
    except Exception as error:
        write_new(a.output, {'passed':False,'error_type':type(error).__name__,'partial_target_retained':True}); raise
    write_new(a.output, {'passed':True,**result})
