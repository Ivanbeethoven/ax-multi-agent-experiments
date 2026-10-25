"""One full OpenCode task with repo + persistent state on recorded FUSE mount."""
import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import threading
import time
import urllib.request
import urllib.error
from full_trace_common import Mount, preflight, replay_check, run, save, tree
from run_swebench_opencode import load_rows, grade, prompt_for


def relay(provider):
    base=provider['options']['baseURL'].rstrip('/')
    key=provider['options']['apiKey']
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args): pass
        def do_POST(self):
            if not self.path.startswith('/v1/'):
                self.send_error(404); return
            body=self.rfile.read(int(self.headers['Content-Length']))
            headers={k:v for k,v in self.headers.items() if k.lower() not in {'host','authorization','connection','accept-encoding','content-length'}}
            headers['Authorization']='Bearer '+key
            req=urllib.request.Request(base+self.path[3:],data=body,headers=headers,method='POST')
            try:
                response=urllib.request.urlopen(req,timeout=180)
            except urllib.error.HTTPError as exc: response=exc
            with response:
                self.send_response(response.status)
                self.send_header('Content-Type',response.headers.get('Content-Type','application/json'))
                self.end_headers()
                while chunk:=response.read1(65536):
                    self.wfile.write(chunk); self.wfile.flush()
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
    threading.Thread(target=server.serve_forever,daemon=True).start()
    return server


