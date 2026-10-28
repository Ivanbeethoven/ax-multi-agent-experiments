#!/usr/bin/env python3
"""Freeze clean source trees and a matching image; no fallback or image build."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import subprocess

REPOSITORY='https://github.com/Ivanbeethoven/rustfs'
BRANCH='feat/dynamic-block-size-rebased'


def command(args):
    return subprocess.check_output(args,text=True).strip()


def clean_commit(path):
    if command(['git','-C',str(path),'status','--porcelain']):
        raise ValueError('source tree is dirty; freeze a committed tree first')
    sha=command(['git','-C',str(path),'rev-parse','HEAD'])
    if not re.fullmatch('[0-9a-f]{40}',sha): raise ValueError('full SHA required')
    return sha


def main():
    p=argparse.ArgumentParser(description=__doc__)
    for name in ['rustfs-source','brewfs-source','brewfs-binary','image','output']: p.add_argument('--'+name,required=True)
    a=p.parse_args(); out=Path(a.output)
    if out.exists(): raise ValueError('never overwrite a batch pin')
    pin={'repository':REPOSITORY,'branch':BRANCH,'resolved_at':datetime.now(timezone.utc).isoformat(),
         'resolved_by':'freeze_backend.py'}
    try:
        sha=command(['git','ls-remote',REPOSITORY,'refs/heads/'+BRANCH]).split()[0]
        pin['frozen_commit']=sha
        if clean_commit(a.rustfs_source)!=sha: raise ValueError('RustFS checkout does not match current branch')
        pin['built_from_commit']=sha
        pin['brewfs_client_sha']=clean_commit(a.brewfs_source)
        h=hashlib.sha256()
        with Path(a.brewfs_binary).open('rb') as f:
            for block in iter(lambda:f.read(1048576),b''): h.update(block)
        pin['brewfs_binary_sha256']=h.hexdigest()
        image=json.loads(command(['docker','image','inspect',a.image]))[0]
        if image.get('Config',{}).get('Labels',{}).get('org.opencontainers.image.revision')!=sha:
            raise ValueError('image source label differs from frozen source')
        digests=image.get('RepoDigests',[])
        if not digests: raise ValueError('image needs a registry digest, not a mutable local tag')
        if '@sha256:' in a.image and a.image not in digests: raise ValueError('requested image digest is absent')
        pin['image_reference']=a.image if '@sha256:' in a.image else digests[0]
        pin['image_digest']=pin['image_reference'].split('@',1)[1]
        pin['image_id']=image['Id']
        pin['runtime_verified']=False
        pin['build_provenance']='clean checkout and OCI revision checked; retain build logs/attestation separately'
    except Exception as e:
        pin['blocked_reason']=str(e)
    out.parent.mkdir(parents=True,exist_ok=True)
    with out.open('x') as f: json.dump(pin,f,indent=2)
    return 1 if pin.get('blocked_reason') else 0


if __name__=='__main__': raise SystemExit(main())
