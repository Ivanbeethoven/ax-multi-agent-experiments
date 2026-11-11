#!/usr/bin/env python3
"""Pause scheduled maintenance on an owned pilot worker, without killing an upgrade."""
import argparse
from datetime import datetime, timezone
import json
import importlib.util
from pathlib import Path
import re
import socket
import subprocess


def check_spec(spec, instance_id, hostname):
    campaign=spec.get('campaign','');role=spec.get('role')
    if spec.get('created_for_campaign') is not True or not re.fullmatch(r'ax-pilot-[0-9]{8}(-[a-z0-9]+)?',campaign) or \
            role not in ('client','target','metadata','objects') or spec.get('hostname')!=campaign+'-'+role or \
            spec.get('instance_id')!=instance_id or spec.get('hostname')!=hostname:
        raise ValueError('Owned worker spec must match its actual instance and hostname')


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--out',type=Path,required=True)
    p.add_argument('--spec',type=Path,help='Actual owned worker spec for a new campaign')
    a=p.parse_args()
    if a.spec:
        loader=importlib.util.spec_from_file_location('maintenance_provision',Path(__file__).with_name('provision-node.py'))
        provision=importlib.util.module_from_spec(loader);loader.loader.exec_module(provision)
        check_spec(json.loads(a.spec.read_text()),provision.instance_id(),socket.gethostname())
    elif socket.gethostname() not in ['ax-pilot-20261111-'+r for r in ('client','target','metadata','objects')]:
        raise ValueError('Only newly owned worker hosts may use this script')
    a.out.mkdir(parents=True,exist_ok=False)
    for name in ('apt-daily.service','apt-daily-upgrade.service'):
        state=subprocess.run(['systemctl','is-active',name],text=True,capture_output=True).stdout.strip()
        if state in ('active','activating'):raise ValueError('Upgrade still active; do not interrupt it')
    timers=['apt-daily.timer','apt-daily-upgrade.timer','fstrim.timer','e2scrub_all.timer','fwupd-refresh.timer']
    previous=[]
    for timer in timers:
        row=dict(unit=timer,enabled=subprocess.run(['systemctl','is-enabled',timer],text=True,capture_output=True).stdout.strip(),
            active=subprocess.run(['systemctl','is-active',timer],text=True,capture_output=True).stdout.strip())
        previous.append(row)
    (a.out/'previous-timers.json').write_text(json.dumps(previous,indent=2)+'\n')
    with (a.out/'mask.log').open('xb') as f:
        subprocess.run(['systemctl','mask','--runtime','--now',*timers],check=True,stdout=f,stderr=subprocess.STDOUT)
    (a.out/'units.txt').write_text(subprocess.check_output(['systemctl','list-timers','--all','--no-pager'],text=True))
    (a.out/'packages.txt').write_text(subprocess.check_output(['dpkg-query','-W','-f=${Package} ${Version}\n'],text=True))
    (a.out/'meminfo.txt').write_text(Path('/proc/meminfo').read_text())
    (a.out/'cpuinfo.txt').write_text(Path('/proc/cpuinfo').read_text())
    (a.out/'kernel.txt').write_text(subprocess.check_output(['uname','-a'],text=True))
    (a.out/'result.json').write_text(json.dumps(dict(passed=True,at=datetime.now(timezone.utc).isoformat(),
        hostname=socket.gethostname(),boot_id=Path('/proc/sys/kernel/random/boot_id').read_text().strip(),
        reboot_required=Path('/var/run/reboot-required').exists(),
        policy='Keep the recorded running kernel; no automatic upgrade/reboot during measurement. These new workers will be deleted after verified archival.',
        paused_timers=timers,upgrades_killed=False),indent=2)+'\n')
    print((a.out/'result.json').read_text())


if __name__=='__main__':
    main()
