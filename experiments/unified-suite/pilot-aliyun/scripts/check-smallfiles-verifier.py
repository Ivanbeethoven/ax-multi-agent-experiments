#!/usr/bin/env python3
"""Cross-check complete C3 readback, including unchanged files and bad states."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess


def ref(path):return dict(path=str(path),sha256=hashlib.sha256(path.read_bytes()).hexdigest())


def main():
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('verifier','smallfiles','out'):p.add_argument('--'+name,type=Path,required=True)
    a=p.parse_args()
    if os.geteuid()!=0:raise ValueError('Root-owned fixture required')
    a.out.mkdir(parents=True,exist_ok=False);rows=[]
    def run(name,args,passed=True):
        out=a.out/name;out.mkdir()
        (out/'command.json').write_text(json.dumps(list(map(str,args)))+'\n')
        with (out/'stdout').open('xb') as stdout,(out/'stderr').open('xb') as stderr:
            code=subprocess.run(list(map(str,args)),stdout=stdout,stderr=stderr).returncode
        row=dict(name=name,exit_code=code,expected_success=passed);rows.append(row)
        (out/'exit.json').write_text(json.dumps(row)+'\n')
        if (code==0)!=passed:raise RuntimeError('Inspect '+str(out))
    tree=a.out/'tree'
    run('prepare',[a.smallfiles,'prepare','--root',tree,'--output',a.out/'prepared','--files','128'])
    def check(name,state='base',passed=True,workspace=7):
        run(name,[a.verifier,tree,state,a.out/(name+'.json'),'128','8',str(workspace)],passed)
        if not passed and (a.out/(name+'.json')).exists():raise ValueError('Failed check wrote success receipt')
    check('base')
    run('mutate',[a.smallfiles,'run','--root',tree,'--output',a.out/'mutated','--files','128',
        '--hot-files','64','--workers','8','--read-ops','128','--cow-files','8','--write-ops','16',
        '--mixed-ops','64','--seed','9173','--workspace','7'])
    state=a.out/'mutated/touched.tsv'
    # The unchanged workload owns the state filename; reject accidental guessing.
    if not state.exists():
        candidates=list((a.out/'mutated').glob('*.tsv'))
        if len(candidates)!=1:raise ValueError('Expected exactly one original touched state')
        state=candidates[0]
    check('changed',state)
    check('wrong-workspace',state,False,8)
    touched={int(row.split('\t')[0]) for row in state.read_text().splitlines()[1:]}
    index=next(i for i in range(128) if i not in touched)
    f=tree/'small'/f'd{index%1024:04}'/f'f{index:07}';original=f.read_bytes()
    f.write_bytes(b'bad'+original[3:]);check('untouched-corrupt',state,False);f.write_bytes(original)
    f.chmod(0o600);check('wrong-mode',state,False);f.chmod(0o644)
    (tree/'extra').write_bytes(b'extra');check('extra-entry',state,False);(tree/'extra').unlink()
    link=a.out/'alias';os.link(f,link);check('hardlink',state,False);link.unlink()
    bad=a.out/'bad.tsv';bad.write_text(state.read_text()+state.read_text().splitlines()[1]+'\n')
    check('duplicate-state',bad,False)
    check('final',state)
    result=dict(passed=True,at=datetime.now(timezone.utc).isoformat(),checks=rows,
        verifier=ref(a.verifier),original_workload=ref(a.smallfiles),helper=ref(Path(__file__)),
        complete_changed_readback=json.loads((a.out/'changed.json').read_text()),
        scope='128-file functional cross-check against the unchanged workload and negative cases; no cloud performance claim')
    (a.out/'result.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result))


if __name__=='__main__':main()
