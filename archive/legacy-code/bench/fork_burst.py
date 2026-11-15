"""One reviewed creation burst; first use/isolation/drain are separate."""
import argparse
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
import re
import subprocess
import threading
import time
from pathlib import Path
from urllib.parse import urlsplit

from .experiment_io import digest, load, write_new
from .experiment_step import execute, prepare
from .runtime.common import mounts, pinned, private_dir


NATIVE = 'suite-fork-burst-native-v2'
SOURCE_BOUND = 'suite-fork-burst-native-v3'
JUICEFS_CLONE_THREADS = 4


def canonical(name):
    path = Path(name)
    if not path.is_absolute() or str(path) != name or path.resolve() != path:
        raise ValueError('canonical absolute native path required')
    return path


def juicefs_mount(root):
    observed = mounts(root)
    if len(observed) != 1 or observed[0]['filesystem'] != 'fuse.juicefs':
        raise ValueError('one active JuiceFS FUSE mount required')
    return observed[0]


def native_argv(s, child_id):
    native = s['native']
    binary = canonical(str(pinned(native['binary'])))
    if s['method'] == 'brewfs-fork':
        if binary.name != 'brewfs' or set(native) != {'binary','redis_url','namespace','revision','data_config'}:
            raise ValueError('exact BrewFS workspace fork configuration required')
        revision = native['revision']
        if not re.fullmatch(r'[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}:[1-9][0-9]*:[0-9a-f]{64}',revision):
            raise ValueError('exact nonempty Packed carrier revision required')
        namespace = native['namespace']
        url = urlsplit(native['redis_url'])
        if (not re.fullmatch(r'[a-zA-Z0-9_-]{1,128}',namespace) or url.scheme != 'redis' or not url.hostname
                or url.username or url.password or url.query or url.fragment or not re.fullmatch('/[0-9]+',url.path)):
            raise ValueError('explicit Redis DB and workspace namespace required; no credentials in argv')
        data_config = pinned(native['data_config'])
        return [str(binary),'workspace','--meta-backend','redis','--meta-url',native['redis_url'],
                '--workspace-namespace',namespace,'fork',revision,'--require-packed-carrier',
                '--count','1','--data-config',str(data_config)]
    if s['method'] == 'juicefs-clone':
        if binary.name != 'juicefs' or set(native) != {'binary','mount','source','destination_parent','threads'}:
            raise ValueError('exact JuiceFS clone configuration required')
        root, src, parent = (canonical(native[key]) for key in ('mount','source','destination_parent'))
        if (not src.is_dir() or not parent.is_dir() or root in (src,parent) or not src.is_relative_to(root)
                or not parent.is_relative_to(root) or parent.is_relative_to(src)):
            raise ValueError('clone requires disjoint paths under one JuiceFS mount')
        juicefs_mount(root)
        if root.stat().st_dev != src.stat().st_dev or root.stat().st_dev != parent.stat().st_dev:
            raise ValueError('clone paths must resolve to the same JuiceFS device')
        destination = parent/child_id
        if destination.exists() or destination.is_symlink():
            raise ValueError('destination already exists; never retry in place')
        threads = native['threads']
        if type(threads) is not int or threads != JUICEFS_CLONE_THREADS:
            raise ValueError('current C4 uses the JuiceFS default of four workers per clone')
        return [str(binary),'clone','-p','--threads',str(threads),str(src),str(destination)]
    raise ValueError('only BrewFS native fork and JuiceFS native clone are current C4 methods')


def native_step(s, child_id, command):
    if (command['argv'] != native_argv(s,child_id) or
            command['executable_sha256'] != s['native']['binary']['sha256'] or
            s['input_tree'] not in command['inputs'] or
            (s['method'] == 'brewfs-fork' and s['native']['data_config'] not in command['inputs'])):
        raise ValueError('reviewed child argv, binary or frozen input differs from the native arm')


