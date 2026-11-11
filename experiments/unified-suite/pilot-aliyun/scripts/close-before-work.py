#!/usr/bin/env python3
"""Seal a failed observer-directory collision before any formal work starts.

Does not run work, fabricate a sample, alter old paths, or release a reservation.
The separately authorized finish action uses the existing suite_state.finish API
after independent verification of this archive.
"""
import argparse
import base64
from datetime import datetime, timezone
import importlib.util
import json
from pathlib import Path
import shlex
import shutil
import sqlite3
import subprocess
import sys


def audit_commands(directory, tool, observer_directory):
    paths = sorted(directory.iterdir())
    if not paths or [path.name for path in paths]!=[str(i).zfill(4) for i in range(len(paths))]:
        raise ValueError('Complete contiguous original command ledger required')
    result = []
    for index, path in enumerate(paths):
        intent = json.loads((path/'intent.json').read_text()); argv = intent['argv']
        code = json.loads((path/'exit.json').read_text())['exit_code']
        if index<len(paths)-1:
            if len(argv)!=2 or argv[0]!='sha256sum' or not Path(argv[1]).is_absolute() or code!=0:
                raise ValueError('A prior command may have created or started work')
        else:
            error = (path/'stderr').read_text()
            if (intent['role']!='client' or argv[:2]!=['python3', str(tool)] or code!=1 or
                    argv[argv.index('--out')+1]!=observer_directory or
                    "FileExistsError: [Errno 17] File exists: '"+observer_directory+"'" not in error or
                    'a.out.mkdir(parents=True,exist_ok=False)' not in error):
                raise ValueError('Only the original first-client directory collision is supported')
        result.append(dict(command=path.name, role=intent['role'], argv=argv, exit_code=code))
    return result


REMOTE_AUDIT = '''import json,os,sys
from datetime import datetime
from pathlib import Path
s=CONFIG;sys.path.insert(0,s['code'])
from bench.runtime.common import alive,load,mounts,digest
root=Path(s['root']);tag=s['tag'];begin=datetime.fromisoformat(s['formal_started_at']).timestamp()
paths=['q/'+tag+'-observer','o/'+tag]
if s['role']=='client':paths+=['q/'+tag,'m/'+tag,'m/'+tag+'-p','m/'+tag+'-v','w/'+tag,'j/'+tag]
records=[];processes=[];points=[]
for name in paths:
 p=root/name
 record=dict(path=str(p),exists=p.exists())
 if p.exists():
  record.update(mtime=p.lstat().st_mtime,mtime_before_formal=p.lstat().st_mtime<begin)
  if not record['mtime_before_formal']:raise ValueError('Unknown post-start original path: '+str(p))
  for parent,dirs,files in os.walk(p):
   if 'cache' in dirs:dirs.remove('cache')
   for file in files:
    q=Path(parent)/file
    if file not in ('process.json','ready.json'):continue
    value=load(q)
    for key in ('process','supervisor'):
     if key not in value:continue
     identity=value[key]
     if not isinstance(identity,dict) or not {'pid','boot_id','start_ticks'}<=identity.keys():raise ValueError('Unknown original process identity')
     running=alive(identity)
     processes.append(dict(source=str(q),source_sha256=digest(q),identity=identity,alive=running))
     if running:raise ValueError('Original observer/mount/worker still alive')
    if file=='process.json' and 'process' not in value and 'pid' in value:
     if Path('/proc',str(value['pid'])).exists():raise ValueError('Unresolved original recorded PID remains')
     processes.append(dict(source=str(q),source_sha256=digest(q),pid=value['pid'],proc_entry_absent=True))
    if file=='process.json' and not any(key in value for key in ('process','supervisor','pid')):raise ValueError('Unrecognized original process receipt')
    if file=='ready.json' and isinstance(value.get('mount'),dict):
     point=value['mount']['point'];observed=mounts(point);points.append(dict(point=point,mounts=observed))
     if observed:raise ValueError('Original mount point still occupied')
 records.append(record)
slot_empty=True
if s['role']=='client':
 slot_empty=not Path('/sys/fs/cgroup/ax-slot-1/cgroup.procs').read_text().strip()
 if not slot_empty:raise ValueError('Slot occupied')
 asset=load(Path(s['asset'])/'asset.json');point=asset['mount_point'];observed=mounts(point);points.append(dict(point=point,mounts=observed))
 if observed:raise ValueError('Current asset mount remains')
print(json.dumps(dict(passed=True,role=s['role'],pool=s['pool'],cell_id=s['cell_id'],old_paths=records,
 original_process_checks=processes,mount_points=points,all_exited=True,all_unmounted=True,slot_empty=slot_empty,
 formal_observer_or_work_started=False,scope='Only original listed paths/identities; no process search or stop')))
'''


