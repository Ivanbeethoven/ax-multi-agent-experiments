#!/usr/bin/env python3
"""Run one private, local process trace of the exact C4 OpenCode export."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import runpy
import subprocess
import sys
import time

SCRIPTS = Path(__file__).resolve().parent
REPO = SCRIPTS.parents[3]
ARCHIVE_SHA = '9659f428182c55fbd7d8109bb61d0f37e6ef655f61615746b49baf0d3e5821fe'
RECEIPT_SHA = 'abc3852490e01e37c1f728cf1d9b59eeb729196bdebe4c7fcc7e04617bdffe7a'
BINARY_SHA = '025e625d7ee316f9ce246056997551869b72e4bffa778dd3ad8248ba0e014233'
RAW_EXPORT_SHA = '301b691d6fd4ff074a6e8977e32d630c61b5db9c10eb0a6dbf862009db2f109d'
WORKSPACE = Path('/srv/ax-pilot-session/workspace')
STRACE = Path('/usr/bin/strace')


def digest(path):
    with path.open('rb') as f:
        return hashlib.file_digest(f, 'sha256').hexdigest()


def record(path, value):
    with path.open('x', encoding='utf-8') as f:
        json.dump(value, f, ensure_ascii=False, indent=2)
        f.write('\n')


def identities():
    return {ns: os.readlink('/proc/self/ns/'+ns) for ns in ('net', 'mnt', 'pid')}


def loopback_only():
    interfaces = sorted(line.split(':', 1)[0].strip() for line in
                        Path('/proc/self/net/dev').read_text().splitlines()[2:])
    routes = {af: subprocess.check_output(['/usr/sbin/ip', af, '-o', 'route', 'show', 'table', 'main'],
                                         text=True, timeout=3).splitlines() for af in ('-4','-6')}
    if interfaces != ['lo'] or any(routes.values()):
        raise ValueError('Diagnostic namespaces must have only loopback and no routes')
    return dict(interfaces=interfaces, routes=routes)


def proc_snapshot():
    rows = []
    for path in Path('/proc').glob('[0-9]*/stat'):
        root = path.parent
        try:
            raw = path.read_text()
            comm, fields = raw[raw.index('(')+1:].rsplit(')', 1)
            fields = fields.split()
            try:
                exe = os.readlink(root/'exe')
            except OSError:
                exe = None
            rows.append(dict(pid=int(root.name), comm=comm, exe=exe, state=fields[0],
                ppid=int(fields[1]), pgid=int(fields[2]), sid=int(fields[3]),
                user_ticks=int(fields[11]), system_ticks=int(fields[12]),
                threads=int(fields[17]), start_ticks=int(fields[19]),
                wchan=(root/'wchan').read_text().strip()))
        except (FileNotFoundError, ProcessLookupError):
            pass
    return sorted(rows, key=lambda row: row['pid'])


def watched_processes(rows, pgid):
    # Private PID namespace: all live processes other than the monitor and
    # separately detached tracer are workload processes. Track escaped PGIDs.
    return [row for row in rows if row['pid'] != os.getpid() and row['state'] != 'Z'
            and not (row['exe'] == str(STRACE) and row['pgid'] != pgid)]


def observe(child, path):
    started = time.monotonic_ns()
    parent_return = first = final = None
    empty = 0
    with path.open('x', encoding='utf-8', buffering=1) as f:
        while True:
            code = child.poll()
            mono = time.monotonic_ns()
            rows = proc_snapshot()
            live = watched_processes(rows, child.pid)
            sample = dict(monotonic_ns=mono, epoch_ns=time.time_ns(), parent_returncode=code,
                          group=[r for r in live if r['pgid']==child.pid],
                          other_workload=[r for r in live if r['pgid']!=child.pid], processes=rows)
            f.write(json.dumps(sample,separators=(',',':'))+'\n')
            if code is not None:
                if parent_return is None:
                    parent_return, first = mono, sample
                empty = empty+1 if not live else 0
                if empty == 2:
                    final = sample
                    break
                if mono-parent_return >= 15_000_000_000:
                    break
            if mono-started >= 30_000_000_000:
                break
            time.sleep(.02)
    return dict(parent_exit_code=child.returncode, pgid=child.pid, parent_observed_exit_ns=parent_return,
        first_post_exit=first, natural_all_workload_processes_exited=final is not None,
        final_natural_empty=final, last=sample,
        seconds_parent_to_natural_empty=(final['monotonic_ns']-parent_return)/1e9 if final else None,
        diagnostic_only=True, historical_five_second_gate_changed=False,
        cleanup_on_private_pid_namespace_exit=final is None)


def inner(args):
    ns = identities()
    outer = json.loads(args.outer)
    if os.getpid()!=1 or any(ns[key]==outer[key] for key in ns):
        raise ValueError('Expected independent mount, network and PID namespaces')
    net = loopback_only()
    root = args.out
    root.mkdir(mode=0o700)
    packager = runpy.run_path(str(SCRIPTS/'package-session-checkpoint.py'))
    installed = packager['install'](args.archive,args.receipt,RECEIPT_SHA,root/'input')
    if installed['files'] != 894:
        raise ValueError('Exact C4 checkpoint required')
    if not WORKSPACE.is_dir() or WORKSPACE.resolve()!=WORKSPACE or WORKSPACE.is_mount():
        raise ValueError('An existing unmounted canonical directory is needed as a private bind target')
    before = WORKSPACE.stat()
    copied = root/'input/workspace'
    subprocess.run(['/usr/bin/mount','--bind',str(copied),str(WORKSPACE)],check=True,timeout=5)
    actual = WORKSPACE.stat()
    if (actual.st_dev,actual.st_ino)!=(copied.stat().st_dev,copied.stat().st_ino) or actual.st_ino==before.st_ino:
        raise ValueError('Canonical path does not bind to the private copy')
    checkpoint = json.loads((root/'input/checkpoint.json').read_bytes())
    if (checkpoint['canonical_workspace']!=str(WORKSPACE) or checkpoint['canonical_project']!=str(WORKSPACE/'repo')
        or checkpoint['opencode_binary_sha256']!=BINARY_SHA or checkpoint['contains_provider_master_key'] is not False):
        raise ValueError('Checkpoint path or binary proof differs')
    config = WORKSPACE/'config/opencode.json'
    options = json.loads(config.read_bytes())['provider']['experiment']['options']
    if options['apiKey']!='local-session-no-master-key' or not options['baseURL'].startswith('http://127.0.0.1:'):
        raise ValueError('Checkpoint provider differs from the original dummy loopback configuration')
    home = root/'empty-home'
    home.mkdir(mode=0o700)
    env = dict(PATH='/usr/local/bin:/usr/bin:/bin',HOME=str(home),USERPROFILE=str(home),
        OPENCODE_CONFIG=str(config),NO_COLOR='1',OPENCODE_DISABLE_AUTOUPDATE='1',OPENCODE_DISABLE_SHARE='1',
        OPENCODE_DISABLE_LSP_DOWNLOAD='1',OPENCODE_DISABLE_DEFAULT_PLUGINS='true',
        OPENCODE_DISABLE_EXTERNAL_SKILLS='true',OPENCODE_DISABLE_CLAUDE_CODE='true',
        OPENCODE_DISABLE_MODELS_FETCH='true')
    for name in ('data','state','cache','config'):
        path = WORKSPACE/'source'/name
        if not path.is_dir() or path.resolve()!=path:
            raise ValueError('Original workspace-local XDG path required')
        env['XDG_'+name.upper()+'_HOME'] = str(path)
    record(root/'started.json',dict(install=installed,namespace=ns,outer_namespace=outer,
        network=net,local_private_bind=True,home_empty=not any(home.iterdir()),
        clock_ticks_per_second=os.sysconf('SC_CLK_TCK'),cpu_affinity=sorted(os.sched_getaffinity(0)),
        original_archive_sha256=ARCHIVE_SHA,original_binary_sha256=BINARY_SHA))
    worker = runpy.run_path(str(SCRIPTS/'c4-state-reopen-node.py'))
    cmd = [str(STRACE),'-DDD','-ff','-ttt','-s','160','-e','trace=%process','-o',str(root/'process'),
        '/usr/bin/unshare','--net','--',sys.executable,'-c',worker['NETWORK_WRAPPER'],ns['net'],
        str(root/'native-network.json'),str(args.binary),checkpoint['session_id']]
    with (root/'stdout').open('xb') as stdout, (root/'stderr').open('xb') as stderr:
        child = subprocess.Popen(cmd,cwd=WORKSPACE/'repo',env=env,stdout=stdout,stderr=stderr,start_new_session=True)
        outcome = observe(child,root/'observations.ndjson')
    stdout = root/'stdout'
    outcome.update(raw_export_bytes=stdout.stat().st_size,raw_export_sha256=digest(stdout),
        raw_export_matches_original=digest(stdout)==RAW_EXPORT_SHA,
        model_calls=0, observer='local ext4; detached process-only strace and /proc snapshots')
    if outcome['parent_exit_code']==0:
        try:
            result = json.loads(stdout.read_bytes())
            canonical = (json.dumps(result,ensure_ascii=False)+'\n').encode()
            outcome['canonical_export_sha256'] = hashlib.sha256(canonical).hexdigest()
            outcome['canonical_export_matches_original'] = (
                outcome['canonical_export_sha256']==checkpoint['session_export_sha256'])
        except ValueError:
            outcome['canonical_export_matches_original'] = False
    record(root/'diagnosis.json',outcome)
    print(json.dumps({k:v for k,v in outcome.items() if k not in ('first_post_exit','last','final_natural_empty')}))


def main():
    p = argparse.ArgumentParser(description=__doc__)
    for name in ('archive','receipt','binary','out'):
        p.add_argument('--'+name,type=Path,required=True)
    p.add_argument('--outer',help=argparse.SUPPRESS)
    args = p.parse_args()
    os.umask(0o077)
    if os.geteuid()!=0 or not args.out.is_absolute() or args.out.resolve()!=args.out or args.out.exists():
        raise ValueError('Root and a fresh canonical local diagnostic directory are required')
    if (digest(args.archive),digest(args.receipt),digest(args.binary))!=(ARCHIVE_SHA,RECEIPT_SHA,BINARY_SHA):
        raise ValueError('Only the verified original C4 checkpoint and OpenCode binary can run')
    if args.outer is not None:
        return inner(args)
    cmd = ['/usr/bin/unshare','--mount','--net','--pid','--fork','--kill-child','--mount-proc',
           '--propagation','private','--',sys.executable,'-B',str(Path(__file__).resolve()),
           *sys.argv[1:],'--outer',json.dumps(identities())]
    subprocess.run(cmd,check=True,timeout=65)


if __name__=='__main__':
    main()