def main():
    import docker
    p=argparse.ArgumentParser();p.add_argument('--output',type=Path,required=True)
    p.add_argument('--instance-id',default='pytest-dev__pytest-10356')
    p.add_argument('--filesystem-only',action='store_true')
    a=p.parse_args()
    folder=a.output.resolve();folder.mkdir(parents=True,exist_ok=False)
    preflight(folder/'preflight')  # No credentials or model call until gate passes.
    print('Preflight passed: children, SQLite WAL, mmap, Rust replay and final tree',flush=True)
    provider=json.load(sys.stdin)
    rows=load_rows(Path(os.environ['AX_TRACE_DATASET']))
    row=next(r for r in rows if r['instance_id']==a.instance_id)
    client=docker.from_env(timeout=120);image=client.images.get(row['image'])
    save(folder/'selection.json',{'instance_id':row['instance_id'],'base_commit':row['base_commit'],
        'image_id':image.id,'model':'deepseek-v4-flash','opencode':'1.19.4','max_steps':None,'timeout_s':3600,
        'scope':'entire OpenCode process lifetime: repository and all XDG/HOME state on FUSE; image runtime, /tmp and network excluded',
        'prompt':prompt_for(row,None,3600)})
    save(folder/'collection-policy.json',{'filesystem_only':a.filesystem_only,'solution_success_is_acceptance_gate':False})
    if not a.filesystem_only:
        baseline=grade(client,row,image.id,'',folder/'baseline','unmodified')
        gold=grade(client,row,image.id,row['patch'],folder/'gold','reference')
        if baseline['resolved'] or not gold['resolved']: raise RuntimeError('Grading environment invalid')
    backing=folder/'backing';backing.mkdir()
    source=client.containers.create(image.id,['sleep','infinity'],labels={'ax-full-trace':'fixture'})
    try: run(['docker','cp',source.id+':/testbed',backing/'repo'])
    finally: source.remove(force=True)
    # Prepare repository before freezing inputs; no grading material enters this root.
    if subprocess.run(['git','config','--get','remote.origin.url'],cwd=backing/'repo',capture_output=True).returncode==0:
        run(['git','remote','remove','origin'],cwd=backing/'repo')
    state=backing/'state';state.mkdir();(state/'home').mkdir()
    for name in ['config','data','cache','state']: (state/name).mkdir()
    cached=os.environ.get('AX_TRACE_CACHE_SEED')
    if cached: shutil.copytree(Path(cached),state/'cache',dirs_exist_ok=True,symlinks=True)
    server=relay(provider)
    config={'$schema':'https://opencode.ai/config.json','provider':{'experiment':{
        'npm':provider['npm'],'models':{'deepseek-v4-flash':provider['models']['deepseek-v4-flash']},
        'options':{'baseURL':f'http://127.0.0.1:{server.server_port}/v1','apiKey':'trace-local-no-secret'}}},
        'model':'experiment/deepseek-v4-flash','small_model':'experiment/deepseek-v4-flash','share':'disabled',
        'permission':{'*':'allow','webfetch':'deny','websearch':'deny','codesearch':'deny','task':'deny'}}
    (state/'config/opencode').mkdir();save(state/'config/opencode/opencode.json',config)
    shutil.copytree(backing,folder/'initial',symlinks=True)
    save(folder/'initial-tree.json',tree(backing))
    m=Mount(folder)
    env={'HOME':'/agent-state/home','XDG_DATA_HOME':'/agent-state/data','XDG_CONFIG_HOME':'/agent-state/config',
         'XDG_STATE_HOME':'/agent-state/state','XDG_CACHE_HOME':'/agent-state/cache','OPENCODE_CONFIG_DIR':'/agent-state/config/opencode',
         'OPENCODE_DISABLE_AUTOUPDATE':'1','OPENCODE_DISABLE_SHARE':'1','OPENCODE_DISABLE_LSP_DOWNLOAD':'1',
         'PATH':'/opt/miniconda3/envs/testbed/bin:/opt/miniconda3/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin',
         'GIT_CONFIG_COUNT':'1','GIT_CONFIG_KEY_0':'safe.directory','GIT_CONFIG_VALUE_0':'/testbed'}
    env.update({k:v for k,v in os.environ.items() if k.lower() in ('http_proxy','https_proxy','no_proxy')})
    env['NO_PROXY']=env['no_proxy']='127.0.0.1,localhost'
    binary=str(Path(os.environ['AX_TRACE_OPENCODE']).resolve())
    c=client.containers.run(image.id,['sleep','infinity'],detach=True,working_dir='/testbed',environment=env,
        network_mode='host',mem_limit='6g',nano_cpus=4_000_000_000,
        volumes={str(m.mount/'repo'):{'bind':'/testbed','mode':'rw'},str(m.mount/'state'):{'bind':'/agent-state','mode':'rw'},binary:{'bind':'/opt/opencode','mode':'ro'}},labels={'ax-full-trace':'agent'})
    started=time.monotonic(); code=None
    try:
        # Confirm both container-visible mount paths belong to recorder before inference.
        gate=c.exec_run(['sh','-c','stat -f -c %T /testbed /agent-state'])
        (folder/'container-mount-check.txt').write_bytes(gate.output)
        if gate.exit_code or gate.output.splitlines()!=[b'fuseblk',b'fuseblk']:
            if gate.exit_code or not all(b'fuse' in x for x in gate.output.splitlines()) or len(gate.output.splitlines())!=2: raise RuntimeError('Container mounts not FUSE')
        save(folder/'started.json',{'unix':time.time(),'events_before_inference':sum(1 for _ in (folder/'capture/events.jsonl').open())})
        with (folder/'agent-events.jsonl').open('wb') as out,(folder/'agent-stderr.txt').open('wb') as err:
            proc=subprocess.Popen(['docker','exec',c.id,'/opt/opencode','run','--format','json','--model','experiment/deepseek-v4-flash','--agent','build',prompt_for(row,None,3600)],stdout=out,stderr=err)
            deadline=time.monotonic()+3600
            while proc.poll() is None:
                if m.proc.poll() is not None: c.kill();raise RuntimeError('Recorder died during inference')
                if time.monotonic()>deadline: c.kill();code=124;break
                time.sleep(1)
            proc.wait(timeout=20)
            if code is None: code=proc.returncode
        # Stop the whole container so all remaining FDs/mappings close before unmount.
    finally:
        save(folder/'agent-exit.json',{'exit_code':code,'elapsed_s':time.monotonic()-started})
        c.remove(force=True);m.stop();server.shutdown()
    save(folder/'agent-exit.json',{'exit_code':code,'elapsed_s':time.monotonic()-started})
    print('Agent stopped; capture closed; starting Rust replay',flush=True)
    verification=replay_check(folder)
    graded=None
    if not a.filesystem_only:
        patch_root=folder/'patch-worktree'
        shutil.copytree(backing/'repo',patch_root,symlinks=True)
        run(['git','add','-N','.'],cwd=patch_root)
        patch=run(['git','diff','--binary',row['base_commit']],cwd=patch_root)
        (folder/'prediction.patch').write_text(patch)
        graded=grade(client,row,image.id,patch,folder/'grading','opencode-full-fuse')
    run([sys.executable,Path(__file__).with_name('analyze_full_fuse_trace.py'),folder/'capture/events.jsonl',folder/'metrics.json'])
    save(folder/'result.json',{'resolved':graded['resolved'] if graded else None,'grading_performed':graded is not None,'agent_exit_code':code,'trace_verified':verification,
         'full_agent_run':True,'model_steps':sum(json.loads(x).get('type')=='step_finish' for x in (folder/'agent-events.jsonl').read_text().splitlines() if x.startswith('{'))})
    print(json.dumps(json.loads((folder/'result.json').read_text())),flush=True)


if __name__=='__main__': main()