def run_native(s, ref, commands, out, source=None):
    """Open evidence streams and verify inputs before releasing the native commands."""
    gate = threading.Event()
    abort = threading.Event()
    ready = threading.Barrier(len(commands)+1,timeout=15) if source is not None else None
    started = None
    root = Path(s['native']['mount']) if s['method']=='juicefs-clone' else None
    with ExitStack() as logs, ThreadPoolExecutor(max_workers=s['parallelism']) as pool:
        tasks = []
        for child, command, env in commands:
            directory = private_dir(out/child['id'])
            write_new(directory/'spec.json',command)
            stdout = logs.enter_context((directory/'stdout.log').open('xb'))
            stderr = logs.enter_context((directory/'stderr.log').open('xb'))
            tasks.append((child, command, env, directory, stdout, stderr))

        def one(child, command, env, directory, stdout, stderr):
            if ready is not None:
                try: ready.wait()
                except threading.BrokenBarrierError: return None
            gate.wait()
            if abort.is_set(): return None
            launch = time.monotonic_ns()
            pid = None
            try:
                process = subprocess.Popen(command['argv'],cwd=command['cwd'],env=env,stdout=stdout,stderr=stderr)
                pid = process.pid
                try:
                    write_new(directory/'process.json',{'pid':pid,'launch_ns':launch,'step':child['step']})
                finally:
                    code = process.wait()
                done = time.monotonic_ns()
                row = {'id':child['id'],'exit_code':code,'passed':code==0,'launch_ns':launch,'exit_ns':done,
                       'start_offset_seconds':(launch-started)/1e9,'seconds':(done-launch)/1e9}
                write_new(directory/'finished.json',row)
                return {**row,'receipt_sha256':digest(directory/'finished.json')}
            except Exception as error:
                row = {'id':child['id'],'passed':False,'error_type':type(error).__name__,'pid':pid,
                       'launch_ns':launch,'process_state':'inspect original process before retry'}
                write_new(directory/'interrupted.json',row)
                return row

        original_mount = juicefs_mount(root) if root is not None else None
        if source is not None and root is not None and original_mount != source['measurement_mount']['mount']:
            raise ValueError('JuiceFS measurement mount changed after source preflight')
        write_new(out/'native-intent.json',{'spec':ref,'method':s['method'],'native':s['native'],
                  'input_tree':s['input_tree'],'mount':original_mount,'automatic_retry':False})
        futures=[]
        try:
            for task in tasks: futures.append(pool.submit(one,*task))
            if ready is not None:
                ready.wait()
                if root is not None:
                    from .fork_source import alive
                    if (juicefs_mount(root) != original_mount or
                            not alive(source['measurement_mount']['process'])):
                        raise ValueError('JuiceFS measurement mount changed or its process exited before releasing children')
            started = time.monotonic_ns()
            gate.set()
        except BaseException:
            # Never leave a worker blocked on a missing barrier during shutdown.
            if not gate.is_set():
                abort.set()
                if ready is not None: ready.abort()
                gate.set()
            raise
        results = [future.result() for future in futures]
    final_mount = mounts(root) if root is not None else None
    mount_stable = root is None or final_mount == [original_mount]
    if source is not None and root is not None:
        from .fork_source import alive
        mount_stable = mount_stable and alive(source['measurement_mount']['process'])
    passed = all(row['passed'] for row in results) and mount_stable
    result = {'schema':'suite-fork-burst-native-result-v2' if source is None else 'suite-fork-burst-native-result-v3',
            'cell_id':s['cell_id'],
            'completed':all('exit_ns' in row for row in results),
            'passed':passed,'children':len(commands),'parallelism':s['parallelism'],
            'release_ns':started,'wall_seconds':(max(row['exit_ns'] for row in results)-started)/1e9 if passed else None,
            'results':results,'method':s['method'],'input_tree':s['input_tree'],'binary':s['native']['binary'],
            'mount_before':original_mount,'mount_after':final_mount,'performance_accepted':False,
            'scope':'creation command only; first verified read, isolation and durable closure are measured separately'}
    if source is not None: result['source_binding'] = source
    return result


