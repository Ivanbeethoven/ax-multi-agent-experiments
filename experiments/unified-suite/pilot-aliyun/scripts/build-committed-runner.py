#!/usr/bin/env python3
"""Package committed runner bytes without collecting concurrent worktree edits."""
import argparse
from datetime import datetime,timezone
import hashlib
import io
import json
from pathlib import Path,PurePosixPath
import subprocess
import tarfile


def sha(data):return hashlib.sha256(data).hexdigest()


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--repo',type=Path,required=True)
    p.add_argument('--commit',required=True)
    p.add_argument('--base-manifest',type=Path,required=True)
    p.add_argument('--include',action='append',default=[])
    p.add_argument('--out',type=Path,required=True)
    a=p.parse_args()
    def git(*args):return subprocess.check_output(['git','-C',str(a.repo),*args])
    commit=git('rev-parse','--verify',a.commit+'^{commit}').decode().strip()
    base=json.loads(a.base_manifest.read_text(encoding='utf-8-sig'))
    names=sorted(set(base)|set(a.include))
    if not names:raise ValueError('Explicit runner source set required')
    for name in names:
        path=PurePosixPath(name)
        if path.is_absolute() or '..' in path.parts or not name or name.startswith('-'):
            raise ValueError('Repository-relative source paths required')
    archived=git('archive','--format=tar',commit,'--',*names)
    selected=[];excluded=[]
    with tarfile.open(fileobj=io.BytesIO(archived),mode='r:') as source:
        for member in source:
            if member.isdir():continue
            if not member.name.endswith(('.py','.json')):
                excluded.append(member.name);continue
            if not member.isfile():raise ValueError('Only regular runner source files may be packaged')
            selected.append((member,source.extractfile(member).read()))
    if not set(base).issubset({m.name for m,_ in selected}):raise ValueError('A required previous runner member is absent')
    a.out.mkdir(parents=True,exist_ok=False)
    manifest={};archive=a.out/'runner.tar.gz'
    with tarfile.open(archive,'x:gz') as target:
        for member,data in selected:
            manifest[member.name]=sha(data)
            entry=tarfile.TarInfo(member.name);entry.size=len(data);entry.mode=0o755 if member.mode&0o111 else 0o644
            entry.mtime=0;target.addfile(entry,io.BytesIO(data))
    path=a.out/'manifest.json';path.write_text(json.dumps(manifest,indent=2,sort_keys=True)+'\n',encoding='utf-8')
    receipt=dict(passed=True,at=datetime.now(timezone.utc).isoformat(),members=len(manifest),repository_head=commit,
        archive=dict(path=str(archive),bytes=archive.stat().st_size,sha256=sha(archive.read_bytes())),
        manifest=dict(path=str(path),sha256=sha(path.read_bytes())),
        tool_sha256=sha(Path(__file__).read_bytes()),base_manifest=dict(path=str(a.base_manifest),sha256=sha(a.base_manifest.read_bytes())),
        source_policy='git archive of explicit commit only; concurrent worktree edits and untracked files are excluded',
        excluded_non_code=excluded)
    (a.out/'receipt.json').write_text(json.dumps(receipt,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(receipt))


if __name__=='__main__':main()
