"""One reviewed fixed-size creation burst; first use/isolation/drain are separate.

Every child has one pinned experiment-step-v1 native fork/clone/full-copy command.
Wait for all launched commands, retain failed outputs, never retry or kill them.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor
import threading
import time
from pathlib import Path

from .experiment_io import digest, load, write_new
from .experiment_step import execute, prepare
from .runtime.common import private_dir, spec


def run(ref, output):
    s=spec(ref,'suite-fork-burst-v1'); children=s['children']
    if not children or len({c['id'] for c in children})!=len(children) or len(children)>64:
        raise ValueError('1..64 unique children required')
    if type(s.get('parallelism')) is not int or not 1<=s['parallelism']<=len(children):
        raise ValueError('explicit bounded creation concurrency required')
    for c in children:
        if not c['id'].replace('-','').replace('_','').isalnum():raise ValueError('simple child ID required')
        command,_=prepare(c['step']['path'],c['step']['sha256'])
        if command['cell_id']!=s['cell_id'] or command['stage']!='fork-'+c['id']:
            raise ValueError('child step identity differs')
    out=private_dir(output)
    write_new(out/'started.json',{'spec':ref,'cell_id':s['cell_id'],'children':len(children),
                                 'parallelism':s['parallelism'],'automatic_retry':False})
    # Intent is durable before submitting even the first child. A missing child
    # receipt after interruption is unknown, never permission to recreate it.
    write_new(out/'intent.json',{'children':children})
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
