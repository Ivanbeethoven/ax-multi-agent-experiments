#!/usr/bin/env python3
"""One pilot preparation or filesystem operation. Never retries or advances a cell."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time
import uuid
from urllib.request import ProxyHandler,build_opener

REPO=Path(__file__).resolve().parents[4]
sys.path.insert(0,str(REPO))
from bench.runtime.common import digest,identity,write_new,load,pinned
from bench.runtime.prepare import mount as draft_mount
from bench.runtime import node
from bench.runtime.observe import prometheus
from bench.suite_mount import overlay,cell_of
from bench.suite_support import review
from bench.fio_workload import run as fio


def ref(path):
    path=Path(path).resolve(strict=True)
    return dict(path=str(path),sha256=digest(path))


def filesystem_binary(asset):
    value=asset.get('filesystem_binary')
    if value is None:
        value=ref('/srv/ax-client/assets/bin/'+asset['product'])
    pinned(value)
    return value


def environment(path):
    values=dict(line.split('=',1) for line in path.read_text().splitlines())
    os.environ.update(AWS_ACCESS_KEY_ID=values['RUSTFS_ACCESS_KEY'],AWS_SECRET_ACCESS_KEY=values['RUSTFS_SECRET_KEY'],
        ACCESS_KEY=values['RUSTFS_ACCESS_KEY'],SECRET_KEY=values['RUSTFS_SECRET_KEY'],AWS_REGION='us-east-1',
        AWS_EC2_METADATA_DISABLED='true',RUST_LOG='error',NO_COLOR='1',NO_PROXY='127.0.0.1,localhost,10.20.2.163',
        no_proxy='127.0.0.1,localhost,10.20.2.163')


def request(asset,cell,root):
    return dict(kind='fio',binary=ref('/usr/bin/fio'),root=str(root),params=cell['params'],seed=asset['seed'])


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('action',choices=['asset-init','draft-mount','start','stop','fio-base','fio-base-check','copy-fio','copy-smallfiles','copy-trace','verify-smallfiles','smallfiles-base-check','trace-sync','verify-trace','trace-base-check','fio','debt','worker'])
    p.add_argument('--asset',type=Path,required=True)
    p.add_argument('--out',type=Path,required=True)
    p.add_argument('--plan',type=Path)
    p.add_argument('--cell')
    p.add_argument('--product',choices=['brewfs','juicefs'])
    p.add_argument('--filesystem-binary',type=Path,help='Exact binary for a new asset; earlier assets retain their original binary')
    p.add_argument('--filesystem-binary-sha256')
    p.add_argument('--database',type=int)
    p.add_argument('--mount-point',type=Path,help='Explicit canonical C4 session mount; asset initialization only')
    p.add_argument('--mount-run',type=Path)
    p.add_argument('--spec',type=Path)
    p.add_argument('--block-bytes',type=int,choices=[4096,1048576])
    p.add_argument('--phase',choices=['measure','verify'],default='measure')
    p.add_argument('--purpose',choices=['experiment','observer-calibration-on','observer-calibration-off'],default='experiment')
    p.add_argument('--join-slot',action='store_true')
    p.add_argument('--child-name',help='Separate fresh children for calibration; workload identity still comes from the frozen cell')
    p.add_argument('--workspace',type=int,help='Exact positive synthetic small-file child tag')
    p.add_argument('--smallfiles-copier',type=Path,help='Optional byte-checking eight-thread C3 materializer')
    p.add_argument('--smallfiles-copier-sha256')
    p.add_argument('--smallfiles-verifier',type=Path)
    p.add_argument('--smallfiles-verifier-sha256')
    p.add_argument('--smallfiles-state',type=Path)
    p.add_argument('--root-at-mount',action='store_true',help='Entity smallfiles: the fresh namespace is the private child root')
    p.add_argument('--trace-case',type=Path)
    p.add_argument('--trace-manifest',type=Path)
    p.add_argument('--replay-binary',type=Path)
    p.add_argument('--replay-result',type=Path)
    p.add_argument('--trace-verifier',type=Path)
    p.add_argument('--trace-verifier-sha256')
    p.add_argument('--cache-parent-fds',action='store_true')
    a=p.parse_args()
    if bool(a.filesystem_binary)!=bool(a.filesystem_binary_sha256) or (a.filesystem_binary and a.action!='asset-init'):
        p.error('filesystem-binary and its SHA256 are only valid together for asset-init')
    environment(Path('/srv/ax-client/private/rustfs.env'))
    if a.join_slot:
        Path('/sys/fs/cgroup/ax-slot-1/cgroup.procs').write_text(str(os.getpid()))
    if a.action=='asset-init':
        if a.product is None or a.database is None or not 20<=a.database<256:
            raise ValueError('Explicit product and unused pilot database 20..255 required')
        binary=(dict(path=str(a.filesystem_binary),sha256=a.filesystem_binary_sha256) if a.filesystem_binary
                else ref('/srv/ax-client/assets/bin/'+a.product))
        pinned(binary)
        a.asset.mkdir(parents=True,exist_ok=False)
        redis='redis://10.20.2.162:6379/'+str(a.database)
        if subprocess.check_output(['redis-cli','-u',redis,'DBSIZE'],text=True).strip()!='0':
            raise ValueError('Never overwrite metadata')
        plan=load(a.plan)
        cell=cell_of(plan,a.cell) if a.cell else next(c for c in plan['cells'] if c['workload']=='fio' and c['arm']['product']==a.product)
        workspace_handoff=(cell['workload']=='handoff' and cell['arm']['engine']=='workspace-native-v2')
        if cell['arm']['product']!=a.product or (cell['arm_id'] not in ('native','juicefs') and
                not workspace_handoff and not (cell['workload']=='handoff' and a.product=='juicefs')):
            raise ValueError('Only flat routes and live C4 handoff workspace initialization are supported')
        if a.mount_point and (cell['workload']!='handoff' or not a.mount_point.is_absolute()):
            raise ValueError('Explicit canonical mount point is for C4 handoff only')
        bucket='axp-'+uuid.uuid4().hex[:16]
        import boto3
        from botocore.config import Config
        client=boto3.client('s3',endpoint_url='http://10.20.2.163:9000',region_name='us-east-1',
            config=Config(signature_version='s3v4',retries={'max_attempts':0},s3={'addressing_style':'path'}))
        client.create_bucket(Bucket=bucket);client.close()
        config=overlay(dict(meta={'backend':'redis','redis':{'url':redis}},
            data={'backend':'s3','s3':dict(bucket=bucket,endpoint='http://10.20.2.163:9000',region='us-east-1',force_path_style=True)},
            layout={'chunk_size':67108864,'block_size':4194304},fuse={'workers':4,'max_background':512}),cell['client_settings'])
        if workspace_handoff:
            catalog='ax-pilot-session-'+uuid.uuid4().hex
            data_config=a.asset/'data-config.json';write_new(data_config,dict(data=config['data']))
            prefix=[binary['path'],'workspace','--meta-backend','redis',
                    '--meta-url',redis,'--workspace-namespace',catalog]
            initialized=None
            for label,argv in [('init-volume',prefix+['init-volume','--data-config',str(data_config)]),
                               ('init-native',prefix+['init-native'])]:
                with (a.asset/(label+'.stdout')).open('xb') as out,(a.asset/(label+'.stderr')).open('xb') as err:
                    result=subprocess.run(argv,stdout=out,stderr=err)
                write_new(a.asset/(label+'.exit.json'),dict(exit_code=result.returncode,automatic_retry=False))
                result.check_returncode()
                if label=='init-volume':initialized=load(a.asset/(label+'.stdout'))
            config.update(workspace=initialized['workspace_id'],workspace_namespace=catalog,
                native_base=load(Path(__file__).resolve().parents[1]/'profiles/native-base-p1.json'))
            config['volume'].update(chunk_size=67108864,block_size=4194304)
        if a.product=='juicefs':
            config=dict(cell['client_settings'],metadata_url=redis)
            with (a.asset/'format.stdout').open('xb') as out,(a.asset/'format.stderr').open('xb') as err:
                r=subprocess.run([binary['path'],'format','--storage','s3','--bucket',
                    'http://10.20.2.163:9000/'+bucket,'--block-size','4M','--compress','none','--trash-days','0',redis,'ax-pilot-'+uuid.uuid4().hex[:12]],stdout=out,stderr=err)
            write_new(a.asset/'format-exit.json',dict(exit_code=r.returncode));r.check_returncode()
        point=a.mount_point or a.asset/'mnt'
        point.mkdir(parents=True,exist_ok=a.mount_point is not None)
        if point.is_symlink() or point.resolve()!=point or os.path.ismount(point) or any(point.iterdir()):
            raise ValueError('Canonical unmounted empty directory required')
        value=dict(product=a.product,filesystem_binary=binary,bucket=bucket,database=a.database,seed=plan['config']['seed'],
            config=config,mount_point=str(point),plan=ref(a.plan),source=cell['workload']+' base on original shared filesystem',
            created_at=datetime.now(timezone.utc).isoformat())
        write_new(a.asset/'asset.json',value)
        print(json.dumps(dict(passed=True,product=a.product,bucket=bucket)))
        return
    asset=load(a.asset/'asset.json')
    if a.action=='draft-mount':
        plan=load(pinned(asset['plan']));cell=cell_of(plan,a.cell)
        if cell['arm']['product']!=asset['product']:raise ValueError('Product mismatch')
        a.out.mkdir(parents=True,exist_ok=False)
        cache=a.out/'cache';cache.mkdir()
        config=overlay(asset['config'],cell['client_settings'])
        if asset['product']=='brewfs':config['cache']['cache_root']=str(cache)
        config_path=a.out/'native-config.json';write_new(config_path,config)
        value=draft_mount(ref(config_path),filesystem_binary(asset),asset['product'],'pool-a',
            asset['mount_point'],str(REPO),a.out/'specs',
            ['AWS_ACCESS_KEY_ID','AWS_SECRET_ACCESS_KEY','AWS_REGION','AWS_EC2_METADATA_DISABLED','RUST_LOG','NO_COLOR','NO_PROXY','no_proxy'] if asset['product']=='brewfs' else [],
            ['fuse','fuse.brewfs','fuse.juicefs'],
            '127.0.0.1:'+('8910' if asset['product']=='brewfs' else '9567'),
            'experiment' if a.purpose=='observer-calibration-off' else a.purpose,
            'auto','redis',dict(plan=asset['plan'],cell_id=a.cell))
        value['purpose']=a.purpose
        if a.purpose.startswith('observer-calibration-'):
            value['calibration_scope']='Native client counters/endpoints and RustFS OTLP sender remain enabled in both arms. Only external observers/scrapes and the OTLP collector are toggled.'
        command=a.out/'specs/command.json'
        approved=review(command,'pilot-operator','Reviewed fixed private endpoints, exact suite settings, fresh cache and credential environment names.')
        write_new(a.out/'command.reviewed.json',approved)
        value['command']=ref(a.out/'command.reviewed.json')
        write_new(a.out/'mount.bound.draft.json',value)
        write_new(a.out/'mount.reviewed.json',review(a.out/'mount.bound.draft.json','pilot-operator','Reviewed actual source binary and bound native command, suite identity, empty canonical mount and 2 CPU / 8 GiB slot.'))
        print(json.dumps(ref(a.out/'mount.reviewed.json')))
        return
    if a.action=='start':
        value=node.start(ref(a.spec),a.out)
        print(json.dumps(value));return
    if a.action=='stop':
        value=node.control(a.mount_run,'stop');write_new(a.out,value);print(json.dumps(value));return
    if a.action in ('fio-base','fio-base-check'):
        cell=next(c for c in load(pinned(asset['plan']))['cells'] if c['workload']=='fio' and c['params']['block_bytes']==a.block_bytes)
        root=Path(asset['mount_point'])/('base-'+str(a.block_bytes))
        if a.action=='fio-base':root.mkdir();phase='prepare'
        else:phase='verify'
        value=fio(request(asset,cell,root),phase,a.out)
        print(json.dumps(dict(passed=value['passed'],phase=phase,application_bytes=value['application_bytes'])));return
    cell=cell_of(load(pinned(asset['plan'])),a.cell)
    child_name=a.child_name or a.cell
    if not re.fullmatch(r'[A-Za-z0-9_.-]{1,96}',child_name) or child_name in ('.','..'):
        raise ValueError('Simple dedicated child name required')
    root=Path(asset['mount_point'])/child_name
    if a.root_at_mount:
        if cell['workload']!='smallfiles' or cell['arm_id'] not in ('packed','entity-native'):
            raise ValueError('Only fresh entity small-file namespaces use the mount root')
        root=Path(asset['mount_point'])
    if a.action in ('copy-fio','copy-smallfiles','copy-trace'):
        if root.exists():raise ValueError('Fresh private child required')
        if a.action=='copy-smallfiles' and (cell['workload']!='smallfiles' or cell['arm_id'] not in ('native','juicefs')):
            raise ValueError('Copy/clone a C3 flat native or JuiceFS base only')
        if a.action=='copy-trace' and (cell['workload']!='trace' or cell['arm_id'] not in ('native','juicefs')):
            raise ValueError('Copy/clone a qualified flat C2 base only')
        source=Path(asset['mount_point'])/('base' if a.action in ('copy-smallfiles','copy-trace') else 'base-'+str(cell['params']['block_bytes']))
        a.out.mkdir(parents=True,exist_ok=False);begin=time.monotonic()
        argv=([filesystem_binary(asset)['path'],'clone','-p',str(source),str(root)] if asset['product']=='juicefs'
              else ['/usr/bin/cp','-a','--reflink=never','--',str(source),str(root)])
        copier=None
        if a.smallfiles_copier:
            if a.action!='copy-smallfiles' or asset['product']!='brewfs' or digest(a.smallfiles_copier)!=a.smallfiles_copier_sha256:
                raise ValueError('Pinned native C3 materializer only')
            copier=ref(a.smallfiles_copier)
            argv=[str(a.smallfiles_copier),str(source),str(root),str(a.out/'materialize.json'),str(cell['params']['files']),'8']
        with (a.out/'stdout.log').open('xb') as out,(a.out/'stderr.log').open('xb') as err:
            result=subprocess.run(argv,stdout=out,stderr=err)
        if result.returncode==0:
            if copier:
                proof=load(a.out/'materialize.json')
                if not proof['passed'] or proof['files']!=cell['params']['files'] or not proof['fsync_each_file'] or proof['workers']!=8:
                    raise ValueError('Incomplete C3 materialization')
            elif a.action in ('copy-smallfiles','copy-trace'):
                from pilot_io import fsync_import
                count=fsync_import(root,allow_symlinks=a.action=='copy-trace')
                if a.action=='copy-smallfiles' and count!=cell['params']['files']:raise ValueError('Copied child file count differs')
            else:
                for path in sorted(root.iterdir()):
                    with path.open('rb') as f:os.fsync(f.fileno())
                fd=os.open(root,os.O_DIRECTORY|os.O_RDONLY)
                try:os.fsync(fd)
                finally:os.close(fd)
        write_new(a.out/'result.json',dict(passed=result.returncode==0,argv=argv,exit_code=result.returncode,wall_seconds=time.monotonic()-begin,
            source=str(source),child=str(root),copier=copier,method='native-clone' if asset['product']=='juicefs' else 'full-independent-copy'))
        result.check_returncode();return
    if a.action in ('verify-smallfiles','smallfiles-base-check'):
        if cell['workload']!='smallfiles' or not a.smallfiles_verifier or digest(a.smallfiles_verifier)!=a.smallfiles_verifier_sha256:
            raise ValueError('Pinned complete C3 verifier required')
        if a.workspace is None or a.workspace<=0:raise ValueError('Explicit small-file workspace required')
        state='base' if a.action=='smallfiles-base-check' else a.smallfiles_state
        if state is None:raise ValueError('Original small-file touched state required')
        checked_root=Path(asset['mount_point'])/'base' if a.action=='smallfiles-base-check' else root
        a.out.mkdir(parents=True,exist_ok=False)
        subprocess.run([str(a.smallfiles_verifier),str(checked_root),str(state),str(a.out/'result.json'),
            str(cell['params']['files']),'8',str(a.workspace)],check=True)
        value=load(a.out/'result.json')
        if not value['passed'] or value['files']!=cell['params']['files'] or value['bytes_checked']!=cell['params']['files']*4096:
            raise ValueError('Incomplete original C3 cold readback')
        return
    if a.action in ('trace-sync','verify-trace','trace-base-check'):
        if cell['workload']!='trace' or cell['arm_id'] not in ('native','juicefs'):
            raise ValueError('Flat C2 route required')
        if a.action=='trace-sync':
            from pilot_io import fsync_import
            a.out.mkdir(parents=True,exist_ok=False);begin=time.monotonic()
            files=fsync_import(root,allow_symlinks=True)
            write_new(a.out/'result.json',dict(passed=True,files=files,wall_seconds=time.monotonic()-begin,
                scope='Extra final-file durability barrier outside replay timing'));return
        if not a.trace_case:raise ValueError('Exact trace case required')
        if a.action=='trace-base-check':
            sys.path.insert(0,str(REPO/'scripts'))
            from full_trace_common import tree
            if tree(Path(asset['mount_point'])/'base')!=load(a.trace_case/'initial-tree.json'):
                raise ValueError('Immutable trace parent changed')
            a.out.mkdir(parents=True,exist_ok=False)
            write_new(a.out/'result.json',dict(passed=True,expected=ref(a.trace_case/'initial-tree.json')));return
        if not a.trace_verifier or digest(a.trace_verifier)!=a.trace_verifier_sha256:
            raise ValueError('Pinned trace verifier required')
        if not a.replay_binary or not a.replay_result:raise ValueError('Original performance report and binary required')
        subprocess.run([sys.executable,str(a.trace_verifier),'--case',str(a.trace_case),'--binary',str(a.replay_binary),
            '--root',str(root),'--out',str(a.out),'--verify-only','--performance-readback',
            '--replay-result',str(a.replay_result),*(['--cache-parent-fds'] if a.cache_parent_fds else [])],check=True)
        verified=load(a.out/'verification.json')
        verified['verification_source']=ref(a.out/'verification.json')
        write_new(a.out/'result.json',verified);return
    if a.action=='fio':
        value=fio(request(asset,cell,root),a.phase,a.out)
        print(json.dumps(dict(passed=value['passed'],phase=a.phase,application_bytes=value['application_bytes'])));return
    if a.action=='worker':
        from bench.runtime.workloads import worker
        a.out.mkdir(parents=True,exist_ok=False)
        if cell['workload']=='smallfiles':
            if a.workspace is None or a.workspace<=0:raise ValueError('Explicit positive small-file workspace required')
            s=dict(kind='smallfiles',binary=ref('/srv/ax-client/assets/bin/smallfiles'),root=str(root),
                   params=cell['params'],seed=asset['seed'],workspace=a.workspace)
        elif cell['workload']=='fio':s=request(asset,cell,root)
        elif cell['workload']=='trace':
            if not all((a.trace_case,a.trace_manifest,a.replay_binary)):raise ValueError('Pinned full trace, manifest and replay binary required')
            if a.trace_case.name!=cell['params']['trace'] or cell['params']['agents']!=1 or cell['params']['tasks_per_agent']!=1 or cell['params']['pacing']!='off' or cell['params']['replay']!='complete':
                raise ValueError('This pilot adapter uses one complete, unpaced trace from the frozen case')
            s=dict(kind='trace',binary=ref(a.replay_binary),root=str(root),case=str(a.trace_case),manifest=ref(a.trace_manifest),
                   params=cell['params'],seed=asset['seed'],cache_parent_fds=a.cache_parent_fds)
        else:raise ValueError('This worker adapter accepts fio, smallfiles or trace only')
        s.update(worker_id='a01',workload_id=cell['case'],cwd=str(REPO),purpose=a.purpose,
            python=ref(sys.executable),helper=ref(REPO/'bench/fio_workload.py'),io_helper=ref(REPO/'bench/experiment_io.py'),
            mount=dict(spec=ref(a.spec),directory=str(a.mount_run)))
        write_new(a.out/'request.json',s)
        if cell['workload']=='trace':
            from bench.runtime.prepare import trace_worker
            value=trace_worker(a.out/'request.json',a.out/'draft')
        else:value=worker(a.out/'request.json',a.out/'draft')
        command=review(a.out/'draft/step.json','pilot-operator','Exact fixed-work '+cell['workload']+' request bound to this mount, native helper and suite workload parameters.')
        write_new(a.out/'step.reviewed.json',command)
        value['jobs'][0]['step']=ref(a.out/'step.reviewed.json')
        write_new(a.out/'worker.bound.draft.json',value)
        write_new(a.out/'worker.reviewed.json',review(a.out/'worker.bound.draft.json','pilot-operator',
            'One actual cgroup-isolated worker, pinned fixed work and original supervised mount; no replay retry or deadline.'))
        print(json.dumps(ref(a.out/'worker.reviewed.json')));return
    if a.action=='debt':
        a.out.mkdir(parents=True,exist_ok=False)
        samples=[];opener=build_opener(ProxyHandler({}))
        for i in range(2):
            raw=(Path(asset['mount_point'])/'.stats').read_text() if asset['product']=='brewfs' else opener.open('http://127.0.0.1:9567/metrics',timeout=5).read().decode()
            (a.out/f'native-{i}.prom').write_text(raw)
            values=prometheus(raw)
            if asset['product']=='brewfs':
                byte_names=['brewfs_writeback_live_dirty_bytes','brewfs_writeback_recent_pending_upload_bytes',
                    'brewfs_writeback_stage_inflight_bytes','brewfs_writeback_remote_upload_inflight_bytes']
                op_names=['brewfs_writeback_live_slices','brewfs_writeback_recent_pending_upload_slices','brewfs_writeback_metadata_inflight']
            else:
                byte_names=['juicefs_staging_block_bytes'];op_names=['juicefs_staging_blocks','juicefs_staging_writing_blocks']
            selected={k:v for k,v in values.items() if k.split('{',1)[0] in byte_names+op_names}
            if {k.split('{',1)[0] for k in selected}!=set(byte_names+op_names):raise ValueError('Required native debt gauge missing; never substitute zero')
            if any(v!=0 for v in selected.values()):raise ValueError('Native debt remains; preserve this observation')
            value=dict(passed=True,cell_id=a.cell,at=datetime.now(timezone.utc).isoformat(),monotonic_ns=time.monotonic_ns(),
                pending_upload_bytes=int(sum(v for k,v in selected.items() if k.split('{',1)[0] in byte_names)),
                pending_upload_operations=int(sum(v for k,v in selected.items() if k.split('{',1)[0] in op_names)),native_gauges=selected,
                interpretation='Overlapping gauges summed only as a zero predicate; never an operation or throughput count. All application writers must have returned from fsync and exited.')
            write_new(a.out/f'sample-{i}.json',value);samples.append(value)
            if i==0:time.sleep(1.05)
        write_new(a.out/'result.json',dict(passed=True,cell_id=a.cell,samples=[ref(a.out/f'sample-{i}.json') for i in range(2)]))


if __name__=='__main__':
    main()
