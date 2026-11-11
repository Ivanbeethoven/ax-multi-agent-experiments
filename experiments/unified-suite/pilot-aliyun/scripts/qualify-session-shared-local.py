#!/usr/bin/env python3
"""Resume one frozen native OpenCode database through a real local JuiceFS mount.

Run in a private mount namespace. Existing canonical-path data is hidden by a
temporary mount inside that namespace only. No cloud resource or model retry.
"""
import argparse
from copy import deepcopy
from datetime import datetime,timezone
from http.server import ThreadingHTTPServer
import importlib.util
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import subprocess
import sys
import threading
import time
import uuid

REPO=Path(__file__).resolve().parents[4]
sys.path[:0]=[str(REPO),str(REPO/'bench')]
from bench.runtime.common import digest,load,write_new
from bench.session_check import check_after


def module(name,file):
    spec=importlib.util.spec_from_file_location(name,file)
    result=importlib.util.module_from_spec(spec);spec.loader.exec_module(result);return result


def main():
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('build-root','checkpoint-directory','services','provider-config','out'):
        p.add_argument('--'+name,type=Path,required=True)
    p.add_argument('--parent-mount-namespace',required=True)
    p.add_argument('--database',type=int,required=True)
    p.add_argument('--resume',action='store_true',help='One real native resume; default checks export after cold reopening only')
    p.add_argument('--thinking-mode',choices=['passthrough','disabled'],default='passthrough')
    a=p.parse_args()
    if (os.geteuid()!=0 or not re.fullmatch(r'mnt:\[\d+\]',a.parent_mount_namespace) or
            os.readlink('/proc/self/ns/mnt')==a.parent_mount_namespace):
        raise ValueError('Root in a new private mount namespace required')
    if not 0<=a.database<16 or a.out.exists() or not a.out.is_absolute():
        raise ValueError('Explicit local Redis database 0..15 and fresh absolute output required')
    a.out.mkdir(parents=True,exist_ok=False,mode=0o700)
    shutil.copyfile(Path(__file__),a.out/'executed-source.py')
    write_new(a.out/'started.json',dict(at=datetime.now(timezone.utc).isoformat(),
        current_mount_namespace=os.readlink('/proc/self/ns/mnt'),parent_mount_namespace=a.parent_mount_namespace,
        script_sha256=digest(Path(__file__)),thinking_mode=a.thinking_mode,requested_resume=a.resume,
        provider_config_sha256=digest(a.provider_config),automatic_retry=False))
    checkpoint=load(a.checkpoint_directory/'checkpoint.json')
    worker=module('local_shared_session_worker',Path(__file__).with_name('session-worker.py'))
    prep=module('local_shared_session_input',Path(__file__).with_name('prepare-session-input.py'))
    relay=module('local_shared_session_relay',Path(__file__).with_name('session-relay.py'))
    binary=a.build_root/'bin/opencode';jfs=a.build_root/'bin/juicefs'
    if digest(binary)!=checkpoint['opencode_binary_sha256']:raise ValueError('Original OpenCode binary required')
    original=a.checkpoint_directory/'workspace'
    if prep.tree(original)!=load(a.build_root/'session-checkpoint-package-01/source-tree.json'):
        raise ValueError('Original frozen complete checkpoint changed')
    workspace=Path(checkpoint['canonical_workspace']);point=workspace.parent
    if point!=Path('/srv/ax-pilot-session'):raise ValueError('Original canonical path required')
    provider=load(a.provider_config)['provider']['deepseek']
    calls=[];reserved=0;lock=threading.Lock()
    def record(row):
        with lock:
            calls.append(row)
            with (a.out/'relay-calls.jsonl').open('a') as stream:
                stream.write(json.dumps(row)+'\n');stream.flush()
    def reserve():
        nonlocal reserved
        with lock:
            if reserved>=16:return False
            reserved+=1
            with (a.out/'relay-reservations.jsonl').open('a') as stream:
                stream.write(json.dumps(dict(at=datetime.now(timezone.utc).isoformat(),request_number=reserved))+'\n');stream.flush()
            return True
    relay_config=dict(bind='127.0.0.1',port=18443,base_url=provider['options']['baseURL'],
        api_key=provider['options']['apiKey'],relay_key=secrets.token_hex(32),model='deepseek-v4-flash',max_requests=16,thinking_mode=a.thinking_mode)
    relay.validate_config(relay_config)
    server=ThreadingHTTPServer(('127.0.0.1',0),relay.handler(relay_config,record,reserve))
    threading.Thread(target=server.serve_forever,daemon=True).start()
    url='http://127.0.0.1:'+str(server.server_port)+'/v1'
    env=dict(os.environ,AWS_EC2_METADATA_DISABLED='true',AWS_REGION='us-east-1',NO_COLOR='1')
    for line in (a.services/'rustfs.env').read_text().splitlines():
        key,value=line.split('=',1)
        env[{'RUSTFS_ACCESS_KEY':'AWS_ACCESS_KEY_ID','RUSTFS_SECRET_KEY':'AWS_SECRET_ACCESS_KEY'}[key]]=value
    import boto3
    from botocore.config import Config
    s3=boto3.client('s3',endpoint_url='http://127.0.0.1:19000',region_name='us-east-1',
        aws_access_key_id=env['AWS_ACCESS_KEY_ID'],aws_secret_access_key=env['AWS_SECRET_ACCESS_KEY'],
        config=Config(signature_version='s3v4',retries={'max_attempts':0},s3={'addressing_style':'path'}))
    def run(label,argv):
        directory=a.out/label;directory.mkdir()
        write_new(directory/'command.json',dict(argv=list(map(str,argv)),automatic_retry=False))
        with (directory/'stdout').open('xb') as stdout,(directory/'stderr').open('xb') as stderr:
            code=subprocess.run(list(map(str,argv)),env=env,stdout=stdout,stderr=stderr).returncode
        write_new(directory/'exit.json',dict(exit_code=code))
        if code:raise RuntimeError('Inspect original '+str(directory))
    process=None;protected=False;phase='redis-empty-check';phase_mount=None
    result=dict(passed=False,scope='Local real JuiceFS/Redis/RustFS/native OpenCode qualification only; not cloud, cross-host or performance',
        resume_requested=a.resume,thinking_mode=a.thinking_mode,extra_session_import=False,shared_original_native_database=True,
        checkpoint=dict(path=str(a.checkpoint_directory/'checkpoint.json'),sha256=digest(a.checkpoint_directory/'checkpoint.json')),
        opencode_binary_sha256=digest(binary),juicefs_binary_sha256=digest(jfs),automatic_retry=False)
    def mount(label):
        nonlocal process,phase_mount
        phase_mount=label;directory=a.out/label;directory.mkdir()
        argv=[str(jfs),'mount','redis://127.0.0.1:16379/'+str(a.database),str(point),
            '--cache-dir','memory','--cache-size','512','--buffer-size','512','--enable-xattr','--enable-cap',
            '--attr-cache','0','--entry-cache','0','--dir-entry-cache','0','--open-cache','0',
            '--prefetch','1','--max-readahead','128','--cache-large-write=true',
            '--verify-cache-checksum','full','--backup-meta','0','--no-usage-report=true']
        write_new(directory/'command.json',dict(argv=argv,automatic_retry=False))
        with (directory/'stdout').open('xb') as stdout,(directory/'stderr').open('xb') as stderr:
            process=subprocess.Popen(argv,env=env,stdout=stdout,stderr=stderr)
        write_new(directory/'process.json',dict(pid=process.pid))
        for _ in range(1200):
            if process.poll() is not None:raise ValueError('Original local JuiceFS mount exited before readiness')
            rows=Path('/proc/self/mountinfo').read_text().splitlines()
            if any(row.split()[4]==str(point) and ' - fuse.juicefs ' in row for row in rows):return
            time.sleep(.05)
        raise ValueError('Mount readiness failed; inspect original process')
    def stop(label):
        nonlocal process
        run(label+'-unmount',['fusermount3','-u',point]);code=process.wait(timeout=60)
        write_new(a.out/(label+'-closed.json'),dict(passed=code==0,exit_code=code,mount_absent=not any(
            row.split()[4]==str(point) and ' - fuse.juicefs ' in row for row in Path('/proc/self/mountinfo').read_text().splitlines())))
        process=None
        if code:raise ValueError('Original JuiceFS process did not exit normally')
    try:
        # A minimal RESP query avoids relying on a host redis-cli package.
        import socket
        with socket.create_connection(('127.0.0.1',16379),timeout=5) as conn:
            conn.sendall(('SELECT '+str(a.database)+'\r\nDBSIZE\r\n').encode())
            with conn.makefile('rb') as stream:
                if stream.readline()!=b'+OK\r\n' or stream.readline()!=b':0\r\n':
                    raise ValueError('Never reuse nonempty local metadata')
        phase='format';bucket='axp-session-'+uuid.uuid4().hex[:12];result['bucket']=bucket
        s3.create_bucket(Bucket=bucket)
        run('format',[jfs,'format','--storage','s3','--bucket','http://127.0.0.1:19000/'+bucket,
            '--block-size','4M','--compress','none','--trash-days','0',
            'redis://127.0.0.1:16379/'+str(a.database),'ax-local-'+uuid.uuid4().hex[:10]])
        point.mkdir(parents=True,exist_ok=True)
        original_point=dict(device=point.stat().st_dev,inode=point.stat().st_ino)
        run('protect-canonical-path',['mount','-t','tmpfs','-o','size=16m,mode=0755','ax-session-qualification',point]);protected=True
        phase='source-mount';mount('source')
        shutil.copytree(original,workspace,symlinks=True)
        settings=deepcopy(load(workspace/'config/opencode.json'))
        settings['provider']['experiment']['options'].update(baseURL=url,apiKey=relay_config['relay_key'])
        with (workspace/'config/opencode.json').open('w') as stream:
            os.fchmod(stream.fileno(),0o600);json.dump(settings,stream)
        prep.sync_tree(workspace)
        native=dict(action='export',binary=dict(path=str(binary),sha256=digest(binary)),workspace=str(workspace),
            project=checkpoint['canonical_project'],config=str(workspace/'config/opencode.json'),
            state={name:str(workspace/'source'/name) for name in ('data','state','cache','config')},
            relay_url=url,session_id=checkpoint['session_id'])
        before=worker.run(native,a.out/'export-before-stop')
        check_after(checkpoint['session_prefix'],before,False)
        if digest(a.out/'export-before-stop/export.json')!=checkpoint['session_export_sha256']:
            raise ValueError('Original native database export changed')
        prep.sync_tree(workspace);expected=prep.tree(workspace);stop('source')
        phase='cold-mount';mount('cold')
        if prep.tree(workspace)!=expected:raise ValueError('Complete frozen workspace differs after cold remount')
        cold=worker.run(native,a.out/'cold-export');check_after(checkpoint['session_prefix'],cold,False)
        if digest(a.out/'cold-export/export.json')!=checkpoint['session_export_sha256']:
            raise ValueError('Cold original native history changed')
        result['cold_native_export_equal']=True
        if a.resume:
            phase='native-resume'
            worker.run(dict(native,action='resume',checkpoint=result['checkpoint']),a.out/'native-resume')
            after=worker.run(native,a.out/'export-after-resume')
            result['history_check']=check_after(checkpoint['session_prefix'],after,True)
            prep.sync_tree(workspace);expected=prep.tree(workspace);stop('resumed')
            phase='cold-final';mount('final')
            if prep.tree(workspace)!=expected:raise ValueError('Complete resumed workspace differs after cold remount')
            if digest(workspace/'repo/final.txt')!=checkpoint['expected_final_sha256']:
                raise ValueError('Final bytes differ from the independent checkpoint expectation')
            result.update(cold_final_bytes_equal=True,cold_full_resumed_workspace_equal=True)
            stop('final')
        else:stop('cold')
        result['passed']=True
    except BaseException as error:
        result.update(failed_phase=phase,error_type=type(error).__name__,error=str(error));raise
    finally:
        if process is not None:
            try:stop('failure')
            except BaseException as error:result.update(passed=False,cleanup_error_type=type(error).__name__)
        if protected and process is None:
            run('restore-canonical-path',['umount',point])
            result['original_canonical_parent_restored']=dict(device=point.stat().st_dev,inode=point.stat().st_ino)==original_point
            if not result['original_canonical_parent_restored']:result['passed']=False
        server.shutdown();server.server_close();s3.close()
        result.update(at=datetime.now(timezone.utc).isoformat(),relay_calls=calls,model_requests_reserved=reserved,
            original_fuse_process_alive=process is not None and process.poll() is None)
        write_new(a.out/'result.json',result);print(json.dumps(result))


if __name__=='__main__':main()
