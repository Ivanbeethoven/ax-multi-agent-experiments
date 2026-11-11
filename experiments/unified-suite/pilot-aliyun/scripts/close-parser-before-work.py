#!/usr/bin/env python3
"""Close only the audited C3 stage-argument rejection before any filesystem work."""
import argparse
from datetime import datetime,timezone
import json
from pathlib import Path
import runpy
import shlex
import shutil
import sqlite3
import subprocess
import sys

REPO=Path(__file__).resolve().parents[4]
sys.path.insert(0,str(REPO))
from bench.runtime.common import digest,load,pinned,write_new
from bench.runtime import pilot_context,fleet
from bench.runtime.pilot_pools import alias,fleet_node,resource_guard
from bench import archive_check,experiment_step

def ref(path):return dict(path=str(Path(path).resolve(strict=True)),sha256=digest(path))


def audit_commands(directory,observers):
    paths=sorted(directory.iterdir());expected=[f'{i:04}' for i in range(len(paths))]
    if not paths or [p.name for p in paths]!=expected:raise ValueError('Contiguous original command ledger required')
    generated=[];rows=[]
    for path in paths:
        intent=load(path/'intent.json');argv=intent['argv'];exit=load(path/'exit.json')['exit_code']
        if exit!=0:raise ValueError('Unexpected remote failure; inspect instead of inferring no work')
        if len(argv)==2 and argv[0]=='sha256sum' and Path(argv[1]).is_absolute():pass
        elif len(argv)>2 and argv[0]=='python3' and Path(argv[1]).name=='make-observer.py':
            role=intent['role'];wanted=next(o for o in observers if o['id'].endswith('-'+role))
            if argv[argv.index('--role')+1]!=role or argv[argv.index('--out')+1]!=str(Path(wanted['directory']).parent.parent/'q'/(Path(wanted['directory']).name+'-observer')):
                raise ValueError('Observer builder output differs')
            generated.append(role)
        else:raise ValueError('A command may have created a mount or filesystem workload')
        rows.append(dict(command=path.name,role=intent['role'],argv=argv,exit_code=exit))
    if generated!=['client','metadata','objects']:raise ValueError('Exactly the original three observer builders required')
    return rows


def parser_failure(stages,directory):
    actual={row['name']:row['status'] for row in stages}
    if actual!={'observe-start':'complete','create-w01':'failed'}:raise ValueError('Only original observe/start plus failed first create is supported')
    path=directory/'stages/create-w01';end=load(path/'finished.json');error=(path/'stderr.log').read_text()
    if end.get('exit_code')!=2 or end.get('completed') is not True or end.get('passed') is not False or "argument --stage: invalid choice: 'create-w01'" not in error:
        raise ValueError('Actual original argparse rejection required')
    process=load(path/'process.json')
    if any(Path('/proc',str(process[key])).exists() for key in ('pid','wrapper_pid')):raise ValueError('Original parser or wrapper PID remains unresolved')
    for name in ('cohort','sample.json','closure.json','archives.json'):
        if (directory/name).exists():raise ValueError('Formal work or closure may already have started')
    return end


