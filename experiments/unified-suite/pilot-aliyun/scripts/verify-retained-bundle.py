#!/usr/bin/env python3
"""Verify downloaded evidence, including each nested node/calibration archive.

Only explicit regular tar members are copied to numbered files in a NEW output
directory. Never extracts archive paths or writes to an experiment filesystem.
"""
import argparse
import json
from pathlib import Path
import shutil
import sys
import tarfile

REPO=Path(__file__).resolve().parents[4]
sys.path.insert(0,str(REPO))
from bench import archive_check
from bench.runtime.common import digest,write_new


def verify_bundle(path,expected_sha,out):
    out=Path(out)
    if not out.is_absolute() or out.exists() or out.is_symlink():
        raise ValueError('New absolute independent verification directory required')
    out.mkdir(parents=True);records=[]
    def visit(path,sha,identity,depth):
        if depth>8:raise ValueError('Unexpected nested archive depth')
        check=archive_check.verify(path,sha)
        records.append(dict(member=identity,depth=depth,check=check))
        with tarfile.open(path,'r:gz') as archive:
            manifest=json.load(archive.extractfile(archive_check.MANIFEST))
            for member in archive:
                if member.isfile() and member.name.endswith('.tar.gz'):
                    target=out/('nested-'+str(len(records))+'-'+str(member.offset)+'.tar.gz')
                    with archive.extractfile(member) as source,target.open('xb') as destination:
                        shutil.copyfileobj(source,destination)
                    wanted=manifest['files'][member.name]['sha256']
                    if digest(target)!=wanted:raise ValueError('Nested archive copy changed')
                    visit(target,wanted,identity+'!/'+member.name,depth+1)
    visit(Path(path),expected_sha,Path(path).name,0)
    result=dict(passed=True,archives=len(records),nested_archives=len(records)-1,
        file_members=sum(x['check']['file_members'] for x in records),checks=records,
        scope='Every outer and nested archive member checked; protocol completeness is checked separately')
    write_new(out/'result.json',result)
    return result


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--archive',type=Path,required=True)
    p.add_argument('--sha256',required=True)
    p.add_argument('--out',type=Path,required=True)
    a=p.parse_args();r=verify_bundle(a.archive,a.sha256,a.out)
    print(json.dumps({k:r[k] for k in ('passed','archives','nested_archives','file_members')}))
