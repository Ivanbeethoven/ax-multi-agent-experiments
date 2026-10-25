#!/usr/bin/env python3
"""Linux, Python stdlib only. Probe own scope and sample around a foreground command.

No Kubernetes API, root privileges, mounts, host counters, or credential discovery.
Raw counters are preserved; inaccessible values are null with a reason, never zero.
"""
import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import platform
import re
import resource
import signal
import subprocess
import sys
import time

CG_FILES = ('cpu.stat', 'cpu.max', 'cpuset.cpus.effective', 'memory.current',
            'memory.peak', 'memory.max', 'memory.stat', 'memory.events',
            'io.stat', 'cpu.pressure', 'memory.pressure', 'io.pressure',
            'pids.current', 'pids.max')


def read_value(path):
    try:
        return {'value': Path(path).read_text().strip(), 'reason': None}
    except OSError as exc:
        return {'value': None, 'reason': f'{type(exc).__name__}:errno={exc.errno}'}


def unescape(value):
    return re.sub(r'\\([0-7]{3})', lambda m: chr(int(m[1], 8)), value)


def mounts(text):
    result = []
    for line in text.splitlines():
        before, sep, after = line.partition(' - ')
        left, right = before.split(), after.split()
        if not sep or len(left) < 6 or len(right) < 3:
            continue
        result.append({'id': left[0], 'device': left[2], 'root': unescape(left[3]),
                       'mountpoint': unescape(left[4]), 'options': left[5],
                       'fs_type': right[0]})
    return result


def cgroup_candidate(membership, entries):
    """Map the visible v2 membership into its actual mount, never guess the root."""
    group = next((s[3:] for s in membership.splitlines() if s.startswith('0::')), None)
    if group is None or '..' in Path(group).parts:
        return None
    for entry in entries:
        if entry['fs_type'] != 'cgroup2':
            continue
        try:
            relative = Path(group).relative_to(entry['root'])
        except ValueError:
            continue
        return Path(entry['mountpoint']) / relative
    return None


def mount_for(path, entries):
    path = Path(path).resolve()
    matches = [m for m in entries if path.is_relative_to(Path(m['mountpoint']))]
    return max(matches, key=lambda m: len(m['mountpoint'])) if matches else None


def own_cgroup():
    membership = read_value('/proc/self/cgroup')
    info = read_value('/proc/self/mountinfo')
    entries = mounts(info['value'] or '')
    candidate = cgroup_candidate(membership['value'] or '', entries)
    members = read_value(candidate / 'cgroup.procs') if candidate else {'value': None}
    verified = bool(members['value'] and str(os.getpid()) in members['value'].split())
    return {
        'path': str(candidate) if candidate else None,
        'self_membership_verified': verified,
        'scope': 'own_cgroup_container_boundary_unconfirmed' if verified else 'unavailable',
        'reason': None if verified else 'Cannot establish own cgroup v2; no host-root fallback',
        'membership': membership,
    }


def safe_link(path):
    try:
        return os.readlink(path)
    except OSError:
        return None


def probe(work_root):
    entries = mounts(read_value('/proc/self/mountinfo')['value'] or '')
    status = read_value('/proc/self/status')['value'] or ''
    security = dict(line.split(':', 1) for line in status.splitlines()
                    if line.split(':', 1)[0] in ('CapEff', 'NoNewPrivs', 'Seccomp'))
    root = Path(work_root).resolve(strict=True)
    stat = os.statvfs(root)
    return {
        'schema_version': 1, 'utc_ns': time.time_ns(), 'kernel': platform.release(),
        'architecture': platform.machine(), 'uid': os.getuid(), 'gid': os.getgid(),
        'container_label': os.environ.get('EXPERIMENT_CONTAINER_ID'),
        'pod_uid': os.environ.get('EXPERIMENT_POD_UID'),
        'node_id': os.environ.get('EXPERIMENT_NODE_ID'),
        'placement_source': 'operator-supplied env; not inferred from hostname',
        'network_namespace_local_id': safe_link('/proc/self/ns/net'),
        'network_scope': 'namespace; inode is not globally unique across hosts',
        'security': security, 'cgroup': own_cgroup(),
        'work_root': str(root), 'work_mount': mount_for(root, entries),
        'filesystem_available_bytes': stat.f_bavail * stat.f_frsize,
        'fuse_device_exists': Path('/dev/fuse').exists(),
        'fuse_device_access_hint': os.access('/dev/fuse', os.R_OK | os.W_OK),
        'fuse_mount_test': 'not_attempted',
        'capability_note': 'Device access is only a hint, not proof of mount permission',
    }


