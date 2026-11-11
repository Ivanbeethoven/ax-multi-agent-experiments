#!/usr/bin/env python3
"""Small cloud C3 Packed actions; use the existing supervised mount and suite stages."""
import argparse
from copy import deepcopy
from datetime import datetime,timezone
import json
import math
import os
from pathlib import Path
import secrets
import socket
import subprocess
import sys
import time
from urllib.parse import quote,urlsplit,urlunsplit
import uuid

REPO=Path(__file__).resolve().parents[4]
sys.path.insert(0,str(REPO))
from bench.runtime.common import digest,identity,load,pinned,write_new
from bench.suite_mount import cell_of,matches


def ref(path):return dict(path=str(Path(path).resolve(strict=True)),sha256=digest(path))
def utc():return datetime.now(timezone.utc).isoformat()


def packed_cell(asset,plan_path,cell_id):
    plan=load(plan_path);cell=cell_of(plan,cell_id)
    if (cell['workload'],cell['arm_id'],cell['arm']['engine'])!=('smallfiles','packed','workspace-entity-v1'):
        raise ValueError('Declared C3 packed entity cell required')
    if cell['params']['agents']!=1 or cell['params']['workspaces']!=1 or cell['params']['files'] not in (100000,1000000):
        raise ValueError('One complete declared C3 workspace required')
    if asset['product']!='brewfs' or asset['arm']!='packed' or asset['plan']['sha256']!=digest(plan_path):
        raise ValueError('Packed asset belongs to another product or frozen plan')
    if asset.get('pool','pool-a')!=cell['pool']:raise ValueError('Asset belongs to another physical pool')
    matches(asset['config'],cell['client_settings'])
    config=asset['config']
    if (not config.get('packed_manifest_key') or config.get('entity_native_manifest_key') or
            config['meta']['backend']!='redis' or config['data']['backend']!='s3'):
        raise ValueError('Exactly the qualified Redis/S3 packed lower required')
    for key in ('entity_volume_id','workspace'):uuid.UUID(config[key])
    return plan,cell


def binary_pin(path,sha,manifest,name):
    pin=dict(path=str(path),sha256=sha);pinned(pin)
    rows=[row for row in load(manifest)['files'] if row['file']==name]
    if len(rows)!=1 or rows[0]['sha256']!=sha:raise ValueError('Actual '+name+' differs from frozen build manifest')
    return pin


def status_values(raw,namespace,state):
    descriptor=raw.get('descriptor');debt=raw.get('debt')
    if not isinstance(descriptor,dict) or not isinstance(debt,dict):raise ValueError('Native descriptor and debt required')
    if descriptor.get('namespace_id')!=namespace or descriptor.get('state')!=state:
        raise ValueError('Native status belongs to another namespace or lifecycle state')
    if (state=='Active')!=(descriptor.get('active_lease') is not None):raise ValueError('Native lease state differs')
    status_authority(raw)
    values={}
    for output,key in [('pending_upload_bytes','bytes'),('pending_upload_operations','objects')]:
        value=debt.get(key)
        if type(value) not in (int,float) or not math.isfinite(value) or value!=0:
            raise ValueError('Native remote debt is missing, invalid or nonzero: '+key)
        values[output]=int(value)
    return values


def status_authority(raw):
    """Only the native authority identity; heartbeat timing is not a fence."""
    descriptor=raw['descriptor'];lease=descriptor.get('active_lease')
    if (not isinstance(descriptor.get('format'),str) or not descriptor['format'] or
            type(descriptor.get('generation')) is not int or descriptor['generation']<0 or
            not isinstance(descriptor.get('lower'),dict) or not descriptor['lower']):
        raise ValueError('Native descriptor format, generation and lower binding required')
    authority={key:descriptor[key] for key in ('format','namespace_id','state','generation','lower')}
    if lease is not None:
        if (not isinstance(lease,dict) or type(lease.get('holder_generation')) is not int or lease['holder_generation']<0 or
                not isinstance(lease.get('lease_id'),str) or not uuid.UUID(lease['lease_id']).int):
            raise ValueError('Native lease UUID and holder generation required')
        authority['active_lease']={key:lease[key] for key in ('lease_id','holder_generation')}
    else:authority['active_lease']=None
    return authority