def execute(a):
    sys.path.insert(0, str(a.runtime))
    from bench import archive_check, experiment_step, suite_state
    from bench.runtime.common import alive, digest, load, pinned, write_new
    from bench.runtime import pilot_context
    from bench.runtime.pilot_pools import alias, pool_of, resource_guard
    control = a.control.resolve(strict=True); plan = load(control/'suite/plan.json')
    request_ref = dict(path=str(a.request), sha256=a.request_sha256); request = load(pinned(request_ref))
    cell = next(cell for cell in plan['cells'] if cell['id']==request['cell_id']); pool = pool_of(cell)
    original = control/'cells'/cell['id']; invocation = Path(request['out'])
    runtime = pilot_context.select(control, request.get('runtime_control')); ssh = runtime/'ssh.conf'
    out = a.out
    if out.parent!=control/'failed-before-work' or out.exists() or not out.is_absolute():
        raise ValueError('Fresh explicit failure evidence directory required')
    if (request.get('reviewed') is not True or request['control']!=str(control) or
            invocation!=control/'one-cell-runs'/cell['id'] or load(invocation/'started.json')['request']!=request_ref or
            not (invocation/'failed.json').is_file() or (invocation/'result.json').exists()):
        raise ValueError('Exact failed original one-cell request required')
    retainer_path = a.runtime/'experiments/unified-suite/pilot-aliyun/scripts/retain-calibrations.py'
    spec = importlib.util.spec_from_file_location('closed_calibration_retainer', retainer_path)
    retainer = importlib.util.module_from_spec(spec); spec.loader.exec_module(retainer)
    with resource_guard(control, cell, plan):
        out.mkdir(parents=True, exist_ok=False)
        (out/'executed-source.py').write_bytes(Path(__file__).read_bytes())
        write_new(out/'started.json', dict(request=request_ref, unit=a.unit, invocation_id=a.invocation,
            cell_id=cell['id'], performance_started=False, automatic_retry=False))
        with sqlite3.connect('file:'+str(control/'state.sqlite')+'?mode=ro',uri=True) as db:
            db.row_factory=sqlite3.Row
            rows=[dict(row) for row in db.execute('SELECT * FROM stages WHERE cell_id=?',(cell['id'],))]
            leases=[dict(row) for row in db.execute('SELECT * FROM cell_leases WHERE cell_id=?',(cell['id'],))]
        if len(rows)!=1 or rows[0]['name']!='observe-start' or rows[0]['status']!='failed' or len(leases)!=1:
            raise ValueError('Exactly one failed first stage and its one active lease required')
        lease = load(original/'lease.json')
        if lease['cell']!=cell or lease['token']!=leases[0]['token'] or lease['owner']!=leases[0]['owner']:
            raise ValueError('Original lease identity differs')
        stage = original/'stages/observe-start'; end = load(stage/'finished.json')
        if end.get('completed') is not True or end.get('passed') is not False or end['exit_code']!=1:
            raise ValueError('Original failed first-stage result required')
        for name in ('observers.json','observer-start','cohort','sample.json','closure.json','archives.json'):
            if (original/name).exists(): raise ValueError('Formal work or unknown output may already exist: '+name)
        for pid in set(load(stage/'process.json')[key] for key in ('pid','wrapper_pid')):
            if Path('/proc',str(pid)).exists(): raise ValueError('Original stage PID still exists; inspect identity')
        raw = subprocess.check_output(['systemctl','show',a.unit,'-p','LoadState','-p','ActiveState','-p','SubState','-p','MainPID'],text=True)
        state = dict(line.split('=',1) for line in raw.splitlines())
        if state['MainPID']!='0' or state['ActiveState'] not in ('inactive','failed'):
            raise ValueError('Original one-cell unit still active')
        journal = subprocess.check_output(['journalctl','-u',a.unit,'-o','json','--no-pager'])
        (out/'original-unit-journal.jsonl').write_bytes(journal)
        entries=[json.loads(line) for line in journal.decode().splitlines()]
        relevant=[row for row in entries if row.get('INVOCATION_ID',row.get('_SYSTEMD_INVOCATION_ID'))==a.invocation]
        if not relevant or not any('status=1/FAILURE' in row.get('MESSAGE','') for row in relevant):
            raise ValueError('Pinned original failed unit invocation is missing')
        tag='c'+{'fio':'1','trace':'2','smallfiles':'3'}[cell['workload']]+'-'+cell['id']
        context=load(original/'context.json');fleet=load(runtime/'fleet.json')
        client=next(node for node in fleet['nodes'] if node['id']==alias(pool,'client'))
        commands=audit_commands(original/'commands',Path(client['repo'])/'experiments/unified-suite/pilot-aliyun/scripts/make-observer.py',
                                '/srv/ax-client/q/'+tag+'-observer')
        if len(commands)!=7:raise ValueError('Exact original six build checks then the first generator required')
        for index,expected_sha in enumerate([request['node_tool']['sha256']]+[request['observer_tool']['sha256']]*3):
            if (original/'commands'/str(index).zfill(4)/'stdout').read_text().split()[0]!=expected_sha:
                raise ValueError('Original node/observer helper differed from its reviewed request')
        write_new(out/'coordinator-audit.json',dict(passed=True,cell_id=cell['id'],stages=rows,commands=commands,
            unit=state,unit_invocation_id=a.invocation,formal_observer_started=False,formal_mount_started=False,
            formal_worker_started=False,performance_started=False,complete=False))
        def remote(name, role, argv, source=None):
            directory=out/name;directory.mkdir(parents=True,exist_ok=False)
            command=['ssh','-F',str(ssh),alias(pool,role),shlex.join(list(map(str,argv)))]
            write_new(directory/'intent.json',dict(argv=command,automatic_retry=False))
            with (directory/'stdout').open('xb') as stdout,(directory/'stderr').open('xb') as stderr:
                result=subprocess.run(command,input=source,stdout=stdout,stderr=stderr)
            write_new(directory/'exit.json',dict(exit_code=result.returncode));result.check_returncode()
            return json.loads((directory/'stdout').read_text())
        roles=dict(client='/srv/ax-client',metadata='/srv/ax-metadata',objects='/srv/ax-objects-0')
        for role, root in roles.items():
            peer=next(node for node in fleet['nodes'] if node['id']==alias(pool,role))
            config=dict(code=peer['repo'],root=root,tag=tag,role=role,pool=pool,cell_id=cell['id'],
                asset=context['asset'],formal_started_at=end['started_at'])
            remote('formal-path-audit/'+role,role,['python3','-'],REMOTE_AUDIT.replace('CONFIG',repr(config)).encode())
        trials=request['trials']
        if len(trials)!=6 or [trial['mode'] for trial in trials]!=['off','on','on','off','off','on']:
            raise ValueError('All original six calibration trials required')
        directories=['cells/'+cell['id'],'one-cell-runs/'+cell['id']]
        node_archives=[];calibrations=[]
        for trial in trials:
            tag=trial['attempt'];directory=Path(trial['out'])
            if directory!=control/'calibration'/tag:raise ValueError('Original trial path differs')
            end_=load(directory/'finished.json');cohort=load(directory/'cohort.json')
            if end_.get('passed') is not True or load(pinned(end_['cohort'])).get('passed') is not True:
                raise ValueError('Original calibration did not pass')
            if load(directory/'started.json')['cell_id']!=cell['id']:raise ValueError('Original calibration cell differs')
            observers=retainer.observer_roles(cohort,pool)
            actual_roles=['client'] if trial['mode']=='off' else list(roles)
            for role in actual_roles:
                peer=cohort['workers'][0]['peer'] if role=='client' else observers[role]['peer']
                config=dict(code=peer['repo'],root=roles[role],pool=pool,role=role,tag=tag,mode=trial['mode'],
                    bundle=out.name,cell_id=cell['id'],marks=cell['observation_marks'],code_pins=peer['pins'],
                    worker_spec=cohort['workers'][0]['spec'] if role=='client' else None,
                    observer_sha256=observers[role]['spec']['sha256'] if role in observers else None)
                encoded=base64.b64encode(json.dumps(config).encode()).decode()
                retained=remote('calibrations/'+tag+'/'+role,role,['python3','-','--node-config',encoded],retainer_path.read_bytes())
                archive=retained['archive'];target=out/'calibrations'/tag/(role+'.tar.gz')
                subprocess.run(['scp','-F',str(ssh),alias(pool,role)+':'+archive['archive'],str(target)],check=True)
                checked=archive_check.verify(target,archive['sha256']);write_new(target.with_suffix('.check.json'),checked)
                node_archives.append(dict(path=str(target),sha256=checked['sha256']))
            directories.append('calibration/'+tag)
            calibrations.append(dict(tag=tag,mode=trial['mode'],passed=True,roles=actual_roles))
        files,skipped=retainer.file_list(control,directories);before=retainer.stable_snapshot(control,files)
        experiment_step.seal(control,files,out/'original-source-seal.json')
        for name in files:
            destination=out/'raw'/name;destination.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(control/name,destination)
        retainer.assert_unchanged(control,directories,files,skipped,before)
        experiment_step.verify(control,out/'original-source-seal.json',digest(out/'original-source-seal.json'))
        write_new(out/'closure.json',dict(passed=True,cell_id=cell['id'],all_exited=True,all_unmounted=True,
            complete=False,performance_started=False,pending_upload_bytes=0,pending_upload_operations=0,
            debt_native_observation_applicable=False,basis='never-started-formal-work',native_debt_samples=[],
            debt_interpretation='Structural zero: complete command audit proves this formal attempt created no writer or working mount, therefore it created no upload obligation. No native zero gauge was sampled for this attempt.',
            original_unit=a.unit,invocation_id=a.invocation,calibrations=calibrations,
            calibration_debt_scope='Actual original double zero samples are retained separately inside each client calibration archive.',
            original_source_seal=dict(path=str(out/'original-source-seal.json'),sha256=digest(out/'original-source-seal.json'))))
        all_files=sorted(path.relative_to(out).as_posix() for path in out.rglob('*') if path.is_file())
        seal=out.parent/(out.name+'.retention.json');experiment_step.seal(out,all_files,seal)
        archive=archive_check.pack(out,all_files,out.parent/(out.name+'.tar.gz'),cell['id'],'failed-before-formal-work')
        closure_ref=dict(path=str(out/'closure.json'),sha256=digest(out/'closure.json'))
        closing=dict(schema='suite-cell-close-v1',plan_sha256=plan['plan_sha256'],cell_id=cell['id'],outcome='failed',
            performance_started=False,complete=False,closure={key:dict(evidence=closure_ref,equals=value) for key,value in
                dict(processes=dict(all_exited=True),mounts=dict(all_unmounted=True),debt=dict(
                    pending_upload_bytes=0,pending_upload_operations=0,debt_native_observation_applicable=False,basis='never-started-formal-work')).items()},
            retention=dict(root=str(out),manifest=dict(path=str(seal),sha256=digest(seal))),
            archives=[dict(path=str(out.parent/(out.name+'.tar.gz')),sha256=archive['sha256']),*node_archives],
            failure_reason='First client observer spec directory already existed from an older batch; first stage failed before formal observer, mount or workload creation. No valid performance sample exists.')
        closing_path=out.parent/(out.name+'.close.json');write_new(closing_path,closing)
        print(json.dumps(dict(retained=True,released=False,cell_id=cell['id'],closing=dict(path=str(closing_path),sha256=digest(closing_path)),archive=archive)))


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ('control','runtime','request','out'):parser.add_argument('--'+name,type=Path,required=True)
    for name in ('request-sha256','unit','invocation'):parser.add_argument('--'+name,required=True)
    execute(parser.parse_args())


if __name__=='__main__':main()
