#!/usr/bin/env python3
"""Read a deterministic bounded sample of persisted application-object layouts.

No S3 writes, object reads, service restart, recursive full-bucket scan or delete.
Run after the corresponding writer has drained and normally exited.
"""
import argparse
from datetime import datetime,timezone
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--container',required=True);p.add_argument('--revision',required=True)
    p.add_argument('--bucket',required=True)
    p.add_argument('--decoder',type=Path,required=True);p.add_argument('--decoder-sha256',required=True)
    p.add_argument('--limit',type=int,default=32);p.add_argument('--out',type=Path,required=True)
    a=p.parse_args()
    if not re.fullmatch(r'axp-[a-f0-9]{16}',a.bucket) or not 1<=a.limit<=256:
        raise ValueError('Owned pilot bucket and bounded sample required')
    if hashlib.sha256(a.decoder.read_bytes()).hexdigest()!=a.decoder_sha256:raise ValueError('Decoder changed')
    runtime=json.loads(subprocess.check_output(['docker','inspect',a.container]))[0]
    if runtime['Id']!=a.container or not runtime['State']['Running'] or runtime['RestartCount']:
        raise ValueError('Original explicit running container required')
    if runtime['Config']['Labels'].get('org.opencontainers.image.revision')!=a.revision:
        raise ValueError('Application layout must match this source batch')
    disks=sorted(Path(m['Source']) for m in runtime['Mounts'] if m['Destination'].startswith('/data/rustfs'))
    if len(disks)!=4 or len({d.stat().st_dev for d in disks})!=4:raise ValueError('Four actual independent disks required')
    spec=importlib.util.spec_from_file_location('owned_xl2_reader',a.decoder)
    decoder=importlib.util.module_from_spec(spec);spec.loader.exec_module(decoder)
    if a.out.exists():raise FileExistsError(a.out)
    a.out.mkdir(parents=True,exist_ok=False);objects=[]
    base=disks[0]/a.bucket
    if not base.is_dir() or base.is_symlink():raise ValueError('Owned bucket missing')
    for directory,children,files in os.walk(base,followlinks=False):
        children.sort()
        if any((Path(directory)/name).is_symlink() for name in children):raise ValueError('Unexpected storage link')
        if 'xl.meta' not in files:continue
        relative=(Path(directory)/'xl.meta').relative_to(base)
        shards=[]
        for disk in disks:
            meta=disk/a.bucket/relative
            if meta.is_symlink():raise ValueError('Unexpected metadata link')
            decoded=decoder.decode(meta)
            versions=[v['V2Obj'] for v in decoded['versions'] if 'V2Obj' in v]
            if len(versions)!=1:raise ValueError('Expected one current object version')
            v=versions[0];system=v['MetaSys']
            q=system.get('x-rustfs-internal-ec-read-quantum')
            if q!=system.get('x-minio-internal-ec-read-quantum'):raise ValueError('Quantum metadata differs')
            parts=sorted(meta.parent.glob('*/part.*'))
            if any(f.is_symlink() for f in parts):raise ValueError('Unexpected data link')
            shards.append(dict(disk=str(disk),device=disk.stat().st_dev,erasure_index=v['EcIndex'],
                block_bytes=v['EcBSize'],data_shards=v['EcM'],parity_shards=v['EcN'],
                read_quantum_bytes=int(q) if q is not None else None,
                logical_bytes=v.get('Size'),inline_shard_lengths=decoded['inline_shard_lengths'],
                physical_part_bytes=sum(f.stat().st_size for f in parts),
                physical_part_allocated_bytes=sum(f.stat().st_blocks*512 for f in parts),
                xl_meta_bytes=decoded['xl_meta_bytes'],xl_meta_sha256=decoded['xl_meta_sha256']))
        geometry={(s['block_bytes'],s['data_shards'],s['parity_shards'],s['read_quantum_bytes']) for s in shards}
        if len(geometry)!=1 or {s['erasure_index'] for s in shards}!={1,2,3,4} or any((s['data_shards'],s['parity_shards'])!=(2,2) for s in shards):
            raise ValueError('Four persisted shard geometries differ')
        objects.append(dict(relative_metadata=relative.as_posix(),shards=shards))
        if len(objects)==a.limit:break
    if not objects:raise ValueError('No persisted objects found')
    value=dict(passed=True,at=datetime.now(timezone.utc).isoformat(),bucket=a.bucket,container_id=runtime['Id'],
        image_id=runtime['Image'],source_commit=a.revision,decoder_sha256=a.decoder_sha256,
        reader_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),sample_limit=a.limit,objects=objects,
        scope='Deterministic sorted depth-first prefix of persisted objects on four actual disks. B/q and inline/part sizes only; no full-bucket distribution, request-hint receipt, remote content readback or performance acceptance.')
    (a.out/'result.json').write_text(json.dumps(value,indent=2)+'\n')
    print(json.dumps(dict(passed=True,bucket=a.bucket,sampled_objects=len(objects))))


if __name__=='__main__':main()