def fresh(a):
    original=load(a.base_asset/'asset.json');plan,cell=packed_cell(original,a.plan,a.cell)
    if a.asset.exists() or a.out.exists():raise ValueError('New private asset and output required')
    if a.asset.resolve()==a.base_asset.resolve():raise ValueError('Immutable base asset cannot become its child')
    begin=time.monotonic();a.asset.mkdir(parents=True,exist_ok=False);a.out.mkdir(parents=True,exist_ok=False)
    value=deepcopy(original);config=value['config']
    config.update(entity_volume_id=str(uuid.uuid4()),workspace=str(uuid.uuid4()),workspace_namespace='packed-c3-'+uuid.uuid4().hex)
    for key in ('producer','source','created_at'):value.pop(key,None)
    point=a.asset/'mnt';point.mkdir()
    value.update(directory=str(a.asset.resolve()),mount_point=str(point.resolve()),created_at=utc(),
        immutable_parent=ref(a.base_asset/'asset.json'),source='Fresh private entity upper over the original immutable packed manifest')
    write_new(a.asset/'asset.json',value)
    write_new(a.out/'result.json',dict(passed=True,cell_id=cell['id'],asset=ref(a.asset/'asset.json'),
        parent=ref(a.base_asset/'asset.json'),plan=ref(a.plan),source_tool=ref(__file__),wall_seconds=time.monotonic()-begin,
        lower_manifest_key=config['packed_manifest_key'],volume=config['entity_volume_id'],namespace=config['workspace'],
        catalog=config['workspace_namespace'],mounted=False,
        scope='Only fresh identities and a private asset config; original producer output and base asset remain untouched'))


def debt(a):
    asset=load(a.asset/'asset.json');plan,cell=packed_cell(asset,a.plan,a.cell)
    if digest(a.manifest)!=plan['config']['build_manifest_sha256']:raise ValueError('Frozen build manifest differs')
    probe=binary_pin(a.probe,a.probe_sha256,a.manifest,'entity_metadata_probe')
    a.out.mkdir(parents=True,exist_ok=False);config=asset['config'];namespace=config['workspace']
    argv=[str(a.probe),'--backend','redis','--endpoint',config['meta']['redis']['url'],'--volume',config['entity_volume_id']]
    write_new(a.out/'intent.json',dict(argv=argv,asset=ref(a.asset/'asset.json'),plan=ref(a.plan),
        probe=probe,manifest=ref(a.manifest),source_tool=ref(__file__),at=utc(),automatic_retry=False,
        scope='Two native read-only status calls; no metadata snapshot or mutation'))
    process=None
    try:
        with (a.out/'stderr').open('xb') as err,(a.out/'stdin.jsonl').open('xb') as incoming,(a.out/'stdout.jsonl').open('xb') as outgoing:
            process=subprocess.Popen(argv,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=err)
            write_new(a.out/'process.json',dict(process=identity(process.pid),started_at=utc()))
            def read():
                line=process.stdout.readline()
                if not line:raise RuntimeError('Native probe stopped before its response')
                outgoing.write(line);outgoing.flush();return json.loads(line)
            if read()!={'ready':True}:raise ValueError('Native probe readiness differs')
            samples=[];previous=None
            for index in (0,1):
                request=json.dumps(dict(op='status',namespace=namespace),separators=(',',':')).encode()+b'\n'
                incoming.write(request);incoming.flush();process.stdin.write(request);process.stdin.flush()
                raw=read();at=utc();monotonic=time.monotonic_ns()
                rawpath=a.out/f'native-status-{index}.json';write_new(rawpath,raw)
                fields=status_values(raw,namespace,a.state)
                authority=status_authority(raw)
                if previous is not None and authority!=previous:raise ValueError('Native namespace authority changed between debt samples')
                previous=authority
                sample=dict(passed=True,cell_id=cell['id'],at=at,monotonic_ns=monotonic,
                    namespace=namespace,volume=config['entity_volume_id'],native_status=ref(rawpath),native_authority=authority,**fields)
                path=a.out/f'sample-{index}.json';write_new(path,sample);samples.append(ref(path))
                if index==0:time.sleep(1.05)
            process.stdin.close();code=process.wait()
            process.stdout.close()
            write_new(a.out/'exit.json',dict(exit_code=code,at=utc()))
            if code:raise RuntimeError('Native probe did not exit normally')
        values=[load(pin['path']) for pin in samples]
        if values[1]['monotonic_ns']-values[0]['monotonic_ns']<1000000000:raise ValueError('Native debt observations are less than one second apart')
        write_new(a.out/'result.json',dict(passed=True,cell_id=cell['id'],samples=samples,
            pending_upload_bytes=values[-1]['pending_upload_bytes'],pending_upload_operations=values[-1]['pending_upload_operations'],
            raw_stdin=ref(a.out/'stdin.jsonl'),raw_stdout=ref(a.out/'stdout.jsonl'),probe=probe,
            process=ref(a.out/'process.json'),normal_exit=ref(a.out/'exit.json'),
            interpretation='Flat closure fields mechanically extracted from native debt.bytes and debt.objects; missing fields never imply zero'))
    except BaseException as error:
        if process is not None and process.poll() is None:
            process.stdin.close();code=process.wait()
            process.stdout.close()
            if not (a.out/'exit.json').exists():write_new(a.out/'exit.json',dict(exit_code=code,at=utc()))
        write_new(a.out/'failed.json',dict(passed=False,error=type(error).__name__+': '+str(error),automatic_retry=False))
        raise


