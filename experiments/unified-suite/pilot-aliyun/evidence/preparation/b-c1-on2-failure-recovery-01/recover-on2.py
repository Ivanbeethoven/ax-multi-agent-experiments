from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import shlex
import sqlite3
import subprocess
import sys

P=Path('/srv/ax-pilot-control')
CODE=P/'code-15'
sys.path.insert(0,str(CODE))
from bench.runtime.common import digest,load,pinned,write_new
from bench.runtime.pilot_pools import resource_guard,active_conflicts
from bench.runtime import pilot_context

CONTROL=P/'parallel-control-01'
CID='8e3d340ff23c3ece-r1-juicefs'
ATTEMPT='cal-pool-b-j-mx-mxb01-on2'
UNIT='ax-pilot-b-c1-mixed-after-c2-01.service'
INVOCATION='007fcbaf78044d93b04d924e885491df'
OUT=P/'preparation/b-c1-on2-failure-recovery-01'
ASSET='/srv/ax-client/a/parallel-c1-juicefs-01'
MAIN='/srv/ax-client/m/'+ATTEMPT
REMOTE_OUT='/srv/ax-client/q/'+ATTEMPT+'-failure-recovery-01'
RUN=CONTROL/'one-cell-runs'/CID
TRIAL=CONTROL/'calibration'/ATTEMPT
ORIGINAL=(RUN/'failed.json',RUN/'trial-3/exit.json',RUN/'trial-3/stderr',RUN/'trial-3/intent.json',
          TRIAL/'cohort/result.json',TRIAL/'cohort/application-finished/pool-b-objects/protocol.jsonl')
NODE_SHA='811e4eb2b543de9b9c0f75c0c36bb2aaf5f070486aad5aa84824b79f43dcd858'
NODE='/srv/ax-client/code-15/experiments/unified-suite/pilot-aliyun/scripts/pilot-node.py'


def need(ok, msg):
    if not ok: raise ValueError(msg)


def at(): return datetime.now(timezone.utc).isoformat()
def ref(path): return dict(path=str(path),sha256=digest(path))


def logged(name, argv):
    d=OUT/name; d.mkdir()
    write_new(d/'intent.json',dict(argv=argv,at=at(),automatic_retry=False))
    with (d/'stdout').open('xb') as stdout,(d/'stderr').open('xb') as stderr:
        code=subprocess.run(argv,stdin=subprocess.DEVNULL,stdout=stdout,stderr=stderr).returncode
    write_new(d/'exit.json',dict(exit_code=code,at=at()))
    need(code==0,'Original tool refused; see '+str(d))
    return (d/'stdout').read_text()


def boundary(plan,cell):
    data=subprocess.check_output(['systemctl','list-units','--all','--full','--plain','--no-legend','--no-pager',
                                  'ax-pilot-b-*.service'],text=True)
    allowed={('inactive','dead'),('failed','failed'),('active','exited')}
    for line in data.splitlines():
        unit,_,active,sub,*_=line.split()
        status=dict(x.split('=',1) for x in subprocess.check_output(['systemctl','show',unit,'-pActiveState',
            '-pSubState','-pMainPID','-pControlPID','-pControlGroup'],text=True).splitlines())
        need((active,sub) in allowed and (status['ActiveState'],status['SubState']) in allowed and
             status['MainPID']==status['ControlPID']=='0','Another B manager still active: '+unit)
        if status['ControlGroup']:
            group=Path('/sys/fs/cgroup')/status['ControlGroup'].lstrip('/')
            need(group.name==unit and (not group.exists() or 'populated 0' in (group/'cgroup.events').read_text().splitlines()),
                 'Another B manager cgroup populated: '+unit)
    jobs=subprocess.check_output(['systemctl','list-jobs','--full','--plain','--no-legend','--no-pager'],text=True)
    need(not re.search(r'\bax-pilot-b-[a-z0-9-]+\.service\b',jobs),'Another B manager job pending')
    leases={str(p):active_conflicts(p.parent,cell,plan) for p in sorted(P.glob('*/state.sqlite'))}
    need(str(CONTROL/'state.sqlite') in leases and not any(leases.values()),'B or shared resource leased')
    with sqlite3.connect((CONTROL/'state.sqlite').as_uri()+'?mode=ro',uri=True) as db:
        need(db.execute('SELECT status,result FROM cells WHERE id=?',(CID,)).fetchone()==('pending',None) and
             not db.execute('SELECT 1 FROM stages WHERE cell_id=?',(CID,)).fetchone(), 'Formal C1 already started')
    return dict(systemd=data,jobs=jobs,leases=leases)


