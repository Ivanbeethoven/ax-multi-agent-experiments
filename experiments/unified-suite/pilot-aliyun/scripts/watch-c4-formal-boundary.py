#!/usr/bin/env python3
"""Watch one frozen C4 batch; continue only its reviewed admit-only failure."""
import argparse
import importlib.util
import json
from pathlib import Path
import re
import subprocess
import sys
import time

REPO=Path(__file__).resolve().parents[4];sys.path.insert(0,str(REPO))
from bench.runtime.common import digest,load,pinned,write_new


def ref(path):return dict(path=str(Path(path).resolve(strict=True)),sha256=digest(path))


def show(unit):
    if not re.fullmatch(r'ax-pilot-[a-z0-9-]+',unit):raise ValueError('Exact pilot systemd unit name required')
    result=subprocess.run(['systemctl','show',unit,'-pLoadState','-pActiveState','-pSubState','-pMainPID',
        '-pExecMainStatus','-pInvocationID'],capture_output=True,text=True)
    return dict(line.split('=',1) for line in result.stdout.splitlines())


def wait(unit,invocation,deadline):
    while time.monotonic()<deadline:
        current=show(unit);seen=current.get('InvocationID')
        if seen not in (invocation,'',None):raise ValueError('Original systemd invocation replaced: '+unit)
        if current.get('ActiveState') in ('inactive','failed') and current.get('MainPID','0')=='0':return current
        time.sleep(30)
    raise TimeoutError('One-time watcher expired; original work was not stopped: '+unit)


def module(pin):
    p=pinned(pin);spec=importlib.util.spec_from_file_location(p.stem.replace('-','_'),p)
    value=importlib.util.module_from_spec(spec);spec.loader.exec_module(value);return value


def handoff_only(request,s):
    """Observe an already running continuation; never launch another workload."""
    out=Path(s['out']);continuation=load(pinned(s['continuation']))
    if (s.get('reviewed') is not True or not 1800<=s['deadline_seconds']<=21600 or
            continuation.get('schema')!='c4-calibration-continuation-v1' or
            continuation['original_request']!=s['original_request']):
        raise ValueError('Reviewed original continuation for one-time handoff required')
    audit=module(s['handoff_tool']);out.mkdir(parents=True,exist_ok=False);write_new(out/'request.json',s)
    try:
        terminal=wait(s['unit'],s['invocation'],time.monotonic()+s['deadline_seconds'])
        write_new(out/'terminal.json',terminal)
        handed=audit.audit(s['original_request'],s['unit'],s['invocation'],Path(s['handoff_out']),s['next_owner'],s['continuation'])
        write_new(out/'result.json',dict(passed=True,handoff=handed,continuation=s['continuation'],
            unit=s['unit'],invocation=s['invocation'],automatic_retry=False,next_workload_started=False))
    except Exception as error:
        write_new(out/'blocked.json',dict(passed=False,error=str(error),automatic_retry=False,next_workload_started=False));raise
    return ref(out/'result.json')


def watched(request):
    s=load(pinned(request))
    if s.get('schema')=='c4-calibration-handoff-watch-v1':return handoff_only(request,s)
    out=Path(s['out']);original=load(pinned(s['original_request']));batch=Path(original['out'])
    if (s.get('schema')!='c4-admit-boundary-watch-v1' or s.get('reviewed') is not True or
            not 1800<=s['deadline_seconds']<=21600 or s['original_unit']==s['continuation_unit']):
        raise ValueError('Reviewed bounded one-batch continuation watcher required')
    cont=module(s['continuation_tool']);audit=module(s['handoff_tool']);pinned(s['fixed_admit']);pinned(s['original_batch_tool'])
    pre=load(pinned(s['preflight']))
    if pre.get('passed') is not True or pre['admit']!=s['fixed_admit'] or pre['request']!=s['original_request']:
        raise ValueError('Original first full route preflight required before watching')
    deadline=time.monotonic()+s['deadline_seconds'];out.mkdir(parents=True,exist_ok=False)
    write_new(out/'request.json',s)
    try:
        stop=wait(s['original_unit'],s['original_invocation'],deadline);write_new(out/'original-terminal.json',stop)
        actual_unit=s['original_unit'];actual_invocation=s['original_invocation'];continuation=None
        if not (batch/'result.json').is_file():
            fields={key:ref(batch/path) for key,path in dict(failed='failed.json',admit_stderr='admit/stderr',
                original_admit_request='admit-request.json',overhead='overhead/result.json',route='route.json').items()}
            if not pinned(fields['admit_stderr']).read_text().rstrip().endswith('ValueError: '+cont.ERROR):
                raise ValueError('C4 ended for another reason; no automatic continuation')
            body=dict(schema='c4-admit-continuation-v1',reviewed=True,**fields,
                **{k:s[k] for k in ('original_request','original_unit','original_invocation','preflight','fixed_admit','original_batch_tool')},
                out=s['continuation_out'])
            p=out/'continuation.request.json';write_new(p,body);continuation=ref(p)
            actual_unit=s['continuation_unit']
            if show(actual_unit).get('LoadState')!='not-found':raise ValueError('New continuation unit name required')
            argv=['systemd-run','--unit='+actual_unit,'--property=Type=exec','--property=WorkingDirectory='+s['runtime_repo'],
                  '--setenv=PYTHONPATH='+s['runtime_repo'],'--setenv=PYTHONDONTWRITEBYTECODE=1',
                  '--property=StandardOutput=append:'+str(out/'continuation.stdout'),
                  '--property=StandardError=append:'+str(out/'continuation.stderr'),sys.executable,
                  str(pinned(s['continuation_tool'])),'run','--request',continuation['path'],'--sha256',continuation['sha256']]
            write_new(out/'continuation-intent.json',dict(argv=argv,automatic_retry=False))
            started=subprocess.run(argv,capture_output=True,text=True)
            write_new(out/'continuation-launch.json',dict(exit_code=started.returncode,stdout=started.stdout,stderr=started.stderr))
            if started.returncode:raise ValueError('Inspect exact continuation launch failure; no retry')
            actual=show(actual_unit);actual_invocation=actual.get('InvocationID')
            if not actual_invocation:raise ValueError('Continuation invocation missing; inspect original receipts')
            write_new(out/'continuation-started.json',actual)
            write_new(out/'continuation-terminal.json',wait(actual_unit,actual_invocation,deadline))
        handed=audit.audit(s['original_request'],actual_unit,actual_invocation,Path(s['handoff_out']),s['next_owner'],continuation)
        write_new(out/'result.json',dict(passed=True,handoff=handed,continuation=continuation,
            unit=actual_unit,invocation=actual_invocation,automatic_retry=False,next_workload_started=False))
    except Exception as error:
        write_new(out/'blocked.json',dict(passed=False,error=str(error),automatic_retry=False,next_workload_started=False))
        raise
    return ref(out/'result.json')


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--request',required=True);p.add_argument('--sha256',required=True)
    a=p.parse_args();print(json.dumps(watched(dict(path=a.request,sha256=a.sha256))))