def redis_response(stream):
    prefix=stream.read(1);line=stream.readline()
    if not line.endswith(b'\r\n'):raise ValueError('Truncated Redis response')
    line=line[:-2]
    if prefix==b'-':raise RuntimeError(line.decode())
    if prefix in (b'+',b':'):return line
    if prefix==b'$':
        length=int(line)
        if length==-1:return None
        if length<0:raise ValueError('Invalid Redis bulk length')
        value=stream.read(length)
        if len(value)!=length or stream.read(2)!=b'\r\n':raise ValueError('Truncated Redis bulk response')
        return value
    if prefix==b'*':
        length=int(line)
        if length==-1:return None
        if length<0:raise ValueError('Invalid Redis array length')
        return [redis_response(stream) for _ in range(length)]
    raise ValueError('Unexpected Redis administrative response')


def redis_command(url,arguments,username=None,password=None):
    parsed=urlsplit(url)
    if parsed.scheme!='redis' or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError('Plain owned Redis endpoint required; no credentials in published arguments')
    def send(stream,values):
        encoded=[str(value).encode() for value in values]
        stream.write(b'*'+str(len(encoded)).encode()+b'\r\n')
        for value in encoded:stream.write(b'$'+str(len(value)).encode()+b'\r\n'+value+b'\r\n')
        stream.flush();return redis_response(stream)
    with socket.create_connection((parsed.hostname,parsed.port or 6379),timeout=5) as sock:
        with sock.makefile('rwb') as stream:
            if username:send(stream,['AUTH',username,password])
            if parsed.path not in ('','/','/0'):send(stream,['SELECT',int(parsed.path.lstrip('/'))])
            return send(stream,arguments)


def redis_reader_permissions(url):
    # CLIENT SETINFO is optional in Redis clients and absent before Redis 7.2.
    # Grant only this exact subcommand when the actual server advertises it.
    command=['COMMAND','INFO','client|setinfo'];reply=redis_command(url,command)
    if not isinstance(reply,list) or len(reply)!=1:raise ValueError('Unexpected optional CLIENT capability response')
    entry=reply[0]
    if entry is not None and (not isinstance(entry,list) or not entry or entry[0]!=b'client|setinfo'):
        raise ValueError('Redis advertised another command for CLIENT SETINFO')
    grants=['+type','+get','+mget','+exists','+zscore','+zrangebylex','+strlen','+time','+evalsha','+script|load','+ping','+select']
    if entry is not None:grants.append('+client|setinfo')
    def serializable(value):
        if isinstance(value,bytes):return value.decode()
        if isinstance(value,list):return [serializable(item) for item in value]
        return value
    return grants,dict(query=command,response=serializable(reply),client_setinfo_available=entry is not None,
                       granted_commands=grants,write_permissions_added=False)