def run(ref, output):
    s=load(pinned(ref))
    if s.get('schema') not in ('suite-fork-burst-v1',NATIVE,SOURCE_BOUND) or s.get('reviewed') is not True:
        raise ValueError('reviewed fork burst required')
    native=s['schema'] in (NATIVE,SOURCE_BOUND)
    children=s['children']
    if not children or len({c['id'] for c in children})!=len(children) or len(children)>64:
        raise ValueError('1..64 unique children required')
    if type(s.get('parallelism')) is not int or not 1<=s['parallelism']<=len(children):
        raise ValueError('explicit bounded creation concurrency required')
    if native and (len(children) not in (1,8,16,32) or s['parallelism']!=len(children)):
        raise ValueError('current C4 uses 1/8/16/32 independently issued requests')
    if native: pinned(s['input_tree'])
    commands=[]
    for c in children:
        valid=re.fullmatch(r'[a-zA-Z0-9_-]{1,64}',c['id']) if native else c['id'].replace('-','').replace('_','').isalnum()
        if not valid:raise ValueError('simple child ID required')
        command,env=prepare(c['step']['path'],c['step']['sha256'])
        if command['cell_id']!=s['cell_id'] or command['stage']!='fork-'+c['id']:
            raise ValueError('child step identity differs')
        if native:
            native_step(s,c['id'],command)
            commands.append((c,command,env))
    source = None
    if s['schema']==SOURCE_BOUND:
        from .fork_source import preflight
        proof = s.get('source_qualification')
        if not isinstance(proof,dict) or set(proof)!={'receipt','probe_stopped','measurement'}:
            raise ValueError('reviewed v3 requires exact independent source qualification and close')
        source = preflight(proof['receipt'],cell_id=s['cell_id'],method=s['method'],native=s['native'],
                           input_tree=s['input_tree'],probe_stopped=proof['probe_stopped'],measurement=proof['measurement'])
    out=private_dir(output)
    write_new(out/'started.json',{'spec':ref,'cell_id':s['cell_id'],'children':len(children),
                                 'parallelism':s['parallelism'],'automatic_retry':False})
    # Intent is durable before submitting even the first child. A missing child
    # receipt after interruption is unknown, never permission to recreate it.
    write_new(out/'intent.json',{'children':children})
    if native:
        result=run_native(s,ref,commands,out,source)
        write_new(out/'result.json',result)
        return result
    gate=threading.Event();begin=time.monotonic()
    def one(child):
        gate.wait();started=time.monotonic()
        try:
            code=execute(child['step']['path'],child['step']['sha256'],out/child['id'])
            return {'id':child['id'],'exit_code':code,'passed':code==0,
                    'start_offset_seconds':started-begin,'seconds':time.monotonic()-started,
                    'receipt_sha256':digest(out/child['id']/'finished.json')}
        except Exception as error:
            return {'id':child['id'],'passed':False,'error_type':type(error).__name__,
                    'seconds':time.monotonic()-started,'process_state':'inspect original receipts'}
    with ThreadPoolExecutor(max_workers=s['parallelism']) as pool:
        futures=[pool.submit(one,c) for c in children];gate.set()
        results=[f.result() for f in futures]
    result={'cell_id':s['cell_id'],'completed':True,'passed':all(x['passed'] for x in results),
            'children':len(children),'parallelism':s['parallelism'],'wall_seconds':time.monotonic()-begin,
            'results':results,'performance_accepted':False,
            'scope':'native creation command completion only; first use, isolation, durable closure remain required'}
    write_new(out/'result.json',result);return result


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    for k in ('spec','sha256','output'):p.add_argument('--'+k,required=True)
    a=p.parse_args();raise SystemExit(0 if run({'path':a.spec,'sha256':a.sha256},a.output)['passed'] else 1)
