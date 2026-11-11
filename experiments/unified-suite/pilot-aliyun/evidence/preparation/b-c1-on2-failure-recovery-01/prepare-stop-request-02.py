#!/usr/bin/env python3
"""Bind the recorded B C1 On2 failure and C2 closure to the stop guardian.

Run on the coordinator. Only creates a fresh preparation directory. The
optional refresh reads original worker probe commands and never reruns cleanup.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess

P = Path('/srv/ax-pilot-control')
CTL = P/'parallel-control-01'
REC = P/'preparation/b-c1-on2-failure-recovery-01'
WATCH = P/'preparation/b-c2-to-c1-watch-01/run'
RUNTIME = P/'fork-control-01/runtime-15-01'
PACKAGE_RELATIVE = 'experiments/unified-suite/pilot-aliyun'
C1 = '8e3d340ff23c3ece-r1-juicefs'
C2 = '05357b5089cd679c-r1-juicefs'
UNITS = {
    'c1': ('ax-pilot-b-c1-mixed-after-c2-01.service','007fcbaf78044d93b04d924e885491df',1),
    'c2': ('ax-pilot-b-c2-jfs-runtime15-01.service','4ca2d264b11d49eda6e0a9925516326a',0),
    'watcher': ('ax-pilot-b-c2-to-c1-watch-01.service','ba4a66d0cd80409ab372aecf858081c5',0),
}


def require(ok, message):
    if not ok:
        raise ValueError(message)


def pin(path, expected=None):
    require(path.is_file() and path.resolve()==path, 'Original source missing: '+str(path))
    with path.open('rb') as f:
        sha=hashlib.file_digest(f,'sha256').hexdigest()
    require(expected is None or sha==expected, 'Original source SHA differs: '+str(path))
    return dict(path=str(path),sha256=sha)


def read(path): return json.loads(path.read_bytes())


def write(path,value):
    with path.open('x',encoding='utf-8') as f:
        json.dump(value,f,indent=2)
        f.write('\n')
    return pin(path)


def manager(unit):
    fields=('LoadState','ActiveState','SubState','MainPID','ControlPID','InvocationID','ExecMainStatus')
    result=subprocess.check_output(['systemctl','show',unit,*['-p'+key for key in fields]],text=True)
    return dict(line.split('=',1) for line in result.splitlines())


def terminal(out,label,cell):
    unit,invocation,code=UNITS[label]
    source_pins=[]
    extra={}
    if label=='c2':
        raw=pin(WATCH/'c2-terminal.json','91786ff24406b71496de0b8e9d123ef140026404766f9d157bcb933f5ab1c083')
        source_pins.append(pin(WATCH/'c2-closed.json','be93461607d8a10a699d37d8ee7a23b300297b1b81b43345a70be431d7bf766b'))
    else:
        if label=='c1':
            journal=pin(REC/'original-manager-journal.jsonl','443fc92a50ee3c309531c3a83ac880ac4c887051ce62264397fb469de32b3fe5')
            source_pins.append(journal)
            extra['journal_evidence']=journal
        else:
            destination=out/'watcher-journal.jsonl'
            with destination.open('xb') as f:
                subprocess.run(['journalctl','--no-pager','-o','json','UNIT='+unit,'INVOCATION_ID='+invocation,
                                '_SYSTEMD_UNIT=init.scope'],stdout=f,check=True)
            journal=pin(destination)
            source_pins.extend([journal,pin(WATCH/'result.json','149d6b226a2e02a74022fe2eabf2017544691da2ad0e200035612af7412c1fa4')])
        rows=[json.loads(line) for line in Path(journal['path']).read_text().splitlines()]
        good=[r for r in rows if r.get('UNIT')==unit and r.get('INVOCATION_ID')==invocation]
        start,=[r for r in good if r['MESSAGE'].startswith('Started '+unit+' ')]
        end=unit+": Failed with result 'exit-code'." if code else unit+': Deactivated successfully.'
        stop,=[r for r in good if r['MESSAGE']==end]
        require(not code or (stop['UNIT_RESULT']=='exit-code' and any(r.get('EXIT_STATUS')=='1' for r in good)),
                'Failed C1 journal does not prove exit 1')
        status=manager(unit)
        require(status['MainPID']==status['ControlPID']=='0' and status['ActiveState'] in ('inactive','failed'),
                'Original manager not terminal: '+unit)
        raw=write(out/(label+'-terminal-native.json'),dict(systemd=status,start=start,stop=stop))
    return unit,write(out/(label+'-terminal.json'),dict(schema='ax-pilot-terminal-unit-v1',passed=True,
        unit=unit,invocation_id=invocation,main_pid=0,control_pid=0,exit_status=code,terminal_evidence=raw,
        source_pins=source_pins,**({'failed':cell['failed']} if code else {'closing':cell['closing']}),**extra))


def main():
    a=argparse.ArgumentParser(description=__doc__)
    a.add_argument('--out',type=Path,required=True)
    a.add_argument('--runner-sha256',required=True)
    a.add_argument('--code-dir',type=Path,default=P/'stop-code-02')
    a.add_argument('--refresh-probes',action='store_true')
    a.add_argument('--reviewed',action='store_true')
    args=a.parse_args()
    require(args.code_dir.parent==P and args.code_dir.name.startswith('stop-code-') and
            args.code_dir.resolve()==args.code_dir,'Explicit coordinator stop code directory required')
    package=args.code_dir/PACKAGE_RELATIVE
    runner=pin(package/'scripts/stop-idle-pool.py',args.runner_sha256)
    allowlist=pin(package/'evidence/preparation/release-pool-b-freeze-01/result.json',
                  '0a7c6e3751f14f3b4d0a45a30d4352bc0e232fc2e39954fec78535ce770f38cf')
    runtime=pin(RUNTIME/'binding.json','eb08a7fe5824a6c8d887d660c947aa3f61e760a497b810d8ae1200f12b132ad4')
    plan=pin(CTL/'suite/plan.json','8649d8b77163e9c47de5a5df2a8f8b2a633e305ec4f657c7ba8115a3e02a76e7')
    recovery=pin(REC/'result.json','d535b968fba96142258b12bf4ade40912c8518904f5b6ef87ec53f7f7fc96184')
    one=CTL/'one-cell-runs'/C1
    c1=dict(request=pin(CTL/'drafts/pool-b-c1-mixed-after-c2-01/request.json',
                        'ff23c7498b8c8c58fc24252c79203ba17b428fdc0e54d699e2d4598aac063693'),
            failed=pin(one/'failed.json','401b6253a06292fb0a65c9f482d3df6d640e7633584984b5cf7a3dc26cf1dc68'),
            debt_samples=[pin(REC/('04-read-debt-sample-'+str(i))/'stdout',sha) for i,sha in enumerate((
                '2c8d30aa612985cd038b3870fb283fc97273e4dbb6c77d4cf7fcd04ca2938b33',
                '061dec740cdea647af9abf5430d2564adacb3c80a4031fe567381da41a27bfda'))],
            unmount=pin(REC/'05-normal-mount-stop/stdout','20122c704de7505f70465ccb8a4ddc04cbcb90e74b686f3ed96b25bcc154aaff'))
    retained=read(Path(pin(WATCH/'c2-retained.json','856798e5888f7d8ae54f26dc893181cc58f4b950948cf588578daca1c8e1ccac')['path']))
    c2=dict(closing=pin(CTL/'cells'/(C2+'.close.json'),'dae1acf7f157a09f0abcf02598f5954721c8a548a2d789097a10ebb2a66a4bf5'),
            calibration_result=retained['result'],calibration_check=retained['check'])
    roles=read(Path(allowlist['path']))['roles']
    proofs={p['path']:p for p in read(Path(runtime['path']))['proofs']}
    out=args.out
    require(out.is_absolute() and out.resolve()==out and out.parent==P/'preparation','Fresh coordinator preparation path required')
    out.mkdir(mode=0o700)
    terminals=dict(terminal(out,k,c1 if k=='c1' else c2) for k in UNITS)
    role_pins={}
    for role in ('client','metadata','objects'):
        src=REC/('08-postcheck-'+role)
        expected=read(Path(recovery['path']))['postchecks'][role]
        live=pin(src/'stdout',expected['sha256'])
        if args.refresh_probes:
            d=out/('readonly-refresh-'+role); d.mkdir()
            argv=read(src/'intent.json')['argv']
            require(argv[:5]==['/usr/bin/ssh','-n','-F',str(RUNTIME/'ssh.conf'),'pool-b-'+role] and
                    len(argv)==6 and argv[-1].startswith('/usr/bin/python3 -B -c '),'Unexpected original probe command')
            write(d/'intent.json',dict(argv=argv,read_only=True))
            with (d/'stdout').open('xb') as stdout,(d/'stderr').open('xb') as stderr:
                status=subprocess.run(argv,stdin=subprocess.DEVNULL,stdout=stdout,stderr=stderr).returncode
            write(d/'exit.json',dict(exit_code=status)); require(status==0,'Original read-only probe failed')
            live=pin(d/'stdout')
        raw=read(Path(live['path'])); v=raw['result']
        sync=pin(REC/('07-sync-'+role)/'exit.json')
        original=proofs[str(RUNTIME/'probes'/('pool-b-'+role)/'probe.json')]
        require(raw['role']==role and raw['boot_id']==read(Path(original['path']))['boot_id'] and
                v['safe_for_release'] is True and not any(v[k] for k in ('live','unknown_startup_directories','slot','mounts')) and
                read(Path(sync['path']))['exit_code']==0 and
                0<=(datetime.now(timezone.utc)-datetime.fromisoformat(raw['at'])).total_seconds()<=900,
                'Unsafe or expired role probe; refresh with --refresh-probes: '+role)
        role_pins[role]=write(out/(role+'-quiescence.json'),dict(at=raw['at'],role=role,boot_id=raw['boot_id'],
            instance_id=roles[role]['instance']['id'],original_probe=original,passed=True,slot_empty=True,
            all_workload_processes_exited=True,all_mounts_absent=True,pending_upload_bytes=0,pending_upload_operations=0,
            sync_evidence=sync,live_evidence=live,source_pins=[recovery]))
    quiescence=write(out/'quiescence.json',dict(schema='ax-pilot-b-stop-failed-quiescence-v1',passed=True,pool='pool-b',
        at=datetime.now(timezone.utc).isoformat(),failed=c1['failed'],roles=role_pins))
    request=write(out/'request.json',dict(schema='ax-pilot-stop-failed-pool-v1',pool='pool-b',reviewed=args.reviewed,
        runner=runner,allowlist=allowlist,plan=plan,runtime_binding=runtime,cells={C1:c1,C2:c2},terminal_units=terminals,
        quiescence=quiescence,lease_controls=sorted(str(p.parent) for p in P.glob('*/state.sqlite'))))
    print(json.dumps(request))


if __name__=='__main__':
    main()
