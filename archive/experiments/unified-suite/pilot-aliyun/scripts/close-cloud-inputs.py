#!/usr/bin/env python3
"""Check completed source preparations, including a separately retained trace fix."""
import argparse
from datetime import datetime,timezone
import hashlib
import json
from pathlib import Path
import subprocess


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--root',type=Path,required=True)
    p.add_argument('--trace',type=Path)
    p.add_argument('--files',type=int,choices=[100000,1000000],default=1000000)
    p.add_argument('--sources-only',action='store_true')
    p.add_argument('--preparation-unit',action='append',help='Exact owned preparation unit; use repeatedly for multiple units')
    p.add_argument('--expected-c4-tree-sha256',required=True)
    p.add_argument('--out',type=Path,required=True)
    a=p.parse_args()
    if not a.sources_only and not a.trace:p.error('trace required outside sources-only mode')
    if a.sources_only and not a.preparation_unit:p.error('sources-only requires its exact preparation unit')
    def ref(path):
        with path.open('rb') as f:sha=hashlib.file_digest(f,'sha256').hexdigest()
        return dict(path=str(path),sha256=sha)
    for unit in a.preparation_unit or ('ax-pilot-inputs-01','ax-pilot-trace-exact-02'):
        if not unit.startswith('ax-pilot-') or any(x not in 'abcdefghijklmnopqrstuvwxyz0123456789-.' for x in unit):
            raise ValueError('Exact owned pilot preparation unit required')
        pid=int(subprocess.check_output(['systemctl','show',unit,'--property=MainPID','--value'],text=True))
        if pid:raise ValueError('Preparation is still active; do not measure')
    c3=json.loads((a.root/'receipts/c3-verify/result.json').read_text())
    if not c3['passed'] or c3['files']!=a.files or c3['bytes_checked']!=a.files*4096:
        raise ValueError('C3 source verification failed')
    c4=json.loads((a.root/'receipts/c4-fixture.json').read_text())
    tree=ref(a.root/'receipts/c4-fixture-tree.json')
    if c4['files']!=a.files or c4['bytes']!=a.files*4096 or tree['sha256']!=a.expected_c4_tree_sha256:
        raise ValueError('C4 source differs from the locally verified reference')
    if a.sources_only:
        for name in ('c3-prepare','c3-verify','c4-fixture','c4-snapshot'):
            if json.loads((a.root/'receipts'/(name+'-exit.json')).read_text())['exit_code']!=0:
                raise ValueError('Source preparation failed: '+name)
        result=dict(passed=True,at=datetime.now(timezone.utc).isoformat(),files_per_input=a.files,
            bytes_per_input=a.files*4096,c3_check=ref(a.root/'receipts/c3-verify/result.json'),c4_tree=tree,
            preparation_processes_exited=True,sources_only=True,scope='Declared complete C3/C4 sources; C2 unchanged and not rerun')
        with a.out.open('x') as f:json.dump(result,f,indent=2);f.write('\n')
        print(json.dumps(result,indent=2));return
    trace=json.loads((a.trace/'result.json').read_text())
    if not trace['passed'] or trace['callbacks']!=1747109 or trace['actual_read_bytes']!=281774624 or trace['actual_write_bytes']!=194706661:
        raise ValueError('Complete strict trace work differs')
    for name in ('c3-prepare','c3-verify','c4-fixture','c4-snapshot','trace-download'):
        if json.loads((a.root/'receipts'/(name+'-exit.json')).read_text())['exit_code']!=0:
            raise ValueError('Source preparation failed: '+name)
    result=dict(passed=True,at=datetime.now(timezone.utc).isoformat(),
        c3_check=ref(a.root/'receipts/c3-verify/result.json'),c4_tree=tree,trace_check=ref(a.trace/'result.json'),
        trace_case=str(a.trace/'cases/pytest-full-02'),
        prior_trace_failure=ref(a.root/'receipts/strict-trace-exit.json'),
        preparation_processes_exited=True,scope='Final input qualification after a new exact-permission extraction. The first extraction failure remains preserved; no workload measurement has been retried.')
    with a.out.open('x') as f:json.dump(result,f,indent=2);f.write('\n')
    print(json.dumps(result,indent=2))


if __name__=='__main__':main()