def run():
    plan=load(CONTROL/'suite/plan.json'); cell=next(c for c in plan['cells'] if c['id']==CID)
    intent=load(RUN/'trial-3/intent.json')['argv']
    need(intent[intent.index('--attempt')+1]==ATTEMPT and intent[intent.index('--node-tool')+1]==NODE and
         intent[intent.index('--node-tool-sha256')+1]==NODE_SHA,'Wrong original trial/tool')
    binding=dict(path=intent[intent.index('--runtime-control')+1],sha256=intent[intent.index('--runtime-control-sha256')+1])
    runtime=pilot_context.select(CONTROL,binding)
    ssh=lambda role,argv: ['/usr/bin/ssh','-n','-F',str(runtime/'ssh.conf'),'pool-b-'+role,shlex.join(argv)]
    peer_pins={Path(x['path']).parent.name:x for x in load(pinned(binding))['proofs'] if x['path'].endswith('/peer.json')}
    data=load(TRIAL/'cohort/result.json')
    need(load(RUN/'trial-3/exit.json')['exit_code']==1 and data['passed'] is False and
         data['workload_passed'] is True and data['telemetry_live_check_passed'] is False and
         len(data['jobs'])==1 and data['jobs'][0]['exit_code']==0 and not data['jobs'][0]['remaining_process_group'],
         'Exact completed fio/failed observer boundary required')
    failure=json.loads(ORIGINAL[-1].read_text()); need(failure['error_type']=='ConnectionRefusedError','Wrong telemetry failure')
    with resource_guard(CONTROL,cell,plan):
        saved=boundary(plan,cell)
        journal=subprocess.check_output(['journalctl','--no-pager','-o','json','UNIT='+UNIT,
            'INVOCATION_ID='+INVOCATION,'_SYSTEMD_UNIT=init.scope'],text=True)
        need(any(json.loads(line).get('UNIT_RESULT')=='exit-code' for line in journal.splitlines()),'Original manager failure not found')
        original=[ref(p) for p in ORIGINAL]
        OUT.mkdir()
        write_new(OUT/'boundary-before.json',dict(at=at(),unit=UNIT,invocation=INVOCATION,manager=saved,
                  originals=original,runtime_binding=binding,automatic_retry=False))
        (OUT/'original-manager-journal.jsonl').write_text(journal)
        try:
            status='''set -eu
test "$(sha256sum '''+NODE+''' | cut -d ' ' -f1)" = '''+NODE_SHA+'''
if pgrep -x fio >/dev/null; then exit 3; fi
cat '''+MAIN+'''/ready.json
'''
            ready=json.loads(logged('01-client-preflight',ssh('client',['/bin/bash','-c',status])))
            need(ready['passed'] is True and ready['mount']['point']==ASSET+'/mnt' and ready['process']['pid']==70376 and
                 ready['process']['start_ticks']==2883898 and ready['process']['boot_id']=='b4316c9e-f7da-47dc-a443-8f4c87a6cf11',
                 'Exact original still-owned mount required')
            logged('02-native-debt',ssh('client',['python3',NODE,'debt','--asset',ASSET,'--cell',CID,'--out',REMOTE_OUT+'/debt']))
            debt=json.loads(logged('03-read-native-debt',ssh('client',['cat',REMOTE_OUT+'/debt/result.json'])))
            samples=[]
            for i,p in enumerate(debt['samples']):
                path=REMOTE_OUT+'/debt/sample-'+str(i)+'.json'
                need(p['path']==path,'Original two native samples required')
                s=logged('04-read-debt-sample-'+str(i),ssh('client',['cat',path]))
                need(hashlib.sha256(s.encode()).hexdigest()==p['sha256'],'Debt receipt digest differs')
                samples.append(json.loads(s))
            need(debt['passed'] is True and debt['cell_id']==CID and len(samples)==2 and
                 samples[1]['monotonic_ns']-samples[0]['monotonic_ns']>=1_000_000_000 and
                 all(s['passed'] and s['pending_upload_bytes']==s['pending_upload_operations']==0 and
                     s['native_gauges'] and all(v==0 for v in s['native_gauges'].values()) for s in samples),
                 'Two native zero debt samples required before unmount')
            boundary(plan,cell)
            mount=json.loads(logged('05-normal-mount-stop',ssh('client',['python3',NODE,'stop','--asset',ASSET,
                  '--mount-run',MAIN,'--out',REMOTE_OUT+'/native-stop.json'])))
            need(mount['passed'] is True and mount['mount_absent'] is True and mount['exit_code']==0,'Mount did not exit normally')
            observer={}
            for role,root in [('client','/srv/ax-client'),('metadata','/srv/ax-metadata')]:
                peer=peer_pins['pool-b-'+role]; pinned(peer)
                row=json.loads(logged('06-observe-stop-'+role,[sys.executable,'-B','-m','bench.runtime.fleet','call',
                    '--peer',peer['path'],'--sha256',peer['sha256'],'--operation','observe-stop','--state',root+'/o/'+ATTEMPT,
                    '--output',str(OUT/('observer-'+role))]))
                observer[role]=dict(passed=row['passed'],samples=row['samples'])
            for role,root in [('client','/srv/ax-client'),('metadata','/srv/ax-metadata'),('objects','/srv/ax-objects-0')]:
                logged('07-sync-'+role,ssh(role,['/usr/bin/sync']))
            after={}
            for role,root in [('client','/srv/ax-client'),('metadata','/srv/ax-metadata'),('objects','/srv/ax-objects-0')]:
                probe='''from datetime import datetime,timezone
import json,os,socket,subprocess,sys
from pathlib import Path
role=sys.argv[1]
slotpath=Path('/sys/fs/cgroup/ax-slot-1/cgroup.procs')
slot=slotpath.read_text().strip() if slotpath.exists() else ''
live=[]
for line in subprocess.check_output(['ps','-eo','pid=,comm=,args='],text=True).splitlines():
    fields=line.strip().split(None,2)
    if len(fields)==3 and int(fields[0])!=os.getpid() and (fields[1]=='fio' or (fields[1] in ('python3','juicefs') and 'cal-pool-b-j-mx-mxb01-on2' in fields[2])):
        live.append(int(fields[0]))
mounts=[x for x in Path('/proc/self/mountinfo').read_text().splitlines() if x.split()[4]=='/srv/ax-client/a/parallel-c1-juicefs-01/mnt']
result=dict(safe_for_release=not any((slot,live,mounts)),live=live,unknown_startup_directories=[],slot=slot,mounts=mounts)
print(json.dumps(dict(at=datetime.now(timezone.utc).isoformat(),role=role,hostname=socket.gethostname(),boot_id=Path('/proc/sys/kernel/random/boot_id').read_text().strip(),result=result)))'''
                row=json.loads(logged('08-postcheck-'+role,ssh(role,['/usr/bin/python3','-B','-c',probe,role])))
                need(row['result']['safe_for_release'] is True,'Workload still present after cleanup: '+role)
                after[role]=ref(OUT/('08-postcheck-'+role)/'stdout')
            saved_after=boundary(plan,cell)
            need([ref(p) for p in ORIGINAL]==original,'Original failure evidence changed')
            write_new(OUT/'result.json',dict(passed=True,at=at(),cell_id=CID,failed_measurement_preserved=True,
                      measurement_accepted=False,native_debt=debt,mount=mount,observers=observer,objects_observer_not_stopped=True,
                      no_original_marks_added=True,no_restart=True,manager=saved_after,postchecks=after,originals=original))
            print(OUT/'result.json')
        except Exception as e:
            write_new(OUT/'failed.json',dict(passed=False,at=at(),error=str(e),automatic_retry=False))
            raise


run()
