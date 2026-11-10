#!/usr/bin/env python3
"""Freeze runner code separately from large binaries; install into a new code-NN."""
import argparse
from datetime import datetime,timezone
import hashlib
import json
from pathlib import Path
import subprocess
import tarfile

REPO=Path(__file__).resolve().parents[4]


def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--out',type=Path,required=True);a=p.parse_args()
    out=a.out.absolute()
    if out.is_relative_to(REPO):raise ValueError('Code artifact belongs outside the repository')
    out.mkdir(parents=True,exist_ok=False)
    files=[]
    for directory in ('bench','infra/rustfs','experiments/unified-suite/pilot-aliyun/scripts'):
        files.extend(f for f in (REPO/directory).rglob('*.py') if '__pycache__' not in f.parts)
    files.extend(REPO/name for name in ('scripts/full_trace_common.py','scenarios/k8s-containers/archives.json',
        'scenarios/k8s-containers/scripts/download_archives.py','scenarios/k8s-containers/scripts/check_archives.py',
        'scenarios/k8s-containers/scripts/restore_parts.py','experiments/retest-2026-10-29/scripts/inspect-layout.py',
        'experiments/unified-suite/pilot-aliyun/scope.json'))
    files.extend((REPO/'experiments/unified-suite/pilot-aliyun/profiles').glob('*.json'))
    if any(f.is_symlink() or not f.is_file() for f in files):raise ValueError('Regular runner inputs required')
    manifest={f.relative_to(REPO).as_posix():sha(f) for f in sorted(set(files))}
    manifest_path=out/'manifest.json';manifest_path.write_text(json.dumps(manifest,indent=2)+'\n')
    archive=out/'runner.tar.gz'
    with tarfile.open(archive,'w:gz') as t:
        for name,wanted in manifest.items():
            f=REPO/name
            if sha(f)!=wanted:raise ValueError('Code changed while freezing: '+name)
            t.add(f,arcname=name,recursive=False)
    with tarfile.open(archive) as t:
        if {m.name for m in t.getmembers()}!=set(manifest):raise ValueError('Code member list differs')
        for m in t:
            if not m.isfile() or hashlib.sha256(t.extractfile(m).read()).hexdigest()!=manifest[m.name]:raise ValueError('Code member differs')
    receipt=dict(passed=True,at=datetime.now(timezone.utc).isoformat(),members=len(manifest),
        archive=dict(path=str(archive),bytes=archive.stat().st_size,sha256=sha(archive)),
        manifest=dict(path=str(manifest_path),sha256=sha(manifest_path)),
        repository_head=subprocess.check_output(['git','-C',str(REPO),'rev-parse','HEAD'],text=True).strip(),
        scope='Per-member source bytes, including local changes; Git HEAD alone is not this code identity. No binary, credential or workload data included.')
    (out/'receipt.json').write_text(json.dumps(receipt,indent=2)+'\n');print(json.dumps(receipt))


if __name__=='__main__':main()
