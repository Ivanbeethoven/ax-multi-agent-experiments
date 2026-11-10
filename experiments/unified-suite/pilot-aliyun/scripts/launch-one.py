#!/usr/bin/env python3
"""Start one explicitly selected cell after independently checking its predecessor.

No selection, retry, recovery, next-cell loop or cloud resource mutation.
"""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import subprocess
import sys

REPO = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(REPO))
from bench.runtime.common import digest, load, pinned, write_new
from bench import archive_check, experiment_step, suite_state


def main():
    p = argparse.ArgumentParser(description=__doc__)
    for name in ('request','request-sha256','previous','previous-sha256','runner','runner-sha256','unit','out'):
        p.add_argument('--'+name, required=True)
    a = p.parse_args()
    request_ref = dict(path=a.request, sha256=a.request_sha256)
    previous_ref = dict(path=a.previous, sha256=a.previous_sha256)
    runner_ref = dict(path=a.runner, sha256=a.runner_sha256)
    request = load(pinned(request_ref)); previous = load(pinned(previous_ref)); pinned(runner_ref)
    if previous.get('passed') is not True or request.get('reviewed') is not True:
        raise ValueError('Completed original predecessor and reviewed explicit request required')
    control = Path(request['control'])
    closing = load(pinned(previous['closing']))
    if (closing['cell_id'] != previous['cell_id'] or closing['outcome'] != 'complete' or
            Path(previous['closing']['path']).parent != control/'cells'):
        raise ValueError('Previous cell is not normally complete in this control directory')
    retention = closing['retention']
    experiment_step.verify(retention['root'], retention['manifest']['path'], retention['manifest']['sha256'])
    for archive in closing['archives']:
        archive_check.verify(archive['path'], archive['sha256'])
    for entry in closing['closure'].values():
        evidence = load(pinned(entry['evidence']))
        if not all(evidence.get(k) is True for k in ('passed','all_exited','all_unmounted')):
            raise ValueError('Previous mount or process remains unresolved')
        for key in ('pending_upload_bytes','pending_upload_operations'):
            if key in evidence and evidence[key] != 0:
                raise ValueError('Previous upload debt remains')
    if request['trials'][0]['collector_name'] != previous['final_collector_name']:
        raise ValueError('Collector lifecycle does not continue from the previous cell')
    with suite_state.transaction(control/'state.sqlite') as db:
        if db.execute('SELECT COUNT(*) FROM cell_leases').fetchone()[0]:
            raise ValueError('Another cell remains reserved')
        row = db.execute('SELECT status FROM cells WHERE id=?',(request['cell_id'],)).fetchone()
        if not row or row[0] != 'pending':
            raise ValueError('Only an unstarted selected cell may launch')
    slot = subprocess.check_output(['ssh','-F',str(control/'ssh.conf'),'pool-a-client',
        'cat /sys/fs/cgroup/ax-slot-1/cgroup.procs'],text=True)
    if slot.strip() or Path(request['out']).exists():
        raise ValueError('Slot or cell output is not fresh')
    if not re.fullmatch('ax-pilot-[a-z0-9-]+', a.unit):
        raise ValueError('Dedicated simple systemd unit required')
    status = subprocess.check_output(['systemctl','show',a.unit+'.service','-p','LoadState','--value'],text=True).strip()
    if status != 'not-found':
        raise ValueError('Unit already exists; inspect original invocation')
    out = Path(a.out); out.mkdir(parents=True,exist_ok=False)
    argv = ['systemd-run','--unit='+a.unit,'--property=Restart=no','--property=Type=exec',
        sys.executable,a.runner,'--request',a.request,'--sha256',a.request_sha256]
    write_new(out/'intent.json',dict(at=datetime.now(timezone.utc).isoformat(),
        request=request_ref, previous=previous_ref, runner=runner_ref, argv=argv, automatic_retry=False))
    with (out/'stdout').open('xb') as stdout, (out/'stderr').open('xb') as stderr:
        code = subprocess.run(argv,stdout=stdout,stderr=stderr).returncode
    write_new(out/'exit.json',dict(exit_code=code,automatic_retry=False,
        scope='Launch return only; inspect original service and cell evidence for completion'))
    if code:
        raise RuntimeError('Launch unresolved; inspect original service without retry')
    print(json.dumps(dict(launched=True,unit=a.unit,cell_id=request['cell_id'],next_cell=False)))


if __name__ == '__main__':
    main()