def cold_verify(a):
    asset=load(a.asset/'asset.json');parent=load(a.base_asset/'asset.json');plan,cell=packed_cell(asset,a.plan,a.cell)
    packed_cell(parent,a.plan,a.cell)
    if asset.get('immutable_parent')!=ref(a.base_asset/'asset.json'):raise ValueError('Private entity does not bind this original parent')
    child=asset['config'];base=parent['config']
    for field in ('packed_manifest_key','data','meta'):
        if child[field]!=base[field]:raise ValueError('Child changed the original packed lower or endpoints')
    if any(child[key]==base[key] for key in ('entity_volume_id','workspace','workspace_namespace')):
        raise ValueError('Private upper reused a parent identity')
    stopped=load(pinned(dict(path=str(a.normal_stop),sha256=a.normal_stop_sha256)))
    if stopped.get('passed') is not True or stopped.get('exit_code')!=0 or stopped.get('mount_absent') is not True:
        raise ValueError('Original measured mount must have stopped normally')
    if stopped.get('anchor',{}).get('workspace')!=child['workspace'] or stopped.get('anchor',{}).get('namespace')!=child['workspace_namespace']:
        raise ValueError('Normal stop belongs to another original entity namespace')
    if digest(a.manifest)!=plan['config']['build_manifest_sha256']:raise ValueError('Frozen build manifest differs')
    for path,sha in [(a.reader,a.reader_sha256),(a.verifier,a.verifier_sha256),(a.oracle,a.oracle_sha256)]:pinned(dict(path=str(path),sha256=sha))
    source_proof=load(a.source_proof);files=cell['params']['files']
    if source_proof.get('passed') is not True or source_proof.get('files')!=files or source_proof.get('bytes_checked')!=files*4096:
        raise ValueError('Full original source proof required')
    producer=load(pinned(parent['producer']))['argv']
    if '--source-directory' not in producer or Path(producer[producer.index('--source-directory')+1]).resolve()!=a.source.resolve():
        raise ValueError('Cold oracle source differs from the actual immutable producer input')
    a.out.mkdir(parents=True,exist_ok=False)
    if a.private_object_env.resolve().is_relative_to(a.out.resolve()):raise ValueError('Private credentials must remain outside evidence')
    if os.name!='nt' and (a.private_object_env.stat().st_mode&0o777)!=0o600:raise ValueError('Private object environment must have mode 0600')
    pins=[ref(path) for path in [a.asset/'asset.json',a.base_asset/'asset.json',a.plan,a.manifest,a.reader,a.verifier,a.oracle,a.source_proof,a.state_file,a.normal_stop,Path(__file__)]]
    write_new(a.out/'intent.json',dict(at=utc(),cell_id=cell['id'],pins=pins,automatic_retry=False,remount=False,
        namespace=child['workspace'],volume=child['entity_volume_id'],scope='Original stopped child and original immutable parent; no FUSE reopen'))
    env=dict(os.environ,AWS_EC2_METADATA_DISABLED='true',AWS_REGION='us-east-1',NO_COLOR='1')
    values=dict(line.split('=',1) for line in a.private_object_env.read_text().splitlines())
    env.update(AWS_ACCESS_KEY_ID=values['RUSTFS_ACCESS_KEY'],AWS_SECRET_ACCESS_KEY=values['RUSTFS_SECRET_KEY'])
    endpoint=child['data']['s3']['endpoint'];bypass='127.0.0.1,localhost,'+urlsplit(endpoint).hostname
    env.update(NO_PROXY=bypass,no_proxy=bypass)
    def run(name,argv,environment=env):
        directory=a.out/name;directory.mkdir();write_new(directory/'command.json',dict(argv=list(map(str,argv)),automatic_retry=False))
        begin=time.monotonic()
        with (directory/'stdout').open('xb') as out,(directory/'stderr').open('xb') as err:
            process=subprocess.Popen(list(map(str,argv)),env=environment,stdout=out,stderr=err)
            write_new(directory/'process.json',dict(process=identity(process.pid)));code=process.wait()
        write_new(directory/'exit.json',dict(exit_code=code,wall_seconds=time.monotonic()-begin))
        if code:raise RuntimeError('Inspect original failed command: '+str(directory))
    username='packed-c3-verify-'+uuid.uuid4().hex;password=secrets.token_hex(24);registered=False
    try:
        run('source-before',[a.verifier,a.source,'base',a.out/'source-before.json',str(files),'8','1'])
        def source_checked(name):
            value=load(a.out/name)
            if (value.get('passed') is not True or value.get('full_namespace') is not True or value.get('modes_owners_links') is not True or
                    value.get('files')!=files or value.get('bytes_checked')!=files*4096):
                raise ValueError('Original deterministic source validation failed')
        source_checked('source-before.json')
        for name,state in [('expected-base',None),('expected-child',a.state_file)]:
            argv=['python3',a.oracle,'--source',a.source,'--source-proof',a.source_proof,'--files',str(files),'--out',a.out/name]
            if state is not None:argv+=['--state',state]
            run(name+'-command',argv)
        patterns=[]
        for config in [child,base]:patterns+=['~{brewfs-entity:'+config['entity_volume_id']+'}/*','~{brewfs-ws-v1}:'+config['workspace_namespace']+':ws:v1/*']
        url=child['meta']['redis']['url']
        grants,capability=redis_reader_permissions(url)
        write_new(a.out/'redis-acl-capabilities.json',capability)
        redis_command(url,['ACL','SETUSER',username,'on','>'+password,*sorted(set(patterns)),*grants])
        registered=True
        try:redis_command(url,['SET','{brewfs-entity:'+child['entity_volume_id']+'}/must-not-write','invalid'],username,password)
        except RuntimeError as error:
            if not str(error).startswith('NOPERM'):raise
            write_new(a.out/'readonly-account.json',dict(passed=True,write_rejected=True,error_class='NOPERM',allowed_key_patterns=sorted(set(patterns))))
        else:raise ValueError('Verifier account was allowed to mutate metadata')
        parsed=urlsplit(url);verify_url=urlunsplit((parsed.scheme,quote(username,safe='')+':'+quote(password,safe='')+'@'+parsed.netloc,parsed.path,'',''))
        verify_env=dict(env,AX_VERIFY_REDIS_URL=verify_url)
        proofs={}
        for name,config,expected in [('child',child,'expected-child'),('parent',base,'expected-base')]:
            run(name+'-readback',[a.reader,'packed',config['entity_volume_id'],config['workspace'],config['workspace_namespace'],
                config['data']['s3']['bucket'],endpoint,a.out/(name+'-cold'),a.out/expected/'expected-tree.json',a.out/expected/'expected-hardlinks.json'],verify_env)
            proof=load(a.out/(name+'-cold')/'verify.json')
            required=('passed','expected_tree_equal','expected_hardlinks_equal','namespace_unchanged','authority_unchanged','volume_rows_unchanged')
            if not all(proof.get(key) is True for key in required) or proof.get('files')!=files or proof.get('logical_file_bytes')!=files*4096:
                raise ValueError('Incomplete original cold '+name+' proof')
            if proof.get('namespace')!=config['workspace'] or proof.get('volume')!=config['entity_volume_id']:
                raise ValueError('Reader verified another original namespace')
            proofs[name]=ref(a.out/(name+'-cold')/'verify.json')
        run('source-after',[a.verifier,a.source,'base',a.out/'source-after.json',str(files),'8','1'])
        source_checked('source-after.json')
        if any(ref(Path(pin['path']))!=pin for pin in pins):raise ValueError('Original input or helper changed')
        write_new(a.out/'result.json',dict(passed=True,cell_id=cell['id'],cold_child=proofs['child'],parent_unchanged=proofs['parent'],
            readonly_account=ref(a.out/'readonly-account.json'),normal_exit=ref(a.normal_stop),source_pins=pins,
            scope='Full original stopped child and parent names, bytes, modes, links; native reader also enforces Quiescent, no active lease/pending debt and unchanged authority/volume rows'))
    except BaseException as error:
        write_new(a.out/'failed.json',dict(passed=False,error=type(error).__name__+': '+str(error),automatic_retry=False));raise
    finally:
        if registered:
            reply=redis_command(child['meta']['redis']['url'],['ACL','DELUSER',username])
            write_new(a.out/'readonly-account-exit.json',dict(removed_users=int(reply),normal_cleanup=True))


