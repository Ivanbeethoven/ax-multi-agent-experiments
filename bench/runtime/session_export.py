"""Open the original native OpenCode state with empty HOME and no external network."""
import hashlib
import json
import os
from pathlib import Path
import runpy
import signal
import subprocess
import sys

REPO=Path(__file__).resolve().parents[2]
SCRIPTS=REPO/'experiments/unified-suite/pilot-aliyun/scripts'


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
    with (out/'stdout').open('xb') as stdout, (out/'stderr').open('xb') as stderr:
        child=subprocess.Popen(argv,cwd=workspace/'repo',env=env,stdout=stdout,stderr=stderr,start_new_session=True)
        try:
            code=child.wait(timeout=90)
        finally:
            if process_group_members(child.pid):
                os.killpg(child.pid,signal.SIGTERM)
                try: child.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    os.killpg(child.pid,signal.SIGKILL); child.wait(timeout=5)
    if code or process_group_members(child.pid):
        raise RuntimeError('Native export failed or left running processes; see private stderr')
    value=json.loads((out/'stdout').read_text())
    validation=check_after(checkpoint['session_prefix'],value,False)
    sha=hashlib.sha256((json.dumps(value,ensure_ascii=False)+'\n').encode()).hexdigest()
    if sha != checkpoint['session_export_sha256']:
        raise ValueError('Original session export changed')
    proof={'passed':True,'session_id':checkpoint['session_id'],'export_sha256':sha,
        'validation':validation,'binary_sha256':binary_sha,'home_empty_at_start':True,
        'xdg_in_workspace':True,'network':json.loads((out/'network.json').read_text()),
        'native_model_calls':0,'real_resume':False,'exit_code':code}
    (out/'result.json').write_text(json.dumps(proof,indent=2)+'\n')
    return proof
