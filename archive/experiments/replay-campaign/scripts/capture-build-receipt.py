#!/usr/bin/env python3
"""Verify the exported source against the frozen Git object and retain build pins."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import tarfile


def sha(path):
    with path.open('rb') as stream:return hashlib.file_digest(stream,'sha256').hexdigest()


def main(a):
    commit=(a.build/'brewfs-source-sha.txt').read_text().strip()
    proc=subprocess.Popen(['git','-C',str(a.repository),'archive',commit],stdout=subprocess.PIPE)
    count=0
    try:
        with tarfile.open(fileobj=proc.stdout,mode='r|') as archive:
            for item in archive:
                file=a.build/'brewfs'/item.name
                if item.isfile():
                    expected=hashlib.file_digest(archive.extractfile(item),'sha256').hexdigest()
                    if sha(file)!=expected:raise ValueError('exported source changed: '+item.name)
                    count+=1
                elif item.issym() and (not file.is_symlink() or str(file.readlink())!=item.linkname):
                    raise ValueError('exported symlink changed: '+item.name)
        if proc.wait()!=0:raise ValueError('frozen Git archive failed')
    finally:
        if proc.poll() is None:proc.terminate();proc.wait()
    binaries={name:sha(a.build/'brewfs-target/release'/name) for name in ('brewfs','packed_v3_snapshot_fixture')}
    recorded=(a.build/'brewfs-binaries.sha256').read_text()
    for name,value in binaries.items():
        if not any(line.split()==[value,str(a.build/'brewfs-target/release'/name)] for line in recorded.splitlines()):
            raise ValueError('binary differs from successful build receipt')
    report={'source_sha':commit,'exported_source_files_verified':count,'source_matches_frozen_git':True,
            'profile':'release','debug_info':0,'incremental':False,'jobs':1,
            'features':(a.build/'features.txt').read_text().strip(),
            'rustc':(a.build/'rustc.txt').read_text().strip(),'cargo':(a.build/'cargo.txt').read_text().strip(),
            'binary_sha256':binaries,'self_reported_source_stamp':'unknown (Git archive build); use verified export/build receipt',
            'performance_measurement':False}
    a.output.write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps({'passed':True,'source_files':count,'binary_sha256':binaries}))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('repository','build','output'):p.add_argument('--'+name,type=Path,required=True)
    main(p.parse_args())
