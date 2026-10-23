"""Scan known credentials, archive complete capture, and split for ordinary Git."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import tarfile


def main():
    p=argparse.ArgumentParser();p.add_argument('--source',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    p.add_argument('--credential-config',type=Path,required=True);a=p.parse_args()
    source=a.source.resolve();out=a.output.resolve();out.mkdir(parents=True,exist_ok=False)
    config=json.loads(a.credential_config.read_text(encoding='utf-8-sig'))
    keys=[v.get('options',{}).get('apiKey','').encode() for v in config.get('provider',{}).values()]
    keys=[k for k in keys if k and not k.startswith(b'{')]
    names=['initial','initial-tree.json','final-tree.json','capture','agent-events.jsonl','agent-stderr.txt',
        'agent-exit.json','selection.json','prediction.patch','grading','baseline/report.json','gold/report.json',
        'preflight','started.json','container-mount-check.txt','daemon.stderr','daemon.stdout','replay.json',
        'verification.json','result.json','metrics.json','provenance.json','tests.txt','hardlink-check.json','session-db-check.json']
    paths=[]
    for name in names:
        item=source/name
        paths.extend(p for p in item.rglob('*') if p.is_file() and not p.is_symlink()) if item.is_dir() else paths.append(item)
    tail_len=max(map(len,keys),default=1)-1
    for path in paths:
        tail=b''
        with path.open('rb') as f:
            while chunk:=f.read(1024*1024):
                data=tail+chunk
                if any(k in data for k in keys):raise RuntimeError('Credential in artifact: '+str(path.relative_to(source)))
                tail=data[-tail_len:] if tail_len else b''
    print(f'Credential scan passed for {len(paths)} files',flush=True)
    archive=source/'publish.tar.gz'
    with tarfile.open(archive,'w:gz',compresslevel=6) as tar:
        for name in names:tar.add(source/name,arcname=name)
    print(f'Archive complete: {archive.stat().st_size} bytes',flush=True)
    parts=[];digest=hashlib.sha256()
    with archive.open('rb') as f:
        while chunk:=f.read(20*1024*1024):
            name=f'capture.tar.gz.part-{len(parts):03d}'
            (out/name).write_bytes(chunk);digest.update(chunk)
            parts.append({'path':name,'bytes':len(chunk),'sha256':hashlib.sha256(chunk).hexdigest()})
    for name in ['result.json','metrics.json','provenance.json','verification.json','session-db-check.json','tests.txt','selection.json']:
        shutil.copy2(source/name,out/name)
    manifest={'format':'ax-full-fuse-capture-bundle','version':1,'capture_format':'ax-fuse-callbacks-v1',
        'archive_sha256':digest.hexdigest(),'archive_bytes':archive.stat().st_size,'parts':parts,
        'credential_scan':{'passed':True,'files_scanned':len(paths),'redactions':0},
        'contents':names,'note':'Complete raw callbacks and write payloads; no sampling. Frozen initial repo and all Agent state included.'}
    (out/'bundle.json').write_text(json.dumps(manifest,indent=2))
    print(json.dumps({'parts':len(parts),'archive_bytes':archive.stat().st_size}),flush=True)


if __name__=='__main__':main()
