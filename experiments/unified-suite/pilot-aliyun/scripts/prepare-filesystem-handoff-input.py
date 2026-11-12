#!/usr/bin/env python3
"""Create an explicit smaller filesystem fixture without changing the original."""
import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import shutil
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[4]))
from bench.runtime.smoke_tree import file_tree

p=argparse.ArgumentParser(description=__doc__)
p.add_argument('--checkpoint',type=Path,required=True)
p.add_argument('--out',type=Path,required=True)
p.add_argument('--omit',action='append',default=[])
a=p.parse_args()
source=a.checkpoint.resolve().parent/'workspace'
omitted=set()
for name in a.omit:
    rel=PurePosixPath(name)
    if rel.is_absolute() or not rel.parts or '..' in rel.parts or str(rel)!=name:
        p.error('Omissions must be canonical relative paths')
    path=source.joinpath(*rel.parts)
    if not path.exists() or not path.resolve().is_relative_to(source):
        p.error('Omitted path must exist inside the source workspace')
    omitted.add(name)
a.out.mkdir(mode=0o700)
def ignore(directory,names):
    return [name for name in names if (Path(directory)/name).relative_to(source).as_posix() in omitted]
shutil.copytree(source,a.out/'workspace',symlinks=True,ignore=ignore)
checkpoint=json.loads(a.checkpoint.read_text())
checkpoint['scope']='filesystem-only derived fixture; agent state is opaque data'
(a.out/'checkpoint.json').write_text(json.dumps(checkpoint,indent=2)+'\n')
tree=file_tree(a.out/'workspace')
raw=(json.dumps(tree,sort_keys=True)+'\n').encode()
(a.out/'tree.json').write_bytes(raw)
receipt=dict(scope=checkpoint['scope'],source_checkpoint_sha256=hashlib.sha256(a.checkpoint.read_bytes()).hexdigest(),
    omitted=sorted(omitted),entries=len(tree),files=sum(v['type']=='file' for v in tree.values()),
    bytes=sum(v.get('bytes',0) for v in tree.values()),tree_sha256=hashlib.sha256(raw).hexdigest(),
    original_modified=False,agent_process_started=False)
(a.out/'input.json').write_text(json.dumps(receipt,indent=2)+'\n')
print(json.dumps(receipt))
