#!/usr/bin/env python3
"""Pack one already closed cell for independent download; no workload or cleanup."""
import argparse
import json
from pathlib import Path
import shutil
import sys

REPO=Path(__file__).resolve().parents[4]
sys.path.insert(0,str(REPO))
from bench import suite_state as state,archive_check,experiment_step
from bench.runtime.common import digest,load,write_new


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--control',type=Path,required=True)
    p.add_argument('--cell',required=True)
    p.add_argument('--out',type=Path,required=True)
    a=p.parse_args()
    with state.transaction(a.control/'state.sqlite') as db:
        row=db.execute('SELECT status,result FROM cells WHERE id=?',(a.cell,)).fetchone()
        if not row or row['status'] not in ('complete','failed'):raise ValueError('Cell is not closed')
        closing_ref=json.loads(row['result'])
    closing=state.evidence(closing_ref);retention=closing['retention']
    experiment_step.verify(retention['root'],retention['manifest']['path'],retention['manifest']['sha256'])
    root=Path(retention['root']);files=sorted(load(retention['manifest']['path'])['files'])
    actual=sorted(p.relative_to(root).as_posix() for p in root.rglob('*') if p.is_file())
    if files!=actual:raise ValueError('Closed evidence tree gained or lost files')
    a.out.mkdir(parents=True,exist_ok=False)
    for name,source in [('closing.json',closing_ref['path']),('retention.json',retention['manifest']['path']),
                        ('plan.json',a.control/'suite/plan.json')]:
        shutil.copyfile(source,a.out/name)
    checked=archive_check.pack(root,files,a.out/'cell.tar.gz',a.cell,'controller-closed-cell')
    write_new(a.out/'archive-check.json',checked)
    write_new(a.out/'metadata.json',dict(cell_id=a.cell,status=row['status'],files={
        p.name:dict(bytes=p.stat().st_size,sha256=digest(p)) for p in sorted(a.out.iterdir()) if p.is_file()}))
    print(json.dumps(checked))


if __name__=='__main__':main()
