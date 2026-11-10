#!/usr/bin/env python3
"""Measure owned 4 KiB objects' stored bytes/inodes before million-file imports."""
import argparse
from contextlib import closing
from datetime import datetime,timezone
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import uuid

REPO=Path(__file__).resolve().parents[4]


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--endpoint',required=True);p.add_argument('--credentials',type=Path,required=True)
    p.add_argument('--disk',type=Path,action='append',required=True);p.add_argument('--container',required=True)
    p.add_argument('--local-layout-only',action='store_true');p.add_argument('--out',type=Path,required=True)
    a=p.parse_args()
    if len(a.disk)!=4 or len(set(d.resolve() for d in a.disk))!=4:raise ValueError('Four explicit data directories required')
    if not a.local_layout_only and len({d.stat().st_dev for d in a.disk})!=4:raise ValueError('Four actual independent devices required')
    if a.credentials.stat().st_mode&0o077:raise ValueError('Private credentials file required')
    a.out.mkdir(parents=True,exist_ok=False)
    runtime=json.loads(subprocess.check_output(['docker','inspect',a.container],text=True))[0]
    if not runtime['State']['Running'] or runtime['RestartCount']:raise ValueError('Original running container required')
    actual={Path(m['Source']).resolve() for m in runtime['Mounts'] if m['Destination'].startswith('/data/rustfs')}
    if actual!={d.resolve() for d in a.disk}:raise ValueError('Actual container disks differ')
    image=json.loads(subprocess.check_output(['docker','image','inspect',runtime['Image']],text=True))[0]
    revision=image['Config']['Labels']['org.opencontainers.image.revision']
    secret=dict(line.split('=',1) for line in a.credentials.read_text().splitlines())
    import boto3
    from botocore.config import Config
    client=boto3.client('s3',endpoint_url=a.endpoint,region_name='us-east-1',
        aws_access_key_id=secret['RUSTFS_ACCESS_KEY'],aws_secret_access_key=secret['RUSTFS_SECRET_KEY'],
        config=Config(signature_version='s3v4',retries={'max_attempts':0},s3={'addressing_style':'path'}))
    source=REPO/'experiments/retest-2026-10-29/scripts/inspect-layout.py'
    spec=importlib.util.spec_from_file_location('small_object_decoder',source);decoder=importlib.util.module_from_spec(spec);spec.loader.exec_module(decoder)
    bucket='ax-pilot-small-object-'+uuid.uuid4().hex;client.create_bucket(Bucket=bucket);rows=[]
    for hint in (None,65536,1048576):
        for i in range(8):
            key=f'h{hint}-i{i}';data=hashlib.shake_256(key.encode()).digest(4096)
            metadata={} if hint is None else {'rustfs-ec-block-size-hint':str(hint)}
            client.put_object(Bucket=bucket,Key=key,Body=data,Metadata=metadata)
            with closing(client.get_object(Bucket=bucket,Key=key)['Body']) as stream:
                if stream.read()!=data:raise ValueError('Small-object readback differs')
            disks=[]
            for d in a.disk:
                root=d/bucket/key;decoded=decoder.decode(root/'xl.meta');obj=decoded['versions'][0]['V2Obj']
                files=[f for f in root.rglob('*') if f.is_file()];directories=[root,*[f for f in root.rglob('*') if f.is_dir()]]
                disks.append(dict(data_path=str(d),block_bytes=obj['EcBSize'],data_shards=obj['EcM'],parity_shards=obj['EcN'],
                    inline_shard_lengths=decoded['inline_shard_lengths'],regular_file_bytes=sum(f.stat().st_size for f in files),
                    file_allocated_bytes=sum(f.stat().st_blocks*512 for f in files),directory_allocated_bytes=sum(f.stat().st_blocks*512 for f in directories),
                    regular_files=len(files),directories=len(directories),xl_meta_sha256=decoded['xl_meta_sha256']))
            rows.append(dict(key=key,hint=hint,application_bytes=4096,disks=disks))
    result=dict(passed=True,at=datetime.now(timezone.utc).isoformat(),bucket=bucket,objects=rows,
        container_id=runtime['Id'],image_id=runtime['Image'],source_commit=revision,decoder_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
        local_layout_only=a.local_layout_only,performance_accepted=False,
        scope='Owned 4 KiB S3 objects and per-object files/directories only; excludes shared bucket/filesystem journal/Redis overhead. No deletion, throughput or cloud capacity acceptance.')
    (a.out/'result.json').write_text(json.dumps(result,indent=2)+'\n');client.close()
    summary={str(h):dict(per_disk_allocated_bytes=max(d['file_allocated_bytes']+d['directory_allocated_bytes'] for r in rows if r['hint']==h for d in r['disks']),
        per_disk_inodes=max(d['regular_files']+d['directories'] for r in rows if r['hint']==h for d in r['disks'])) for h in (None,65536,1048576)}
    print(json.dumps(dict(passed=True,local_layout_only=a.local_layout_only,objects=len(rows),summary=summary)))


if __name__=='__main__':main()
