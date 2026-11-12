#!/usr/bin/env python3
"""Read the existing 100k Packed base through a fresh workspace-v1 mount in pool A."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import socket
import subprocess
import time
import uuid


CLIENT = Path('/srv/ax-client')
ASSET = CLIENT/'a/parallel-c3-packed-base-01/asset.json'
BREWFS = CLIENT/'build-ca07159-01/bin/brewfs'
ARTIFACTS = {
    ASSET: 'c344b42810042f2b64eb243a436e6a129dd0e59d07e43242a4e04d33bdaf6f45',
    BREWFS: '814499855a949cad7a8752c04f272fa1b02524b36b7ea77b0e4b3bcc6031c5c0',
    CLIENT/'assets/bin/smallfiles': '8c45bca1348e936e0b8e5d01e7e8ee11f1db72120dc8aa7800aa72e1e0bcde96',
}
EXPECTED = dict(files=100000,bytes_checked=409600000)
REDIS = 'redis://10.20.2.162:6379/'
OBJECTS = 'http://10.20.2.163:9000'


def write(path, value):
    with path.open('x') as stream:
        json.dump(value,stream,indent=2)
        stream.write('\n')


def capture(argv, *, timeout=10):
    return subprocess.check_output(argv,text=True,timeout=timeout).strip()


def active(path):
    for row in Path('/proc/self/mountinfo').read_text().splitlines():
        left,right = row.split(' - ',1)
        if left.split()[4] == str(path):
            return dict(id=int(left.split()[0]),type=right.split()[0])
    return None


def require(ok, reason):
    if not ok:
        raise ValueError(reason)


def run(tag, db):
    require(re.fullmatch(r'c3-current-base-[0-9]{8}t[0-9]{6}z',tag) and 20 <= db < 256 and db != 70,
            'Exact new tag and dedicated Redis DB required')
    require(socket.gethostname()=='ax-pilot-20261111-client' and active(CLIENT) is not None,
            'Pool A client data disk must be mounted')
    require(os.statvfs(CLIENT).f_bavail * os.statvfs(CLIENT).f_frsize >= 2*1024**3,
            'Client disk below this probe reserve')
    for path,expected in ARTIFACTS.items():
        require(path.resolve(strict=True)==path,'Unexpected artifact path: '+str(path))
        with path.open('rb') as stream:
            require(hashlib.file_digest(stream,'sha256').hexdigest()==expected,'Artifact digest mismatch: '+str(path))
    asset = json.loads(ASSET.read_text())
    data = {'backend':'s3','s3':dict(bucket='axp-f50629e4f0f74d62',endpoint=OBJECTS,
                  region='us-east-1',force_path_style=True)}
    require(asset['config']['data']['backend']=='s3' and
            all(asset['config']['data']['s3'].get(k)==v for k,v in data['s3'].items()) and
            re.fullmatch(r'base/objects/[0-9]+/[a-f0-9]{64}',asset['manifest_key']),
            'Unexpected immutable Packed source')
    rurl = REDIS+str(db)
    require(capture(['redis-cli','-u',rurl,'DBSIZE'])=='0','Refusing to use a populated Redis DB')
    slot = Path('/sys/fs/cgroup/ax-slot-1')
    require((slot/'cgroup.procs').read_text()=='' and (slot/'cpu.max').read_text().strip()=='200000 100000'
            and (slot/'memory.max').read_text().strip()=='8589934592', 'Expected a free 2-CPU/8-GiB slot')
    out = CLIENT/'preparation'/tag
    out.mkdir(mode=0o700)
    mount = out/'mnt'; mount.mkdir()
    namespace = str(uuid.uuid4())
    write(out/'intent.json',dict(schema='c3-workspace-base-qualification-v1',tag=tag,namespace=namespace,
          redis_db=db,bucket=data['s3']['bucket'],manifest_key=asset['manifest_key'],
          pins={str(p):h for p,h in ARTIFACTS.items()},formal_sample=False,automatic_retry=False,
          boot_id=Path('/proc/sys/kernel/random/boot_id').read_text().strip()))
    (slot/'cgroup.procs').write_text(str(os.getpid()))
    auth = dict(line.split('=',1) for line in (CLIENT/'private/rustfs.env').read_text().splitlines() if '=' in line)
    env = dict(os.environ,AWS_ACCESS_KEY_ID=auth['RUSTFS_ACCESS_KEY'],AWS_SECRET_ACCESS_KEY=auth['RUSTFS_SECRET_KEY'],
        AWS_EC2_METADATA_DISABLED='true',NO_COLOR='1',RUST_LOG='error',NO_PROXY='10.20.2.162,10.20.2.163',
        no_proxy='10.20.2.162,10.20.2.163')
    data_cfg = out/'data.json'; write(data_cfg,dict(data=data))
    brewfs = BREWFS
    with (out/'init.stdout').open('xb') as stdout,(out/'init.stderr').open('xb') as stderr:
        subprocess.run([brewfs,'workspace','--meta-backend','redis','--meta-url',rurl,'--workspace-namespace',namespace,
            'init-packed-volume','--manifest-key',asset['manifest_key'],'--data-config',data_cfg],
            env=env,stdout=stdout,stderr=stderr,check=True,timeout=30)
    parent = json.loads((out/'init.stdout').read_text())
    workspace = str(uuid.UUID(parent['workspace_id']))
    cfg = out/'mount.json'
    write(cfg,dict(volume_format='workspace-v1',volume={'format':'workspace-v1'},workspace=workspace,
        workspace_namespace=namespace,data=data,meta={'backend':'redis','redis':{'url':rurl}},
        layout=dict(chunk_size=67108864,block_size=4194304),fuse=dict(workers=4,max_background=128),
        cache=dict(cache_root=str(out/'cache'),writeback_mode='upload_before_commit',
                   read_memory_bytes=536870912,read_ssd_bytes=0,compression='none')))
    with (out/'mount.log').open('xb') as log:
        proc = subprocess.Popen([brewfs,'mount','--config',cfg,mount],stdout=log,stderr=subprocess.STDOUT,env=env)
        try:
            deadline = time.monotonic()+45
            while not active(mount) and proc.poll() is None and time.monotonic()<deadline:
                time.sleep(.2)
            identity = active(mount)
            require(identity and identity['type'].startswith('fuse') and proc.poll() is None,'Current FUSE mount did not start')
            write(out/'ready.json',dict(mount=identity,pid=proc.pid,workspace_id=workspace))
            with (out/'verify.log').open('xb') as verify:
                subprocess.run([CLIENT/'assets/bin/smallfiles','verify-base','--root',mount,
                    '--output',out/'verify','--files',str(EXPECTED['files'])],
                    stdout=verify,stderr=subprocess.STDOUT,check=True,timeout=480)
            summary = json.loads((out/'verify/result.json').read_text())
            require(summary.get('passed') is True and all(summary.get(k)==v for k,v in EXPECTED.items()),
                    'Full base result mismatch')
            require(active(mount)==identity and proc.poll() is None,'Mount changed during readback')
        finally:
            code = None
            if active(mount):
                code = subprocess.run(['fusermount3','-u',mount],timeout=15).returncode
            if not active(mount) and proc.poll() is None:
                try: proc.wait(timeout=12)
                except subprocess.TimeoutExpired:
                    proc.send_signal(signal.SIGINT)
            exit_code = proc.wait(timeout=12)
            write(out/'mount-exit.json',dict(unmount_exit=code,exit_code=exit_code,mount_absent=active(mount) is None))
            require(code==0 and exit_code==0 and active(mount) is None,'Mount did not close normally')
    result = dict(passed=True,**EXPECTED,workspace_id=workspace,namespace=namespace,formal_sample=False,
        scope='Stock workspace-v1 reads the complete immutable base; no child, mutation or performance claim')
    write(out/'result.json',result)
    print(json.dumps(result))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--tag',required=True)
    parser.add_argument('--database',type=int,required=True)
    args=parser.parse_args()
    def terminate(_signum, _frame):
        raise TimeoutError('Qualification interrupted')
    signal.signal(signal.SIGTERM,terminate)
    run(args.tag,args.database)
