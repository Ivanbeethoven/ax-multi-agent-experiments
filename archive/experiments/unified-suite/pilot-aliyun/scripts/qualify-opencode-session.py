#!/usr/bin/env python3
"""Two tiny real turns: export/import under empty HOME, then native resume.

Local functional qualification only. No cloud timing, FUSE or live migration
claim. Master provider credentials stay in this relay process, never the agent's
workspace, command line, retained environment or result files.
"""
import argparse
from datetime import datetime, timezone
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import secrets
import shutil
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request

REPO=Path(__file__).resolve().parents[4]
sys.path.insert(0,str(REPO/'bench'))
from session_check import checkpoint,check_after


def digest(path):
    with path.open('rb') as f:return hashlib.file_digest(f,'sha256').hexdigest()


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--binary',type=Path,required=True)
    p.add_argument('--provider-config',type=Path,required=True)
    p.add_argument('--out',type=Path,required=True)
    p.add_argument('--workspace-path',type=Path,help='Use this new absolute project path, also used unchanged on cloud executors')
    p.add_argument('--checkpoint-out',type=Path,help='Preserve the workspace immediately after source export, before target resume')
    p.add_argument('--checkpoint-only',action='store_true',help='Prepare one real task boundary; this alone is not a resume qualification')
    p.add_argument('--runtime-tree',type=Path,help='Previously qualified config/opencode dependencies, without provider config')
    p.add_argument('--relay-port',type=int,default=0)
    a=p.parse_args()
    if a.checkpoint_only and not a.checkpoint_out:raise ValueError('checkpoint-only requires a retained checkpoint directory')
    if a.workspace_path and (not a.workspace_path.is_absolute() or a.workspace_path.exists()):
        raise ValueError('A new absolute workspace directory is required')
    provider=json.loads(a.provider_config.read_text(encoding='utf-8-sig'))['provider']['deepseek']
    key=provider['options']['apiKey'];base=provider['options']['baseURL'].rstrip('/')
    if not key or key.startswith('{'):raise ValueError('external literal provider credential required')
    a.out.mkdir(parents=True,exist_ok=False);a.out.chmod(0o700)
    workspace=a.workspace_path or a.out/'workspace';repo=workspace/'repo';repo.mkdir(parents=True)
    (repo/'README.md').write_text('Small native session continuity task.\n')
    subprocess.run(['git','init','-q',str(repo)],check=True)
    subprocess.run(['git','-C',str(repo),'add','README.md'],check=True)
    subprocess.run(['git','-C',str(repo),'-c','user.name=Session Test','-c','user.email=test@example.invalid','commit','-qm','Initial task'],check=True)
    relay_calls=[]
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args):pass
        def do_POST(self):
            if not self.path.startswith('/v1/'):
                self.send_error(404);return
            n=int(self.headers.get('Content-Length','0'))
            if not 0<n<=16*1024*1024:self.send_error(413);return
            body=self.rfile.read(n)
            request=urllib.request.Request(base+self.path[3:],data=body,
                headers={'Authorization':'Bearer '+key,'Content-Type':'application/json'},method='POST')
            begin=time.monotonic()
            try:
                try:response=urllib.request.urlopen(request,timeout=180)
                except urllib.error.HTTPError as error:response=error
                with response:
                    self.send_response(response.status)
                    self.send_header('Content-Type',response.headers.get('Content-Type','application/json'));self.end_headers()
                    while chunk:=response.read1(65536):self.wfile.write(chunk);self.wfile.flush()
                    relay_calls.append(dict(status=response.status,seconds=time.monotonic()-begin,request_bytes=n))
            except (OSError,TimeoutError):
                relay_calls.append(dict(error='upstream/transport failed',seconds=time.monotonic()-begin,request_bytes=n))
    server=ThreadingHTTPServer(('127.0.0.1',a.relay_port),Handler)
    threading.Thread(target=server.serve_forever,daemon=True).start()
    config={'$schema':'https://opencode.ai/config.json','provider':{'experiment':{
        'npm':provider['npm'],'models':{'deepseek-v4-flash':provider['models']['deepseek-v4-flash']},
        'options':{'baseURL':f'http://127.0.0.1:{server.server_port}/v1','apiKey':'local-session-no-master-key'}}},
        'model':'experiment/deepseek-v4-flash','small_model':'experiment/deepseek-v4-flash','share':'disabled',
        'permission':{'*':'allow','task':'deny','webfetch':'deny','websearch':'deny','codesearch':'deny'}}
    config_file=workspace/'config/opencode.json';config_file.parent.mkdir();config_file.write_text(json.dumps(config))
    def environment(name):
        home=a.out/(name+'-empty-home');home.mkdir()
        state=workspace/name;state.mkdir()
        if a.runtime_tree:
            allowed={'.gitignore','package.json','bun.lock','node_modules'}
            if {f.name for f in a.runtime_tree.iterdir()}-allowed:raise ValueError('Unexpected runtime config files')
            package=json.loads((a.runtime_tree/'package.json').read_text())
            version=subprocess.check_output([str(a.binary),'--version'],text=True).strip()
            if package.get('dependencies')!={'@opencode-ai/plugin':version}:raise ValueError('Runtime plugin version differs')
            for file in a.runtime_tree.rglob('*'):
                if file.is_symlink() and not file.resolve(strict=True).is_relative_to(a.runtime_tree.resolve()):
                    raise ValueError('Runtime dependency link escapes its tree')
            shutil.copytree(a.runtime_tree,state/'config/opencode',symlinks=True)
        env=dict(PATH='/usr/local/bin:/usr/bin:/bin',HOME=str(home),USERPROFILE=str(home),
            XDG_DATA_HOME=str(state/'data'),XDG_STATE_HOME=str(state/'state'),XDG_CACHE_HOME=str(state/'cache'),
            XDG_CONFIG_HOME=str(state/'config'),OPENCODE_CONFIG=str(config_file),
            OPENCODE_DISABLE_AUTOUPDATE='1',OPENCODE_DISABLE_SHARE='1',OPENCODE_DISABLE_LSP_DOWNLOAD='1',
            OPENCODE_DISABLE_DEFAULT_PLUGINS='true',OPENCODE_DISABLE_EXTERNAL_SKILLS='true',
            OPENCODE_DISABLE_CLAUDE_CODE='true',
            OPENCODE_DISABLE_MODELS_FETCH='true',NO_COLOR='1',NO_PROXY='127.0.0.1,localhost',no_proxy='127.0.0.1,localhost')
        env.update({k:v for k,v in os.environ.items() if k.lower() in ('http_proxy','https_proxy','all_proxy')})
        return env
    def run(name,arguments,env):
        out=a.out/name;out.mkdir();started=time.monotonic()
        argv=[str(a.binary),*arguments]
        # No overall deadline or automatic retry; errors and actual exits remain.
        with (out/'stdout').open('xb') as stdout,(out/'stderr').open('xb') as stderr:
            process=subprocess.Popen(argv,cwd=repo,env=env,stdout=stdout,stderr=stderr,start_new_session=True)
            (out/'started.json').write_text(json.dumps(dict(pid=process.pid,argv=argv,at=datetime.now(timezone.utc).isoformat()))+'\n')
            code=process.wait()
        result=dict(exit_code=code,seconds=time.monotonic()-started)
        (out/'exit.json').write_text(json.dumps(result)+'\n')
        if code:raise RuntimeError('native command failed: '+name)
        return (out/'stdout').read_bytes()
    report=dict(passed=False,scope='local CLI export/import/resume under empty HOME; no filesystem performance or cross-machine result',
        opencode_binary_sha256=digest(a.binary),model='deepseek-v4-flash')
    try:
        source_env=environment('source')
        secret='session-'+secrets.token_hex(8)
        source_prompt=f'Remember this exact token in our conversation: {secret}. Do not write the token to a file or command. Use a tool to create checkpoint.txt containing ready and a newline. Do not create final.txt. Stop after that with a brief reply.'
        raw=run('source-run',['run','--format','json','--model','experiment/deepseek-v4-flash','--agent','build',source_prompt],source_env)
        events=[json.loads(line) for line in raw.splitlines() if line.startswith(b'{')]
        ids={e['sessionID'] for e in events if e.get('sessionID')}
        if len(ids)!=1 or (repo/'checkpoint.txt').read_text()!='ready\n':raise ValueError('source did not produce a valid checkpoint')
        sid=ids.pop()
        before=json.loads(run('source-export',['export',sid],source_env))
        check=checkpoint(before)
        transfer=workspace/'session-export.json';transfer.write_text(json.dumps(before,ensure_ascii=False)+'\n')
        if a.checkpoint_out:
            # The native process has exited. Copy its entire XDG tree, including
            # any WAL/SHM, and verify the copied SQLite file independently.
            a.checkpoint_out.mkdir(parents=True,exist_ok=False);a.checkpoint_out.chmod(0o700)
            saved=a.checkpoint_out/'workspace'
            shutil.copytree(workspace,saved,symlinks=True)
            import sqlite3
            db=saved/'source/data/opencode/opencode.db'
            with sqlite3.connect('file:'+str(db)+'?mode=ro',uri=True) as connection:
                if connection.execute('PRAGMA quick_check').fetchall()!=[('ok',)]:raise ValueError('Checkpoint SQLite check failed')
            for file in saved.rglob('*'):
                if file.is_file() and key.encode() in file.read_bytes():raise ValueError('Credential leaked into checkpoint')
            checkpoint_record=dict(session_id=sid,canonical_workspace=str(workspace),canonical_project=str(repo),
                session_export_sha256=digest(saved/'session-export.json'),session_prefix=check,
                source_process_exited=True,sqlite_quick_check='ok',contains_provider_master_key=False,
                model='experiment/deepseek-v4-flash',opencode_binary_sha256=digest(a.binary),
                resume_prompt='Continue the saved task. Use a tool to write final.txt containing only the exact token from our earlier conversation and a newline. Do not change checkpoint.txt. Stop with a brief reply.',
                expected_final_sha256=hashlib.sha256((secret+'\n').encode()).hexdigest(),
                scope='frozen input checkpoint; not a migration or performance result')
            (a.checkpoint_out/'checkpoint.json').write_text(json.dumps(checkpoint_record,indent=2)+'\n')
            report['checkpoint']=checkpoint_record
        if a.checkpoint_only:
            report.update(passed=True,scope='one real OpenCode checkpoint prepared; resume qualification is separate',
                session_id=sid,source_tool_boundary_completed=True,session_export_sha256=digest(transfer),
                canonical_project_path=str(repo),opencode_version=subprocess.check_output([str(a.binary),'--version'],text=True).strip())
            report['native_usage']=[{k:e['part'][k] for k in ('tokens','cost','reason') if k in e['part']}
                for e in events if e.get('type')=='step_finish' and isinstance(e.get('part'),dict)]
            return
        target_env=environment('target')
        run('target-import',['import',str(transfer)],target_env)
        restored=json.loads(run('target-export-restored',['export',sid],target_env))
        check_after(check,restored,False)
        resume_prompt='Continue the saved task. Use a tool to write final.txt containing only the exact token from our earlier conversation and a newline. Do not change checkpoint.txt. Stop with a brief reply.'
        resumed_raw=run('target-resume',['run','--format','json','--model','experiment/deepseek-v4-flash','--agent','build','--session',sid,resume_prompt],target_env)
        after=json.loads(run('target-export-after',['export',sid],target_env))
        validation=check_after(check,after,True)
        if (repo/'final.txt').read_text()!=secret+'\n' or (repo/'checkpoint.txt').read_text()!='ready\n':
            raise ValueError('resumed conversation/file continuity failed')
        # Native JSON events must contain an actually completed new tool.
        native=[json.loads(line) for line in resumed_raw.splitlines() if line.startswith(b'{')]
        completed=[e for e in native if e.get('sessionID')==sid and e.get('type')=='tool_use' and e.get('part',{}).get('state',{}).get('status')=='completed']
        if not completed:raise ValueError('missing native completed-tool event')
        # Credential scan includes SQLite/WAL bytes; never publish a tree with a leaked key.
        for file in workspace.rglob('*'):
            if file.is_file() and key.encode() in file.read_bytes():raise ValueError('credential leaked into workspace')
        report.update(passed=True,session_id=sid,validation=validation,workspace_only_state=True,
            separate_empty_homes=True,separate_target_database=True,token_recovered=True,
            completed_native_tool_events=len(completed),session_export_sha256=digest(transfer),
            opencode_version=subprocess.check_output([str(a.binary),'--version'],text=True).strip())
        report['native_usage']=[{k:e['part'][k] for k in ('tokens','cost','reason') if k in e['part']}
            for e in events+native if e.get('type')=='step_finish' and isinstance(e.get('part'),dict)]
    except BaseException as error:
        report['error']=type(error).__name__+': '+str(error)
        raise
    finally:
        server.shutdown();server.server_close()
        report['relay_calls']=relay_calls
        (a.out/'result.json').write_text(json.dumps(report,indent=2)+'\n')
        print(json.dumps(report,indent=2))


if __name__=='__main__':main()
