"""Linux node endpoint: supervised foreground mount and one barrier worker.

Use in the SAME per-Agent container/cgroup for mount and workload. No lazy or
forced unmount, PID search/kill, automatic replay retry, or resource deletion.
"""
import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import select
import signal
import socket
import subprocess
import sys
import threading
import time

from .common import (alive, cgroup_limits, digest, emit, identity, load, mounts,
                     pinned, prepare, private_dir, result_check, spec, write_new)
from .prepare import anchor, native_argv


def control(directory, action, **fields):
    directory = Path(directory)
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as conn:
        conn.connect(str(directory/'control.sock'))
        conn.sendall((json.dumps({'action': action, **fields})+'\n').encode())
        with conn.makefile('rb') as f:
            result = json.loads(f.readline(1024*1024))
    if not result.get('passed'):
        raise ValueError(result.get('error', 'mount control failed'))
    return result


def mount_snapshot(s, child, expected):
    actual = mounts(s['mount_point'])
    if child.poll() is not None or actual != [expected]:
        raise ValueError('mount/process changed or stopped')
    return {'passed': True, 'mount': expected, 'process': identity(child.pid),
            'anchor': s['anchor'], 'budget': cgroup_limits(s['budget'])}


def validate_mount(s):
    if s['filesystem'] not in ('brewfs', 'juicefs') or not s.get('anchor'):
        raise ValueError('filesystem and stable shared-volume anchor required')
    point = Path(s['mount_point'])
    if not point.is_absolute() or point.is_symlink() or not point.is_dir() or str(point.resolve()) != str(point):
        raise ValueError('existing canonical mount directory required')
    if mounts(point) or any(point.iterdir()):
        raise ValueError('unmounted empty directory required')
    cmd, env = prepare(s['command']['path'], s['command']['sha256'])
    if cmd['argv'][1] != 'mount' or str(point) not in cmd['argv']:
        raise ValueError('native mount command with explicit mount point required')
    if any(x in cmd['argv'] for x in ('-d', '--background', '--daemon')):
        raise ValueError('foreground native mount required')
    if s['filesystem'] == 'brewfs':
        if '--config' not in cmd['argv']:
            raise ValueError('pinned BrewFS config required')
        config_path = cmd['argv'][cmd['argv'].index('--config')+1]
        config = load(config_path)  # JSON is also accepted by BrewFS YAML parser.
        if not any(x['path'] == config_path for x in cmd['inputs']):
            raise ValueError('mount config not pinned')
        backend = config.get('meta', {}).get('backend')
        if backend not in ('redis', 'tikv'):
            raise ValueError('Redis/TiKV required; no SQLite fallback')
        if config.get('volume_format', 'flat-v1') == 'workspace-native-v2' and config.get('cache', {}).get('writeback_mode') != 'upload_before_commit':
            raise ValueError('native-v2 requires explicit supported upload mode')
        if config.get('workspace') != s['anchor'].get('workspace'):
            raise ValueError('workspace differs from handoff anchor')
        if config.get('workspace_namespace') != s['anchor'].get('namespace'):
            raise ValueError('namespace differs from handoff anchor')
    else:
        config_path = s['config']['path']
        config = load(pinned(s['config']))
    if anchor(s['filesystem'],config) != s['anchor']:
        raise ValueError('shared backend identity differs from mount anchor')
    if not s.get('metrics_address') and s.get('purpose')!='observer-calibration-off':raise ValueError('native metrics must be explicitly enabled')
    expected = native_argv(s['filesystem'],cmd['argv'][0],config_path,config,str(point),s['cluster'],s['metrics_address'],
                           s.get('dynamic_block','auto'))
    if cmd['argv'] != expected: raise ValueError('mount argv overrides reviewed native profile')
    cache=Path(config['cache']['cache_root'] if s['filesystem']=='brewfs' else config['cache_dir'])
    if not cache.is_absolute() or cache.is_symlink() or not cache.is_dir() or any(cache.iterdir()):
        raise ValueError('fresh existing private cache directory required')
    if cache.resolve().is_relative_to(point) or point.is_relative_to(cache.resolve()):
        raise ValueError('cache and tested mount must be disjoint')
    return cmd, env


