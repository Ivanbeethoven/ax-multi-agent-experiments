#!/usr/bin/env python3
"""Retain only OpenCode's installed config dependency tree after qualification."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import tarfile


def digest(path):
    with path.open('rb') as f:return hashlib.file_digest(f,'sha256').hexdigest()


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--qualified-run',type=Path,required=True)
    p.add_argument('--out',type=Path,required=True)
    a=p.parse_args();qualification=json.loads((a.qualified_run/'result.json').read_text())
    if qualification.get('passed') is not True:raise ValueError('successful native session qualification required')
    source=(a.qualified_run/'workspace/source/config/opencode').resolve(strict=True)
    allowed={'package.json','bun.lock','node_modules','.gitignore'}
    if {x.name for x in source.iterdir()}-allowed:raise ValueError('unexpected config files; do not package credentials')
    package=json.loads((source/'package.json').read_text())
    version=qualification['opencode_version']
    if package.get('dependencies')!={'@opencode-ai/plugin':version}:
        raise ValueError('dependency version differs from qualified binary')
    files={}
    for file in sorted(source.rglob('*')):
        name=file.relative_to(source).as_posix()
        if file.is_symlink():
            if not file.resolve(strict=True).is_relative_to(source):raise ValueError('dependency link escapes bundle')
            files[name]=dict(type='symlink',target=os.readlink(file))
        elif file.is_file():files[name]=dict(type='file',bytes=file.stat().st_size,sha256=digest(file))
        elif not file.is_dir():raise ValueError('unsupported dependency file')
    a.out.mkdir(parents=True,exist_ok=False)
    archive=a.out/'opencode-config-dependencies.tar'
    with tarfile.open(archive,'x',dereference=False) as tar:
        tar.add(source,arcname='config/opencode',recursive=True)
    receipt=dict(opencode_version=version,opencode_sha256=qualification['opencode_binary_sha256'],
        qualified_run_result_sha256=digest(a.qualified_run/'result.json'),files=files,
        archive=dict(path=archive.name,bytes=archive.stat().st_size,sha256=digest(archive)),
        source_scope='installed plugin/SDK dependency files only; no provider config, session database, HOME or API credentials')
    (a.out/'receipt.json').write_text(json.dumps(receipt,indent=2)+'\n')
    print(json.dumps({k:v for k,v in receipt.items() if k!='files'},indent=2))


if __name__=='__main__':main()
