#!/usr/bin/env python3
"""Decode only owned probe objects from RustFS XL2 format 1.3."""
import argparse
import hashlib
import json
from pathlib import Path
import struct
import msgpack
import xxhash

def decode(path):
    raw=path.read_bytes()
    if raw[:4]!=b'XL2 ' or struct.unpack('<HH',raw[4:8])!=(1,3):
        raise ValueError('unsupported persisted format')
    outer=msgpack.Unpacker(raw=False,strict_map_key=False)
    outer.feed(raw[8:]); body=outer.unpack(); crc=outer.unpack()
    if crc != xxhash.xxh64(body,seed=0).intdigest() & 0xffffffff:
        raise ValueError('XL2 metadata checksum mismatch')
    inline=raw[8+outer.tell():]
    inline_sizes={}
    if inline:
        if inline[0]!=1:raise ValueError('unsupported inline format')
        inline_sizes={k:len(v) for k,v in msgpack.unpackb(inline[1:],raw=False).items()}
    inner=msgpack.Unpacker(raw=False,strict_map_key=False)
    inner.feed(body)
    header_version,meta_version,count=inner.unpack(),inner.unpack(),inner.unpack()
    values=[]
    for _ in range(count):
        header=inner.unpack(); value=msgpack.unpackb(inner.unpack(),raw=False,strict_map_key=False)
        values.append(value)
    return {'format':[1,3,header_version,meta_version], 'versions':values,
            'metadata_crc_verified':True,'inline_shard_lengths':inline_sizes,
            'xl_meta_bytes':len(raw),
            'xl_meta_sha256':hashlib.sha256(raw).hexdigest()}

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('root',type=Path);a=p.parse_args()
    report=json.loads((a.root/'result.json').read_text())
    results=[]
    for run in report['runs']:
        for row in run['objects']:
            shards=[]
            for disk in range(1,5):
                path=a.root/run['mode']/'data'/f'disk{disk}'/row['bucket']/row['key']/'xl.meta'
                value=decode(path)
                assert value['format']==[1,3,3,3]
                assert len(value['versions'])==1
                obj=value['versions'][0]['V2Obj']
                assert (obj['EcM'],obj['EcN'])==(2,2), 'unexpected actual EC geometry'
                assert obj['EcBSize']==row['expected_bytes'], 'persisted block differs from request outcome'
                quantum=obj['MetaSys'].get('x-rustfs-internal-ec-read-quantum')
                compatible=obj['MetaSys'].get('x-minio-internal-ec-read-quantum')
                assert quantum==compatible, 'inconsistent read-quantum keys'
                quantum=int(quantum) if quantum is not None else None
                b=obj['EcBSize']; shard=b//obj['EcM']
                selected=shard//2 if b<=262144 else (shard if b<=1048576 else 1048576)
                expected_quantum=selected if run['mode']=='enabled' and selected < shard else None
                assert quantum==expected_quantum, 'persisted quantum differs from qualified geometry'
                parts=list(path.parent.glob('*/part.*'))
                shards.append({'disk':disk,'erasure_index':obj['EcIndex'],
                               'block_bytes':b,'data_shards':obj['EcM'],'parity_shards':obj['EcN'],
                               'read_quantum_bytes':quantum,'xl_meta_sha256':value['xl_meta_sha256'],
                               'physical_part_bytes':sum(p.stat().st_size for p in parts)})
            assert {s['erasure_index'] for s in shards}=={1,2,3,4}
            results.append({'mode':run['mode'],'requested_bytes':row['requested_bytes'],
                            'effective_bytes':row['effective_bytes'],'shards':shards,
                            'layout_changed':row['effective_bytes']!=1048576,
                            'smaller_read_frames':shards[0]['read_quantum_bytes'] is not None})
    evidence={'passed':True,'objects':results,'source':report['source'],
              'scope':'decoded XL2 geometry and read-quantum on all four simulated slots; API restart content verified separately'}
    (a.root/'persisted-layout.json').write_text(json.dumps(evidence,indent=2)+'\n')
    report['persisted_layout_verified']=True
    (a.root/'result.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps({'passed':True,'objects':len(results),
                      'enabled':[{k:r[k] for k in ('requested_bytes','effective_bytes','layout_changed','smaller_read_frames')}
                                 for r in results if r['mode']=='enabled']}))
