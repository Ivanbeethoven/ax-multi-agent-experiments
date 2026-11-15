"""Build the workspace administration tool against a selected BrewFS checkout."""
import argparse
import fcntl
import json
import os
from pathlib import Path
import shutil
import subprocess
import time


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--source',type=Path,required=True)
    ap.add_argument('--template',type=Path,required=True)
    ap.add_argument('--out',type=Path,required=True)
    opts=ap.parse_args()
    locks=[]
    for name in ('build.lock','measurement.lock'):
        stream=(Path('/srv/ax-idcai-20261113')/name).open('a+')
        fcntl.flock(stream,fcntl.LOCK_EX)
        locks.append(stream)
    opts.out.mkdir(parents=True,exist_ok=True)
    work=opts.out/'admin-source'
    work.mkdir(exist_ok=True)
    shutil.copytree(opts.template/'src',work/'src',dirs_exist_ok=True)
    source=opts.template/'Cargo.toml'
    lines=source.read_text().splitlines()
    lines=[('brewfs = { path = '+json.dumps(str(opts.source.resolve()))+', features = ["workspace", "observability", "rustfs-ec-block-size-hint"] }') if l.startswith('brewfs =') else l for l in lines]
    source_manifest=(opts.source/'Cargo.toml').read_text()
    if '[profile.release]' in source_manifest:
        release_section=source_manifest.split('[profile.release]',1)[1].split('\n[',1)[0]
        lines.extend(['','[profile.release]',release_section])
    (work/'Cargo.toml').write_text('\n'.join(lines)+'\n')
    lock=opts.source/'Cargo.lock'
    if lock.exists():shutil.copy2(lock,work/'Cargo.lock')
    toolchain=Path('/srv/ax-idcai-20261113/toolchain-1.99.0/bin')
    env={**os.environ,'PATH':str(toolchain)+os.pathsep+os.environ.get('PATH',''),
         'RUSTC':str(toolchain/'rustc'),'CARGO_HOME':'/srv/ax-idcai-20261113/cargo-build',
         'CARGO_TARGET_DIR':'/srv/ax-cluster/target','CARGO_BUILD_JOBS':'8',
         'CARGO_INCREMENTAL':'0','RUSTFLAGS':'-C target-cpu=x86-64'}
    start=time.monotonic()
    with (opts.out/'admin-build.log').open('w') as log:
        subprocess.run([str(toolchain/'cargo'),'build','--release','--offline','-j','8'],
                       cwd=work,env=env,stdout=log,stderr=subprocess.STDOUT,check=True)
    binary=opts.out/'ax-brewfs-admin'
    shutil.copy2('/srv/ax-cluster/target/release/ax-brewfs-admin',binary)
    report={'passed':True,'source':str(opts.source),'binary':str(binary),
            'bytes':binary.stat().st_size,'seconds':time.monotonic()-start}
    (opts.out/'admin-build.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps(report),flush=True)


if __name__=='__main__':main()
