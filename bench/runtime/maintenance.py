"""Read-only Linux maintenance/environment snapshot before/after a cell."""
import argparse
from datetime import datetime, timezone
import os
from pathlib import Path
import platform
import shutil
import subprocess

from .common import write_new, identity


def snapshot(paths):
    if platform.system()!='Linux':raise ValueError('probe actual Linux execution host')
    result={'schema':'suite-maintenance-snapshot-v1','utc':datetime.now(timezone.utc).isoformat(),
            'boot_id':Path('/proc/sys/kernel/random/boot_id').read_text().strip(),
            'kernel':platform.release(),'python':platform.python_version(),
            'reboot_required':Path('/var/run/reboot-required').exists(),
            'uptime':Path('/proc/uptime').read_text().strip(),'capacity':[],'maintenance_processes':[]}
    for path in paths:
        p=Path(path).resolve(strict=True);space=shutil.disk_usage(p);stat=os.statvfs(p)
        result['capacity'].append({'path':str(p),'device':p.stat().st_dev,'bytes_total':space.total,
            'bytes_available':space.free,'inodes_available':stat.f_favail,'inodes_total':stat.f_files})
    for p in Path('/proc').glob('[0-9]*'):
        try:
            name=(p/'comm').read_text().strip()
            if any(x in name for x in ('unattended','needrestart','apt','dnf','yum','packagekit')):
                result['maintenance_processes'].append({'name':name,'identity':identity(int(p.name))})
        except (OSError,ValueError):continue
    if shutil.which('systemctl'):
        cmd=['systemctl','list-timers','--all','--no-pager','--plain']
        run=subprocess.run(cmd,text=True,capture_output=True,check=False)
        result['timers']={'exit_code':run.returncode,'stdout':run.stdout,'stderr':run.stderr}
    result['scope']='inventory only; operator must resolve upgrade/reboot/service restart conflicts before claiming measurement'
    return result


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--paths',nargs='+',required=True);p.add_argument('--output',required=True)
    a=p.parse_args();write_new(a.output,snapshot(a.paths))