def sample(cgroup, pid=None):
    begin = time.monotonic_ns()
    if cgroup['self_membership_verified']:
        values = {name: read_value(Path(cgroup['path']) / name) for name in CG_FILES}
    else:
        values = {name: {'value': None, 'reason': cgroup['reason']} for name in CG_FILES}
    process = None
    if pid:
        # One process only, not a sum of all children. wait4 rusage is separate.
        process = {name: read_value(f'/proc/{pid}/{name}') for name in ('stat', 'io')}
    return {'utc_ns': time.time_ns(), 'monotonic_ns': begin,
            'cgroup': values, 'network_dev': read_value('/proc/self/net/dev'),
            'process': process, 'collector_read_ns': time.monotonic_ns() - begin}


def write_json(path, data):
    with Path(path).open('x', encoding='utf-8') as stream:
        json.dump(data, stream, ensure_ascii=False, indent=2)
        stream.write('\n')


def group_exists(pgid):
    try:
        os.killpg(pgid, 0)
        return True
    except ProcessLookupError:
        return False


def cleanup_group(pgid, grace=5):
    """Only the new session we created; direct-parent exit does not end cleanup."""
    if not group_exists(pgid):
        return {'descendants_detected': False, 'sigkill_sent': False}
    try:
        os.killpg(pgid, signal.SIGTERM)
    except ProcessLookupError:
        return {'descendants_detected': False, 'sigkill_sent': False}
    deadline = time.monotonic() + grace
    while group_exists(pgid) and time.monotonic() < deadline:
        time.sleep(0.02)
    killed = False
    if group_exists(pgid):
        try:
            os.killpg(pgid, signal.SIGKILL)
            killed = True
        except ProcessLookupError:
            pass
    return {'descendants_detected': True, 'sigkill_sent': killed}


