"""Open the original native OpenCode state with empty HOME and no external network."""
import hashlib
import json
import os
from pathlib import Path
import runpy
import signal
import subprocess
import sys
import time

REPO=Path(__file__).resolve().parents[2]
SCRIPTS=REPO/'experiments/unified-suite/pilot-aliyun/scripts'
POLL_NS=50_000_000
GROUP_SCOPE='original-linux-process-group'


def _receipt(path, value):
    with path.open('x') as stream:
        json.dump(value,stream,indent=2)
        stream.write('\n')


def _peek_leader(pid):
    # This Popen is the sole waiter. A dead, unreaped leader still owns its PID,
    # so the kernel cannot assign that number to a new process group.
    return os.waitid(os.P_PID,pid,os.WEXITED|os.WNOHANG|os.WNOWAIT)


def _wait_for_leader(pid, deadline):
    while True:
        info=_peek_leader(pid)
        observed=time.monotonic_ns()
        if info is not None and observed <= deadline:
            code=info.si_status if info.si_code==os.CLD_EXITED else -info.si_status
            return code,observed
        if observed >= deadline:
            return None
        time.sleep(min(POLL_NS,deadline-observed)/1e9)


def _reap_leader(child):
    # Unlike Popen.wait(), a missing child here cannot be mistaken for exit 0.
    _,status=os.waitpid(child.pid,0)
    child.returncode=os.waitstatus_to_exitcode(status)
    return child.returncode


def _wait_for_group(pgid, members, deadline):
    """Observe the original PGID; descendants which leave it are out of scope."""
    started=time.monotonic_ns()
    first=current=sorted(members(pgid))
    observed=time.monotonic_ns()
    consecutive_empty=int(not current and observed <= deadline)
    while consecutive_empty < 2 and deadline-observed >= POLL_NS:
        time.sleep(POLL_NS/1e9)
        current=sorted(members(pgid))
        observed=time.monotonic_ns()
        consecutive_empty=consecutive_empty+1 if not current and observed <= deadline else 0
    return dict(group_members_first_observation=first,group_members_after=current,
        group_closure_wait_seconds=(observed-started)/1e9,
        group_closure_timeout_seconds=max(0,(deadline-started)/1e9),
        group_consecutive_empty_observations=consecutive_empty,
        group_stably_empty=consecutive_empty==2,closure_observed_monotonic_ns=observed)


def _cleanup_owned_group(child, out, members):
    """Keep the leader until all group probes/signals are over; never upgrade a failure."""
    pgid=child.pid
    attempts=[]
    initial=outcome=None
    code=reaped_at=None
    anchor_lost=False
    try:
        _peek_leader(pgid)
        initial=outcome=_wait_for_group(pgid,members,time.monotonic_ns()+3*POLL_NS)
        for action in (signal.SIGTERM,signal.SIGKILL):
            if outcome['group_stably_empty']:
                break
            _peek_leader(pgid)
            signal_started=time.monotonic_ns()
            try:
                os.killpg(pgid,action)
                sent=True
            except ProcessLookupError:
                sent=False
            outcome=_wait_for_group(pgid,members,time.monotonic_ns()+2_000_000_000)
            attempts.append(dict(signal=action.name,sent=sent,
                signal_attempt_monotonic_ns=signal_started,**outcome))
        if _wait_for_leader(pgid,time.monotonic_ns()+2_000_000_000) is not None:
            code=_reap_leader(child)
            reaped_at=time.monotonic_ns()
    except ChildProcessError:
        # An external reaper would invalidate group ownership; stop using the PGID.
        anchor_lost=True
    _receipt(out/'cleanup.json',dict(pgid=pgid,group_scope=GROUP_SCOPE,
        group_members_before=initial['group_members_first_observation'] if initial else None,
        group_members_after=outcome['group_members_after'] if outcome else None,
        group_stably_empty=outcome['group_stably_empty'] if outcome else False,
        leader_anchor_lost=anchor_lost,attempts=attempts,parent_exit_code=code,
        parent_reaped_monotonic_ns=reaped_at))


