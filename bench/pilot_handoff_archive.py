"""Retain C4 v2 handoff and target closure evidence under the caller's lock."""
import base64
import hashlib
import json
from pathlib import Path
import stat
import subprocess
import tarfile

from . import pilot_handoff_closure_bridge as bridge
from .experiment_io import linux_path
from .runtime.common import load, pinned, private_dir, write_new


FILE_CAP = 8 * 1024**2
TARGET_CAP = 32 * 1024**2
LOCAL_CAP = 1024**3
MEMBER_CAP = 512

# The target reads only already closed, pinned evidence. It sends bytes to the
# coordinator without creating another file, changing a mount or accessing a
# credential/config file. The coordinator independently checks every response.
READBACK = """import base64,hashlib,json,os,pathlib,stat,sys
x=json.load(sys.stdin)
p=pathlib.Path(sys.executable).resolve(strict=True)
if str(p)!=x['python']['path']: raise ValueError('wrong target interpreter')
with p.open('rb') as f:
 if hashlib.file_digest(f,'sha256').hexdigest()!=x['python']['sha256']: raise ValueError('changed target interpreter')
remaining=x['total_cap']
for name, pin in x['files'].items():
 p=pathlib.Path(pin['path'])
 if not p.is_absolute() or p.resolve(strict=True)!=p: raise ValueError('noncanonical target evidence')
 fd=os.open(p,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
 with os.fdopen(fd,'rb') as f:
  s=os.fstat(f.fileno())
  if not stat.S_ISREG(s.st_mode) or s.st_size>min(remaining,x['file_cap']): raise ValueError('target evidence exceeds cap')
  raw=f.read(s.st_size+1)
 if len(raw)!=s.st_size or hashlib.sha256(raw).hexdigest()!=pin['sha256']: raise ValueError('target evidence changed')
 remaining-=len(raw)
 print(json.dumps(dict(name=name, pin=pin, bytes=len(raw), body=base64.b64encode(raw).decode())),flush=True)
"""


def target_files(context):
    closed = context['target_closure']
    if load(pinned(context['target_closure_readback'])) != closed:
        raise ValueError('target closure stdout changed before archive')
    if (closed.get('schema')!=bridge.CLOSE_RESULT or closed.get('passed') is not True
            or closed['cell_id'] != context['cell_id'] or closed['all_exited'] is not True
            or closed['all_unmounted'] is not True):
        raise ValueError('exact successful target closure required')
    root = linux_path(context['target']['mount_directory'])
    out = linux_path(context['route']['output'])
    files = {k+'.json':bridge.remote_pin(closed[k],root/(k+'.json'))
             for k in ('ready','process','stopping','stopped')}
    files['suite-closure.json'] = bridge.remote_pin(closed['closure'],out/'suite-closure.json')
    files['debt-result.json'] = bridge.remote_pin(closed['debt']['batch'],out/'debt'/'result.json')
    samples = closed['debt']['samples']
    if len(samples)!=2:
        raise ValueError('exactly two target debt samples required')
    for i, row in enumerate(samples):
        for key, name in [('sample',f'sample-{i}.json'),('native',f'native-{i}.prom')]:
            files[name] = bridge.remote_pin(row[key],out/'debt'/name)
    return files


