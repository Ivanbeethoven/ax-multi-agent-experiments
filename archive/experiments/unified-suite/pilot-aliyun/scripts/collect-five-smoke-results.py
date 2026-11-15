#!/usr/bin/env python3
"""Collect only small proof files, never binaries, workspaces or SSH keys."""
import argparse
import hashlib
import io
import json
from pathlib import Path
import tarfile

p=argparse.ArgumentParser(description=__doc__)
p.add_argument('--job-root',type=Path,required=True)
p.add_argument('--out',type=Path,required=True)
a=p.parse_args();root=a.job_root.resolve()
if a.out.exists(): p.error('Output must be new')
selected={}
for folder in sorted(root.glob('*results-*')):
    if not folder.is_dir(): continue
    for file in sorted(folder.rglob('*')):
        if not file.is_file() or file.is_symlink() or file.name in ('stdout','stderr'): continue
        if file.suffix not in ('.json','.jsonl','.stats','.log','.txt','.tsv','.csv'): continue
        if not file.resolve().is_relative_to(root): p.error('Result escapes job root')
        selected[file.relative_to(root).as_posix()]=file
build_files=('candidate.patch','build-provenance.json','build.exit','build-started','build-ended','build.log','artifacts.sha256')
for folder in [root,*sorted(root.glob('candidate-*'))]:
    if not folder.is_dir(): continue
    for name in build_files:
        file=folder/name
        if file.is_file() and not file.is_symlink():
            selected['build/'+('01' if folder==root else folder.name)+'/'+name]=file
for name in ('input.json','tree.json'):
    file=root/'fs-input'/name
    if file.is_file(): selected['input/'+name]=file
for pattern in ('run-*.sh','build-and-check-*.sh','build-02.sh','write-provenance.py','move-tools.sha256'):
    for file in root.glob(pattern): selected['commands/'+file.name]=file
total=sum(file.stat().st_size for file in selected.values())
if total>64*1024*1024: p.error('Small results exceed 64 MiB; inspect before collecting')
manifest={}
with tarfile.open(a.out,'w:gz') as archive:
    for name,file in sorted(selected.items()):
        data=file.read_bytes()
        if b'PRIVATE KEY-----' in data: p.error('Private key in selected evidence')
        manifest[name]={'bytes':len(data),'sha256':hashlib.sha256(data).hexdigest()}
        info=tarfile.TarInfo(name);info.size=len(data);info.mode=0o644
        archive.addfile(info,io.BytesIO(data))
    data=(json.dumps(manifest,indent=2)+'\n').encode()
    info=tarfile.TarInfo('manifest.json');info.size=len(data);info.mode=0o644
    archive.addfile(info,io.BytesIO(data))
print(json.dumps({'files':len(selected),'uncompressed_bytes':total,'archive_bytes':a.out.stat().st_size,
                  'sha256':hashlib.sha256(a.out.read_bytes()).hexdigest()}))
