#!/usr/bin/env python3
"""Freeze TinyStories source; materialize a readonly small-file data-loader corpus.

Preparation is outside measurement. Replicated scale is explicitly disclosed.
This is an ML preprocessing access workload, not a live-agent benchmark score.
"""
import argparse
import hashlib
import json
from pathlib import Path
import urllib.request

REVISION='df479c2caefda65cbb295f6f9d3e61b0be8d6593'
URL=f'https://huggingface.co/datasets/roneneldan/TinyStories/resolve/{REVISION}/TinyStoriesV2-GPT4-train.txt'
SHA='6418d412de72888f52b5142c761ac21a582f7d1166f0bfbdb5f03ccfdec90443'
SOURCE_BYTES=2227753162
DELIMITER=b'<|endoftext|>'

def digest(path):
    h=hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda:stream.read(1048576),b''):h.update(chunk)
    return h.hexdigest()

def records(path):
    pending=b''
    with path.open('rb') as stream:
        for chunk in iter(lambda:stream.read(1048576),b''):
            pending+=chunk
            while DELIMITER in pending:
                item,pending=pending.split(DELIMITER,1)
                item=item.strip(b'\r\n')
                if item:yield item+b'\n'
            if len(pending)>64*1024*1024:raise ValueError('malformed oversized story')
    if pending.strip(b'\r\n'):yield pending.strip(b'\r\n')+b'\n'

def download(source):
    source.parent.mkdir(parents=True,exist_ok=True)
    partial=source.with_suffix('.partial')
    offset=partial.stat().st_size if partial.exists() else 0
    if offset>SOURCE_BYTES:raise ValueError('oversized partial download; retained for inspection')
    if offset<SOURCE_BYTES:
        request=urllib.request.Request(URL,headers={'Range':f'bytes={offset}-'} if offset else {})
        try:
            with urllib.request.urlopen(request,timeout=60) as src:
                if offset and (src.status!=206 or not src.headers.get('Content-Range','').startswith(f'bytes {offset}-')):
                    raise ValueError('server did not honor resume range')
                with partial.open('ab' if offset else 'xb') as dst:
                    for chunk in iter(lambda:src.read(1048576),b''):dst.write(chunk)
        except Exception as error:
            raise RuntimeError('dataset download failed: '+type(error).__name__) from None
    if partial.stat().st_size!=SOURCE_BYTES:
        raise ValueError(f'incomplete download: {partial.stat().st_size} of {SOURCE_BYTES}; retained for resume')
    if digest(partial)!=SHA:raise ValueError('download checksum mismatch; retained for inspection')
    partial.replace(source)

def prepare(source, root, manifest, count):
    if count<=0 or root.exists() or manifest.exists():raise ValueError('new output and positive count required')
    if manifest.resolve().is_relative_to(root.resolve()):raise ValueError('manifest must be outside corpus')
    assert digest(source)==SHA,'source checksum mismatch'
    root.mkdir(parents=True);manifest.parent.mkdir(parents=True,exist_ok=True)
    actual=0;total_bytes=0;source_count=0;cycles=0
    with manifest.open('x',encoding='utf-8',newline='\n') as stream:
        while actual<count:
            seen=0
            for payload in records(source):
                seen+=1
                if actual>=count:continue
                relative=f'{actual//65536:04x}/{(actual//256)%256:02x}/{actual:010d}.txt'
                path=root/relative;path.parent.mkdir(parents=True,exist_ok=True)
                with path.open('xb') as dest:dest.write(payload)
                stream.write(f'{hashlib.sha256(payload).hexdigest()}\t{len(payload)}\t{relative}\n')
                actual+=1;total_bytes+=len(payload)
            if seen==0:raise ValueError('empty source')
            if source_count and seen!=source_count:raise ValueError('source record count changed')
            source_count=seen;cycles+=1
    receipt={'source_url':URL,'source_revision':REVISION,'source_sha256':SHA,'license':'cdla-sharing-1.0',
             'source_records':source_count,'materialized_files':actual,'total_payload_bytes':total_bytes,
             'replicated_scale':count>source_count,'source_cycles':cycles,
             'distinct_source_records_used':min(count,source_count),'manifest_sha256':digest(manifest),
             'format':'SHA256 TAB bytes TAB relative-path; LF',
             'scope':'ML text-data discovery/preprocessing proxy; not an executed MLAgentBench agent',
             'timed_writes_allowed':False}
    manifest.with_suffix('.receipt.json').write_text(json.dumps(receipt,indent=2)+'\n')
    print(json.dumps(receipt))

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--source',type=Path,required=True)
    p.add_argument('--download',action='store_true');p.add_argument('--root',type=Path)
    p.add_argument('--manifest',type=Path);p.add_argument('--files',type=int,default=1048576)
    a=p.parse_args()
    if a.download and not a.source.exists():
        download(a.source)
    if a.root and a.manifest:prepare(a.source,a.root,a.manifest,a.files)
    else:
        assert digest(a.source)==SHA,'source checksum mismatch'
        print(json.dumps({'source_verified':True,'sha256':SHA,'bytes':a.source.stat().st_size}))