def _run_with_natural_closure(argv, cwd, env, out, members, *, export_timeout=90):
    if sys.platform!='linux' or not hasattr(os,'WNOWAIT'):
        raise RuntimeError('Native export process closure requires Linux waitid(WNOWAIT)')
    with (out/'stdout').open('xb') as stdout, (out/'stderr').open('xb') as stderr:
        started=time.monotonic_ns()
        deadline=started+int(export_timeout*1_000_000_000)
        child=subprocess.Popen(argv,cwd=cwd,env=env,stdout=stdout,stderr=stderr,start_new_session=True)
        natural=False
        try:
            exited=_wait_for_leader(child.pid,deadline)
            if exited is None:
                _receipt(out/'process-closure.json',dict(pgid=child.pid,group_scope=GROUP_SCOPE,
                    status='parent-timeout',
                    command_started_monotonic_ns=started,parent_exited_monotonic_ns=None,
                    closure_observed_monotonic_ns=time.monotonic_ns(),
                    process_budget_seconds=export_timeout,group_members_after=sorted(members(child.pid))))
                raise RuntimeError('Native export parent did not exit by its deadline')
            code,parent_exited=exited
            closure=_wait_for_group(child.pid,members,deadline)
            natural=closure['group_stably_empty']
            proof=dict(pgid=child.pid,group_scope=GROUP_SCOPE,exit_code=code,
                status='natural' if natural else 'group-timeout',
                command_started_monotonic_ns=started,parent_exited_monotonic_ns=parent_exited,
                process_budget_seconds=export_timeout,
                group_members_after_parent_exit=closure.pop('group_members_first_observation'),**closure)
            if natural:
                if _reap_leader(child)!=code:
                    raise RuntimeError('Native export leader status changed before reap')
                proof['parent_reaped_monotonic_ns']=time.monotonic_ns()
            _receipt(out/'process-closure.json',proof)
            if not natural:
                raise RuntimeError('Native export group did not exit naturally by its deadline')
            if code:
                raise RuntimeError('Native export failed; see private stderr')
            return proof
        finally:
            if not natural:
                _cleanup_owned_group(child,out,members)


def export(workspace, checkpoint, binary, out):
    sys.path.insert(0,str(REPO/'bench'))
    from bench.session_check import check_after
    from bench.runtime.node import process_group_members
    with binary.open('rb') as stream:
        binary_sha=hashlib.file_digest(stream,'sha256').hexdigest()
    if binary_sha != checkpoint['opencode_binary_sha256']:
        raise ValueError('OpenCode binary differs from the original checkpoint')
    if (str(workspace)!=checkpoint['canonical_workspace'] or
            str(workspace/'repo')!=checkpoint['canonical_project']):
        raise ValueError('Preserve the original canonical workspace and project')
    settings=json.loads((workspace/'config/opencode.json').read_text())
    options=settings['provider']['experiment']['options']
    if options['apiKey']!='local-session-no-master-key' or not options['baseURL'].startswith('http://127.0.0.1:'):
        raise ValueError('Checkpoint must contain only its original dummy provider configuration')
    out.mkdir()
    home=out/'empty-home'; home.mkdir(mode=0o700)
    env=dict(PATH='/usr/local/bin:/usr/bin:/bin',HOME=str(home),USERPROFILE=str(home),
        OPENCODE_CONFIG=str(workspace/'config/opencode.json'),NO_COLOR='1',
        OPENCODE_DISABLE_AUTOUPDATE='1',OPENCODE_DISABLE_SHARE='1',
        OPENCODE_DISABLE_LSP_DOWNLOAD='1',OPENCODE_DISABLE_DEFAULT_PLUGINS='true',
        OPENCODE_DISABLE_EXTERNAL_SKILLS='true',OPENCODE_DISABLE_CLAUDE_CODE='true',
        OPENCODE_DISABLE_MODELS_FETCH='true')
    for name in ('data','state','cache','config'):
        path=workspace/'source'/name
        if not path.is_dir() or path.resolve()!=path:
            raise ValueError('Workspace-local XDG path required')
        env['XDG_'+name.upper()+'_HOME']=str(path)
    wrapper=runpy.run_path(str(SCRIPTS/'c4-state-reopen-node.py'))['NETWORK_WRAPPER']
    argv=['unshare','--net','--',sys.executable,'-c',wrapper,os.readlink('/proc/self/ns/net'),
          str(out/'network.json'),str(binary),checkpoint['session_id']]
    closure=_run_with_natural_closure(argv,workspace/'repo',env,out,process_group_members)
    value=json.loads((out/'stdout').read_text())
    validation=check_after(checkpoint['session_prefix'],value,False)
    sha=hashlib.sha256((json.dumps(value,ensure_ascii=False)+'\n').encode()).hexdigest()
    if sha != checkpoint['session_export_sha256']:
        raise ValueError('Original session export changed')
    proof={'passed':True,'session_id':checkpoint['session_id'],'export_sha256':sha,
        'validation':validation,'binary_sha256':binary_sha,'home_empty_at_start':True,
        'xdg_in_workspace':True,'network':json.loads((out/'network.json').read_text()),
        'native_model_calls':0,'real_resume':False,'exit_code':closure['exit_code'],
        'process_closure':closure,'native_stdout_validated_monotonic_ns':time.monotonic_ns()}
    (out/'result.json').write_text(json.dumps(proof,indent=2)+'\n')
    return proof