def serve(ref, directory):
    s = spec(ref, 'experiment-mount-v1')
    cmd, env = validate_mount(s)
    budget = cgroup_limits(s['budget'])
    directory = Path(directory)
    active = set()
    with (directory/'stdout.log').open('xb') as out, (directory/'stderr.log').open('xb') as err:
        child = subprocess.Popen(cmd['argv'], cwd=cmd['cwd'], env=env, stdout=out, stderr=err, start_new_session=True)
        write_new(directory/'process.json', {'process': identity(child.pid), 'supervisor': identity(os.getpid()), 'spec': ref})
        deadline = time.monotonic() + s.get('ready_timeout_seconds', 60)
        observed = []
        while time.monotonic() < deadline:
            if child.poll() is not None:
                raise ValueError('mount exited before readiness')
            observed = mounts(s['mount_point'])
            if observed: break
            time.sleep(.05)
        if len(observed) != 1 or observed[0]['filesystem'] not in s['allowed_fstypes'] or not observed[0]['filesystem'].startswith('fuse'):
            raise ValueError('exact expected FUSE mount missing; process retained for inspection')
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as server:
            server.bind(str(directory/'control.sock')); os.chmod(directory/'control.sock', 0o600)
            server.listen(16)
            write_new(directory/'ready.json', {**mount_snapshot(s, child, observed[0]), 'budget': budget, 'spec': ref})
            while True:
                readable, _, _ = select.select([server], [], [], .2)
                if child.poll() is not None:
                    write_new(directory/'unexpected-exit.json', {'exit_code': child.returncode, 'active_workers': sorted(active)})
                    return
                if not readable: continue
                with server.accept()[0] as conn:
                    request={}
                    try:
                        with conn.makefile('rb') as f: request = json.loads(f.readline(65536))
                        snapshot = mount_snapshot(s, child, observed[0])
                        action = request['action']
                        if action == 'status': result = snapshot
                        elif action == 'acquire':
                            if active: raise ValueError('one workload worker per Agent mount')
                            active.add(request['worker']); result = snapshot
                        elif action == 'release':
                            active.remove(request['worker']); result = snapshot
                        elif action == 'stop':
                            if active: raise ValueError('workload active or unaccounted; refuse unmount')
                            # Original live Popen object owns the signal target. Never kill a reused PID.
                            child.send_signal(signal.SIGINT)
                            code = child.wait()  # drain may be long; never force/lazy unmount.
                            absent = not mounts(s['mount_point'])
                            result = {'passed': code == 0 and absent, 'exit_code': code,
                                      'mount_absent': absent, 'anchor': s['anchor'],
                                      'original_mount': observed[0], 'active_workers': []}
                            write_new(directory/'stopped.json', result)
                        else: raise ValueError('unknown mount action')
                    except Exception as error:
                        result = {'passed': False, 'error': str(error)}
                    conn.sendall((json.dumps(result)+'\n').encode())
                    if request.get('action') == 'stop' and 'exit_code' in result: return


def start(ref, directory):
    s = spec(ref, 'experiment-mount-v1')
    validate_mount(s); cgroup_limits(s['budget'])
    directory = private_dir(directory)
    if directory.is_relative_to(Path(s['mount_point'])):
        raise ValueError('mount evidence must be outside mount')
    with (directory/'supervisor.log').open('xb') as log:
        child = subprocess.Popen([sys.executable, '-m', 'bench.runtime.node', 'serve', '--spec', ref['path'],
                                  '--sha256', ref['sha256'], '--output', str(directory)],
                                 cwd=Path(__file__).resolve().parents[2], stdout=log, stderr=log, start_new_session=True)
    deadline = time.monotonic() + s.get('ready_timeout_seconds', 60) + 2
    while time.monotonic() < deadline:
        if (directory/'ready.json').exists():
            threading.Thread(target=child.wait,daemon=True).start()
            return control(directory, 'status')
        if child.poll() is not None: raise ValueError('mount supervisor failed; inspect retained logs')
        time.sleep(.05)
    raise ValueError('mount readiness unresolved; inspect original process, do not retry')


def process_group_members(pgid):
    members = []
    for item in Path('/proc').iterdir():
        if not item.name.isdecimal(): continue
        try:
            fields = (item/'stat').read_text().rsplit(')', 1)[1].split()
            if int(fields[2]) == pgid and fields[0] != 'Z': members.append(int(item.name))
        except (FileNotFoundError, ProcessLookupError): pass
    return members


