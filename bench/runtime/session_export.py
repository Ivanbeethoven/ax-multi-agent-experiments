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


def _receipt(path, value):
    with path.open('x') as stream:
        json.dump(value,stream,indent=2)
        stream.write('\n')


def _cleanup_owned_group(child, out, members, wait_group):
    """Clean up a failed export without treating a signal as natural closure."""
    pgid=child.pid
    initial=sorted(members(pgid))
    remaining=initial
    attempts=[]
    for action in (signal.SIGTERM,signal.SIGKILL):
        if not remaining:
            break
        signal_started=time.monotonic_ns()
        try:
            os.killpg(pgid,action)
            sent=True
        except ProcessLookupError:
            sent=False
        outcome=wait_group(pgid,members,timeout=2.0)
        attempts.append(dict(signal=action.name,sent=sent,
            signal_attempt_monotonic_ns=signal_started,**outcome))
        remaining=outcome['group_members_after']
    _receipt(out/'cleanup.json',dict(pgid=pgid,group_members_before=initial,
        group_members_after=remaining,attempts=attempts,parent_exit_code=child.poll()))


def _run_with_natural_closure(argv, cwd, env, out, members, wait_group, *, export_timeout=90):
    with (out/'stdout').open('xb') as stdout, (out/'stderr').open('xb') as stderr:
        started=time.monotonic_ns()
        deadline=started+int(export_timeout*1_000_000_000)
        child=subprocess.Popen(argv,cwd=cwd,env=env,stdout=stdout,stderr=stderr,start_new_session=True)
        natural=False
        try:
            try:
                code=child.wait(timeout=max(0,(deadline-time.monotonic_ns())/1e9))
            except subprocess.TimeoutExpired as exc:
                _receipt(out/'process-closure.json',dict(pgid=child.pid,status='parent-timeout',
                    command_started_monotonic_ns=started,parent_exited_monotonic_ns=None,
                    closure_observed_monotonic_ns=time.monotonic_ns(),
                    export_timeout_seconds=export_timeout,group_members_after=sorted(members(child.pid))))
                raise RuntimeError('Native export parent did not exit by its deadline') from exc
            parent_exited=time.monotonic_ns()
            closure=wait_group(child.pid,members,timeout=max(0,(deadline-parent_exited)/1e9))
            observed=time.monotonic_ns()
            natural=not closure['group_members_after'] and observed <= deadline
            proof=dict(pgid=child.pid,exit_code=code,
                status='natural' if natural else 'group-timeout',
                command_started_monotonic_ns=started,parent_exited_monotonic_ns=parent_exited,
                closure_observed_monotonic_ns=observed,export_timeout_seconds=export_timeout,**closure)
            _receipt(out/'process-closure.json',proof)
            if not natural:
                raise RuntimeError('Native export group did not exit naturally by its deadline')
            if code:
                raise RuntimeError('Native export failed; see private stderr')
            return proof
        finally:
            if not natural:
                _cleanup_owned_group(child,out,members,wait_group)


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
    native=runpy.run_path(str(SCRIPTS/'c4-state-reopen-node.py'))
    wrapper=native['NETWORK_WRAPPER']
    argv=['unshare','--net','--',sys.executable,'-c',wrapper,os.readlink('/proc/self/ns/net'),
          str(out/'network.json'),str(binary),checkpoint['session_id']]
    closure=_run_with_natural_closure(argv,workspace/'repo',env,out,process_group_members,
        native['wait_export_group'])
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
