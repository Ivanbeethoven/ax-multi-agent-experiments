"""Streaming counters and repeat-byte accounting for the recorded mount boundary."""
import argparse
from collections import Counter
import json
from pathlib import Path
from fs_trace_capture import overlap, insert, invalidate


def read_events(path):
    with path.open() as source:
        for line in source:
            yield json.loads(line)


def analyze(path):
    paths, handles, seen = {}, {}, {}
    next_object=0
    counts, errors, namespaces, subtrees = Counter(), Counter(), {}, {}
    total_read=total_write=repeated=request_read=request_write=0
    last=0; pids=set(); first=None; end=None
    def identity(path):
        nonlocal next_object
        if path not in paths:
            next_object+=1;paths[path]=next_object;seen[next_object]=[]
        return paths[path]
    for e in read_events(path):
        op=e['op'];a=e['args'];success=e['errno']==0
        if e['seq']!=last+1: raise ValueError('Sequence gap')
        last=e['seq'];counts[op]+=1;pids.add(e['pid'])
        if first is None:first=e['start_ns']
        end=e['end_ns']
        if not success:errors[str(e['errno'])]+=1;continue
        if op in ('open','create'):
            if op=='create':paths.pop(a[0],None)
            obj=identity(a[0]);handles[e['result']]={'object':obj,'path':a[0]}
            if op=='open' and a[1]&512:seen[obj]=[]
        elif op in ('read','write'):
            h=handles[a[3]];obj=h['object'];start=a[2]
            namespace=h['path'].strip('/').split('/')[0]
            n=namespaces.setdefault(namespace,{'actual_read_bytes':0,'actual_write_bytes':0,'repeated_read_bytes':0})
            subkey='/'.join(h['path'].strip('/').split('/')[:2]) if namespace=='state' else 'repo'
            sub=subtrees.setdefault(subkey,{'actual_read_bytes':0,'actual_write_bytes':0,'repeated_read_bytes':0})
            if op=='read':
                amount=e['result']['len'];total_read+=amount;request_read+=a[1]
                dup=overlap(seen[obj],start,start+amount);repeated+=dup
                seen[obj]=insert(seen[obj],start,start+amount)
                n['actual_read_bytes']+=amount;n['repeated_read_bytes']+=dup
                sub['actual_read_bytes']+=amount;sub['repeated_read_bytes']+=dup
            else:
                amount=e['result'];total_write+=amount;request_write+=a[1]['len']
                seen[obj]=invalidate(seen[obj],start,start+amount)
                n['actual_write_bytes']+=amount
                sub['actual_write_bytes']+=amount
        elif op=='release':handles.pop(a[1])
        elif op=='truncate':
            obj=handles[a[2]]['object'] if len(a)>2 and a[2] in handles else identity(a[0])
            seen[obj]=invalidate(seen[obj],a[1],2**63)
        elif op in ('unlink','rmdir'):paths.pop(a[0],None)
        elif op=='link':paths[a[0]]=identity(a[1])
        elif op=='rename':
            old,new=a[:2]
            moved={new+p[len(old):]:obj for p,obj in paths.items() if p==old or p.startswith(old+'/')}
            for p in list(paths):
                if p==old or p.startswith(old+'/') or p==new or p.startswith(new+'/'):paths.pop(p)
            paths.update(moved)
            for h in handles.values():
                if h['path']==old or h['path'].startswith(old+'/'):h['path']=new+h['path'][len(old):]
        elif op=='destroy':handles.clear()
    if not last:raise ValueError('Empty capture')
    for n in namespaces.values():n['repeated_read_ratio']=n['repeated_read_bytes']/n['actual_read_bytes'] if n['actual_read_bytes'] else None
    return {'callbacks':last,'operations_by_type':dict(counts),'errors_by_errno':dict(errors),
        'actual_read_bytes':total_read,'actual_write_bytes':total_write,'requested_read_bytes':request_read,
        'requested_write_bytes':request_write,'repeated_read_bytes':repeated,
        'repeated_read_ratio':repeated/total_read if total_read else None,'namespaces':namespaces,'subtrees':subtrees,
        'distinct_request_pids':len(pids),'capture_elapsed_s':(end-first)/1e9,
        'scope':'FUSE callback bytes; excludes kernel cache hits, local lock handling, image runtime and /tmp',
        'repeat_definition':'same file identity and offset already read since last overwrite; rename/link preserve identity',
        'physical_device_bytes':None,'application_syscall_bytes':None}


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('events',type=Path);p.add_argument('output',type=Path);a=p.parse_args()
    result=analyze(a.events);a.output.write_text(json.dumps(result,indent=2));print(json.dumps(result))
