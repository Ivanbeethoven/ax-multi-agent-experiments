"""Restore frozen POSIX modes in a new trace copy; keep all checks strict."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys

B = Path('/srv/ax-idcai-20261113')
sys.path.insert(0, str(B/'src/ax'))
from bench.runtime.smoke_tree import file_tree

def main():
    source = B/'input/pytest/pytest-full-02'
    dest = B/'input/pytest-posix/pytest-full-02'
    expected = json.loads((source/'initial-tree.json').read_text())
    original = file_tree(source/'initial')
    if original.keys()!=expected.keys() or any({k:v for k,v in original[p].items() if k!='mode'} !=
              {k:v for k,v in expected[p].items() if k!='mode'} for p in expected):
        raise ValueError('Initial input differs beyond POSIX modes')
    dest.mkdir(mode=0o700, parents=True, exist_ok=False)
    # Copy regular files independently before chmod: never change the archived input.
    subprocess.run(['cp','-a','--',str(source/'initial'),str(dest/'initial')],check=True)
    changes=0
    for name, row in expected.items():
        if original[name]['mode'] == row['mode']:
            continue
        p=dest/'initial'/name
        if p.is_symlink():
            raise ValueError('A symlink mode must not be rewritten')
        p.chmod(row['mode'])
        changes+=1
    if file_tree(dest/'initial') != expected:
        raise ValueError('Restored POSIX tree differs')
    for name in ('capture','metrics.json','initial-tree.json','final-tree.json','hardlink-check.json'):
        (dest/name).symlink_to(source/name, target_is_directory=name=='capture')
    with (dest/'capture/events.jsonl').open('rb') as f:
        sha=hashlib.file_digest(f,'sha256').hexdigest()
    receipt=dict(passed=True,source=str(source),entries=len(expected),modes_restored=changes,events_sha256=sha,
                 initial_tree_sha256=hashlib.sha256((source/'initial-tree.json').read_bytes()).hexdigest())
    (dest/'mode-restoration.json').write_text(json.dumps(receipt,indent=2)+'\n')
    print(json.dumps(receipt))

if __name__=='__main__':
    main()
