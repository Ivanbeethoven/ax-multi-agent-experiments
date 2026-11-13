"""Capture original host, cgroup and native service data on each machine."""
import json
import os
from pathlib import Path
import subprocess as sp
import sys
import time
from urllib.request import build_opener, ProxyHandler

BASE = Path('/srv/ax-idcai-20261113')
PROC = ('stat','meminfo','vmstat','diskstats','net/dev','net/snmp','net/netstat',
        'pressure/cpu','pressure/memory','pressure/io')
CGROUP = ('cpu.stat','cpu.max','memory.current','memory.peak','memory.max','memory.events',
          'memory.swap.max','io.stat','pids.current')
CLIENT_PROGRAMS = {'brewfs','juicefs','full-fuse-replay','smallfiles','smallfiles-verify',
                   'smallfiles-verify-juicefs','smallfiles-copy','packed_snapshot_fixture','fio'}


def capture_client_processes(out, run):
    """Keep raw counters only for processes linked to this run by their ancestry."""
    rows = []
    token = run.encode()
    def belongs(proc):
        # The C4 target gets this non-secret ID through its mount environment.
        # Only test the marker; never persist the environment or command line.
        if b'AX_IDCAI_RUN='+token in (proc/'environ').read_bytes().split(b'\0'):
            return True
        seen = set()
        while proc.name not in seen and proc.name not in ('0','1'):
            seen.add(proc.name)
            if token in (proc/'cmdline').read_bytes():
                return True
            status = (proc/'status').read_text().splitlines()
            parent = next(line.split()[1] for line in status if line.startswith('PPid:'))
            proc = Path('/proc')/parent
        return False
    for proc in Path('/proc').glob('[0-9]*'):
        try:
            executable = proc.joinpath('exe').readlink()
            if executable.name not in CLIENT_PROGRAMS or not belongs(proc):
                continue
            stat = (proc/'stat').read_bytes()
            pid = int(proc.name)
            # Field 22 identifies the process independently of PID reuse.
            start_ticks = int(stat.rsplit(b') ', 1)[1].split()[19])
            prefix = f'client-pid-{pid}-'
            (out/(prefix+'stat.txt')).write_bytes(stat)
            row = dict(pid=pid, executable=str(executable), start_ticks=start_ticks,
                       clock_ticks_per_second=os.sysconf('SC_CLK_TCK'),
                       ownership='run ID marker or ancestor argv; environment and argv are not saved')
            rows.append(row)
            try:
                for name in ('status','io','cgroup'):
                    (out/(prefix+name+'.txt')).write_bytes((proc/name).read_bytes())
                relative = (proc/'cgroup').read_text().strip().split('::',1)[1]
                group = Path('/sys/fs/cgroup')/relative.lstrip('/')
                row['cgroup_path'] = relative
                row['cgroup_scope'] = 'may include other processes; use PID counters for process CPU'
                for name in CGROUP:
                    if (group/name).exists():
                        (out/(prefix+'cgroup-'+name+'.txt')).write_bytes((group/name).read_bytes())
            except (FileNotFoundError, ProcessLookupError):
                row['exited_during_sample'] = True
        except (FileNotFoundError, ProcessLookupError):
            continue
    (out/'client-processes.json').write_text(json.dumps(rows)+'\n')
    return rows


def sample(out, role, run):
    out.mkdir(mode=0o700)
    start = time.monotonic_ns()
    utc = time.time_ns()
    def put(name, data):
        (out/name).write_bytes(data)
    for name in PROC:
        put('host-'+name.replace('/','-')+'.txt', (Path('/proc')/name).read_bytes())
    if role in ('client','target'):
        capture_client_processes(out, run)
    if role.startswith('obj'):
        opener = build_opener(ProxyHandler({}))
        with opener.open('http://127.0.0.1:9464/metrics', timeout=2) as response:
            raw = response.read(16*1024*1024 + 1)
        if len(raw) > 16*1024*1024 or b'\nrustfs_' not in b'\n'+raw:
            raise ValueError('Missing or oversized native RustFS metrics')
        put('rustfs.prom', raw)
        container = json.loads(sp.check_output(['docker','inspect','--format','{{json .State}}',run],timeout=3))
        pid = int(container['Pid'])
        if pid < 2:
            raise ValueError('RustFS container not running')
        p = Path('/proc')/str(pid)
        group = Path('/sys/fs/cgroup') / (p/'cgroup').read_text().strip().split('::',1)[1].lstrip('/')
        for name in ('stat','status','io'):
            put('rustfs-pid-'+name+'.txt', (p/name).read_bytes())
        for name in CGROUP:
            if (group/name).exists():
                put('rustfs-cgroup-'+name+'.txt',(group/name).read_bytes())
        if role=='obj4':
            cfg = BASE/'four-node'/run/'redis-private.conf'
            secret = next(line.split(maxsplit=1)[1] for line in cfg.read_text().splitlines() if line.startswith('requirepass '))
            env = dict(os.environ, REDISCLI_AUTH=secret)
            argv = ['redis-cli','-h','10.77.13.24','-p','16379','--raw']
            info = sp.check_output(argv+['INFO','ALL'],env=env,timeout=3)
            if b'aof_enabled:1' not in info:
                raise ValueError('Redis AOF status unavailable')
            put('redis-info.txt',info)
            put('redis-latency.txt',sp.check_output(argv+['LATENCY','HISTOGRAM'],env=env,timeout=3))
    v = os.statvfs(BASE)
    record = dict(role=role, utc_ns=utc, monotonic_start_ns=start, monotonic_end_ns=time.monotonic_ns(),
                  client_process_collection=role in ('client','target'),
                  boot_id=Path('/proc/sys/kernel/random/boot_id').read_text().strip(),
                  bytes_free=v.f_bavail*v.f_frsize, inodes_free=v.f_favail)
    (out/'sample.json').write_text(json.dumps(record)+'\n')


def main():
    role, run, out = sys.argv[1:]
    out = Path(out)
    (out/'clock.txt').write_bytes(sp.check_output(['chronyc','tracking'],timeout=3))
    end = time.monotonic()
    i = 0
    while not (out/'stop').exists():
        try:
            sample(out/f'sample-{i:06}', role, run)
        except Exception as error:
            with (out/'errors.jsonl').open('a') as f:
                f.write(json.dumps({'index':i,'error':type(error).__name__})+'\n')
        end += 5
        i += 1
        time.sleep(max(0, end-time.monotonic()))
    (out/'finished.json').write_text(json.dumps({'samples_attempted':i,'passed':not (out/'errors.jsonl').exists()})+'\n')


if __name__=='__main__':
    main()
