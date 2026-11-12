"""Cold-open a shared filesystem on a second host; accept private config on stdin."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess as sp
import tempfile
import time

from bench.runtime.tree_verify import write_probe
from bench.runtime.smoke_cleanup import mounts_below


def identity():
    return {name: hashlib.sha256(Path(path).read_bytes()).hexdigest() for name,path in (
        ('machine', '/etc/machine-id'), ('boot', '/proc/sys/kernel/random/boot_id'))}


def different_host(source, target):
    if any(source.get(key) == target.get(key) or not source.get(key) or not target.get(key)
           for key in ('machine','boot')):
        raise ValueError('Move requires distinct machine-id and boot-id')


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def mounted(point):
    # stat-based ismount() returns false for a disconnected FUSE mount.
    return bool(mounts_below([point]))


def close_target(proc, point):
    proof = dict(exit_code=None, process_exit=None, mount_absent=False, errors={})
    try:
        if mounted(point):
            proof['exit_code'] = sp.run(['fusermount3','-u',str(point)],capture_output=True,timeout=30).returncode
    except Exception as error:
        proof['errors']['unmount'] = str(error)
    try:
        if proc:
            try:
                proof['process_exit'] = proc.wait(timeout=20)
            except sp.TimeoutExpired:
                proc.terminate()
                try: proc.wait(timeout=5)
                except sp.TimeoutExpired:
                    proc.kill();proc.wait(timeout=5)
    except Exception as error:
        proof['errors']['process'] = str(error)
    try:
        proof['mount_absent'] = not mounted(point)
    except Exception as error:
        proof['errors']['mount-status'] = str(error)
    proof['passed'] = (proof['exit_code']==0 and proof['process_exit']==0
                       and proof['mount_absent'] and not proof['errors'])
    return proof


def retain_or_remove_scratch(proof, scratch):
    proof['scratch_path'] = str(scratch)
    if proof['passed']:
        try:
            if mounts_below([scratch]):
                raise ValueError('target scratch contains a mount')
            shutil.rmtree(scratch)
        except Exception as error:
            proof['scratch_cleanup_error'] = str(error)
            proof['passed'] = False
    proof['scratch_removed'] = not scratch.exists()
    proof['passed'] = proof['passed'] and proof['scratch_removed']


def execute(request):
    check_timeout=request.get('check_timeout',300)
    if type(check_timeout)!=int or not 1<=check_timeout<=900:
        raise ValueError('check_timeout must be 1..900 seconds')
    signal.alarm(check_timeout+210)
    proof = {'passed':False,'formal_sample':False,'target_identity':identity(),
             'workspace_payload_copied':False,'real_resume':False,'native_model_calls':0,
             'agent_process_started':False,'scope':'filesystem-only clean reopen'}
    proof['verifier_sha256']=digest(Path(__file__).with_name('tree_verify.py'))
    proof['target_script_sha256']=digest(Path(__file__))
    proof['check_timeout_seconds']=check_timeout
    different_host(request['source_identity'],proof['target_identity'])
    point = Path('/srv/ax-pilot-session')
    if mounted(point) or point.is_symlink() or (point.exists() and any(point.iterdir())):
        raise ValueError('Canonical mount must be absent or empty on target')
    tools = Path(request['tools'])
    product = request['product']
    if product not in ('brewfs','juicefs'):
        raise ValueError('Unsupported filesystem')
    for name in (product,):
        if digest(tools/name) != request['binary_sha256'][name]:
            raise ValueError('Target binary differs: '+name)
    point.mkdir(exist_ok=True)
    scratch = Path(tempfile.mkdtemp(prefix='ax-five-move-'))
    proc = None
    with (scratch/'mount.log').open('w') as log:
        try:
            env = dict(os.environ, **request['env'])
            if product == 'brewfs':
                config = request['config']
                config['cache']['cache_root'] = str(scratch/'cache')
                path=scratch/'mount.json'; path.write_text(json.dumps(config)); path.chmod(0o600)
                command=[str(tools/product),'mount','--config',str(path),str(point)]
            else:
                command=[str(tools/product),'mount','--enable-xattr','--enable-cap',
                         '--cache-dir',str(scratch/'cache'),request['redis_url'],str(point)]
            started=time.monotonic_ns()
            proc=sp.Popen(command,env=env,stdout=log,stderr=sp.STDOUT)
            deadline=time.monotonic()+90
            while not mounted(point):
                if proc.poll() is not None:
                    raise RuntimeError('Target filesystem process exited before mount')
                if time.monotonic()>deadline:
                    raise TimeoutError('Target mount readiness')
                time.sleep(.1)
            proof['mount_seconds']=(time.monotonic_ns()-started)/1e9
            workspace=point/'workspace'
            check_started=time.monotonic_ns()
            import sys
            # A child deadline closes all hashing threads/FDs before unmount cleanup.
            checked=sp.run([sys.executable,'-m','bench.runtime.tree_verify',str(workspace)],
                input=json.dumps(request['expected_tree']),text=True,capture_output=True,
                timeout=check_timeout,check=True)
            proof['tree_verification']=json.loads(checked.stdout)
            proof['tree_check_seconds']=(time.monotonic_ns()-check_started)/1e9
            proof['source_tree_equal']=True
            proof['tree_entries']=len(request['expected_tree'])
            proof['write_probe']=write_probe(workspace)
            proof['elapsed_seconds']=(time.monotonic_ns()-started)/1e9
            proof['passed']=True
        except Exception as error:
            proof['error']=str(error)
            if isinstance(error,sp.CalledProcessError):
                proof['check_stderr']=(error.stderr or '')[-1500:]
        finally:
            signal.alarm(0)
            proof['unmount']=close_target(proc,point)
            proof['passed'] = proof['passed'] and proof['unmount']['passed']
            if not proof['passed']:
                text=(scratch/'mount.log').read_text(errors='replace')[-2000:]
                for value in request['env'].values():
                    if len(value)>12: text=text.replace(value,'[redacted]')
                proof['log_tail']=text
            retain_or_remove_scratch(proof,scratch)
    return proof


def main():
    import sys
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--identity',action='store_true')
    args=parser.parse_args()
    if args.identity:
        print(json.dumps(identity()));return 0
    def expired(*_):
        raise TimeoutError('Bounded target process deadline or termination')
    signal.signal(signal.SIGALRM,expired)
    signal.signal(signal.SIGTERM,expired)
    signal.alarm(450)
    try:
        result=execute(json.load(sys.stdin))
    except Exception as error:
        result={'passed':False,'error':str(error)}
    finally:
        signal.alarm(0)
    print(json.dumps(result))
    return 0 if result['passed'] else 2


if __name__=='__main__':
    raise SystemExit(main())
