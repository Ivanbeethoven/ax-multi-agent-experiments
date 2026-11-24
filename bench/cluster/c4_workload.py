"""Prepare, modify, and directly check deterministic C4 file trees."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import math
import struct
import time


def relative(index):
    return f'files/d{index//1000:04d}/f{index:07d}'


def block(index, version, size):
    unit=struct.pack('<QQ',index,version)
    return (unit*((size+15)//16))[:size]


def write_file(path,index,version,length):
    path.parent.mkdir(parents=True,exist_ok=True)
    with path.open('wb') as stream:
        left=length
        payload=block(index,version,min(length,1024*1024))
        while left:
            piece=payload[:min(left,len(payload))]
            stream.write(piece);left-=len(piece)
        stream.flush();os.fsync(stream.fileno())
    os.chmod(path,0o644)


def sync_directory(path):
    if os.name=='posix':
        fd=os.open(path,os.O_RDONLY|os.O_DIRECTORY)
        try:os.fsync(fd)
        finally:os.close(fd)


def prepare(root,files,file_bytes,workers=16):
    root=Path(root);root.mkdir(parents=True,exist_ok=True)
    for i in range(64):(root/'children'/f'c{i:03d}').mkdir(parents=True,exist_ok=True)
    start=time.monotonic()
    def one(index):write_file(root/relative(index),index,0,file_bytes)
    with ThreadPoolExecutor(max_workers=workers) as pool:list(pool.map(one,range(files)))
    for directory,_,_ in os.walk(root):sync_directory(directory)
    return {'files':files,'logical_write_bytes':files*file_bytes,'seconds':time.monotonic()-start}


def make_changes(count,child=0,start_index=0,kind='mixed-v1',write_bytes=4096,seed=1):
    entries=[]
    mix=['overwrite']*5+['create']*2+['delete','rename','chmod']
    for i in range(count):
        index=start_index+i
        operation='chmod' if kind=='metadata-only' else ('overwrite' if kind=='range-overwrite' else mix[i%10])
        path=relative(index)
        entry={'operation':operation,'index':index,'version':seed+child+1,'path':path,'offset':0,'bytes':write_bytes}
        if operation=='create':entry['path']=f'children/c{child:03d}/new-{index:07d}'
        if operation=='rename':entry['destination']=f'children/c{child:03d}/renamed-{index:07d}'
        if operation=='chmod':entry['mode']=0o600
        entries.append(entry)
    return entries


def apply(root,changes,full_file=False):
    root=Path(root);start=time.monotonic();written=0;read=0;touched=set()
    for entry in changes:
        path=root/entry['path'];op=entry['operation'];touched.add(path.parent)
        if op=='create':
            write_file(path,entry['index'],entry['version'],entry['bytes']);written+=entry['bytes']
        elif op=='overwrite':
            if full_file:
                content=bytearray(path.read_bytes());read+=len(content)
                offset=entry['offset'];content[offset:offset+entry['bytes']]=block(entry['index'],entry['version'],entry['bytes'])
                with path.open('r+b') as stream:
                    stream.write(content);stream.flush();os.fsync(stream.fileno())
                written+=len(content)
            else:
                with path.open('r+b') as stream:
                    stream.seek(entry['offset']);stream.write(block(entry['index'],entry['version'],entry['bytes']))
                    stream.flush();os.fsync(stream.fileno())
                written+=entry['bytes']
        elif op=='delete':path.unlink()
        elif op=='rename':
            destination=root/entry['destination'];path.rename(destination);touched.add(destination.parent)
        elif op=='chmod':os.chmod(path,entry['mode'])
        else:raise ValueError('Unknown operation: '+op)
    for directory in sorted(touched):sync_directory(directory)
    return {'operations':len(changes),'logical_read_bytes':read,'logical_write_bytes':written,'seconds':time.monotonic()-start}


def expected_entries(files,file_bytes,changes):
    entries={relative(i):{'index':i,'size':file_bytes,'mode':0o644,'version':0,'patches':[]} for i in range(files)}
    for entry in changes:
        path=entry['path'];op=entry['operation']
        if op=='create':entries[path]={'index':entry['index'],'size':entry['bytes'],'mode':0o644,'version':entry['version'],'patches':[]}
        elif op=='delete':del entries[path]
        elif op=='rename':entries[entry['destination']]=entries.pop(path)
        elif op=='chmod':entries[path]['mode']=entry['mode']
        elif op=='overwrite':entries[path]['patches'].append(entry)
    return entries


def read_tree(root,files,file_bytes):
    root=Path(root);latencies=[];read=0;first=None;start=time.monotonic()
    for index in range(files):
        tick=time.monotonic()
        with (root/relative(index)).open('rb') as stream:
            count=0
            while chunk:=stream.read(1024*1024):count+=len(chunk)
        if count!=file_bytes:raise RuntimeError('Read length differs: '+relative(index))
        read+=count;latencies.append(time.monotonic()-tick)
        if first is None:first=time.monotonic()-start
    elapsed=time.monotonic()-start;latencies.sort()
    return {'files':files,'logical_read_bytes':read,'seconds':elapsed,
            'first_read_seconds':first,'p99_file_seconds':latencies[max(0,math.ceil(len(latencies)*.99)-1)]}


def verify(root,files,file_bytes,changes,workers=8,sample=False):
    root=Path(root);entries=expected_entries(files,file_bytes,changes)
    if sample:
        names=sorted(entries)
        chosen=[names[0],names[-1]]
        modified=[change.get('destination',change['path']) for change in changes if change['operation']!='delete']
        chosen.extend(name for name in modified[:1] if name in entries)
        entries={name:entries[name] for name in dict.fromkeys(chosen)}
    start=time.monotonic()
    if not sample:
        virtual_files={'.accesslog','.config','.stats'}
        actual={p.relative_to(root).as_posix() for p in root.rglob('*')
                if p.relative_to(root).as_posix() not in virtual_files and p.is_file()}
        if actual!=set(entries):raise RuntimeError(f'File names differ: missing={len(set(entries)-actual)}, extra={len(actual-set(entries))}')
    else:
        if not (root/'files').is_dir() or not (root/'children').is_dir():raise RuntimeError('Required directory is absent')
    def one(item):
        name,entry=item;path=root/name;st=path.stat()
        if st.st_size!=entry['size'] or st.st_mode & 0o777!=entry['mode']:raise RuntimeError('File attributes differ: '+name)
        read=0
        limit=min(entry['size'],4096) if sample else entry['size']
        with path.open('rb') as stream:
            while read<limit:
                size=min(1024*1024,limit-read)
                expected=bytearray(block(entry['index'],entry['version'],size))
                for patch in entry['patches']:
                    low=max(read,patch['offset']);high=min(read+size,patch['offset']+patch['bytes'])
                    if high>low:
                        payload=block(patch['index'],patch['version'],patch['bytes'])
                        expected[low-read:high-read]=payload[low-patch['offset']:high-patch['offset']]
                if stream.read(size)!=expected:raise RuntimeError('File content differs: '+name)
                read+=size
        return read
    with ThreadPoolExecutor(max_workers=workers) as pool:read=sum(pool.map(one,entries.items()))
    return {'passed':True,'files':len(entries),'read_bytes':read,'seconds':time.monotonic()-start}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=['prepare','apply','verify','read','first-read'])
    parser.add_argument('--root',type=Path,required=True)
    parser.add_argument('--files',type=int,default=100000)
    parser.add_argument('--file-bytes',type=int,default=4096)
    parser.add_argument('--changes',type=Path)
    parser.add_argument('--workers',type=int,default=8)
    parser.add_argument('--full-file',action='store_true')
    opts=parser.parse_args()
    entries=json.loads(opts.changes.read_text()) if opts.changes else []
    if opts.action=='prepare':result=prepare(opts.root,opts.files,opts.file_bytes,opts.workers)
    elif opts.action=='apply':result=apply(opts.root,entries,opts.full_file)
    elif opts.action=='read':result=read_tree(opts.root,opts.files,opts.file_bytes)
    elif opts.action=='first-read':result=verify(opts.root,opts.files,opts.file_bytes,entries,1,True)
    else:result=verify(opts.root,opts.files,opts.file_bytes,entries,opts.workers)
    print(json.dumps(result))


if __name__=='__main__':main()