def qualify(a):
    """One explicitly requested cloud route qualification; never a formal sample."""
    parent=load(a.base_asset/'asset.json');plan,cell=packed_cell(parent,a.plan,a.cell)
    if digest(a.manifest)!=plan['config']['build_manifest_sha256']:raise ValueError('Frozen build manifest differs')
    binary_pin(a.smallfiles,a.smallfiles_sha256,a.manifest,'smallfiles')
    binary_pin(a.probe,a.probe_sha256,a.manifest,'entity_metadata_probe')
    pinned(dict(path=str(a.node_tool),sha256=a.node_tool_sha256))
    if a.asset.exists() or a.mount_state.exists() or a.out.exists():raise ValueError('Fresh qualification asset, supervisor and output required')
    group=Path('/sys/fs/cgroup/ax-slot-1')
    quota,period=map(int,(group/'cpu.max').read_text().split())
    if quota!=2*period or (group/'memory.max').read_text().strip()!=str(8*1024**3) or (group/'memory.swap.max').read_text().strip()!='0':
        raise ValueError('Actual 2 CPU / 8 GiB / swap 0 slot required')
    if (group/'cgroup.procs').read_text().strip():raise ValueError('Qualification cannot share an occupied Agent slot')
    (group/'cgroup.procs').write_text(str(os.getpid()))
    a.out.mkdir(parents=True,exist_ok=False);active=False
    write_new(a.out/'started.json',dict(at=utc(),cell_id=cell['id'],purpose='cloud-packed-route-qualification',
        formal_sample=False,automatic_retry=False,plan=ref(a.plan),parent=ref(a.base_asset/'asset.json'),
        source_tool=ref(__file__),node_tool=dict(path=str(a.node_tool),sha256=a.node_tool_sha256)))
    def run(name,argv):
        directory=a.out/name;directory.mkdir();write_new(directory/'command.json',dict(argv=list(map(str,argv)),automatic_retry=False))
        begin=time.monotonic()
        with (directory/'stdout').open('xb') as out,(directory/'stderr').open('xb') as err:
            process=subprocess.Popen(list(map(str,argv)),stdout=out,stderr=err)
            write_new(directory/'process.json',dict(process=identity(process.pid)));code=process.wait()
        write_new(directory/'exit.json',dict(exit_code=code,wall_seconds=time.monotonic()-begin))
        if code:raise RuntimeError('Inspect original qualification command: '+str(directory))
    def helper(action,name,**fields):
        argv=[sys.executable,a.node_tool,action,'--asset',a.asset]
        for key,value in fields.items():
            if value is True:argv+=['--'+key.replace('_','-')]
            elif value is not None:argv+=['--'+key.replace('_','-'),value]
        run(name,argv)
    try:
        fresh(argparse.Namespace(**{**vars(a),'out':a.out/'fresh'}))
        helper('draft-mount','draft',cell=a.cell,out=a.out/'mount')
        helper('start','mount-start',join_slot=True,spec=a.out/'mount/mount.reviewed.json',out=a.mount_state);active=True
        point=load(a.asset/'asset.json')['mount_point'];params=cell['params']
        argv=[a.smallfiles,'run','--root',point,'--output',a.out/'workload']
        for flag in ('files','hot-files','workers','read-ops','cow-files','write-ops','mixed-ops'):
            argv+=['--'+flag,str(params[flag.replace('-','_')])]
        argv+=['--seed',str(plan['config']['seed']),'--workspace','1'];run('eight-phases',argv)
        from bench.suite_validate import work
        packet=dict(cell_id=cell['id'],results=[dict(agent_index=0,workspace_index=0,evidence=ref(a.out/'workload/result.json'),
            raw_logs=[ref(a.out/'workload'/f'worker-{index}.csv') for index in range(params['workers'])])])
        write_new(a.out/'work-packet.json',packet);write_new(a.out/'work-check.json',work(plan,cell['id'],packet))
        debt(argparse.Namespace(**{**vars(a),'out':a.out/'debt','state':'Active'}))
        active=False
        helper('stop','normal-stop-command',mount_run=a.mount_state,out=a.out/'normal-stop.json')
        cold_verify(argparse.Namespace(**{**vars(a),'out':a.out/'cold',
            'state_file':a.out/'workload/expected-touched.tsv','normal_stop':a.out/'normal-stop.json',
            'normal_stop_sha256':digest(a.out/'normal-stop.json')}))
        write_new(a.out/'result.json',dict(passed=True,cell_id=cell['id'],asset=ref(a.asset/'asset.json'),
            full_base=ref(a.base_asset/'asset.json'),work_check=ref(a.out/'work-check.json'),native_debt=ref(a.out/'debt/result.json'),
            normal_exit=ref(a.out/'normal-stop.json'),original_readback=ref(a.out/'cold/result.json'),
            scope='Cloud functional route qualification, complete original eight-phase work and original cold child/parent checks; no observer overhead qualification or formal performance sample'))
    except BaseException as error:
        write_new(a.out/'failed.json',dict(passed=False,error=type(error).__name__+': '+str(error),automatic_retry=False))
        if active:
            active=False
            helper('stop','failure-normal-stop-command',mount_run=a.mount_state,out=a.out/'failure-normal-stop.json')
        raise


