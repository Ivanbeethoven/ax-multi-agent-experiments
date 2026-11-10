#!/usr/bin/env python3
"""One pinned native OpenCode phase with an empty HOME and workspace-local XDG.

Export/import/resume and writer inspection are separate reviewed jobs. Native
resume JSON events pass through immediately for the coordinator's tool ACK.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time

REPO=Path(__file__).resolve().parents[4]
sys.path.insert(0,str(REPO))
from bench.runtime.common import alive,digest,load,mounts,pinned,write_new


def environment(request, output):
    workspace=Path(request['workspace']).resolve(strict=True)
    project=Path(request['project']).resolve(strict=True)
    if project==workspace or not project.is_relative_to(workspace):
        raise ValueError('Project must be inside the shared workspace')
    config=Path(request['config']).resolve(strict=True)
    if not config.is_relative_to(workspace):
        raise ValueError('The model configuration must travel with the workspace')
    settings=load(config)
    provider=settings['provider']['experiment']
    if settings['model']!='experiment/deepseek-v4-flash' or provider['options']['baseURL']!=request['relay_url']:
        raise ValueError('Exact private relay and model required')
    state=request['state']
    paths={}
    for name in ('data','state','cache','config'):
        path=Path(state[name]).resolve(strict=True)
        if not path.is_relative_to(workspace):
            raise ValueError('All XDG state must be in the shared workspace')
        paths[name]=str(path)
    output=Path(output).resolve(strict=True)
    if output==workspace or output.is_relative_to(workspace):
        raise ValueError('Phase evidence and empty HOME must be outside the workspace')
    home=output/'empty-home';home.mkdir(mode=0o700)
    env=dict(PATH='/usr/local/bin:/usr/bin:/bin',HOME=str(home),USERPROFILE=str(home),
        XDG_DATA_HOME=paths['data'],XDG_STATE_HOME=paths['state'],XDG_CACHE_HOME=paths['cache'],
        XDG_CONFIG_HOME=paths['config'],OPENCODE_CONFIG=str(config),
        OPENCODE_DISABLE_AUTOUPDATE='1',OPENCODE_DISABLE_SHARE='1',OPENCODE_DISABLE_LSP_DOWNLOAD='1',
        OPENCODE_DISABLE_DEFAULT_PLUGINS='true',OPENCODE_DISABLE_EXTERNAL_SKILLS='true',
        OPENCODE_DISABLE_CLAUDE_CODE='true',OPENCODE_DISABLE_MODELS_FETCH='true',NO_COLOR='1',
        NO_PROXY='127.0.0.1,localhost,10.20.0.10',no_proxy='127.0.0.1,localhost,10.20.0.10')
    return env


def run(request, output):
    output=Path(output);output.mkdir(parents=True,exist_ok=False,mode=0o700)
    action=request['action']
    if action not in ('export','import','resume','inspect-writer','verify-stopped'):
        raise ValueError('One supported native phase required')
    if action=='verify-stopped':
        mount=request['mount'];config=load(pinned(mount['spec']))
        directory=Path(mount['directory']);ready=load(directory/'ready.json')
        stopped=load(directory/'stopped.json');process=load(directory/'process.json')
        if (ready['spec']!=mount['spec'] or process['spec']!=mount['spec'] or
                ready['anchor']!=config['anchor'] or stopped['anchor']!=config['anchor'] or
                stopped['original_mount']!=ready['mount'] or stopped['passed'] is not True or
                stopped['exit_code']!=0 or stopped['mount_absent'] is not True or
                stopped['active_workers'] or mounts(config['mount_point']) or
                alive(process['process']) or alive(process['supervisor'])):
            raise ValueError('Original supervised mount/process has not stopped normally')
        result=dict(passed=True,original_mount_stopped=True,all_exited=True,mount_absent=True,
            anchor=config['anchor'],mount=request['mount'],automatic_retry=False,
            scope='Original supervisor/process exit and mount absence; no distributed writer fencing claim')
        write_new(output/'result.json',result)
        return result
    binary=str(pinned(request['binary']))
    if action=='inspect-writer':
        config=load(pinned(request['mount_config']))
        if config['volume_format']!='workspace-native-v2':
            raise ValueError('Inspect the original native workspace writer')
        argv=[binary,'workspace','--meta-backend','redis','--meta-url',config['meta']['redis']['url'],
            '--workspace-namespace',config['workspace_namespace'],'inspect',config['workspace']]
        env=dict(PATH='/usr/local/bin:/usr/bin:/bin',HOME=str(output),NO_COLOR='1',RUST_LOG='error')
        cwd=str(output)
    else:
        env=environment(request,output);cwd=request['project']
        sid=request['session_id']
        if not isinstance(sid,str) or not sid.startswith('ses_'):
            raise ValueError('Original native session ID required')
        if action=='export':argv=[binary,'export',sid]
        elif action=='import':argv=[binary,'import',str(pinned(request['transfer']))]
        else:
            checkpoint=load(pinned(request['checkpoint']))
            if checkpoint['session_id']!=sid:
                raise ValueError('Resume the original checkpoint session')
            argv=[binary,'run','--format','json','--model','experiment/deepseek-v4-flash',
                  '--agent','build','--session',sid,checkpoint['resume_prompt']]
    write_new(output/'started.json',dict(action=action,argv=argv,at=datetime.now(timezone.utc).isoformat(),
        binary=request['binary'],automatic_retry=False,total_timeout_seconds=None))
    begin=time.monotonic();events=[]
    with (output/'stdout').open('xb') as stdout,(output/'stderr').open('xb') as stderr:
        process=subprocess.Popen(argv,cwd=cwd,env=env,stdout=subprocess.PIPE,stderr=stderr)
        write_new(output/'process.json',dict(pid=process.pid))
        for line in process.stdout:
            stdout.write(line);stdout.flush()
            if action=='resume':
                sys.stdout.buffer.write(line);sys.stdout.buffer.flush()
                try:events.append(json.loads(line))
                except (ValueError,TypeError):pass
        process.stdout.close();code=process.wait()
    write_new(output/'exit.json',dict(exit_code=code,seconds=time.monotonic()-begin,automatic_retry=False))
    if code:
        raise RuntimeError('Original native phase failed; inspect retained stdout/stderr')
    if action in ('export','inspect-writer'):
        result=json.loads((output/'stdout').read_bytes())
        if action=='export' and result['info']['id']!=request['session_id']:
            raise ValueError('Native export changed session identity')
        # Match handoff.py's canonical exported-JSON transfer bytes exactly.
        with (output/'export.json').open('x') as stream:
            stream.write(json.dumps(result,ensure_ascii=False)+'\n');stream.flush();os.fsync(stream.fileno())
    else:
        result=dict(passed=True,action=action,session_id=request['session_id'])
        if action=='resume':
            completed=[e for e in events if isinstance(e,dict) and e.get('sessionID')==request['session_id']
                and e.get('type')=='tool_use' and e.get('part',{}).get('state',{}).get('status')=='completed']
            expected=load(pinned(request['checkpoint']))
            final=Path(request['project'])/'final.txt'
            if not completed or not final.is_file() or digest(final)!=expected['expected_final_sha256']:
                raise ValueError('Missing actual completed tool or expected final bytes')
            if (Path(request['project'])/'checkpoint.txt').read_bytes()!=b'ready\n':
                raise ValueError('Original task checkpoint was modified')
            result.update(completed_native_tools=len(completed),final_sha256=digest(final))
        write_new(output/'result.json',result)
    return result


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--request',type=Path,required=True);parser.add_argument('--sha256',required=True)
    parser.add_argument('--out',type=Path,required=True)
    args=parser.parse_args()
    run(load(pinned(dict(path=str(args.request),sha256=args.sha256))),args.out)


if __name__=='__main__':main()