REMOTE=r'''import json,pathlib,runpy,sys,shutil
s=CONFIG;sys.path.insert(0,s['repo'])
from bench.runtime.common import alive,digest,load,mounts,write_new
from bench.runtime.observe import audit
from bench.archive_check import pack
root=pathlib.Path(s['root']);tag=s['tag'];observer=root/'o'/tag
identity=load(observer/'ready.json')['process']
if alive(identity):raise ValueError('Original observer still live')
checked=audit(observer,30,s['marks']);absent=[]
for prefix in ('q','m','w','j'):
 for suffix in ('','-p','-v') if prefix=='m' else ('',):
  path=root/prefix/(tag+suffix)
  if path.exists():raise ValueError('Possible original filesystem work path exists: '+str(path))
  absent.append(str(path))
if s['role']=='client':
 if pathlib.Path('/sys/fs/cgroup/ax-slot-1/cgroup.procs').read_text().strip():raise ValueError('Agent slot occupied')
 if mounts(s['mount_point']):raise ValueError('Asset mount remains')
result=dict(passed=True,cell_id=s['cell_id'],role=s['role'],all_exited=True,all_unmounted=True,observer_process=identity,
 raw_audit=checked,absent_work_paths=absent,formal_mount_started=False,formal_worker_started=False,performance_started=False)
out=root/'parser-failure-retained'/s['bundle'];out.mkdir(parents=True,exist_ok=False)
write_new(out/'audit.json',result)
retainer=runpy.run_path(s['repo']+'/experiments/unified-suite/pilot-aliyun/scripts/retain-calibrations.py')
directories=['o/'+tag,'q/'+tag+'-observer'];files,skipped=retainer['file_list'](root,directories)
before=retainer['stable_snapshot'](root,files)
archive=retainer['pack_node_files'](root,files,out,s['cell_id'],'failed-parser-'+s['role'])
retainer['assert_unchanged'](root,directories,files,skipped,before)
result.update(archive=archive,skipped=skipped);write_new(out/'result.json',result);print(json.dumps(result))
'''


