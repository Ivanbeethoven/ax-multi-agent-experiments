"""Pinned SSH/stdin transport shared by ECS and provided Linux servers."""
import json
from pathlib import Path
import re
import shlex
import subprocess
import sys

from .common import digest, pinned

# Small constant argv; long specs never enter a Windows command line. The full
# Python runtime and every imported local module are pinned before exec.
BOOTSTRAP = '''import sys,json,hashlib,os,pathlib
line=bytearray()
while not line.endswith(b'\x5cn'):
 b=os.read(0,1)
 if not b: raise RuntimeError('missing envelope')
 line.extend(b)
x=json.loads(line)
root=pathlib.Path(x['repo']).resolve(strict=True)
required={str(p.resolve()) for p in (root/'bench/runtime').glob('*.py')}
required.update(str(root/'bench'/n) for n in ('__init__.py','experiment_io.py','experiment_step.py','session_check.py'))
required.add(str(pathlib.Path(sys.executable).resolve()))
pins={str(pathlib.Path(p['path']).resolve()):p['sha256'] for p in x['pins']}
if not required.issubset(pins): raise ValueError('runtime closure not pinned')
for p,h in pins.items():
 with open(p,'rb') as f:
  if hashlib.file_digest(f,'sha256').hexdigest()!=h: raise ValueError('runtime/input changed')
if x['module']!='bench.runtime.node': raise ValueError('unexpected module')
if x.get('cgroup'):
 group=pathlib.Path(x['cgroup']).resolve(strict=True)
 if not str(group).startswith('/sys/fs/cgroup/ax-'): raise ValueError('dedicated ax cgroup required')
 (group/'cgroup.procs').write_text(str(os.getpid()))
os.chdir(root)
os.execv(sys.executable,[sys.executable,'-u','-m',x['module'],*x['args']])
'''


def runtime_pins(repo, python):
    repo = Path(repo)
    paths = list((repo/'bench/runtime').glob('*.py'))
    paths += [repo/'bench'/n for n in ('__init__.py','experiment_io.py','experiment_step.py','session_check.py')]
    paths += [Path(python).resolve()]
    return [{'path': str(p.resolve()), 'sha256': digest(p)} for p in sorted(set(paths))]


def verify_ssh(binary,config,known,alias,seen=None):
    """Check jump hosts too: target command-line options do not secure a jump."""
    seen=set() if seen is None else seen
    if alias in seen:raise ValueError('recursive SSH jump')
    seen.add(alias)
    raw=Path(config).read_text(encoding='utf-8')
    if re.search(r'(?im)^\s*(Include|Match|ProxyCommand|LocalCommand)\s',raw):
        raise ValueError('use self-contained Host blocks without executable/include directives')
    reply=subprocess.run([binary,'-F',config,'-G',alias],capture_output=True,text=True,check=True)
    values=dict(line.split(' ',1) for line in reply.stdout.splitlines() if ' ' in line)
    if values.get('stricthostkeychecking')!='true' and values.get('stricthostkeychecking')!='yes':
        raise ValueError('pin StrictHostKeyChecking yes in every Host block including jump')
    if values.get('forwardagent')!='no' or values.get('permitlocalcommand')!='no':raise ValueError('disable agent forwarding/local commands')
    if values.get('globalknownhostsfile')!='none':raise ValueError('disable unpinned global host keys including jump')
    actual=values.get('userknownhostsfile','').strip('"')
    if Path(actual).resolve()!=Path(known).resolve():raise ValueError('jump/target must use the exact pinned known_hosts file')
    if values.get('proxycommand','none')!='none':raise ValueError('ProxyCommand unsupported; use reviewed ProxyJump')
    for jump in values.get('proxyjump','none').split(','):
        if jump=='none':continue
        if not re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9_.-]*',jump):raise ValueError('use a configured simple jump alias')
        verify_ssh(binary,config,known,jump,seen)


def command(peer, test=False):
    if peer['transport'] == 'local':
        if not test: raise ValueError('local transport is adapter-test only')
        return [peer['python'], '-u', '-c', BOOTSTRAP]
    if peer['transport'] != 'ssh': raise ValueError('ssh transport required')
    ssh = peer['ssh']
    if peer.get('container') and peer.get('cgroup'):raise ValueError('choose container or host cgroup, not both')
    binary = str(pinned(ssh['executable']))
    config = str(pinned(ssh['config'])); known = str(pinned(ssh['known_hosts']))
    if not re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9_.-]*', peer['alias']):
        raise ValueError('simple approved SSH alias required')
    verify_ssh(binary,config,known,peer['alias'])
    remote = [peer['python'], '-u', '-c', BOOTSTRAP]
    if peer.get('container'):
        if not re.fullmatch(r'ax-[a-z0-9-]+', peer['container']): raise ValueError('campaign container name required')
        remote = ['/usr/bin/docker', 'exec', '-i', peer['container'], *remote]
    # Quoting is for the remote POSIX shell. No user data is interpolated into
    # script text. Jump hosts and all their host keys live in reviewed -F files.
    return [binary, '-F', config, '-o', 'BatchMode=yes', '-o', 'StrictHostKeyChecking=yes',
            '-o', 'UserKnownHostsFile='+known, '-o', 'GlobalKnownHostsFile=none', '-o', 'ForwardAgent=no', '-o', 'ClearAllForwardings=yes',
            '-o', 'ServerAliveInterval=15', '-o', 'ServerAliveCountMax=3', '-T',
            peer['alias'], shlex.join(remote)]


def connect(peer, args, stderr, test=False):
    proc = subprocess.Popen(command(peer, test), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                            stderr=stderr, text=True, encoding='utf-8', bufsize=1)
    envelope = {'repo': peer['repo'], 'pins': peer['pins'], 'module': 'bench.runtime.node', 'args': args,
                'cgroup':peer.get('cgroup')}
    send(proc, envelope)
    return proc


def send(proc, message):
    proc.stdin.write(json.dumps(message, separators=(',', ':'))+'\n'); proc.stdin.flush()


def one(peer, args, directory, test=False, payload=None):
    """One node operation. Return all private protocol records; never retry."""
    with (Path(directory)/'ssh.stderr.log').open('x', encoding='utf-8') as err:
        proc = connect(peer, args, err, test)
        if payload is not None: send(proc, payload)
        proc.stdin.close()
        rows = []
        with (Path(directory)/'protocol.jsonl').open('x', encoding='utf-8') as out:
            for line in proc.stdout:
                out.write(line); out.flush(); rows.append(json.loads(line))
        code = proc.wait(); proc.stdout.close()
    if code != 0 or not rows or rows[-1].get('type') == 'error':
        raise ValueError('node operation failed; inspect private protocol, no retry')
    return rows[-1]