def stage_spec(a):
    request=load(a.request)
    action=request.get('action');stages={'fresh':'create-w01','debt':'drain-w01','cold-verify':'verify-w01','qualify':'qualification'}
    if request.get('schema')!='packed-c3-node-action-v1' or action not in stages:raise ValueError('Explicit packed C3 node action required')
    fields=request['arguments']
    if not isinstance(fields,dict) or not fields or any(not isinstance(key,str) or not isinstance(value,str) for key,value in fields.items()):
        raise ValueError('Explicit string CLI fields required')
    argv=[sys.executable,str(Path(__file__).resolve()),action]
    for key,value in fields.items():argv+=['--'+key.replace('_','-'),value]
    parsed=parser().parse_args(argv[2:]);inputs=[ref(a.request),ref(__file__)]
    source_fields=('plan','manifest','probe','reader','verifier','oracle','source_proof','state_file','normal_stop','node_tool','smallfiles')
    for key in source_fields:
        value=getattr(parsed,key,None)
        if value is not None:inputs.append(ref(value))
    if action!='fresh':inputs.append(ref(parsed.asset/'asset.json'))
    if getattr(parsed,'base_asset',None) is not None:inputs.append(ref(parsed.base_asset/'asset.json'))
    plan=load(parsed.plan);cell=cell_of(plan,parsed.cell)
    if (cell['workload'],cell['arm_id'])!=('smallfiles','packed'):raise ValueError('Packed C3 cell required')
    spec=dict(schema='experiment-step-v1',reviewed=False,cell_id=cell['id'],stage=stages[action],argv=argv,
        cwd=str(REPO),executable_sha256=digest(sys.executable),inputs=inputs,inherit_environment=[])
    write_new(a.out,spec)
    print(json.dumps(dict(draft=ref(a.out),reviewed=False,stage=stages[action],
        scope='Use the existing suite review/run-stage entry and original state.sqlite/lease. No new state, claim, sample, or acceptance is created.')))


