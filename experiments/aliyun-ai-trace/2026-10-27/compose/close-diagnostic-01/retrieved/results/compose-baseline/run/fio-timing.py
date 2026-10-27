"""Low-volume timing/syscall diagnostic; fio remains the data path."""
import json
from pathlib import Path
import subprocess
import sys
import time


def clock():
    return {'monotonic_ns':time.monotonic_ns(),'realtime_ns':time.time_ns()}


if sys.argv[1]=='mark':
    point=clock()
    point['phase']=sys.argv[3]
    with Path(sys.argv[2]).open('a') as stream:
        stream.write(json.dumps(point)+'\n')
else:
    output,prefix=map(Path,sys.argv[1:3])
    assert sys.argv[3]=='--' and sys.argv[4]=='fio'
    argv=sys.argv[4:]
    command=['strace','--seccomp-bpf','-ff','-ttt','-T','-yy','-e',
             'trace=open,openat,close,fsync,fdatasync,fallocate,ftruncate','-o',str(prefix),*argv]
    state={'command':argv,'strace_command':command,'before_spawn':clock()}
    process=subprocess.Popen(command)
    state['after_spawn']=clock()
    state['pid']=process.pid
    state['exit_code']=process.wait(timeout=None)
    state['after_wait']=clock()
    state['wall_seconds']=(state['after_wait']['monotonic_ns']-state['before_spawn']['monotonic_ns'])/1e9
    output.write_text(json.dumps(state,indent=2)+'\n')
    raise SystemExit(state['exit_code'])
