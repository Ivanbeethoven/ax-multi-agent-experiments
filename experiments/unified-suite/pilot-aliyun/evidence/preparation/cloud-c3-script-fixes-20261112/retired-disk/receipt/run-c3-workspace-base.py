#!/usr/bin/env python3
"""Reserve pool A and run one bounded current-engine base check; never starts ECS."""
import argparse
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
import hashlib
import json
from pathlib import Path
import re
import shlex
import subprocess
import sys
import time


CONTROL = Path('/srv/ax-pilot-control')
CANONICAL = CONTROL/'parallel-control-01'
sys.path.insert(0,str(CONTROL/'code-12'))
from bench.runtime.pilot_pools import active_conflicts, resource_guard

IMAGE = 'sha256:942893356344d292d9fcccadfceb039467c589ba10b4045cb148c0a65d2d3fec'
REVISION = 'f4ed202508f2153afb7956bae6f7799b60eccd00'
FLAGS = ['-F',str(CANONICAL/'ssh.conf'),'-oBatchMode=yes','-oConnectTimeout=5']


def run(args):
    out = Path(__file__).resolve().parent
    if out.parent != CONTROL/'preparation' or not out.name.startswith('c3-current-base-'):
        raise ValueError('Dedicated coordinator preparation directory required')
    tag = out.name
    if args.database not in (0,*range(20,70),*range(71,256)):
        raise ValueError('Use a dedicated database 20..255 except 70, or 0 for locked empty selection')
    if not 0 <= args.workers <= 32 or not 60 <= args.verify_seconds <= 900:
        raise ValueError('Bounded verifier workers and duration required')
    if args.packed_output_mib and (not args.trace_packed_budget or args.workers != 32):
        raise ValueError('The 128 MiB probe requires budget tracing and 32 workers')
    if args.diagnostic_cohort and (args.parallel_sha256 or args.workers != 32
            or args.verify_seconds not in (120,240) or not args.trace_packed_budget
            or (args.packed_output_mib,args.packed_control_mib) != (128,8)):
        raise ValueError('Cohort requires a 120- or 240-second diagnostic window, both raised budgets, and no separate verifier')
    if args.packed_control_mib and (not (args.parallel_sha256 or args.diagnostic_cohort)
            or args.packed_output_mib != 128):
        raise ValueError('The 8 MiB control probe requires the diagnostic verifier and 128 MiB output')
    worker = out/'qualify-c3-workspace-base.py'
    script = worker.read_bytes()
    if hashlib.sha256(script).hexdigest()!=args.worker_sha256:
        raise ValueError('Staged worker script digest mismatch')
    verifier = None
    if args.parallel_sha256:
        p = out/'smallfiles-verify'
        if (not args.workers or not re.fullmatch(r'[a-f0-9]{64}',args.parallel_sha256)
                or p.is_symlink() or not p.is_file() or not 4 <= p.stat().st_size <= 8*1024**2):
            raise ValueError('Expected a bounded regular parallel verifier')
        verifier = p.read_bytes()
        if not verifier.startswith(b'\x7fELF') or hashlib.sha256(verifier).hexdigest()!=args.parallel_sha256:
            raise ValueError('Staged Linux verifier binary digest differs')
    plan = json.loads((CANONICAL/'suite/plan.json').read_text())
    cell = next(c for c in plan['cells'] if c['pool']=='pool-a' and c['workload']=='smallfiles')
    controls = sorted(p.parent for p in CONTROL.glob('*/state.sqlite'))
    if len(controls)<5 or CANONICAL not in controls:
        raise ValueError('Existing coordinator control set missing')

    def ssh(role, command, **kw):
        return subprocess.run(['ssh',*FLAGS,'pool-a-'+role,command],check=True,**kw)

    def setup(role):
        deadline = time.monotonic()+130
        while time.monotonic()<deadline:
            try:
                ssh(role,'true',stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=8)
                break
            except (subprocess.CalledProcessError,subprocess.TimeoutExpired):
                time.sleep(3)
        else:
            raise RuntimeError(role+' did not become reachable')
        with (out/(role+'-restore.log')).open('xb') as stream:
            restore = f'python3 /srv/ax-receipts/a-pool-up/a-pool-up.py {role} --out /srv/ax-receipts/a-pool-up/{role}-{tag}'
            ssh(role,'timeout --kill-after=8s 105s '+restore,stdout=stream,stderr=subprocess.STDOUT,timeout=120)

    with ExitStack() as stack:
        roots=set()
        for ctl in controls:
            locks=(ctl/'runner-locks').resolve(strict=True)
            if locks not in roots:
                stack.enter_context(resource_guard(ctl,cell,plan)); roots.add(locks)
        if controls!=sorted(p.parent for p in CONTROL.glob('*/state.sqlite')) or any(active_conflicts(c,cell,plan) for c in controls):
            raise ValueError('Pool A or a shared physical resource has an active lease')
        with (out/'locks-held.json').open('x') as stream:
            json.dump(dict(pool='pool-a',controls=list(map(str,controls)),locks=sorted(map(str,roots))),stream)
        with ThreadPoolExecutor(max_workers=3) as executor:
            list(executor.map(setup,('objects','metadata','client')))
        image = ssh('objects', "docker inspect -f '{{.Image}} {{.State.Running}}' ax-pilot-20261111-objects-01-rustfs-1; "
            "docker image inspect -f '{{index .Config.Labels \"org.opencontainers.image.revision\"}}' "+IMAGE,
            capture_output=True,text=True,timeout=12).stdout.splitlines()
        if image!=[IMAGE+' true',REVISION]:
            raise ValueError('Actual running RustFS image/revision differs')
        (out/'rustfs-image.txt').write_text('\n'.join(image)+'\n')
        slot_spec = CANONICAL/'hardware-inputs-01/data/pool-a-client-spec.json'
        slot_bytes = slot_spec.read_bytes()
        if hashlib.sha256(slot_bytes).hexdigest()!='3592411238e1af9eb21980c78d61394c24ea33f74d6452babd335a497d0b5d55':
            raise ValueError('Exact original slot spec differs')
        remote = '/srv/ax-receipts/'+tag
        ssh('client','mkdir -m 700 '+remote,timeout=12)
        def upload(name, data):
            ssh('client','set -C; cat > '+remote+'/'+name,input=data,timeout=12)
        upload(worker.name,script)
        upload('slot.json',slot_bytes)
        if verifier:
            upload('smallfiles-verify',verifier)
            ssh('client','chmod 700 '+remote+'/smallfiles-verify',timeout=8)
        ssh('client','test -d /sys/fs/cgroup/ax-slot-1 || python3 /srv/ax-client/code-12/experiments/unified-suite/'
            f'pilot-aliyun/scripts/configure-node.py slot --spec {remote}/slot.json --out {remote}/slot-setup',timeout=15)
        cmd = ['timeout','--kill-after=25s',str(args.verify_seconds+105)+'s','python3','-B',remote+'/'+worker.name,
               '--tag',tag,'--database',str(args.database),'--workers',str(args.workers),
               '--verify-seconds',str(args.verify_seconds)]
        if verifier:
            cmd.extend(('--parallel-sha256',args.parallel_sha256))
        if args.trace_packed_budget:
            cmd.append('--trace-packed-budget')
        if args.packed_output_mib:
            cmd.extend(('--packed-output-mib',str(args.packed_output_mib)))
        if args.packed_control_mib:
            cmd.extend(('--packed-control-mib',str(args.packed_control_mib)))
        if args.diagnostic_cohort:
            cmd.append('--diagnostic-cohort')
        try:
            with (out/'worker.log').open('xb') as stream:
                ssh('client',shlex.join(cmd),stdout=stream,stderr=subprocess.STDOUT,timeout=args.verify_seconds+140)
        finally:
            names = ('intent.json','init.stdout','init.stderr','ready.json','mount.log','mount-exit.json','verify.log','verify-aborted.json',
                     'verify/result.json','result.json')
            if args.diagnostic_cohort:
                names += ('cohort-events.ndjson','cohort-result.json')
            for name in names:
                source = '/srv/ax-client/preparation/'+tag+'/'+name
                proc = subprocess.run(['ssh',*FLAGS,'pool-a-client','test -f '+source+' && cat '+source],
                                      capture_output=True,timeout=10)
                if proc.returncode==0:
                    with (out/('worker-'+name.replace('/','-'))).open('xb') as stream: stream.write(proc.stdout)


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--worker-sha256',required=True)
    p.add_argument('--database',type=int,required=True)
    p.add_argument('--workers',type=int,default=0)
    p.add_argument('--verify-seconds',type=int,default=480)
    p.add_argument('--parallel-sha256')
    p.add_argument('--trace-packed-budget',action='store_true')
    p.add_argument('--packed-output-mib',type=int,choices=(0,128),default=0)
    p.add_argument('--packed-control-mib',type=int,choices=(0,8),default=0)
    p.add_argument('--diagnostic-cohort',action='store_true')
    run(p.parse_args())
