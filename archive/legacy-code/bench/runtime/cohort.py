"""Release ONE reviewed cohort after every worker is ready. No next cell."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import json
import math
from pathlib import Path
import queue
import re
import threading
import time

from .common import digest, load, pinned, private_dir, spec, write_new
from .transport import connect, send, one


def task_statistics(jobs):
    groups={}
    for job in jobs:
        groups.setdefault(job['workload_id'],[]).append(job)
    result={}
    for name,rows in groups.items():
        times=sorted(r['seconds'] for r in rows if r['passed'])
        result[name]={'attempted':len(rows),'completed':len(times),'failed':len(rows)-len(times),
                      'seconds':{str(q):times[math.ceil(q*len(times))-1] if times else None for q in (.5,.95,.99)},
                      'scope':'successful supervised task wall time, nearest-rank empirical quantiles; small-n tails approximate maximum; native replay timing retained separately'}
    return result


def validate(s):
    workers = s['workers']; n = s['agents']
    if type(n) is not int or n < 1 or n != len(workers): raise ValueError('exact cohort size required')
    if len({w['id'] for w in workers}) != n: raise ValueError('duplicate worker ID')
    if any(not re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9_-]*',w['id']) for w in workers):raise ValueError('simple worker IDs required')
    identities = [(w['peer'].get('alias', 'local'), w['peer'].get('container'), w['output']) for w in workers]
    if len(set(identities)) != n: raise ValueError('duplicate worker placement/output')
    if s.get('purpose') == 'adapter-test': return
    import experiment_inventory
    inv = load(pinned(s['inventory'])); experiment_inventory.validate(inv)
    if inv['provider'] != s['provider']: raise ValueError('provider mismatch')
    nodes = {n['id']: n for n in inv['nodes']}
    observers=s.get('observers',[])
    involved={nodes[w['node_id']]['cluster_id'] for w in workers}
    required_nodes={n['id'] for n in inv['nodes'] if n['cluster_id'] in involved}
    calibration_off=s.get('purpose')=='observer-calibration-off'
    if not calibration_off and {o['node_id'] for o in observers} != required_nodes:
        raise ValueError('telemetry must cover every allocated node of involved clusters')
    if calibration_off and observers:raise ValueError('off calibration must not start collectors')
    if len({o['id'] for o in observers})!=len(observers):raise ValueError('duplicate observer')
    if any(not re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9_-]*',o['id']) for o in observers):raise ValueError('simple observer IDs required')
    for o in observers:
        if o['peer']['alias']!=nodes[o['node_id']]['ssh_alias']:raise ValueError('observer inventory mismatch')
        if inv['schema'] == 'experiment-inventory-v3':
            node = nodes[o['node_id']]; peer = o['peer']
            if peer.get('transport') != 'ssh' or peer.get('repo') != node['repo'] or peer.get('python') != node['python']:
                raise ValueError('observer runtime differs from inventory')
            if peer.get('container') or peer.get('cgroup'):
                raise ValueError('observer peer must use its inventory SSH environment directly, outside Agent isolation')
    contract=load(pinned(s['metric_contract']))
    work=spec(s['workload_contract'],'experiment-workload-contract-v1')
    if not work.get('binary_pins') or not work.get('input_pins'):
        raise ValueError('freeze measured binaries and inputs in workload contract')
    if contract.get('schema')!='experiment-metric-contract-v1' or not contract.get('reviewed'):
        raise ValueError('reviewed metrics meanings/units/coverage contract required')
    if not contract.get('bindings') or not contract.get('source_pins'):
        raise ValueError('freeze actual metric names, layer, units and source versions')
    for binding in contract['bindings']:
        if not all(binding.get(k) for k in ('observer_id','target_id','required_names','layer','unit','meaning')):
            raise ValueError('incomplete metric semantics binding')
    gates=['allocation_qualification','hardware_qualification','input_qualification','filesystem_qualification']
    if s.get('purpose') not in ('observer-calibration-on','observer-calibration-off'):gates.append('observer_overhead')
    for gate in gates:
        evidence=load(pinned(s[gate]))
        if evidence.get('passed') is not True:raise ValueError('qualification not passed: '+gate)
        if gate in ('allocation_qualification','hardware_qualification') and evidence.get('inventory_sha256')!=s['inventory']['sha256']:
            raise ValueError('allocation/hardware evidence uses another inventory')
        if gate=='observer_overhead' and evidence.get('metric_contract_sha256')!=s['metric_contract']['sha256']:
            raise ValueError('observer overhead qualification uses another metrics contract')
        if gate=='observer_overhead' and evidence.get('workload_contract_sha256')!=s['workload_contract']['sha256']:
            raise ValueError('observer overhead qualification uses another workload contract')
    if inv['schema'] == 'experiment-inventory-v3':
        from ssh_inventory import validate_workers
        validate_workers(inv, workers)
        return
    counts = {}
    for w in workers:
        node = nodes[w['node_id']]
        if w['peer']['alias'] != node['ssh_alias'] or w['peer']['transport'] != 'ssh':
            raise ValueError('worker not in approved inventory')
        counts[w['node_id']] = counts.get(w['node_id'], 0)+1
        cap = 1 if not w['peer'].get('container') else node['requirements'].get('max_agents',8)
        if counts[w['node_id']] > cap: raise ValueError('worker placement exceeds host capacity')
    # Two workers must not share the same Agent container or VM cgroup.
    placements = [(w['node_id'], w['peer'].get('container')) for w in workers]
    if len(set(placements)) != n: raise ValueError('Agent isolation reused')


def run(ref, output, observer=None):
    s = spec(ref, 'experiment-cohort-v1'); validate(s)
    output = private_dir(output); write_new(output/'spec.json', s)
    events = queue.Queue(); procs = {}; logs = []; threads = []; released = False
    ready = set(); ended = {}; times = {}; jobs = []; release_times = []; expected_jobs = {}; kinds = {}
    isolation_keys = set()
    def read(worker, proc):
        try:
            for line in proc.stdout:
                events.put((time.monotonic_ns(), worker, json.loads(line)))
        except Exception as error:
            events.put((time.monotonic_ns(), worker, {'type':'protocol_error','error_type':type(error).__name__}))
        finally:
            code = proc.wait(); proc.stdout.close()
            events.put((time.monotonic_ns(), worker, {'type':'exit','exit_code':code}))
    started = time.monotonic_ns()
    def observe_mark(label):
        rows=s.get('observers',[])
        if not rows:return []
        directory=private_dir(output/label)
        def call(o):
            child=private_dir(directory/o['id'])
            value=one(o['peer'],['observe-mark','--output',o['directory'],'--label',label],child)
            if value['spec_sha256']!=o['spec']['sha256'] or value['metric_contract_sha256']!=s['metric_contract']['sha256']:
                raise ValueError('observer spec/metrics contract differs')
            for target in o['required_live_targets']:
                state=value['target_states'].get(target,{})
                if state.get('state')!='live':raise ValueError('required metrics target not live: '+target)
            contract=load(pinned(s['metric_contract']))
            for binding in contract['bindings']:
                if binding['observer_id']==o['id'] and binding['target_id'] in o['required_live_targets']:
                    names=value['target_states'][binding['target_id']]['required_names']
                    if not set(binding['required_names']).issubset(names):raise ValueError('metrics semantic contract not covered')
            return value
        with ThreadPoolExecutor(max_workers=min(16,len(rows))) as pool:
            return list(pool.map(call,rows))
    try:
        for w in s['workers']:
            log = (output/(w['id']+'.stderr.log')).open('x', encoding='utf-8'); logs.append(log)
            proc = connect(w['peer'], ['worker','--spec',w['spec']['path'],'--sha256',w['spec']['sha256'],
                                     '--output',w['output']], log, s.get('purpose') == 'adapter-test')
            procs[w['id']] = proc
            thread = threading.Thread(target=read, args=(w['id'],proc)); thread.start(); threads.append(thread)
        deadline = time.monotonic() + s.get('ready_timeout_seconds', 120)
        with (output/'events.jsonl').open('x', encoding='utf-8') as stream:
            def record(event):
                now, worker, message = event
                row = {'coordinator_ns':now, 'worker':worker, 'message':message}
                stream.write(json.dumps(row)+'\n'); stream.flush()
                if observer: observer(row)
                return now, worker, message
            while len(ready) < s['agents']:
                event = events.get(timeout=max(.01, deadline-time.monotonic()))
                now, worker, message = record(event)
                if message['type'] != 'ready' or worker in ready: raise ValueError('worker failed before barrier')
                expected = next(w['spec']['sha256'] for w in s['workers'] if w['id'] == worker)
                if message['spec_sha256'] != expected or message['worker'] != worker: raise ValueError('worker spec/identity mismatch')
                if message['purpose']!=s.get('purpose','experiment'):raise ValueError('worker/cohort purpose differs')
                expected_jobs[worker]=message['expected_jobs'];kinds[worker]=message['workload_kind']
                if s.get('purpose') != 'adapter-test' and 'effective' not in message['resources']:
                    raise ValueError('worker did not validate actual resource limits')
                if s.get('provider') == 'ssh' and s.get('purpose') != 'adapter-test':
                    actual = message.get('isolation')
                    if not actual or set(actual) != {'boot_id','cgroup_namespace','cgroup_membership'}:
                        raise ValueError('actual SSH worker isolation identity required')
                    key = json.dumps(actual, sort_keys=True)
                    if key in isolation_keys: raise ValueError('two SSH aliases share one Agent cgroup')
                    isolation_keys.add(key)
                ready.add(worker)
            if any(p.poll() is not None for p in procs.values()): raise ValueError('ready worker disconnected')
            observe_mark('measurement-start')
            # Persist release intent first. A lost connection after this point
            # means UNKNOWN, never permission to replay the cohort again.
            write_new(output/'release-intent.json', {'spec_sha256':ref['sha256'], 'workers':sorted(ready)})
            released = True
            for w in s['workers']:
                release_times.append(time.monotonic_ns())
                send(procs[w['id']], {'action':'go','spec_sha256':w['spec']['sha256']})
                procs[w['id']].stdin.close()
            finished = {}
            while len(ended) < len(procs):
                now, worker, message = record(events.get())  # no total replay deadline
                kind = message['type']
                if kind == 'started': times.setdefault(worker, {})['started_ack'] = now
                elif kind == 'job_finished': jobs.append({'worker':worker,'kind':kinds[worker], **message})
                elif kind == 'finished':
                    finished[worker] = message; times.setdefault(worker,{})['finished_ack'] = now
                elif kind == 'exit': ended[worker] = message['exit_code']
            passed = (all(v == 0 for v in ended.values()) and len(finished) == len(procs)
                      and all(x['passed'] and x['jobs']==expected_jobs[w] and x['expected_jobs']==expected_jobs[w]
                              for w,x in finished.items())
                      and all(sum(j['worker']==w and j['passed'] for j in jobs)==expected_jobs[w] for w in ready))
            telemetry_ok=True
            try: observe_mark('application-finished')
            except Exception as error:
                telemetry_ok=False
                write_new(output/'telemetry-failed.json',{'error_type':type(error).__name__})
            last = max((x.get('finished_ack', release_times[0]) for x in times.values()), default=release_times[0]) if passed else time.monotonic_ns()
            wall = (last-release_times[0])/1e9
            result = {'passed':passed and telemetry_ok,'workload_passed':passed,'telemetry_live_check_passed':telemetry_ok,
                      'performance_accepted':False,
                      'acceptance_pending':['drain/remote readback','stop and audit complete telemetry','seal retained evidence'],
                      'agents':s['agents'], 'ready_workers':len(ready),
                      'completed_workers':len(finished), 'completed_jobs':sum(j['passed'] for j in jobs),
                      'release_spread_seconds':(release_times[-1]-release_times[0])/1e9,
                      'coordinator_makespan_seconds':wall,
                      'jobs_per_second':sum(j['passed'] for j in jobs)/wall if wall > 0 else None,
                      'purpose':s.get('purpose','experiment'), 'worker_ack_times':times,
                      'metric_contract_sha256':s.get('metric_contract',{}).get('sha256'),
                      'workload_contract_sha256':s.get('workload_contract',{}).get('sha256'),
                      'per_workload_task_statistics':task_statistics(jobs),
                      'scope':'coordinator release through terminal ACK, includes transport; ACK overlap is not simultaneous I/O proof',
                      'automatic_retry':False, 'jobs':jobs}
            write_new(output/'result.json', result)
            return result
    except BaseException as error:
        if not released:
            for proc in procs.values():
                try: send(proc, {'action':'abort'}); proc.stdin.close()
                except (BrokenPipeError, OSError, ValueError): pass
        write_new(output/'failed.json', {'error_type':type(error).__name__, 'released':released,
                                        'remote_state':'inspect original outputs and processes; never resubmit automatically'})
        raise
    finally:
        # Do not kill transports/remote processes on interruption. Readers keep
        # draining when possible; incomplete outputs remain explicitly unknown.
        if not released:
            for thread in threads:thread.join(timeout=5)
        for log in logs: log.close()


if __name__ == '__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--spec',required=True); p.add_argument('--sha256',required=True); p.add_argument('--output',required=True)
    a=p.parse_args(); result=run({'path':a.spec,'sha256':a.sha256},a.output)
    print(json.dumps({k:v for k,v in result.items() if k not in ('jobs','worker_ack_times')}))
    raise SystemExit(0 if result['passed'] else 1)