def run_job(job, directory, send=emit, prepared=None):
    # Large trace/blob SHA verification belongs before the release barrier.
    # Reviewed inputs are immutable/read-only throughout the cohort.
    s, env = prepared if prepared is not None else prepare(job['step']['path'], job['step']['sha256'])
    directory = private_dir(directory)
    result_path = Path(job['result']['path']) if job.get('result') else None
    if result_path and result_path.exists(): raise ValueError('stale result file')
    started = time.monotonic_ns()
    with (directory/'stdout.log').open('xb') as out, (directory/'stderr.log').open('xb') as err:
        child = subprocess.Popen(s['argv'], cwd=s['cwd'], env=env, stdout=subprocess.PIPE, stderr=err, start_new_session=True)
        write_new(directory/'process.json', {'process': identity(child.pid), 'step': job['step']})
        send('started', job=job['id'], process=identity(child.pid))
        # Drain stdout concurrently. Only a completed native OpenCode tool event
        # is forwarded, without arguments, messages or file contents.
        def copy_output():
            for line in child.stdout:
                out.write(line); out.flush()
                if job.get('observe_opencode_tools'):
                    try:
                        event = json.loads(line)
                        part = event.get('part', {})
                        if event.get('type') == 'tool_use' and part.get('state', {}).get('status') == 'completed':
                            send('tool_completed', job=job['id'], session_id=event.get('sessionID'), call_id=part.get('callID'))
                    except (ValueError, TypeError): pass
        reader = threading.Thread(target=copy_output); reader.start()
        code = child.wait(); reader.join(); child.stdout.close()
    leaked = process_group_members(child.pid)
    value = {'job': job['id'], 'exit_code': code, 'seconds': (time.monotonic_ns()-started)/1e9,
             'passed': code == 0 and not leaked, 'remaining_process_group': leaked,
             'automatic_retry': False, 'step_sha256': job['step']['sha256'],
             'workload_id':job.get('workload_id','unspecified')}
    if result_path:
        value['result_path']=str(result_path)
        try:
            value['result'] = load(result_path)  # Preserve mismatched/partial actual values too.
            value['result_sha256'] = digest(result_path)
            result_check(result_path, job['result']['equals'])
        except Exception as error:
            value.update(passed=False, result_error=type(error).__name__)
    write_new(directory/'finished.json', value)
    return value


