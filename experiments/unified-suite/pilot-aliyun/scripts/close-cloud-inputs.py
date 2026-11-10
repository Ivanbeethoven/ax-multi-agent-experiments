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
    p.add_argument('--trace',type=Path,required=True)
    p.add_argument('--expected-c4-tree-sha256',required=True)
    p.add_argument('--out',type=Path,required=True)
    a=p.parse_args()
    def ref(path):
        with path.open('rb') as f:sha=hashlib.file_digest(f,'sha256').hexdigest()
        return dict(path=str(path),sha256=sha)
    for unit in ('ax-pilot-inputs-01','ax-pilot-trace-exact-02'):
        pid=int(subprocess.check_output(['systemctl','show',unit,'--property=MainPID','--value'],text=True))
        if pid:raise ValueError('Preparation is still active; do not measure')
    c3=json.loads((a.root/'receipts/c3-verify/result.json').read_text())
    if not c3['passed'] or c3['files']!=1000000 or c3['bytes_checked']!=4096000000:
        raise ValueError('C3 source verification failed')
    c4=json.loads((a.root/'receipts/c4-fixture.json').read_text())
    tree=ref(a.root/'receipts/c4-fixture-tree.json')
    if c4['files']!=1000000 or c4['bytes']!=4096000000 or tree['sha256']!=a.expected_c4_tree_sha256:
        raise ValueError('C4 source differs from the locally verified reference')
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
