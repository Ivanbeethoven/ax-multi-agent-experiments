"""Pack an explicit closed evidence set; verify outer SHA and every tar member.

Never extracts or deletes input. Interrupted outputs remain for diagnosis.
"""
import argparse
import hashlib
import io
import json
from pathlib import Path, PurePosixPath
import re
import tarfile

from .experiment_io import digest, load, write_new, contained

MANIFEST='.suite-manifest.json'


def safe(name):
    p=PurePosixPath(name)
    if not name or name.startswith('/') or '\\' in name or ':' in name or '..' in p.parts or str(p)!=name.rstrip('/'):
        raise ValueError('unsafe/noncanonical archive path')
    return name.rstrip('/')


def verify(path, expected_sha):
    if digest(path)!=expected_sha:raise ValueError('outer archive checksum differs')
    found={};seen=set();manifest=None
    with tarfile.open(path,'r:gz') as archive:
        for member in archive:
            name=safe(member.name)
            if name in seen:raise ValueError('duplicate archive member')
            seen.add(name)
            if member.isdir():continue
            if not member.isfile():raise ValueError('archive links/special files are not retained evidence bytes')
            with archive.extractfile(member) as stream:
                if name==MANIFEST:
                    if member.size>16*1024**2:raise ValueError('unbounded internal manifest')
                    manifest=json.load(stream)
                else:
                    checksum=hashlib.file_digest(stream,'sha256').hexdigest()
                    found[name]={'bytes':member.size,'sha256':checksum}
    if not manifest or manifest.get('schema')!='suite-archive-manifest-v1' or manifest.get('deferred')!=[]:
        raise ValueError('complete internal manifest with no deferred entries required')
    if not found or found!=manifest['files']:raise ValueError('internal member list/size/checksum differs')
    return {'schema':'suite-archive-check-v1','passed':True,'archive':str(Path(path).resolve()),
            'sha256':expected_sha,'bytes':Path(path).stat().st_size,'file_members':len(found),
            'cell_id':manifest['cell_id'],'kind':manifest['kind'],'deferred':[],
            'scope':'all archive bytes; protocol raw-data completeness is checked separately'}


def pack(root, files, output, cell_id, kind):
    root=Path(root).resolve(strict=True);out=Path(output)
    if not out.is_absolute() or out.resolve().is_relative_to(root) or out.exists() or out.is_symlink():
        raise ValueError('new absolute archive outside evidence root required')
    if not cell_id or not kind or not files or len(set(files))!=len(files):raise ValueError('cell/kind and explicit file allowlist required')
    entries={}
    for name in files:
        safe(name);p=contained(root,name)
        if name==MANIFEST or not p.is_file() or (root/name).is_symlink():raise ValueError('regular evidence files required')
        if re.search(r'(?i)(credential|secret|\.env$|id_(rsa|ed25519)|\.pem$)',name):raise ValueError('credential-like evidence excluded')
        entries[name]={'bytes':p.stat().st_size,'sha256':digest(p)}
    manifest={'schema':'suite-archive-manifest-v1','cell_id':cell_id,'kind':kind,'files':entries,'deferred':[]}
    raw=(json.dumps(manifest,sort_keys=True)+'\n').encode()
    with out.open('xb') as f:
        import os
        if os.name!='nt':os.chmod(out,0o600)
        with tarfile.open(fileobj=f,mode='w:gz') as archive:
            m=tarfile.TarInfo(MANIFEST);m.size=len(raw);m.mode=0o600
            archive.addfile(m,io.BytesIO(raw))
            for name in files:archive.add(contained(root,name),arcname=name,recursive=False)
        f.flush();os.fsync(f.fileno())
    # Detect mutation between hashing and packing. A failure retains the file
    # and caller must not label/download it as a verified final archive.
    return verify(out,digest(out))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);sub=p.add_subparsers(dest='action',required=True)
    s=sub.add_parser('pack')
    for k in ('root','allowlist','archive','cell','kind','output'):s.add_argument('--'+k,required=True)
    s=sub.add_parser('verify')
    for k in ('archive','sha256','output'):s.add_argument('--'+k,required=True)
    a=p.parse_args()
    value=pack(a.root,load(a.allowlist),a.archive,a.cell,a.kind) if a.action=='pack' else verify(a.archive,a.sha256)
    write_new(a.output,value)