def read_target(peer, files, directory):
    cmd = bridge.transport.ssh_command(peer,[peer['python'],'-I','-B','-u','-c',READBACK])
    envelope = dict(python=bridge.target_python(peer),files=files,file_cap=FILE_CAP,total_cap=TARGET_CAP)
    wire, err = directory/'remote.jsonl', directory/'ssh.stderr.log'
    with wire.open('xb') as stdout, err.open('xb') as stderr:
        subprocess.run(cmd,input=json.dumps(envelope).encode(),stdout=stdout,stderr=stderr,check=True,timeout=90)
    remaining = TARGET_CAP
    seen = {}
    with wire.open('rb') as f:
        while line := f.readline(FILE_CAP*4//3+4096):
            if not line.endswith(b'\n') or len(seen)>=len(files):
                raise ValueError('unexpected target readback output')
            row = json.loads(line)
            name = row['name']
            if name in seen or name not in files or row['pin']!=files[name]:
                raise ValueError('target readback identity differs')
            raw = base64.b64decode(row['body'],validate=True)
            if (type(row['bytes']) is not int or row['bytes']!=len(raw) or len(raw)>min(remaining,FILE_CAP)
                    or hashlib.sha256(raw).hexdigest()!=files[name]['sha256']):
                raise ValueError('target readback content differs')
            remaining -= len(raw)
            item = directory/name
            with item.open('xb') as out: out.write(raw)
            seen[name] = item
    if seen.keys()!=files.keys():
        raise ValueError('target readback is incomplete')
    return seen


def tree(root):
    members = {}
    for p in sorted(root.rglob('*')):
        mode = p.lstat().st_mode
        if stat.S_ISDIR(mode): continue
        if not stat.S_ISREG(mode) or p.resolve(strict=True)!=p:
            raise ValueError('nonregular handoff evidence')
        members['handoff/'+p.relative_to(root).as_posix()] = p
        if len(members)>MEMBER_CAP:
            raise ValueError('too many handoff evidence files')
    return members


def verify_bundle(archive, expected):
    seen = set()
    with tarfile.open(archive,'r|gz') as source:
        for member in source:
            if (not member.isfile() or member.name in seen or member.name not in expected
                    or member.size!=expected[member.name]['bytes']):
                raise ValueError('archive contains unexpected file')
            with source.extractfile(member) as stream:
                if hashlib.file_digest(stream,'sha256').hexdigest()!=expected[member.name]['sha256']:
                    raise ValueError('archive member changed')
            seen.add(member.name)
    if seen!=expected.keys():
        raise ValueError('archive is incomplete')


def archive_handoff(context, output):
    """Use as run_guarded's archive callback. Retains private bytes, no upload."""
    result = pinned(context['original']['result']).resolve(strict=True)
    root = result.parent
    close = pinned(context['target_closure_readback']).resolve(strict=True)
    if (result.name!='result.json' or root.name!='handoff' or root.parent!=close.parent.parent
            or close.name!='stdout.json' or close.parent.name!='target-close'):
        raise ValueError('archive requires original handoff and target-close sibling directories')
    output = Path(output)
    if output.resolve()!=output or output.is_relative_to(root) or output.is_relative_to(close.parent):
        raise ValueError('new archive directory must be outside original evidence')
    members = tree(root)
    for key in ('result','target_start','target_cold'):
        original = pinned(context['original'][key]).resolve(strict=True)
        if original not in members.values(): raise ValueError('missing original handoff boundary')
    intent = close.parent/'intent.json'
    authorization = load(pinned(bridge.ref(intent)))['authorization']
    approved = authorization['reviewed_request']
    approved_path = pinned(approved).resolve(strict=True)
    approved_value = bridge.spec(approved,bridge.CLOSE_REQUEST)
    if (authorization['remote_request']!=context['target_closure']['request']
            or approved['sha256']!=authorization['remote_request']['sha256']
            or approved_value['cell_id']!=context['cell_id']):
        raise ValueError('archive requires the original operator review')
    files = target_files(context)
    out = private_dir(output)
    readback = private_dir(out/'target-readback')
    remote = read_target(context['target']['peer'],files,readback)
    members.update({'target/'+name:path for name,path in remote.items()})
    members['target/closure-stdout.json'] = close
    members['target/closure-intent.json'] = intent
    members['target/reviewed-request.json'] = approved_path
    expected = {}
    used = 0
    for name, path in members.items():
        size = path.stat().st_size
        used += size
        if used>LOCAL_CAP or len(members)>MEMBER_CAP: raise ValueError('archive exceeds declared bounds')
        expected[name] = dict(bytes=size,sha256=bridge.digest(path))
    for name, pin in files.items():
        if expected['target/'+name]['sha256']!=pin['sha256']: raise ValueError('original target bytes changed')
    for key in ('result','target_start','target_cold'):
        original = context['original'][key]
        name = 'handoff/'+Path(original['path']).relative_to(root).as_posix()
        if expected[name]['sha256']!=original['sha256']: raise ValueError('original handoff bytes changed')
    if expected['target/closure-stdout.json']['sha256']!=context['target_closure_readback']['sha256']:
        raise ValueError('original target closure stdout changed')
    if expected['target/reviewed-request.json']['sha256']!=approved['sha256']:
        raise ValueError('original operator review changed')
    bundle = out/'handoff.tar.gz'
    with tarfile.open(bundle,'x:gz',compresslevel=1) as archive:
        for name, path in members.items():
            info=tarfile.TarInfo(name)
            info.size=expected[name]['bytes']
            info.mode=0o600
            with path.open('rb') as stream: archive.addfile(info,stream)
    verify_bundle(bundle,expected)
    write_new(out/'manifest.json',expected)
    receipt = out/'readback.json'
    write_new(receipt,dict(schema=bridge.ARCHIVE_READBACK,passed=True,cell_id=context['cell_id'],
        handoff=context['original']['result'],target_closure_readback=context['target_closure_readback'],
        bundle=bridge.ref(bundle),manifest=bridge.ref(out/'manifest.json'),member_count=len(expected),
        original_bytes=used,remote_evidence_pins=files,private_local_archive=True,uploaded=False))
    return bridge.ref(receipt)