def execute(args):
    original=load(pinned(dict(path=str(args.request),sha256=args.sha256)));control=Path(original['control']);plan=load(control/'suite/plan.json')
    cell=next(c for c in plan['cells'] if c['id']==original['cell_id']);runtime=pilot_context.select(control,original.get('runtime_control'));inv=load(runtime/'fleet.json')
    base=runpy.run_path(str(pinned(original['tools']['run'])));context=control/'cells'/cell['id']/'context.json';c=base['Cell'](context)
    out=args.out
    if out.parent!=control/'failed-before-work' or not out.is_absolute():raise ValueError('Explicit new failure evidence directory required')
    with resource_guard(control,cell,plan):
        with sqlite3.connect('file:'+str(control/'state.sqlite')+'?mode=ro',uri=True) as db:
            db.row_factory=sqlite3.Row;stages=[dict(row) for row in db.execute('SELECT * FROM stages WHERE cell_id=?',(cell['id'],))]
            leases=[dict(row) for row in db.execute('SELECT * FROM cell_leases WHERE cell_id=?',(cell['id'],))]
        parser_failure(stages,c.out)
        if len(leases)!=1 or leases[0]['token']!=load(c.out/'lease.json')['token']:raise ValueError('Exact original lease required')
        observers=load(c.out/'observers.json');commands=audit_commands(c.out/'commands',observers)
        raw=subprocess.check_output(['systemctl','show',args.unit,'-p','MainPID','-p','ActiveState','-p','InvocationID','-p','ExecMainStatus'],text=True)
        unit=dict(line.split('=',1) for line in raw.splitlines())
        if unit['MainPID']!='0' or unit['ActiveState']!='failed' or unit['ExecMainStatus']!='1':raise ValueError('Original failed continuation must have stopped')
        out.mkdir(parents=True,exist_ok=False);write_new(out/'started.json',dict(at=datetime.now(timezone.utc).isoformat(),request=ref(args.request),unit=unit,source=ref(__file__)))
        write_new(out/'command-audit.json',dict(passed=True,cell_id=c.id,commands=commands,stages=stages,performance_started=False))
        (out/'executed-source.py').write_bytes(Path(__file__).read_bytes())
        with (out/'original-unit-journal.jsonl').open('xb') as journal:
            subprocess.run(['journalctl','-u',args.unit,'--no-pager','-o','json'],stdout=journal,check=True)
        archives=[];audits=[]
        for observer in observers:
            role=observer['id'].removeprefix(c.pool+'-');peer=ref(runtime/'probes'/observer['id']/'peer.json')
            fleet.call(peer,'observe-stop',observer['directory'],out/'observer-stop'/role)
            root={'client':'/srv/ax-client','metadata':'/srv/ax-metadata','objects':'/srv/ax-objects-0'}[role]
            cfg=dict(repo=fleet_node(inv,c.pool,role)['repo'],root=root,tag=c.tag,role=role,cell_id=c.id,marks=c.cell['observation_marks'],
                mount_point=load(Path(original['qualification'])/'assets'/c.arm/'asset.json')['mount_point'],bundle=out.name)
            # No measurement marks should exist: no cohort was released.
            cfg['marks']=[]
            folder=out/'node-audit'/role;folder.mkdir(parents=True)
            argv=['ssh','-F',str(runtime/'ssh.conf'),observer['id'],'python3 -'];write_new(folder/'intent.json',dict(argv=argv,config=cfg))
            with (folder/'stdout').open('xb') as stdout,(folder/'stderr').open('xb') as stderr:
                result=subprocess.run(argv,input=REMOTE.replace('CONFIG',repr(cfg)).encode(),stdout=stdout,stderr=stderr)
            write_new(folder/'exit.json',dict(exit_code=result.returncode));result.check_returncode();value=json.loads((folder/'stdout').read_text());audits.append(value)
            source=value['archive'];target=out/'archives'/(role+'.tar.gz');target.parent.mkdir(exist_ok=True)
            subprocess.run(['scp','-F',str(runtime/'ssh.conf'),observer['id']+':'+source['archive'],str(target)],check=True)
            archive_check.verify(target,source['sha256']);archives.append(ref(target))
        retainer=runpy.run_path(str(Path(original['tools']['run']['path']).with_name('retain-calibrations.py')))
        directories=['cells/'+c.id,'one-cell-runs/'+c.id];files,skipped=retainer['file_list'](control,directories)
        before=retainer['stable_snapshot'](control,files);experiment_step.seal(control,files,out/'original-controller-seal.json')
        for name in files:
            target=out/'raw'/name;target.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(control/name,target)
        retainer['assert_unchanged'](control,directories,files,skipped,before)
        write_new(out/'closure.json',dict(passed=all(a['passed'] for a in audits),cell_id=c.id,all_exited=True,all_unmounted=True,performance_started=False,
            pending_upload_bytes=0,pending_upload_operations=0,debt_native_observation_applicable=False,basis='never-started-formal-work',native_debt_samples=[],
            interpretation='Structural zero: exact parser exit and complete command/absent-path audit prove no formal mount or writer existed. No native zero gauge is claimed.',node_audits=audits,
            original_six_calibrations_preserved=[ref(Path(t['out'])/'finished.json') for t in original['trials']]))
        files=sorted(path.relative_to(out).as_posix() for path in out.rglob('*') if path.is_file());seal=out.parent/(out.name+'.retention.json');experiment_step.seal(out,files,seal)
        package=archive_check.pack(out,files,out.parent/(out.name+'.tar.gz'),c.id,'failed-parser-before-formal-work');archives.append(ref(package['archive']))
        closure=ref(out/'closure.json');closing=dict(schema='suite-cell-close-v1',plan_sha256=plan['plan_sha256'],cell_id=c.id,outcome='failed',performance_started=False,
            failure_reason='Frozen controller CLI rejected create-w01 before dispatch. Only three external observers started; all were normally stopped. No filesystem work or valid performance sample exists.',
            closure={key:dict(evidence=closure,equals=value) for key,value in dict(processes=dict(all_exited=True),mounts=dict(all_unmounted=True),debt=dict(pending_upload_bytes=0,pending_upload_operations=0,debt_native_observation_applicable=False,basis='never-started-formal-work')).items()},
            retention=dict(root=str(out),manifest=ref(seal)),archives=archives)
        path=out.parent/(out.name+'.close.json');write_new(path,closing);print(json.dumps(dict(retained=True,released=False,closing=ref(path))))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--request',type=Path,required=True);p.add_argument('--sha256',required=True)
    p.add_argument('--unit',required=True);p.add_argument('--out',type=Path,required=True);execute(p.parse_args())
