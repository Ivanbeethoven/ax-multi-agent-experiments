#!/usr/bin/env python3
"""Observe one reviewed preparation/diagnostic command on all three pilot roles.

Keeps raw 5-second samples and independently verified role archives. This is not
a formal performance cell or a substitute for paired observer calibration.
"""
import argparse
from datetime import datetime,timezone
import json
from pathlib import Path
import re
import shlex
import subprocess
import sys

REPO=Path(__file__).resolve().parents[4]
sys.path.insert(0,str(REPO))
from bench.runtime.common import digest,load,pinned,write_new
from bench.runtime import fleet
from bench import experiment_step,archive_check

ROOTS=dict(client='/srv/ax-client',metadata='/srv/ax-metadata',objects='/srv/ax-objects-0')


def ref(path):return dict(path=str(path),sha256=digest(path))


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--request',type=Path,required=True)
    p.add_argument('--sha256',required=True)
    p.add_argument('--recover-closure',action='store_true',help='Close and archive an already finished command; never execute its workload again')
    a=p.parse_args();request=dict(path=str(a.request),sha256=a.sha256);s=load(pinned(request))
    if s.get('reviewed') is not True or s.get('purpose') not in ('preparation','diagnostic'):
        raise ValueError('Reviewed preparation or diagnostic command required')
    tag=s['tag']
    if not re.fullmatch(r'[a-z0-9-]{3,48}',tag):raise ValueError('Short unique observation tag required')
    step=s['step'];experiment_step.prepare(step['path'],step['sha256'])
    pinned(s['metric_contract'])
    control=Path(s['control']);out=Path(s['out'])
    if a.recover_closure:
        if load(out/'started.json')['request']!=request:raise ValueError('Original request differs')
        finished=load(out/'command/finished.json')
        if not finished.get('completed') or finished.get('spec_sha256')!=step['sha256']:
            raise ValueError('Original workload must have a complete pinned exit record')
        logroot=out/'closure-recovery-01';logroot.mkdir(exist_ok=False)
        write_new(logroot/'started.json',dict(at=datetime.now(timezone.utc).isoformat(),request=request,workload_reexecuted=False))
    else:
        out.mkdir(parents=True,exist_ok=False);logroot=out
        write_new(out/'started.json',dict(request=request,at=datetime.now(timezone.utc).isoformat(),automatic_retry=False))
    observers=[]
    def remote(role,argv):
        base=logroot/'commands';base.mkdir(exist_ok=True);directory=base/f'{len(list(base.iterdir())):04}';directory.mkdir()
        write_new(directory/'intent.json',dict(role=role,argv=list(map(str,argv)),automatic_retry=False))
        with (directory/'stdout').open('xb') as stdout,(directory/'stderr').open('xb') as stderr:
            code=subprocess.run(['ssh','-F',str(control/'ssh.conf'),'pool-a-'+role,shlex.join(list(map(str,argv)))],
                                stdout=stdout,stderr=stderr).returncode
        write_new(directory/'exit.json',dict(exit_code=code))
        if code:raise RuntimeError('Inspect '+str(directory))
        return (directory/'stdout').read_text()
    def mark(label):
        for o in observers:fleet.call(o['peer'],'observe-mark',o['directory'],out/'marks'/label/o['role'],label=label)
    for role,root in ([] if a.recover_closure else ROOTS.items()):
        tool=root+'/code-01/experiments/unified-suite/pilot-aliyun/scripts/'+s['observer_tool']['name']
        if remote(role,['sha256sum',tool]).split()[0]!=s['observer_tool']['sha256']:raise ValueError('Observer builder changed')
        args=['python3',tool,'--role',role,'--product',s['product'],
              '--metric-contract-sha256',s['metric_contract']['sha256'],'--collector-name',s['collector_name'],
              '--out',root+'/q/'+tag+'-observer']
        if role=='client':
            if s.get('entity'):args+=['--entity']
            args+=['--mount-directory',s['mount_directories'][0]]
            for directory in s['mount_directories'][1:]:args+=['--extra-mount-directory',directory]
        spec=json.loads(remote(role,args));peer=ref(control/'probes'/('pool-a-'+role)/'peer.json')
        directory=root+'/o/'+tag
        fleet.call(peer,'observe-start',directory,out/'observer-start'/role,spec)
        observers.append(dict(role=role,peer=peer,directory=directory))
    if a.recover_closure:
        observers=load(out/'observers.json');code=finished['exit_code']
    else:
        write_new(out/'observers.json',observers)
        mark('command-start')
        code=experiment_step.execute(step['path'],step['sha256'],out/'command')
        mark('command-end')
    # Close every sampler before packing any archive. An archive error must not
    # leave the other roles sampling indefinitely.
    for o in observers:
        stop=out/'observer-stop'/o['role']
        if a.recover_closure and stop.exists():
            rows=[json.loads(line) for line in (stop/'protocol.jsonl').read_text().splitlines()]
            if not rows or rows[-1].get('type')!='result' or rows[-1].get('passed') is not True:
                raise ValueError('Inspect incomplete original observer stop: '+str(stop))
        else:fleet.call(o['peer'],'observe-stop',o['directory'],stop)
    archives=[];audits=[]
    for o in observers:
        role=o['role'];root=ROOTS[role]
        # Only explicit observer/spec trees enter this archive. Command output
        # and workload-specific evidence stay separate and retain their own pins.
        source="""import pathlib,sys,json
root=pathlib.Path(ROOT);sys.path.insert(0,str(root/'code-01'))
from bench.runtime.observe import audit
from bench.archive_check import pack
from bench.runtime.common import write_new
directory=root/'o'/TAG
checked=audit(directory,30,['command-start','command-end'])
audit_file=directory/'retained-audit.json'
if audit_file.exists():
 if json.loads(audit_file.read_text())!=checked:raise ValueError('Retained audit changed')
else:write_new(audit_file,checked)
if not checked['passed']:raise ValueError('Raw observer audit failed')
files=[]
for folder in [root/'o'/TAG,root/'q'/(TAG+'-observer')]:
 for p in sorted(folder.rglob('*')):
  if p.is_symlink():raise ValueError('Unexpected observer evidence link')
  if p.is_file():files.append(p.relative_to(root).as_posix())
# The archive must be outside the evidence root, as required by pack().
output=root.parent/'ax-observer-archives'/(TAG+'-'+ROLE+'.tar.gz');output.parent.mkdir(exist_ok=True)
archive=pack(root,files,output,TAG,'diagnostic-observer-'+ROLE)
print(json.dumps(dict(audit=checked,archive=archive)))
"""
        variables=dict(ROOT=root,TAG=tag,ROLE=role)
        response=json.loads(remote(role,['python3','-c','\n'.join(k+'='+repr(v) for k,v in variables.items())+'\n'+source]))
        audits.append(dict(role=role,**response['audit']))
        target=out/'archives'/(role+'.tar.gz');target.parent.mkdir(exist_ok=True)
        subprocess.run(['scp','-F',str(control/'ssh.conf'),'pool-a-'+role+':'+response['archive']['archive'],str(target)],check=True)
        checked=archive_check.verify(target,response['archive']['sha256'])
        write_new(out/'archives'/(role+'-check.json'),checked);archives.append(ref(target))
    write_new(logroot/'result.json',dict(passed=code==0,command_exit_code=code,archives=archives,audits=audits,
              closure_recovered=a.recover_closure,workload_reexecuted=False,
              performance_accepted=False,purpose=s['purpose'],automatic_retry=False,
              scope='One observed command and complete raw observer archives; workload-specific data proofs and formal calibration are separate'))
    return 0 if code==0 else 1


if __name__=='__main__':raise SystemExit(main())