def worker(ref, directory):
    s = spec(ref, 'experiment-worker-v1')
    directory = private_dir(directory)
    if s.get('purpose') != 'adapter-test':
        resources = cgroup_limits(s['budget'])
    else: resources = {'scope': 'adapter-test; not an experiment result'}
    jobs = s['jobs']
    if not jobs or len({j['id'] for j in jobs}) != len(jobs): raise ValueError('unique nonempty jobs required')
    prepared_jobs=[]
    for job in jobs:
        prepared_jobs.append(prepare(job['step']['path'], job['step']['sha256']))
        if job.get('result') and Path(job['result']['path']).exists(): raise ValueError('stale result before barrier')
    if s.get('purpose')!='adapter-test':
        if s['kind'] not in ('trace','ml','s3','control'):raise ValueError('explicit workload kind required')
        if s['kind'] in ('trace','ml') and not s.get('mount'):raise ValueError('filesystem workload requires supervised mount')
        for job in jobs:
            if not job.get('workload_id'):raise ValueError('explicit workload identity required for per-task statistics')
            expected=job.get('result',{}).get('equals',{})
            keys={'trace':{'completed','mode','valid','operations','actual_read_bytes','actual_write_bytes','remaining_handles'},
                  'ml':{'completed','attempted_files','actual_read_bytes','errors'},
                  's3':{'passed','requests','application_bytes','global_concurrency','shard'},'control':set()}[s['kind']]
            if not keys.issubset(expected):raise ValueError('workload result predicates incomplete')
            if s['kind']=='trace' and (expected['completed'] is not True or expected['remaining_handles']!=0 or expected['operations']<=0):
                raise ValueError('incomplete trace acceptance')
    mount = s.get('mount')
    if mount:
        expected_mount = spec(mount['spec'], 'experiment-mount-v1')
        if expected_mount.get('purpose')=='observer-calibration-off' and s.get('purpose')!='observer-calibration-off':
            raise ValueError('uninstrumented mount cannot enter formal cohort')
        ready = load(Path(mount['directory'])/'ready.json')
        if ready['spec'] != mount['spec'] or ready['anchor'] != expected_mount['anchor']:
            raise ValueError('mount receipt belongs to a different reviewed spec')
        snapshot = control(mount['directory'], 'acquire', worker=s['worker_id'])
        if snapshot['mount'] != ready['mount'] or snapshot['anchor'] != ready['anchor']:
            raise ValueError('mount identity changed')
    elif s.get('requires_mount', True): raise ValueError('missing mount receipt')
    write_new(directory/'ready.json', {'worker_id': s['worker_id'], 'spec': ref, 'resources': resources})
    from .common import isolation_identity
    emit('ready', worker=s['worker_id'], spec_sha256=ref['sha256'], resources=resources,
         isolation=isolation_identity() if s.get('purpose') != 'adapter-test' else None,
         purpose=s.get('purpose','experiment'), expected_jobs=len(jobs), workload_kind=s.get('kind','adapter-test'))
    request = json.loads(sys.stdin.readline())
    if request != {'action': 'go', 'spec_sha256': ref['sha256']}:
        if mount: control(mount['directory'], 'release', worker=s['worker_id'])
        raise ValueError('barrier not released with this exact spec')
    if mount: control(mount['directory'], 'status')
    write_new(directory/'released.json', {'spec': ref, 'monotonic_ns': time.monotonic_ns()})
    results = []
    for index, job in enumerate(jobs):
        value = run_job(job, directory/f'job-{index:04}', prepared=prepared_jobs[index])
        results.append(value); emit('job_finished', worker=s['worker_id'], **value)
        if not value['passed']: break
    passed = len(results) == len(jobs) and all(x['passed'] for x in results)
    # A leftover descendant retains the admission token; never call it drained.
    if mount and not any(x['remaining_process_group'] for x in results):
        control(mount['directory'], 'release', worker=s['worker_id'])
    result = {'passed': passed, 'worker': s['worker_id'], 'jobs': len(results), 'expected_jobs': len(jobs),
              'purpose': s.get('purpose', 'experiment'), 'spec_sha256': ref['sha256']}
    write_new(directory/'finished.json', result); emit('finished', **result)
    return 0 if passed else 1


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('action', choices=['start','serve','status','stop','worker','stage-json',
                                     'observe-start','observe-status','observe-mark','observe-stop'])
    p.add_argument('--spec'); p.add_argument('--sha256'); p.add_argument('--output', required=True)
    p.add_argument('--label')
    a = p.parse_args(); ref = {'path': a.spec, 'sha256': a.sha256}
    if a.action.startswith('observe-'):
        from . import observe
        action=a.action.removeprefix('observe-')
        result=observe.start(ref,a.output) if action=='start' else observe.request(a.output,action,a.label)
        emit('result',**result);return 0
    if a.action == 'stage-json':
        # Only a new private handoff file under an existing approved work root.
        request = spec(ref, 'experiment-stage-json-v1')
        data = json.loads(sys.stdin.readline())
        raw = base64.b64decode(data['base64'], validate=True)
        if hashlib.sha256(raw).hexdigest() != data['sha256']: raise ValueError('transfer checksum')
        json.loads(raw)
        root = Path(request['root']).resolve(strict=True); path = Path(a.output)
        if not path.is_absolute() or not path.parent.resolve(strict=True).is_relative_to(root):
            raise ValueError('transfer outside approved work root')
        if str(path) not in request['allowed_paths']: raise ValueError('transfer path not in reviewed allowlist')
        with path.open('xb') as out:
            os.chmod(path,0o600); out.write(raw); out.flush(); os.fsync(out.fileno())
        emit('result', passed=True, sha256=digest(path)); return 0
    if a.action == 'worker': return worker(ref, a.output)
    if a.action == 'serve': serve(ref, a.output); return 0
    result = start(ref, a.output) if a.action == 'start' else control(a.output, a.action)
    emit('result', **result)
    return 0


if __name__ == '__main__':
    try: sys.exit(main())
    except Exception as error:
        emit('error', error_type=type(error).__name__, message=str(error)); sys.exit(1)
