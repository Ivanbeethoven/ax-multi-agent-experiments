"""Per-host raw telemetry, phase markers and coverage checks. No benchmarks.

Run outside Agent cgroups. Read /proc, exact cgroups and native metrics; never
RESETSTAT, FLUSH*, drop caches, enable tracing dynamically or infer missing data.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import math
import os
from pathlib import Path
import re
import select
import socket
import ssl
import subprocess
import statistics
import sys
import time
import threading
from urllib.parse import urlsplit
from urllib.request import Request, build_opener, ProxyHandler, HTTPSHandler, HTTPRedirectHandler

from .common import alive, digest, identity, load, pinned, prepare, private_dir, spec, write_new

HOST_FILES = ['stat','meminfo','vmstat','diskstats','net/dev','net/snmp','net/netstat',
              'pressure/cpu','pressure/memory','pressure/io']
CGROUP_FILES = ['cpu.stat','cpu.max','memory.current','memory.peak','memory.max','memory.events',
                'memory.swap.current','memory.swap.max','io.stat','cpu.pressure','memory.pressure','io.pressure','pids.current']


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs): raise ValueError('metrics redirect rejected')


def prometheus(text):
    values = {}
    for line in text.splitlines():
        if not line or line.startswith('#'): continue
        match = re.fullmatch(r'([a-zA-Z_:][a-zA-Z0-9_:]*(?:\{.*\})?)\s+([^\s]+)(?:\s+[-0-9.]+)?',line)
        if not match: raise ValueError('invalid Prometheus sample')
        value=float(match[2])
        if match[1] in values: raise ValueError('duplicate series')
        values[match[1]]=value
    return values


def required_values(target,raw):
    if target['format']=='prometheus':
        values=prometheus(raw)
        names={k.split('{',1)[0] for k in values}
        if not set(target['required_names']).issubset(names): raise ValueError('required metric name absent')
        if any(not math.isfinite(v) for k,v in values.items() if k.split('{',1)[0] in target['required_names']):
            raise ValueError('required metric is nonfinite')
    elif target['format']=='redis-info':
        values=dict(line.split(':',1) for line in raw.splitlines() if line and not line.startswith('#') and ':' in line)
        if not set(target['required_names']).issubset(values): raise ValueError('required Redis INFO field absent')
    else: raise ValueError('unsupported metrics format')
    return values


def collect_target(t):
    if t['kind']=='http':
        url=urlsplit(t['url'])
        if url.scheme not in ('http','https') or not url.hostname or url.username or url.password or url.query or url.fragment:
            raise ValueError('plain explicit metrics URL required')
        headers={}
        if t.get('bearer_file'):
            token=pinned(t['bearer_file']).read_text().strip()
            headers['Authorization']='Bearer '+token
        context=ssl.create_default_context(cafile=str(pinned(t['ca_file'])) if t.get('ca_file') else None)
        opener=build_opener(ProxyHandler({}),HTTPSHandler(context=context),NoRedirect())
        with opener.open(Request(t['url'],headers=headers),timeout=t.get('timeout_seconds',2)) as response:
            if response.status!=200: raise ValueError('metrics HTTP status')
            raw=response.read(16*1024*1024+1)
        if len(raw)>16*1024*1024: raise ValueError('metrics response exceeds cap')
        raw=raw.decode('utf-8')
    elif t['kind']=='command':
        command,env=prepare(t['step']['path'],t['step']['sha256'])
        if Path(command['argv'][0]).name!='redis-cli' or [x.upper() for x in command['argv'][-2:]]!=['INFO','ALL']:
            raise ValueError('command telemetry only supports native redis-cli INFO ALL')
        result=subprocess.run(command['argv'],cwd=command['cwd'],env=env,capture_output=True,
                              timeout=t.get('timeout_seconds',2),check=True)
        raw=result.stdout.decode('utf-8')
    else: raise ValueError('unsupported telemetry source')
    return raw,required_values(t,raw)


def snapshot(s,directory,index,previous=None):
    begin=time.monotonic_ns();directory=private_dir(Path(directory)/f'sample-{index:06}')
    errors=[]; files={}; counters={}; target_states={}
    def retain(name,raw):
        (directory/name).write_text(raw,encoding='utf-8')
        files[name]={'sha256':digest(directory/name),'bytes':(directory/name).stat().st_size}
    for name in HOST_FILES:
        try: retain('host-'+name.replace('/','-')+'.txt',Path('/proc',name).read_text())
        except OSError as error:errors.append({'source':name,'error':type(error).__name__})
    for group in s['cgroups']:
        try:
            root=Path(group['path']).resolve(strict=True)
            if not root.is_relative_to('/sys/fs/cgroup'): raise ValueError('cgroup path outside cgroupfs')
            for name in CGROUP_FILES:retain(group['id']+'-'+name+'.txt',(root/name).read_text())
        except (OSError,ValueError) as error:errors.append({'source':group['id'],'error':type(error).__name__})
    processes=[]
    for expected in s['processes']:
        try:
            if not alive(expected):raise ValueError('process disappeared/restarted')
            pid=expected['pid']; processes.append(expected)
            for name in ('stat','status','io'):
                retain(f'pid-{pid}-{name}.txt',Path(f'/proc/{pid}/{name}').read_text())
        except (OSError,ValueError) as error:errors.append({'source':f'pid-{expected["pid"]}','error':type(error).__name__})
    capacity={}
    for path in s['data_paths']:
        try:
            v=os.statvfs(path);capacity[path]={'bytes_free':v.f_bavail*v.f_frsize,'inodes_free':v.f_favail}
        except OSError as error:errors.append({'source':path,'error':type(error).__name__})
    with ThreadPoolExecutor(max_workers=max(1,len(s['targets']))) as pool:
        pending=[]
        for t in s['targets']:
            # Lifecycle gaps must have actual native mount evidence. This is
            # recorded as stopped/not-started, never as a zero-valued metric.
            mount=t.get('mount_directory')
            if mount and not (Path(mount)/'ready.json').exists():
                target_states[t['id']]={'state':'not-started'};continue
            if mount and (Path(mount)/'stopped.json').exists():
                stopped=load(Path(mount)/'stopped.json')
                if stopped.get('passed') and stopped.get('mount_absent') and stopped.get('exit_code')==0:
                    target_states[t['id']]={'state':'cleanly-stopped','receipt_sha256':digest(Path(mount)/'stopped.json')};continue
            pending.append((t,pool.submit(collect_target,t)))
        for target,future in pending:
            try:
                raw,values=future.result();retain(target['id']+'.txt',raw)
                target_states[target['id']]={'state':'live','required_names':target['required_names']}
                selected={k:v for k,v in values.items() if k.split('{',1)[0] in target.get('counter_names',[])}
                old=(previous or {}).get(target['id'],{})
                if set(old)-set(selected):raise ValueError('counter series disappeared')
                for key,value in selected.items():
                    if key in old and float(value)<float(old[key]):raise ValueError('counter reset')
                counters[target['id']]=selected
            except Exception as error:
                target_states[target['id']]={'state':'failed'}
                errors.append({'source':target['id'],'error':type(error).__name__})
    result={'index':index,'monotonic_start_ns':begin,'monotonic_end_ns':time.monotonic_ns(),
            'utc_ns':time.time_ns(),'passed':not errors,'errors':errors,'files':files,
            'counters':counters,'target_states':target_states,'processes':processes,'capacity':capacity,
            'collector_cpu_seconds':time.process_time()}
    write_new(directory/'sample.json',result);return result


def validate(s):
    if not .25<=s['interval_seconds']<=10:raise ValueError('sampling interval outside reviewed range')
    if not s['data_paths'] or (s.get('role')!='host-only' and (not s['targets'] or not s['cgroups'] or not s['processes'])):
        raise ValueError('explicit service targets, data paths, cgroups and process identities required')
    ids=[t['id'] for t in s['targets']]+[t['id'] for t in s['cgroups']]
    if len(ids)!=len(set(ids)) or any(not re.fullmatch(r'[a-z0-9-]+',n) for n in ids):raise ValueError('unique simple source IDs required')
    if any(not t.get('required_names') for t in s['targets']):raise ValueError('freeze mandatory metrics names after real endpoint probe')
    if not s.get('metric_contract_sha256'):raise ValueError('bind layer/units/denominator contract')


def request(directory,action,label=None):
    with socket.socket(socket.AF_UNIX,socket.SOCK_STREAM) as conn:
        conn.connect(str(Path(directory)/'observer.sock'))
        conn.sendall((json.dumps({'action':action,'label':label})+'\n').encode())
        with conn.makefile('rb') as stream: result=json.loads(stream.readline(4*1024*1024))
    if not result.get('passed'):raise ValueError('telemetry incomplete: '+str(result.get('errors',[])))
    return result


def serve(ref,output):
    s=spec(ref,'experiment-observer-v1');validate(s);output=Path(output)
    count=0;last=None;history=[];marks=[]
    def sample():
        nonlocal count,last
        value=snapshot(s,output,count,last['counters'] if last else None)
        history.append({k:value[k] for k in ('index','monotonic_start_ns','monotonic_end_ns','passed','errors')})
        history[-1]['sample_sha256']=digest(output/f'sample-{count:06}'/'sample.json')
        last=value;count+=1;return value
    with socket.socket(socket.AF_UNIX,socket.SOCK_STREAM) as server:
        server.bind(str(output/'observer.sock'));os.chmod(output/'observer.sock',0o600);server.listen(16)
        first=sample()
        write_new(output/'ready.json',{'passed':first['passed'],'spec':ref,'process':identity(os.getpid()),
                                      'metric_contract_sha256':s['metric_contract_sha256']})
        next_sample=time.monotonic()+s['interval_seconds']
        while True:
            readable,_,_=select.select([server],[],[],max(0,next_sample-time.monotonic()))
            if not readable:
                sample();next_sample=time.monotonic()+s['interval_seconds'];continue
            with server.accept()[0] as conn:
                with conn.makefile('rb') as stream: message=json.loads(stream.readline(65536))
                if message['action'] not in ('status','mark','stop'):raise ValueError('observer action')
                current=sample();next_sample=time.monotonic()+s['interval_seconds']
                if message['action']=='mark':
                    if not re.fullmatch(r'[a-zA-Z0-9_.-]+',message['label']):raise ValueError('simple phase label')
                    marks.append({'label':message['label'],'sample_index':current['index'],'monotonic_ns':time.monotonic_ns()})
                    write_new(output/f'mark-{len(marks):04}.json',marks[-1])
                result={'passed':all(x['passed'] for x in history),'errors':[e for x in history for e in x['errors']],
                        'samples':count,'latest_sample_ns':current['monotonic_end_ns'],'spec_sha256':ref['sha256'],
                        'metric_contract_sha256':s['metric_contract_sha256'],'marks':marks,
                        'target_states':current['target_states']}
                if message['action']=='stop':
                    result['history']=history;write_new(output/'finished.json',result)
                conn.sendall((json.dumps(result)+'\n').encode())
                if message['action']=='stop':return


def start(ref,output):
    s=spec(ref,'experiment-observer-v1');validate(s);output=private_dir(output)
    with (output/'collector.log').open('xb') as log:
        child=subprocess.Popen([sys.executable,'-m','bench.runtime.observe','serve','--spec',ref['path'],
                                '--sha256',ref['sha256'],'--output',str(output)],
                               cwd=Path(__file__).resolve().parents[2],stdout=log,stderr=log,start_new_session=True)
    deadline=time.monotonic()+30
    while time.monotonic()<deadline:
        if (output/'ready.json').exists():
            threading.Thread(target=child.wait,daemon=True).start()
            return request(output,'status')
        if child.poll() is not None:raise ValueError('collector failed before ready')
        time.sleep(.05)
    raise ValueError('collector startup unresolved; inspect retained process')


def audit(directory,max_gap_seconds,required_marks):
    directory=Path(directory);end=load(directory/'finished.json')
    if max_gap_seconds<=0 or not end['passed']:raise ValueError('failed telemetry samples or invalid maximum gap')
    previous=None
    samples=sorted(directory.glob('sample-*/sample.json'))
    if len(samples)!=end['samples'] or len(samples)<2 or len(end['history'])!=len(samples):raise ValueError('incomplete sample sequence')
    for index,sample in enumerate(samples):
        s=load(sample)
        h=end['history'][index]
        if sample.parent.name!=f'sample-{index:06}' or s['index']!=index or h['index']!=index:
            raise ValueError('non-contiguous sample sequence')
        if digest(sample)!=h['sample_sha256']:raise ValueError('sample record changed')
        if not s['passed'] or not s['files'] or s['monotonic_end_ns']<s['monotonic_start_ns']:raise ValueError('invalid telemetry sample')
        if previous is not None and s['monotonic_start_ns']<=previous:raise ValueError('non-monotonic telemetry clock')
        if previous is not None and (s['monotonic_start_ns']-previous)/1e9>max_gap_seconds:raise ValueError('telemetry gap')
        previous=s['monotonic_start_ns']
        for name,expected in s['files'].items():
            file=sample.parent/name
            if file.parent!=sample.parent or file.is_symlink():raise ValueError('unexpected telemetry path')
            if file.stat().st_size!=expected['bytes'] or digest(file)!=expected['sha256']:raise ValueError('telemetry bytes changed')
    for mark in end['marks']:
        index=mark['sample_index']
        if not 0<=index<len(samples) or mark['monotonic_ns']<load(samples[index])['monotonic_end_ns']:
            raise ValueError('marker is not bound to a completed sample')
    if not set(required_marks).issubset({m['label'] for m in end['marks']}):raise ValueError('phase marker missing')
    return {'passed':True,'samples':len(samples),'max_gap_seconds':max_gap_seconds,'marks':required_marks,
            'finished_sha256':digest(directory/'finished.json'),'metric_contract_sha256':end.get('metric_contract_sha256'),
            'scope':'recorded coverage/hashes only; metric semantics, overhead and independent remote readback are separate gates'}


def overhead(ref):
    """Paired remount comparison, not a same-mount strict causal claim."""
    s=spec(ref,'experiment-observer-overhead-plan-v1')
    if len(s['pairs'])<3 or not 0<s['max_relative_change']<=.05:
        raise ValueError('at least three pairs and predeclared <=5% tolerance required')
    times={'on':[],'off':[]};tails={'on':[],'off':[]}
    for pair in s['pairs']:
        signature=[]
        for mode in ('on','off'):
            result=load(pinned(pair[mode]))
            if not result['passed'] or not result['workload_passed']:raise ValueError('failed overhead workload or live telemetry check')
            if result['purpose']!='observer-calibration-'+mode:raise ValueError('not a paired observer calibration')
            if result['metric_contract_sha256']!=s['metric_contract_sha256'] or result['workload_contract_sha256']!=s['workload_contract_sha256']:
                raise ValueError('calibration changed metrics or workload contract')
            if result['coordinator_makespan_seconds']<=0:raise ValueError('invalid overhead timing')
            times[mode].append(result['coordinator_makespan_seconds'])
            durations=sorted(j['seconds'] for j in result['jobs'])
            if not durations or durations[0]<=0:raise ValueError('missing or invalid job durations')
            tails[mode].append(durations[math.ceil(.99*len(durations))-1])
            signatures=[]
            for job in result['jobs']:
                fields={'trace':['operations','actual_read_bytes','actual_write_bytes','remaining_handles'],
                        'ml':['attempted_files','actual_read_bytes','errors'],
                        's3':['requests','application_bytes','global_concurrency','shard']}.get(job['kind'])
                if not job['passed'] or not fields:raise ValueError('missing measured workload calibration')
                signatures.append((job['kind'],*[job['result'][k] for k in fields]))
            signature.append((result['agents'], sorted(signatures)))
        if signature[0]!=signature[1]:raise ValueError('overhead comparison changed work')
    paired_changes={name:[on/off-1 for on,off in zip(v['on'],v['off'])] for name,v in [('makespan',times),('task_p99',tails)]}
    changes={name:abs(statistics.median(values)) for name,values in paired_changes.items()}
    return {'schema':'experiment-observer-overhead-v1','passed':all(v<=s['max_relative_change'] for v in changes.values()),
            'relative_changes':changes,'paired_relative_changes':paired_changes,'pairs':s['pairs'],'max_relative_change':s['max_relative_change'],
            'metric_contract_sha256':s['metric_contract_sha256'],
            'workload_contract_sha256':s['workload_contract_sha256'],
            'scope':'three or more paired fresh mounts; no same-process causal claim; rerun if binary, sampler interval or target density changes'}


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('action',choices=['start','serve','status','mark','stop','audit','overhead'])
    p.add_argument('--spec');p.add_argument('--sha256');p.add_argument('--output',required=True);p.add_argument('--label')
    p.add_argument('--max-gap-seconds',type=float,default=3);p.add_argument('--marks',nargs='*',default=[])
    a=p.parse_args();ref={'path':a.spec,'sha256':a.sha256}
    if a.action=='serve':serve(ref,a.output)
    elif a.action=='overhead':write_new(a.output,overhead(ref))
    else:
        value=start(ref,a.output) if a.action=='start' else audit(a.output,a.max_gap_seconds,a.marks) if a.action=='audit' else request(a.output,a.action,a.label)
        print(json.dumps(value))