def parser():
    p=argparse.ArgumentParser(description=__doc__);sub=p.add_subparsers(dest='action',required=True)
    spec=sub.add_parser('stage-spec');spec.add_argument('--request',type=Path,required=True);spec.add_argument('--out',type=Path,required=True)
    for name in ('fresh','debt','cold-verify','qualify'):
        parser=sub.add_parser(name)
        for key in ('asset','plan','out'):parser.add_argument('--'+key,type=Path,required=True)
        parser.add_argument('--cell',required=True)
        if name in ('fresh','cold-verify','qualify'):parser.add_argument('--base-asset',type=Path,required=True)
        if name in ('debt','cold-verify','qualify'):parser.add_argument('--manifest',type=Path,required=True)
        if name in ('debt','qualify'):
            parser.add_argument('--probe',type=Path,required=True);parser.add_argument('--probe-sha256',required=True)
        if name=='debt':
            parser.add_argument('--state',choices=['Active','Quiescent'],default='Active')
        if name in ('cold-verify','qualify'):
            for key in ('reader','verifier','oracle','source','source-proof','private-object-env'):parser.add_argument('--'+key,type=Path,required=True)
            for key in ('reader','verifier','oracle'):parser.add_argument('--'+key+'-sha256',required=True)
        if name=='cold-verify':
            for key in ('state-file','normal-stop'):parser.add_argument('--'+key,type=Path,required=True)
            parser.add_argument('--normal-stop-sha256',required=True)
        if name=='qualify':
            for key in ('node-tool','smallfiles','mount-state'):parser.add_argument('--'+key,type=Path,required=True)
            for key in ('node-tool','smallfiles'):parser.add_argument('--'+key+'-sha256',required=True)
    return p


def main():
    a=parser().parse_args();{'fresh':fresh,'debt':debt,'cold-verify':cold_verify,'qualify':qualify,'stage-spec':stage_spec}[a.action](a)


if __name__=='__main__':main()
