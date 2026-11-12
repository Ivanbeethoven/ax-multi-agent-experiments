#!/usr/bin/env python3
"""Package a verified BrewFS build separately from unchanged services and inputs.

Install with install-deployment.py into a new directory. No cloud mutation.
"""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import shutil
import sys

REPO=Path(__file__).resolve().parents[4]
sys.path.insert(0,str(REPO))
from bench import archive_check
from bench.runtime.common import digest,load,pinned,write_new


def main():
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('build-root','latest','reader-result','out'):
        p.add_argument('--'+name,type=Path,required=True)
    a=p.parse_args();manifest=a.build_root/'receipts/build-manifest.json'
    build=load(manifest);latest=load(a.latest)
    if not latest['passed'] or latest['build_manifest_sha256']!=digest(manifest):
        raise ValueError('Matching latest-branch check required')
    for source in build['sources']:
        entry=next(x for x in latest['sources'] if x['component']==source['component'])
        if not entry['matches'] or entry['built_commit']!=source['source_commit']:
            raise ValueError('Branch proof differs from this build')
    attestation=next(x for x in build['build_attestations'] if x['component']=='brewfs')
    finished=load(pinned(attestation))
    if not finished['passed'] or finished['source']['source_commit']!=next(x['source_commit'] for x in build['sources'] if x['component']=='brewfs'):
        raise ValueError('Completed BrewFS attestation differs')
    reader=load(a.reader_result)
    if reader['exit_code']!=0 or '--test' in reader['argv'] or reader['build_attestation_sha256']!=attestation['sha256']:
        raise ValueError('Deployable reader must link this exact completed BrewFS build')
    if not a.out.is_absolute() or a.out.exists():raise ValueError('New absolute package directory required')
    a.out.mkdir(parents=True);stage=a.out/'payload';stage.mkdir();files=[]
    def put(source,relative,expected=None):
        if expected is not None:pinned(dict(path=str(source),sha256=expected))
        if source.is_symlink() or not source.is_file():raise ValueError('Regular source required')
        target=stage/relative;target.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(source,target)
        if expected is not None and digest(target)!=expected:raise ValueError('Package copy changed')
        files.append(relative)
    for name in ('brewfs','packed_snapshot_fixture'):
        entry=next(x for x in build['files'] if x['file']==name)
        put(a.build_root/'bin'/name,'bin/'+name,entry['sha256'])
    reader_path=Path(reader['argv'][reader['argv'].index('-o')+1])
    if reader_path.name!='verify_entity_remote':raise ValueError('Original namespace reader required')
    put(reader_path,'bin/verify_entity_remote',reader['binary_sha256'])
    put(manifest,'provenance/build-manifest.json',digest(manifest))
    put(a.latest,'provenance/latest.json',digest(a.latest))
    put(Path(attestation['path']),'provenance/brewfs-build.json',attestation['sha256'])
    put(a.reader_result,'provenance/verify_entity_remote.json',digest(a.reader_result))
    put(Path(__file__),'provenance/package-brewfs-binaries.py',digest(Path(__file__)))
    result=archive_check.pack(stage,files,a.out/'binaries.tar.gz','brewfs-versioned-binaries','prepared-assets')
    result.update(at=datetime.now(timezone.utc).isoformat(),source_commit=finished['source']['source_commit'],
        scope='New immutable client binaries only; no RustFS image, model, service restart or input change')
    write_new(a.out/'receipt.json',result);print(json.dumps(result))


if __name__=='__main__':main()
