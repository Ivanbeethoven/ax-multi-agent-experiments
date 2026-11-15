"""Read-only check: a run must have no workload processes or mounts before cleanup."""
import json
from pathlib import Path
import re
import socket
import subprocess
import sys

PROGRAMS = {'brewfs','juicefs','full-fuse-replay','smallfiles','smallfiles-copy',
            'smallfiles-verify','smallfiles-verify-juicefs','packed_snapshot_fixture','fio'}
RUNNERS = {'rbm-lifecycle.py','rbm-native-fork.py','run-performance-c1.py','qualify-trace.py'}


def inspect(run):
    if not re.fullmatch(r'ax-four-[0-9a-f]{12}', run):
        raise ValueError('Invalid run ID')
    token = run.encode()
    base = '/srv/ax-idcai-20261113/campaign-100k-001/'+run
    command = subprocess.run(['findmnt','-rn','-o','TARGET'], capture_output=True, text=True)
    if command.returncode != 0:
        raise RuntimeError('Cannot inspect mounts')
    mounts = [line for line in command.stdout.splitlines()
              if line == base or line.startswith(base+'/')
              or line == '/srv/ax-pilot-session' or line.startswith('/srv/ax-pilot-session/')]
    processes = []
    for proc in Path('/proc').glob('[0-9]*'):
        try:
            exe = (proc/'exe').readlink().name
            argv = (proc/'cmdline').read_bytes().split(b'\0')
            runner = any(Path(part.decode(errors='replace')).name in RUNNERS for part in argv if part)
            if exe not in PROGRAMS and not runner:
                continue
            own = any(token in part for part in argv)
            if not own and exe in PROGRAMS:
                own = b'AX_IDCAI_RUN='+token in (proc/'environ').read_bytes().split(b'\0')
            if own:
                processes.append(dict(pid=int(proc.name), executable=exe))
        except (FileNotFoundError, ProcessLookupError):
            continue
    return dict(run=run, hostname=socket.gethostname(), idle=not mounts and not processes,
                workload_processes=processes, mounts=mounts, read_only=True)


if __name__ == '__main__':
    result = inspect(sys.argv[1])
    print(json.dumps(result))
    raise SystemExit(0 if result['idle'] else 2)
