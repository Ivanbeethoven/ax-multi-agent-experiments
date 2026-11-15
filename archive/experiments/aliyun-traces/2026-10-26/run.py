"""Bounded AI-trace performance pilot. Timed I/O is the Rust replay binary."""
import collections
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import time
import urllib.request

sys.path.insert(0, '/data/bin')
import pilot as p

OUT=p.OUT
CASE=p.CASE
status={'scope':'single-host AI trace performance pilot','trace':'pytest-full-02',
        'correctness_certified':False,'filter':'none; all xattr and ioctl operations executed',
        'methods':{},'started_utc':time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime())}
transfer=json.loads(Path('/data/transfer.local.json').read_text())

def upload(key, data, ctype):
    req=urllib.request.Request(transfer[key],data=data,method='PUT',headers={'Content-Type':ctype})
    with urllib.request.urlopen(req,timeout=60) as response:response.read()

def checkpoint():
    p.save('ai-status',status)
    payload={}
    for path in OUT.glob('*.json'):
        if path.stat().st_size<2_000_000:
            try:payload[path.name]=json.loads(path.read_text())
            except json.JSONDecodeError:pass
    try:upload('checkpoint',json.dumps(payload).encode(),'application/json')
    except Exception as exc:p.save('upload-error',{'type':type(exc).__name__})

def archive():
    path=Path('/data/results.tar.gz')
    with tarfile.open(path,'w:gz') as tar:
        for parent in (OUT,p.LOG):
            for file in parent.rglob('*'):
                # Service logs can contain credentials or transfer URLs; never export them.
                allowed=file.suffix in {'.json','.jsonl'} or file.name.endswith(('.stdout.log','.stderr.log','-mount.log'))
                if file.is_file() and allowed:
                    tar.add(file,arcname=file.relative_to('/').as_posix())
        for name in ('run.py','observer.py'):
            tar.add(p.BIN/name,arcname='code/'+name)
    upload('archive',path.read_bytes(),'application/gzip')

def preflight():
    initial=(CASE/'initial').resolve()
    for path in initial.rglob('*'):
        if path.is_symlink() and not path.resolve().is_relative_to(initial):
            raise RuntimeError('Initial symlink escapes dedicated root')
    counts=collections.Counter();expected=1
    with (CASE/'capture/events.jsonl').open() as stream:
        for line in stream:
            e=json.loads(line)
            assert e['seq']==expected;expected+=1;counts[e['op']]+=1
            args=e['args']
            if args and isinstance(args[0],str):
                assert args[0].startswith('/') and '..' not in args[0].split('/')
            if e['op']=='symlink':
                target=args[1]
                assert not target.startswith('/'), 'Absolute symlink not allowed in trusted fast mode'
                resolved=(initial/args[0].lstrip('/')).parent.joinpath(target).resolve()
                assert resolved.is_relative_to(initial)
    metrics=json.loads((CASE/'metrics.json').read_text())
    assert expected-1==metrics['callbacks']==1747109
    p.save('input-profile',{'callbacks':expected-1,'by_operation':counts,'filtered_operations':0,
                           'read_bytes':metrics['actual_read_bytes'],'write_bytes':metrics['actual_write_bytes'],
                           'trusted_private_root_preflight':True})

def copy_initial(name,root):
    assert not root.exists()
    return p.run(name+'-prepare',['cp','-a','--reflink=never',CASE/'initial',root],timeout=600)

def cold_mount(name,cfg):
    target=Path('/mnt')/name
    p.run(name+'-unmount',['fusermount3','-u',target],timeout=30)
    cache=Path('/data/cache')/name
    assert cache.resolve().parent==Path('/data/cache')
    if cache.exists():shutil.rmtree(cache)
    subprocess.run(['sync'],check=True)
    Path('/proc/sys/vm/drop_caches').write_text('3\n')
    return p.mount(name,cfg)

def measure(name,root,strict=False):
    row=status['methods'].setdefault(name,{})
    row['phase']='replay';checkpoint()
    before=p.redis_stats()
    obs=p.observer(name+'-ai-trace')
    output=OUT/(name+'-replay.json')
    argv=[p.BIN/'full-fuse-replay',CASE/'capture/events.jsonl',CASE/'capture/blobs',root,output]
    if not strict:argv+=['--performance']
    try:result=p.run(name+'-replay',argv,timeout=1500,required=False)
    finally:row['observer']=p.finish_observer(*obs)
    row['command']=result;row['redis_before']=before;row['redis_after']=p.redis_stats()
    row['phase']='finished'
    if output.exists() and output.stat().st_size:
        replay=json.loads(output.read_text());row['replay']=replay
        m=json.loads((CASE/'metrics.json').read_text())
        row['completed_workload']=bool(replay.get('completed') and result['exit_code']==0
            and replay.get('operations')==m['callbacks'] and replay.get('remaining_handles')==0
            and all(replay.get(k)==m[k] for k in ('actual_read_bytes','actual_write_bytes')))
    else:row['completed_workload']=False
    checkpoint();archive()

