#!/usr/bin/env python3
"""Build the complete declared C3 expected tree from source plus touched state.

Never reads the tested output. Hashes the immutable source and computes changed
4 KiB contents from the documented file/version/workspace generator.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import sys

REPO=Path(__file__).resolve().parents[4]
sys.path.insert(0,str(REPO))
from bench.runtime.common import digest,load,write_new

MASK=(1<<64)-1


def payload(index,version,workspace):
    rotate=((workspace<<17)|(workspace>>(64-17)))&MASK
    state=(index^((version*0xd6e8feb86659fd93)&MASK)^(rotate if version else 0))&MASK
    out=bytearray()
    for _ in range(512):
        state=(state+0x9e3779b97f4a7c15)&MASK
        z=state;z=((z^(z>>30))*0xbf58476d1ce4e5b9)&MASK
        z=((z^(z>>27))*0x94d049bb133111eb)&MASK;z^=z>>31
        out.extend(z.to_bytes(8,'little'))
    return bytes(out)


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--source',type=Path,required=True)
    p.add_argument('--source-proof',type=Path,required=True)
    p.add_argument('--state',type=Path)
    p.add_argument('--out',type=Path,required=True)
    p.add_argument('--files',type=int,choices=[100000,1000000],default=1000000)
    a=p.parse_args();source=load(a.source_proof)
    if source.get('passed') is not True or source.get('files')!=a.files or source.get('bytes_checked')!=a.files*4096:
        raise ValueError('Full C3 source qualification required')
    versions={};workspace=None
    if a.state:
        rows=a.state.read_text().splitlines();header=rows[0].split('\t')
        if len(header)!=3 or header[:2]!=['smallfiles-v1',str(a.files)]:raise ValueError('Exact declared file-count state required')
        workspace=int(header[2])
        if not 0<workspace<=MASK:raise ValueError('Positive u64 workspace required')
        for row in rows[1:]:
            index,version=map(int,row.split('\t'))
            if index in versions or not 0<=index<a.files or not 0<version<=MASK:raise ValueError('Bad touched identity')
            versions[index]=version
    a.out.mkdir(parents=True,exist_ok=False)
    count=0;entries=0;touched=set();size=0
    tree=a.out/'expected-tree.json'
    with tree.open('x') as f:
        f.write('{');first=True
        for root,dirs,files in os.walk(a.source,followlinks=False):
            for name in sorted(dirs)+sorted(files):
                path=Path(root)/name;stat=path.lstat();key=path.relative_to(a.source).as_posix()
                if path.is_symlink():raise ValueError('Synthetic C3 source has no symlinks')
                if path.is_dir():value=dict(type='directory',mode=stat.st_mode&0o7777)
                elif path.is_file():
                    match=re.fullmatch(r'small/d([0-9]{4})/f([0-9]{7})',key)
                    if not match:raise ValueError('Unexpected C3 file '+key)
                    directory,index=map(int,match.groups())
                    if not 0<=index<a.files or directory!=index%1024 or stat.st_size!=4096 or stat.st_nlink!=1:
                        raise ValueError('Wrong C3 source identity/size/link topology')
                    data=payload(index,versions[index],workspace) if index in versions else path.read_bytes()
                    value=dict(type='file',bytes=len(data),sha256=hashlib.sha256(data).hexdigest(),mode=stat.st_mode&0o7777)
                    count+=1;size+=len(data)
                    if index in versions:touched.add(index)
                else:raise ValueError('Unsupported C3 source inode')
                if not first:f.write(',')
                first=False;json.dump(key,f);f.write(':');json.dump(value,f);entries+=1
            if any((Path(root)/name).is_symlink() for name in dirs):raise ValueError('Unexpected directory link')
        f.write('}\n')
    if count!=a.files or entries!=a.files+1025 or size!=a.files*4096 or touched!=set(versions):
        raise ValueError('Incomplete independent expected view')
    write_new(a.out/'expected-hardlinks.json',dict(groups=[]))
    write_new(a.out/'result.json',dict(passed=True,files=count,entries=entries,logical_bytes=size,touched_files=len(touched),
        workspace=workspace,tree_sha256=digest(tree),source=str(a.source),source_proof=dict(path=str(a.source_proof),sha256=digest(a.source_proof)),
        state=dict(path=str(a.state),sha256=digest(a.state)) if a.state else None,
        scope='Independent immutable source plus file/version/workspace expected bytes; no tested output read'))


if __name__=='__main__':main()
