"""Reassemble split GitHub bundle and replay full captured mount workload."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
from replay_fs_trace_bundle import restore
from full_trace_common import tree,save


def main():
    p=argparse.ArgumentParser();p.add_argument('--bundle',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    p.add_argument('--binary',type=Path,required=True)
    p.add_argument('--simulate-llm',type=Path,help='explicit multi-agent think plan; default has no synthetic waits')
    a=p.parse_args()
    bundle=a.bundle.resolve();out=a.output.resolve();out.mkdir(parents=True,exist_ok=False)
    manifest=json.loads((bundle/'bundle.json').read_text())
    archive=out/'archive.tar.gz';digest=hashlib.sha256()
    with archive.open('xb') as target:
        for part in manifest['parts']:
            source=(bundle/part['path']).resolve()
            if not source.is_relative_to(bundle):raise ValueError('Unsafe bundle path')
            data=source.read_bytes()
            if hashlib.sha256(data).hexdigest()!=part['sha256']:raise ValueError('Damaged bundle part')
            target.write(data);digest.update(data)
    if digest.hexdigest()!=manifest['archive_sha256']:raise ValueError('Archive hash mismatch')
    restore(archive,out/'input')
    data=out/'input';root=out/'replayed'
    shutil.copytree(data/'initial',root,symlinks=True)
    if tree(root)!=json.loads((data/'initial-tree.json').read_text()):raise ValueError('Initial tree mismatch')
    command=[str(a.binary.resolve()),str(data/'capture/events.jsonl'),str(data/'capture/blobs'),str(root),str(out/'replay.json')]
    if a.simulate_llm: command += ['--simulate-llm',str(a.simulate_llm.resolve())]
    subprocess.run(command,check=True)
    expected=json.loads((data/'final-tree.json').read_text())
    if tree(root)!=expected:raise ValueError('Final tree mismatch')
    report=json.loads((out/'replay.json').read_text());metrics=json.loads((data/'metrics.json').read_text())
    for key in ('actual_read_bytes','actual_write_bytes'):
        if report[key]!=metrics[key]:raise ValueError('Byte count mismatch')
    if report['operations']!=metrics['callbacks']:raise ValueError('Operation count mismatch')
    save(out/'verification.json',{'valid':True,'initial_tree_equal':True,'final_tree_equal':True,
         'actual_read_bytes':report['actual_read_bytes'],'actual_write_bytes':report['actual_write_bytes'],'callbacks':report['operations']})
    print((out/'verification.json').read_text())


if __name__=='__main__':main()
