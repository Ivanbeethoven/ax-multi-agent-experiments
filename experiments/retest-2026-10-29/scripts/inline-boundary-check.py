"""Actual small-object placement under pinned RustFS EC 2+2 and default inline policy."""
from contextlib import closing
import hashlib
import importlib.util
import json
from pathlib import Path
import secrets
import subprocess
import time

module=importlib.util.spec_from_file_location('inline_layout',Path(__file__).with_name('inspect-layout.py'))
layout=importlib.util.module_from_spec(module);module.loader.exec_module(layout)
SIZES=(1024,4096,32768,32769,65536,65537,131072,131073,262144,262145,1048576)
HINTS=(None,65536,262144)


def payload(size):
    return hashlib.shake_256(b'ax-inline-boundary-'+str(size).encode()).digest(size)


def readback(client,bucket,key,size):
    expected=payload(size)
    with closing(client.get_object(Bucket=bucket,Key=key)['Body']) as stream:
        if stream.read()!=expected:raise ValueError('small-object content mismatch')
    start=max(0,size//2-17);end=min(start+4095,size-1)
    with closing(client.get_object(Bucket=bucket,Key=key,Range=f'bytes={start}-{end}')['Body']) as stream:
        if stream.read()!=expected[start:end+1]:raise ValueError('small-object range mismatch')


def check(client,directory,data,enabled,backend_name,small_frames=None):
    if small_frames is None:small_frames=enabled
    directory.mkdir()
    report={'passed':False,'geometry':'EC 2+2','block_hint_enabled':enabled,'read_quantum_enabled':small_frames,
            'inline_policy':'default; no explicit per-shard override','objects':[],
            'scope':'placement/content qualification only, no latency or physical I/O claim'}
    def save(): (directory/'result.json').write_text(json.dumps(report,indent=2)+'\n')
    save()
    try:
        for versioned in (False,True):
            bucket='ax-inline-'+secrets.token_hex(8)
            client.create_bucket(Bucket=bucket)
            if versioned:client.put_bucket_versioning(Bucket=bucket,VersioningConfiguration={'Status':'Enabled'})
            for hint in HINTS:
                for size in SIZES:
                    key=f'hint-{hint}/size-{size}'
                    response=client.put_object(Bucket=bucket,Key=key,Body=payload(size),
                        Metadata={} if hint is None else {'rustfs-ec-block-size-hint':str(hint)})
                    head=client.head_object(Bucket=bucket,Key=key)
                    effective=int(head['ResponseMetadata']['HTTPHeaders']['x-rustfs-effective-ec-block-size'])
                    expected_block=hint if enabled and hint else 1048576
                    if effective!=expected_block:raise ValueError('unexpected actual block size')
                    expected_inline=size<=min(expected_block,32768 if versioned else 262144)
                    row={'bucket':bucket,'key':key,'object_bytes':size,'versioned':versioned,
                         'version_id':response.get('VersionId'),'requested_block_bytes':hint,
                         'effective_block_bytes':effective,'expected_inline':expected_inline,'shards':[]}
                    report['objects'].append(row);save()
                    for disk in range(1,5):
                        path=data/f'disk{disk}'/bucket/key/'xl.meta'
                        decoded=layout.decode(path);obj=decoded['versions'][0]['V2Obj']
                        if (obj['EcM'],obj['EcN'])!=(2,2):raise ValueError('unexpected EC geometry')
                        meta=obj['MetaSys']
                        inline='x-rustfs-internal-inline-data' in meta or 'x-minio-internal-inline-data' in meta
                        parts=list(path.parent.glob('*/part.*'))
                        q=meta.get('x-rustfs-internal-ec-read-quantum')
                        item={'disk':disk,'inline_marker':inline,'xl_meta_bytes':decoded['xl_meta_bytes'],
                              'metadata_crc_verified':decoded['metadata_crc_verified'],
                              'inline_payload_bytes':sum(decoded['inline_shard_lengths'].values()),
                              'part_files':len(parts),'part_bytes':sum(p.stat().st_size for p in parts),
                              'read_quantum_bytes':int(q) if q else None,
                              'xl_meta_sha256':decoded['xl_meta_sha256']}
                        row['shards'].append(item);save()
                        if inline!=expected_inline:raise ValueError('actual inline boundary differs from pinned source prediction')
                        if inline:
                            if not item['inline_payload_bytes'] or parts or q is not None:
                                raise ValueError('inconsistent inline placement/frame marker')
                        elif not parts or item['inline_payload_bytes']:
                            raise ValueError('inconsistent external shard placement')
                        else:
                            expected_q=effective//4 if small_frames and effective<=262144 else None
                            if item['read_quantum_bytes']!=expected_q:
                                raise ValueError('external read frame differs from selected profile')
                    readback(client,bucket,key,size)
                    row['content_and_range_verified']=True;save()
        subprocess.run(['docker','restart',backend_name],check=True,stdout=subprocess.DEVNULL)
        for _ in range(60):
            try:client.list_buckets();break
            except Exception:time.sleep(1)
        else:raise RuntimeError('backend did not restart')
        for row in report['objects']:readback(client,row['bucket'],row['key'],row['object_bytes'])
        report.update(passed=True,restart_content_and_range_verified=True)
    except Exception as error:
        report['error']=type(error).__name__+': '+str(error);raise
    finally:save()
    return {'passed':True,'objects':len(report['objects']),'restart_content_and_range_verified':True}
