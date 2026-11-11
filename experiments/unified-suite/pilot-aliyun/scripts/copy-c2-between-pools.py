#!/usr/bin/env python3
"""Copy the exact qualified C2 input and replay binary between cloud clients.

Run on the coordinator. The tar stream stays in the VPC; initial modes, links
and every manifest hash are checked at the destination before publication.
No mounts, metadata DBs, workload processes, models or services are started.
"""
import argparse
from datetime import datetime,timezone
import hashlib
import json
from pathlib import Path
import shlex
import subprocess


CASE='trace-exact-02/cases/pytest-full-02'
MANIFEST='trace-exact-02/manifest-replay-inputs-03.json'
MANIFEST_SHA='71a0661b82a433e9d14da29287a2bc1860460015d4887031f90a03ff5451a021'
BINARY='assets/bin/full-fuse-replay-parent-fd-01'
BINARY_SHA='35c45abd910d826ea17b5d6dfc750c7083cda5e8ce8f3bbe5ef77a78ffbea1aa'


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--ssh-config',type=Path,required=True)
    p.add_argument('--source',default='pool-a-client')
    p.add_argument('--destination',default='pool-b-client')
    p.add_argument('--out',type=Path,required=True)
    p.add_argument('--staging',default='/srv/ax-client/preparation/c2-input-transfer-01')
    p.add_argument('--destination-repo',default='/srv/ax-client/code-12')
    a=p.parse_args()
    if a.source==a.destination or {a.source,a.destination}!={'pool-a-client','pool-b-client'}:
        raise ValueError('Two distinct explicit client aliases required')
    if (not a.staging.startswith('/srv/ax-client/preparation/') or '..' in Path(a.staging).parts or
        not a.destination_repo.startswith('/srv/ax-client/code-')):
        raise ValueError('Owned staging and frozen code directory required')
    a.out.mkdir(parents=True,exist_ok=False)
    def write(name,value):
        with (a.out/name).open('x',encoding='utf-8') as stream:json.dump(value,stream,indent=2);stream.write('\n')
    write('request.json',dict(at=datetime.now(timezone.utc).isoformat(),source=a.source,destination=a.destination,
        staging=a.staging,ssh_config=dict(path=str(a.ssh_config),sha256=hashlib.sha256(a.ssh_config.read_bytes()).hexdigest()),
        case=CASE,manifest_sha256=MANIFEST_SHA,binary_sha256=BINARY_SHA,automatic_retry=False))
    def ssh(alias,argv):return ['ssh','-F',str(a.ssh_config),alias,shlex.join(list(map(str,argv)))]
    def command(name,alias,code):
        argv=ssh(alias,['nice','-n','15','ionice','-c','3','python3','-c',code]);write(name+'-command.json',dict(argv=argv))
        with (a.out/(name+'-stdout')).open('xb') as stdout,(a.out/(name+'-stderr')).open('xb') as stderr:
            result=subprocess.run(argv,stdout=stdout,stderr=stderr)
        write(name+'-exit.json',dict(exit_code=result.returncode))
        if result.returncode:raise RuntimeError('Inspect '+str(a.out/name))
        return json.loads((a.out/(name+'-stdout')).read_text())
    source=command('source',a.source,"import pathlib,hashlib,json;root=pathlib.Path('/srv/ax-client');m=root/"+repr(MANIFEST)+";b=root/"+repr(BINARY)+";assert hashlib.sha256(m.read_bytes()).hexdigest()=="+repr(MANIFEST_SHA)+";assert hashlib.sha256(b.read_bytes()).hexdigest()=="+repr(BINARY_SHA)+";print(json.dumps(dict(passed=True,manifest_files=len(json.loads(m.read_text())),binary_bytes=b.stat().st_size)))")
    if source['manifest_files']!=67577:raise ValueError('Original full manifest differs')
    command('destination-new',a.destination,"import pathlib,json,os;root=pathlib.Path('/srv/ax-client');stage=pathlib.Path("+repr(a.staging)+");assert not stage.exists();assert not (root/'trace-exact-02').exists();assert not (root/"+repr(BINARY)+").exists();assert os.statvfs(root).f_bavail*os.statvfs(root).f_frsize>8*1024**3;stage.mkdir(mode=0o700,parents=True);(stage/'tar').mkdir();print(json.dumps(dict(passed=True)))")
    sender=ssh(a.source,['nice','-n','15','ionice','-c','3','tar','-C','/srv/ax-client','-cf','-',CASE,MANIFEST,BINARY])
    receiver=ssh(a.destination,['nice','-n','15','ionice','-c','3','tar','-C',a.staging+'/tar','-xpf','-'])
    write('stream-command.json',dict(sender=sender,receiver=receiver,network='VPC through coordinator',automatic_retry=False))
    with (a.out/'sender-stderr').open('xb') as send_error,(a.out/'receiver-stderr').open('xb') as recv_error,(a.out/'receiver-stdout').open('xb') as recv_output:
        sending=subprocess.Popen(sender,stdout=subprocess.PIPE,stderr=send_error)
        receiving=subprocess.Popen(receiver,stdin=sending.stdout,stdout=recv_output,stderr=recv_error)
        sending.stdout.close();received=receiving.wait();sent=sending.wait()
    write('stream-exit.json',dict(sender_exit=sent,receiver_exit=received))
    if sent or received:raise RuntimeError('Transfer failed; preserve original staging and logs')
    code='''import pathlib,hashlib,json,os,sys
sys.path.insert(0,REPO+'/scripts')
from full_trace_common import tree
stage=pathlib.Path(STAGE)/'tar';root=pathlib.Path('/srv/ax-client')
manifest=stage/MANIFEST
assert hashlib.sha256(manifest.read_bytes()).hexdigest()==MANIFEST_SHA
case=stage/CASE;files=json.loads(manifest.read_text());total=0
for name,wanted in files.items():
 path=case/name
 if path.is_symlink() or not path.is_file():raise ValueError('Manifest regular file differs')
 sha=hashlib.sha256()
 with path.open('rb') as stream:
  while data:=stream.read(4*1024*1024):sha.update(data);total+=len(data)
 if sha.hexdigest()!=wanted:raise ValueError('Complete destination input hash differs')
expected=json.loads((case/'initial-tree.json').read_text())
if tree(case/'initial')!=expected:raise ValueError('Initial paths/bytes/modes/links differ')
binary=stage/BINARY
if hashlib.sha256(binary.read_bytes()).hexdigest()!=BINARY_SHA or not os.access(binary,os.X_OK):raise ValueError('Replay binary differs')
if (root/'trace-exact-02').exists() or (root/BINARY).exists():raise ValueError('Never overwrite a destination')
os.rename(stage/'trace-exact-02',root/'trace-exact-02')
os.link(binary,root/BINARY)
print(json.dumps(dict(passed=True,manifest_sha256=MANIFEST_SHA,manifest_files=len(files),verified_bytes=total,
 initial_entries=len(expected),binary_sha256=BINARY_SHA,case=str(root/CASE),manifest=str(root/MANIFEST),binary=str(root/BINARY),
 scope='Every original regular-file SHA and complete initial tree checked before new-path publication; same fixed input as A.')))
'''
    values=dict(REPO=a.destination_repo,STAGE=a.staging,CASE=CASE,MANIFEST=MANIFEST,MANIFEST_SHA=MANIFEST_SHA,BINARY=BINARY,BINARY_SHA=BINARY_SHA)
    result=command('verify-publish',a.destination,'\n'.join(k+'='+repr(v) for k,v in values.items())+'\n'+code)
    result.update(at=datetime.now(timezone.utc).isoformat(),source=a.source,destination=a.destination,bytes_downloaded_to_local=0,automatic_retry=False)
    write('result.json',result);print(json.dumps(result))


if __name__=='__main__':main()
