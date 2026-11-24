"""Retry one failed GraftFS cohort after its coordinator and mounts exit."""
import argparse
import json
from pathlib import Path
import shutil
import subprocess
import sys
import time

import cluster_lib as cl
from run_suite import save


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--prior', required=True, type=Path)
    parser.add_argument('--profile', required=True, type=Path)
    args = parser.parse_args()
    root = cl.RUN_ROOT
    prior = args.prior.resolve(strict=True)
    assert prior.is_relative_to((root / '04-agent-replay').resolve()) and prior.name == 'attempt-01'
    job = json.loads((prior / 'job.json').read_text())
    job_id = job['id']
    agents = job['parameters']['agents']
    assert job['parameters']['system'] == 'graftfs-native'
    profile = json.loads(args.profile.read_text())
    new = profile['graftfs_truncate_flush_seconds'][str(agents)]
    old = json.loads((prior / 'replay-placement.json').read_text())
    old_limit = old['graftfs_truncate_flush_seconds']
    if isinstance(old_limit, dict):
        old_limit = old_limit[str(agents)]
    assert old_limit < new <= 300
    field = 'graftfs_truncate_flush_seconds'
    assert {k:v for k,v in old.items() if k != field} == {k:v for k,v in profile.items() if k != field}
    unit = f'ax-{cl.RUN_ID}.service'
    receipt = root / 'continuations' / f'{job_id}-truncate-retry.json'
    def note(state, **extra):
        save(receipt, dict(state=state, time=cl.utc_iso(), job=job_id, prior=str(prior),
                           timeout_seconds=new, **extra))
    def read_row():
        return json.loads((root / 'status.json').read_text())['jobs'][job_id]
    try:
        note('waiting for the failed attempt to end')
        while True:
            row = read_row()
            if row['output'] != str(prior) or row['state'] == 'passed':
                note('already handled')
                return
            state = subprocess.run(['systemctl', 'is-active', unit], capture_output=True, text=True).stdout.strip()
            if row['state'] == 'failed' and state in ('inactive', 'failed', 'unknown'):
                break
            time.sleep(20)
        tag = job_id + '-' + prior.name + '-'
        report = {}
        for host in cl.ALL_HOST_NAMES:
            probe = """import json,sys
from pathlib import Path
root,host,tag=sys.argv[1:]
mounts=[s.split()[4] for s in Path('/proc/self/mountinfo').read_text().splitlines() if '/mounts/'+tag in s.split()[4]]
errors={}
for d in (Path(root)/'hosts'/host/'c2-tasks').glob(tag+'*'):
 for p in d.glob('a*-replay.json'):
  x=json.loads(p.read_text())
  if not x.get('completed'):
   errors[p.name]=[x.get('event',{}).get('op'),x.get('seq'),x.get('actual')]
print(json.dumps([mounts,errors]))"""
            proc = cl.run_on_host(host, ['python3','-c',probe,str(root),host,tag], log_cmd=False)
            mounts, errors = json.loads(proc.stdout)
            if mounts:
                raise RuntimeError(f'prior mounts remain on {host}')
            report[host] = errors
        errors = [v for group in report.values() for v in group.values()]
        if not errors or not all(op == 'truncate' and 'code: 110,' in str(actual) for op, _, actual in errors):
            raise RuntimeError('failure differs from truncate timeout')
        if shutil.disk_usage(root).free < 8 * 2**30:
            raise RuntimeError('coordinator has less than 8 GiB free')
        path = root / 'replay-placement.json'
        if json.loads(path.read_text()) != old:
            raise RuntimeError('placement changed since the prior attempt')
        save(path, profile)
        subprocess.run(['systemd-run', '--unit='+unit, '--collect', '-p','MemoryMax=8G',
                        '-p','MemorySwapMax=0','-p','LimitNOFILE=1048576','-p',
                        'StandardOutput=append:'+str(root/'logs/suite.log'),'-p','StandardError=inherit',
                        sys.executable,str(cl.SCRIPTS_DIR/'run_suite.py'),'--group','04-agent-replay'],check=True)
        note('started', failures=report)
    except Exception as exc:
        note('requires inspection', error=str(exc))
        raise


if __name__ == '__main__':
    main()
