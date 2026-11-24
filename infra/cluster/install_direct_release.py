"""Distribute a completed release over the coordinator's existing SSH connections."""
import argparse
import json
from pathlib import Path
import shutil
import subprocess
import sys

sys.path.insert(0,'/srv/ax-cluster/direct/scripts')
import cluster_lib as cl


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--release',type=Path,required=True)
    opts=ap.parse_args()
    reports={name:json.loads((opts.release/name).read_text()) for name in ('build.json','admin-build.json')}
    if not all(record.get('passed') for record in reports.values()):
        raise RuntimeError('Both builds must finish successfully')
    names=['graftfs','packed_snapshot_fixture','ax-graftfs-admin']
    binaries=[]
    for name in names:
        src=opts.release/name
        if not src.is_file():raise FileNotFoundError(src)
        target=cl.BIN_DIR/name
        if target.is_symlink():raise RuntimeError('Release target must be an independent file')
        shutil.copy2(src,target)
        binaries.append({'name':name,'bytes':target.stat().st_size})
    receipt={}
    for host in cl.HOSTS:
        if host['name']!='node-coord':
            subprocess.run(['scp',*cl.ssh_options(host),*[str(cl.BIN_DIR/n) for n in names],
                            f"root@{host['ip']}:{cl.BIN_DIR}/"],check=True,timeout=300)
        script='''import json,subprocess
from pathlib import Path
root=Path('/srv/ax-cluster/direct/bin')
names=['graftfs','packed_snapshot_fixture','ax-graftfs-admin','juicefs','juicefs-overlay','full-fuse-replay']
out={name:{'bytes':(root/name).stat().st_size} for name in names}
for name in ('graftfs','juicefs','juicefs-overlay'):
    p=subprocess.run([str(root/name),'--version'],capture_output=True,text=True)
    out[name]['version_output']=(p.stdout+p.stderr).strip();out[name]['version_returncode']=p.returncode
print(json.dumps(out))'''
        process=cl.run_on_host(host['name'],['python3','-c',script],log_cmd=False)
        record=json.loads(process.stdout)
        for binary in binaries:
            if record[binary['name']]['bytes']!=binary['bytes']:
                raise RuntimeError('Binary size differs on '+host['name'])
        receipt[host['name']]=record
        print(json.dumps({'host':host['name'],'binaries_installed':len(names)}),flush=True)
    result={'graftfs_build':reports['build.json'],'admin_build':reports['admin-build.json'],
            'rustfs':{'branch':'feat/dynamic-block-size-rebased','source_commit':'f4ed202508f2153afb7956bae6f7799b60eccd00'},
            'juicefs_overlay':{'branch':'codex/lazy-workspace-fork-20261113','source_commit':'5708225eb6904338b5e2290cba59ee11f55335bc'},
            'hosts':receipt}
    (cl.RUN_ROOT/'versions.json').write_text(json.dumps(result,indent=2)+'\n')
    for name in ('build.log','admin-build.log'):
        target=cl.RUN_ROOT/'build'/name;target.parent.mkdir(parents=True,exist_ok=True)
        shutil.copy2(opts.release/name,target)


if __name__=='__main__':main()