def run(args):
    out = args.out.resolve()
    work = args.work_root.resolve(strict=True)
    if out == work or out.is_relative_to(work):
        raise ValueError('Observation output must be outside the tested work root')
    if not math.isfinite(args.interval) or not math.isfinite(args.timeout) or args.interval <= 0 or args.timeout <= 0:
        raise ValueError('Interval and timeout must be positive')
    command = args.command[1:] if args.command[:1] == ['--'] else args.command
    if not command:
        raise ValueError('A foreground command is required after --')
    out.mkdir(parents=True, exist_ok=False)
    identity = probe(work)
    identity.update({'run_id': args.run_id, 'worker_id': args.worker_id,
                     'phase': args.phase, 'interval_s': args.interval,
                     'timeout_s': args.timeout, 'command_name': Path(command[0]).name,
                     'command_sha256': hashlib.sha256(json.dumps(command).encode()).hexdigest(),
                     'argv_saved': False, 'collector_pid': os.getpid(),
                     'cgroup_includes': 'command + collector + any other process in this cgroup'})
    write_json(out / 'environment.json', identity)
    proc = None
    received = []
    timed_out = False
    terminated_at = None
    samples = 0
    old_handlers = {}
    usage_before = resource.getrusage(resource.RUSAGE_CHILDREN)
    start = time.monotonic_ns()
    error = None
    code = None
    cleanup = {'descendants_detected': False, 'sigkill_sent': False}
    try:
        with (out / 'samples.jsonl').open('x', encoding='utf-8') as stream, \
                (out / 'stdout.log').open('xb') as stdout, (out / 'stderr.log').open('xb') as stderr:
            def emit():
                nonlocal samples
                row = sample(identity['cgroup'], proc.pid if proc else None)
                row['index'] = samples
                stream.write(json.dumps(row) + '\n')
                stream.flush()
                samples += 1
            emit()
            proc = subprocess.Popen(command, stdout=stdout, stderr=stderr, start_new_session=True)
            def on_signal(signum, _frame):
                received.append(signum)
            for signum in (signal.SIGTERM, signal.SIGINT):
                old_handlers[signum] = signal.signal(signum, on_signal)
            next_sample = time.monotonic()
            while proc.poll() is None:
                now = time.monotonic()
                if now >= next_sample:
                    emit()
                    next_sample = time.monotonic() + args.interval
                timed_out = timed_out or now - start / 1e9 >= args.timeout
                if (timed_out or received) and terminated_at is None:
                    try:
                        os.killpg(proc.pid, signal.SIGTERM)
                    except ProcessLookupError:
                        pass
                    terminated_at = now
                if terminated_at is not None and now - terminated_at > 5:
                    try:
                        os.killpg(proc.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                time.sleep(0.02)
            code = proc.wait()
            # A shell/launcher can exit while its children ignore TERM.
            cleanup = cleanup_group(proc.pid)
            emit()
    except Exception as exc:
        error = f'{type(exc).__name__}: {exc}'
        if proc is not None and proc.poll() is None:
            os.killpg(proc.pid, signal.SIGKILL)
            code = proc.wait()
        if proc is not None:
            cleanup = cleanup_group(proc.pid, grace=0)
    finally:
        for signum, previous in old_handlers.items():
            signal.signal(signum, previous)
    end = time.monotonic_ns()
    usage = resource.getrusage(resource.RUSAGE_CHILDREN)
    result = {'schema_version': 1, 'run_id': args.run_id, 'worker_id': args.worker_id,
              'phase': args.phase, 'exit_code': code, 'timed_out': timed_out,
              'signals_received': received, 'error': error,
              'process_group_cleanup': cleanup,
              'wall_seconds': (end - start) / 1e9, 'samples': samples,
              'child_user_cpu_seconds': usage.ru_utime - usage_before.ru_utime,
              'child_system_cpu_seconds': usage.ru_stime - usage_before.ru_stime,
              'child_maxrss_kib': usage.ru_maxrss,
              'maxrss_scope': 'Linux wait4 child maximum; not concurrent process-tree RSS sum',
              'execution_status': 'completed' if code == 0 and not timed_out and not received and not error
                  and not cleanup['descendants_detected'] else 'failed',
              'correctness': 'not_checked_by_observer',
              'durable_export': 'not_performed; copy results before allocation ends'}
    write_json(out / 'observer-result.json', result)
    return 0 if result['execution_status'] == 'completed' else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='mode', required=True)
    p = sub.add_parser('probe')
    p.add_argument('--work-root', type=Path, required=True)
    p.add_argument('--out', type=Path, required=True)
    p = sub.add_parser('run')
    p.add_argument('--work-root', type=Path, required=True)
    p.add_argument('--out', type=Path, required=True)
    p.add_argument('--run-id', required=True)
    p.add_argument('--worker-id', required=True)
    p.add_argument('--phase', choices=['prepare', 'replay', 'verify', 'service', 'migration'], required=True)
    p.add_argument('--interval', type=float, default=1.0)
    p.add_argument('--timeout', type=float, default=1800)
    p.add_argument('command', nargs=argparse.REMAINDER)
    args = parser.parse_args()
    if sys.platform != 'linux':
        parser.error('Run inside the Linux container, not on Windows')
    if args.mode == 'probe':
        write_json(args.out, probe(args.work_root))
        return 0
    return run(args)


if __name__ == '__main__':
    sys.exit(main())
