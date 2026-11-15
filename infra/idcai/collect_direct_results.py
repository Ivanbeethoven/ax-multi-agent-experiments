"""Collect a direct campaign through one SSH entry and compare file counts and lengths."""
import argparse
import json
from pathlib import Path, PurePosixPath
import subprocess
import tarfile

REMOTE = r'''
import io,json,os,sys,tarfile
from pathlib import Path
sys.path.insert(0,'/srv/ax-cluster/direct/scripts')
import cluster_lib as cl
for host in cl.ALL_HOST_NAMES:
    try: cl.collector_rpc(host,{'action':'flush'})
    except Exception: pass
    if host=='A366': continue
    script="""import io,sys,tarfile
from pathlib import Path
p=Path(sys.argv[1]); out=tarfile.open(fileobj=sys.stdout.buffer,mode='w|')
if p.exists():
    for f in sorted(p.rglob('*')):
        if f.is_file() and not f.is_symlink():out.add(f,arcname=f.relative_to(p).as_posix(),recursive=False)
out.close()
"""
    p=cl.run_on_host(host,['python3','-c',script,str(cl.RUN_ROOT/'hosts'/host)],log_cmd=False,timeout=300)
    target=cl.RUN_ROOT/'hosts'/host
    target.mkdir(parents=True,exist_ok=True)
    tarfile.open(fileobj=io.BytesIO(p.stdout),mode='r:').extractall(target,filter='data')
files=[]
excluded={'cache','scratch','workspaces','input','data','private'}
active_mounts={line.split()[1] for line in Path('/proc/mounts').read_text().splitlines()}
with tarfile.open(fileobj=sys.stdout.buffer,mode='w|') as archive:
    for directory,dirs,names in os.walk(cl.RUN_ROOT,followlinks=False):
        dirs[:]=[d for d in dirs if d not in excluded and str(Path(directory)/d) not in active_mounts]
        for name in sorted(names):
            p=Path(directory)/name
            if p.is_symlink() or not p.is_file() or name in ('result-files.json','result-transfer.tar'):continue
            rel=p.relative_to(cl.RUN_ROOT).as_posix()
            data=p.read_bytes()
            member=tarfile.TarInfo(rel);member.size=len(data);member.mtime=int(p.stat().st_mtime)
            archive.addfile(member,io.BytesIO(data))
            files.append({'path':rel,'bytes':len(data)})
    manifest={'remote_root':str(cl.RUN_ROOT),'files':files,'count':len(files),'bytes':sum(f['bytes'] for f in files)}
    data=(json.dumps(manifest,indent=2)+'\n').encode()
    member=tarfile.TarInfo('result-files.json');member.size=len(data)
    archive.addfile(member,io.BytesIO(data))
'''


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--out',type=Path,required=True)
    ap.add_argument('--host',default='idcai-60001')
    opts=ap.parse_args()
    opts.out.mkdir(parents=True,exist_ok=True)
    output=opts.out.resolve()
    bundle=output/'result-transfer.tar'
    with bundle.open('wb') as fh:
        result=subprocess.run(['ssh','-T','-o','BatchMode=yes',opts.host,
                               '/srv/ax-idcai-20261113/venv/bin/python3','-'],
                              input=REMOTE.encode(),stdout=fh,stderr=subprocess.PIPE,timeout=900)
    if result.returncode:
        raise RuntimeError(result.stderr.decode(errors='replace'))
    with tarfile.open(bundle,'r:') as archive:
        for member in archive.getmembers():
            relative=PurePosixPath(member.name)
            if relative.is_absolute() or '..' in relative.parts or not member.isfile():
                raise ValueError('Unexpected archive entry: '+member.name)
            destination=output.joinpath(*relative.parts).resolve()
            if not destination.is_relative_to(output):raise ValueError('Archive path outside output')
        archive.extractall(output,filter='data')
    manifest=json.loads((output/'result-files.json').read_text(encoding='utf-8'))
    for record in manifest['files']:
        path=output/record['path']
        if not path.is_file() or path.stat().st_size!=record['bytes']:
            raise RuntimeError('File length differs: '+record['path'])
        if path.suffix=='.json':json.loads(path.read_text(encoding='utf-8'))
    report={'files':manifest['count'],'bytes':manifest['bytes'],'local_root':str(output),'passed':True}
    (output/'local-transfer.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(report))


if __name__=='__main__':main()