def brew_method(name,db,bucket,ns,packed=False):
    status['methods'][name]={'phase':'preparing'};checkpoint()
    if packed:
        source=Path('/data/pack-source');source.mkdir()
        copy_initial(name+'-local',source/'workload')
        key=Path('/data/packed-manifest-key.txt')
        p.run('packed-build-upload',[p.BIN/'packed_v3_snapshot_fixture','--source-directory',source,
          '--source-consistency','best-effort-detected','--source-hardlink-policy','reject-external',
          '--metadata-codec','raw','--data-codec','raw','--inline-data','off','--bucket',bucket,
          '--endpoint','http://127.0.0.1:9000','--region','us-east-1','--force-path-style','true','--manifest-output',key],timeout=900)
        cfg=p.config(name,db,bucket,namespace=ns)
        p.run('packed-init',p.ws(db,ns,'init-packed-volume','--manifest-key',key.read_text().strip(),'--data-config',cfg))
        wid=p.workspace_id(p.stdout_json('packed-init'))
    elif name=='native-overlay':
        p.run(name+'-init',p.ws(db,ns,'init-volume'));wid=p.workspace_id(p.stdout_json(name+'-init'))
    else:wid=None
    cfg=p.config(name,db,bucket,wid,'workspace-v1' if wid else 'flat-v1',ns)
    target=p.mount(name,cfg)
    if not packed:copy_initial(name,target/'workload')
    target=cold_mount(name,cfg)
    measure(name,target/'workload')
    p.run(name+'-final-unmount',['fusermount3','-u',target],timeout=30,required=False)

def juicefs():
    name='juicefs';status['methods'][name]={'phase':'preparing'};checkpoint()
    p.ENV.update(ACCESS_KEY=p.secrets['RUSTFS_ACCESS_KEY'],SECRET_KEY=p.secrets['RUSTFS_SECRET_KEY'])
    p.run('juicefs-format',[p.BIN/'juicefs','format','--no-update','--storage','s3',
        '--bucket','http://127.0.0.1:9000/ax-jfs','--trash-days','0','--compress','none',
        'redis://127.0.0.1:6380/1','axjfs'])
    target=Path('/mnt/juicefs');target.mkdir(exist_ok=True)
    def mount():
        p.run('juicefs-mount',[p.BIN/'juicefs','mount','-d','--enable-xattr','--enable-cap','--metrics','127.0.0.1:9567',
            '--cache-dir','/data/cache/juicefs','--cache-size','512','--buffer-size','512',
            '--attr-cache','0','--entry-cache','0','--dir-entry-cache','0','redis://127.0.0.1:6380/1',target])
    mount();copy_initial(name,target/'workload')
    p.run('juicefs-unmount',['fusermount3','-u',target],timeout=30)
    shutil.rmtree('/data/cache/juicefs',ignore_errors=True)
    subprocess.run(['sync'],check=True);Path('/proc/sys/vm/drop_caches').write_text('3\n');mount()
    measure(name,target/'workload')

def main():
    preflight();checkpoint()
    for name,strict in [('xfs-strict',True),('xfs-performance',False)]:
        try:
            root=Path('/data')/name/'workload';root.parent.mkdir();copy_initial(name,root)
            subprocess.run(['sync'],check=True);Path('/proc/sys/vm/drop_caches').write_text('3\n')
            measure(name,root,strict)
        except Exception as exc:
            status['methods'].setdefault(name,{})['error']=str(exc);checkpoint()
    methods=[('native',lambda:brew_method('native',0,'ax-native','native')),
             ('native-overlay',lambda:brew_method('native-overlay',2,'ax-overlay','overlay')),
             ('native-overlay-pack',lambda:brew_method('native-overlay-pack',3,'ax-packed','packed',True)),
             ('juicefs',juicefs)]
    for name,fn in methods:
        try:fn()
        except Exception as exc:
            status['methods'].setdefault(name,{})['error']=str(exc);checkpoint();archive()
    status['finished_utc']=time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime());checkpoint();archive()
    Path('/data/DONE').write_text('Results uploaded; safe to release this pilot instance.\n')

if __name__=='__main__':
    try:main()
    except Exception as exc:
        status['fatal_error']=str(exc);checkpoint();archive();raise
