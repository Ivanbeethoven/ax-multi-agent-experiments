#!/usr/bin/env python3
"""Restore the original 2-CPU C4 slot budget on an owned, idle executor."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import socket
import subprocess
import time
from urllib.request import ProxyHandler, Request, build_opener

ROOT = Path('/sys/fs/cgroup')
SLOT = ROOT / 'ax-slot-1'
EXPECTED_CPU = '200000 100000'
UNTUNED_CPU = 'max 100000'
OTHER = {'memory.max':'8589934592', 'memory.swap.max':'0', 'pids.max':'4096',
         'cgroup.procs':''}
SPEC_SHA = {
    'a':'3592411238e1af9eb21980c78d61394c24ea33f74d6452babd335a497d0b5d55',
    'b':'ab0ce1c04ce725d1fb54a3b0e65ddea4c7c2c7dc7a3892f0278ddeaf758afa03',
}


def cpu_action(values):
    cpu = values.get('cpu.max')
    if values != {**OTHER, 'cpu.max':cpu} or cpu not in (EXPECTED_CPU, UNTUNED_CPU):
        raise ValueError('Refuse to modify a busy or differently configured executor')
    return cpu == UNTUNED_CPU


def snapshot():
    return {name:(SLOT/name).read_text().strip() for name in ('cpu.max', *OTHER)}


def record(path, value):
    with path.open('x', encoding='utf-8') as stream:
        json.dump(value, stream, indent=2)
        stream.write('\n')


def capture(argv):
    return subprocess.check_output(argv, text=True, timeout=12).strip()


def instance_id():
    opener = build_opener(ProxyHandler({}))
    base = 'http://100.100.100.200/latest/'
    with opener.open(Request(base+'api/token', method='PUT',
        headers={'X-aliyun-ecs-metadata-token-ttl-seconds':'60'}), timeout=3) as response:
        token = response.read().decode()
    with opener.open(Request(base+'meta-data/instance-id',
        headers={'X-aliyun-ecs-metadata-token':token}), timeout=3) as response:
        return response.read().decode().strip()


def anchor_state(unit):
    data = dict(line.split('=',1) for line in capture(['systemctl','show',unit,
        '-p','ActiveState','-p','ControlGroup','-p','InvocationID','-p','MainPID']).splitlines())
    expected_path = '/system.slice/'+unit
    if (data.get('ControlGroup')!=expected_path or data.get('ActiveState')!='active'
        or not data.get('InvocationID') or not data.get('MainPID','0').isdigit() or int(data['MainPID'])<2
        or (ROOT/expected_path.lstrip('/')/'cpu.max').read_text().strip()!='100000 100000'
        or 'cpu' not in (ROOT/'cgroup.subtree_control').read_text().split()):
        raise ValueError('Systemd did not retain the cpu controller for the exact temporary unit')
    return data


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--pool', choices=tuple(SPEC_SHA), required=True)
    p.add_argument('--attempt', required=True)
    p.add_argument('--spec', type=Path, required=True)
    p.add_argument('--out', type=Path, required=True)
    a = p.parse_args()
    if (os.geteuid()!=0 or not re.fullmatch(r'c4-reopen-a-b-[0-9]{6}',a.attempt)
        or not a.out.is_absolute() or a.out.resolve()!=a.out
        or not a.out.parent.is_dir() or a.out.exists() or a.out.is_symlink()
        or SLOT.resolve()!=SLOT or not SLOT.is_dir()
        or not os.path.ismount('/srv/ax-client')):
        raise ValueError('An original booted executor, mounted data disk and new output are required')
    raw = a.spec.read_bytes()
    sha = hashlib.sha256(raw).hexdigest()
    spec = json.loads(raw)
    actual = instance_id()
    if (sha!=SPEC_SHA[a.pool] or spec['role']!='client'
        or spec['instance_id']!=actual or spec['hostname']!=socket.gethostname()):
        raise ValueError('Original deployment spec must bind this actual executor')
    a.out.mkdir(mode=0o700)
    unit = 'ax-'+a.attempt+'-cpu-anchor.service'
    if capture(['systemctl','show',unit,'-p','LoadState','--value'])!='not-found':
        raise ValueError('A CPU controller anchor for this attempt already exists')
    before = snapshot()
    record(a.out/'before.json', dict(at=datetime.now(timezone.utc).isoformat(), pool=a.pool,
        instance_id=actual, boot_id=Path('/proc/sys/kernel/random/boot_id').read_text().strip(),
        spec_sha256=sha, slot=before, subtree=(ROOT/'cgroup.subtree_control').read_text().split()))
    cpu_action(before)
    # The only process in this systemd service is sleep. Its own CPUQuota keeps
    # the cpu controller enabled for ancestors; it doesn't cap system.slice.
    capture(['systemd-run','--unit='+unit,'--slice=system.slice','--property=Type=exec',
        '--property=Restart=no','--property=CPUQuota=100%','--property=CPUQuotaPeriodSec=100ms',
        '--property=RuntimeMaxSec=780','/usr/bin/sleep','760'])
    unit_before = anchor_state(unit)
    ready = snapshot()
    action = cpu_action(ready)
    if action:
        # Recheck emptiness immediately before the only mutation of the slot.
        if (SLOT/'cgroup.procs').read_text().strip():
            raise ValueError('Executor populated before budget restoration')
        (SLOT/'cpu.max').write_text(EXPECTED_CPU+'\n')
    samples = []
    for delay in (0,1,3,5):
        time.sleep(delay)
        after = snapshot()
        samples.append(dict(at=datetime.now(timezone.utc).isoformat(), slot=after, anchor=anchor_state(unit)))
        if after != {**OTHER,'cpu.max':EXPECTED_CPU}:
            record(a.out/'failed.json',dict(passed=False,reason='Exact empty slot quota did not persist',samples=samples))
            raise ValueError('Exact empty 2-CPU slot did not persist')
    result = dict(passed=True, cpu_max_written=action, unit=unit,
                  unit_before=unit_before, samples=samples, spec_sha256=sha)
    record(a.out/'result.json',result)
    print(json.dumps(result))


if __name__=='__main__':
    main()
