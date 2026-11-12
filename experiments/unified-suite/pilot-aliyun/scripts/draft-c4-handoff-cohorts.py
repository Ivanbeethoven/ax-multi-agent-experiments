#!/usr/bin/env python3
"""Assemble five unreviewed cohort specs for the original JuiceFS A→B move.

Inputs are pinned local peer/qualification files and readbacks of exact remote
mount, worker, step, phase and observer specs. The tool never reviews or runs
anything. Reviewers must re-pin the five reviewed copies in the handoff draft.
"""
import argparse
import json
from pathlib import Path, PurePosixPath
import re
import sys

REPO = Path(__file__).resolve().parents[4]
sys.path[:0] = [str(REPO),str(REPO/'bench')]
from bench.runtime import cohort, handoff
from bench.runtime.common import digest, load, pinned, write_new
from bench.runtime.prepare import AGENT_BUDGET

SCHEMA = 'c4-handoff-cohort-draft-request-v1'
MOUNT = '/srv/ax-pilot-session'
WORKSPACE = MOUNT+'/workspace'
STATE = dict(workspace=WORKSPACE,project=WORKSPACE+'/repo',config=WORKSPACE+'/config/opencode.json',
             xdg={name:WORKSPACE+'/source/'+name for name in ('data','state','cache','config')})
PHASES = dict(quiesce=('source','export',60), lease_check=('source','verify-stopped',60),
              restore=('target','export',60), resume=('target','resume',205),
              export_after=('target','export',60))
CLIENTS = {'pool-a-client','pool-b-client'}
ROUTE_NODES = CLIENTS | {'pool-a-metadata','pool-a-objects'}
GATES = {'inventory','metric_contract','workload_contract','allocation_qualification',
         'hardware_qualification','input_qualification','filesystem_qualification','observer_overhead'}


def require(ok, message):
    if not ok:
        raise ValueError(message)


def linux_path(value):
    path = PurePosixPath(value)
    require(path.is_absolute() and not value.startswith('//') and str(path)==value and
            path!=PurePosixPath('/') and '..' not in path.parts,
            'Canonical remote Linux path required')
    return path


def outside(value):
    p = linux_path(value)
    require(not p.is_relative_to(MOUNT), 'Evidence and runtime state must stay outside the shared mount')
    return p


def remote(mirror):
    """Check the readback bytes locally; the runtime consumes the remote pin."""
    require(set(mirror)=={'local','remote_path'}, 'Use a local SHA pin and its exact remote path')
    outside(mirror['remote_path'])
    p = pinned(mirror['local'])
    return dict(path=mirror['remote_path'],sha256=mirror['local']['sha256']),load(p)


def side(value, fleet, name, cell):
    require(set(value)=={'node_id','slot_id','peer','mount_spec','mount_directory'},
            'Exact '+name+' peer, slot and mount inputs required')
    expect_node = {'source':'pool-a-client','target':'pool-b-client'}[name]
    require(value['node_id']==expect_node, 'Original move is A client to B client')
    node = fleet[value['node_id']]
    require(node['kind']=='host' and node['host_id'].startswith('i-'), 'Original ECS client identity required')
    peer = load(pinned(value['peer']))
    pin, mount = remote(value['mount_spec'])
    require(mount.get('schema')=='experiment-mount-v1' and mount.get('reviewed') is True and
            mount.get('filesystem')=='juicefs' and mount.get('purpose')=='experiment' and
            mount.get('mount_point')==MOUNT and mount.get('suite_binding',{}).get('cell_id')==cell,
            'Original reviewed JuiceFS mount, workspace path and cell required: '+name)
    anchor = mount['anchor']
    require(set(anchor)=={'filesystem','workspace','namespace','storage_identity_sha256'} and
            anchor['filesystem']=='juicefs' and re.fullmatch('[0-9a-f]{64}',anchor['storage_identity_sha256']),
            'Opaque frozen JuiceFS storage anchor required: '+name)
    outside(value['mount_directory'])
    return dict(node_id=value['node_id'],peer=peer,mount_spec=pin,mount_directory=value['mount_directory'],
                anchor=anchor,session_state=STATE)


def worker(value, phase, s, endpoint, bounded_sha):
    require(set(value)=={'worker','step','phase','output','observers'}, 'Exact worker readbacks required: '+phase)
    direction, action, timeout = PHASES[phase]
    worker_pin, w = remote(value['worker'])
    step_pin, step = remote(value['step'])
    phase_pin, native = remote(value['phase'])
    require(w.get('schema')=='experiment-worker-v1' and w.get('reviewed') is True and
            w.get('purpose')=='experiment' and w.get('kind')=='control' and w.get('budget')==AGENT_BUDGET and
            len(w.get('jobs',[]))==1, 'One reviewed bounded native worker required: '+phase)
    job = w['jobs'][0]
    require(job.get('step')==step_pin and job.get('id')=='session-'+action and job.get('workload_id') and
            bool(job.get('observe_opencode_tools',False))==(phase=='resume'),
            'Worker action or completed-tool observation differs: '+phase)
    mounted = action!='verify-stopped'
    mount = dict(spec=endpoint['mount_spec'],directory=endpoint['mount_directory'])
    require(w.get('requires_mount') is mounted and
            ((w.get('mount')==mount) if mounted else 'mount' not in w and native.get('mount')==mount),
            'Phase must bind its own mount or prove the original source stopped: '+phase)
    require(native.get('action')==action and native.get('total_timeout_seconds')==timeout,
            'Wrong native action or timeout: '+phase)
    if mounted:
        require(all(native.get(k)==STATE[k] for k in ('workspace','project','config')) and
                native.get('state')==STATE['xdg'] and native.get('session_id')==s['session_id'] and
                native.get('relay_url')==s['relay_url'] and native.get('binary',{}).get('sha256')==s['opencode_sha256'],
                'Canonical shared session, release or independent relay differs: '+phase)
    require(step.get('schema')=='experiment-step-v1' and step.get('reviewed') is True and
            step.get('cell_id')==s['cell_id'] and step.get('stage')=='session-'+action and
            step.get('inherit_environment')==[], 'Original reviewed exact native step required: '+phase)
    argv = step['argv']
    require(isinstance(argv,list) and len(argv)==8, 'Exact native invocation required: '+phase)
    interpreter = outside(argv[0])
    python = outside(endpoint['peer']['python'])
    require(interpreter.parent==python.parent and
            re.fullmatch(re.escape(python.name)+r'(?:\.\d+)?',interpreter.name) and
            re.fullmatch('[0-9a-f]{64}',step.get('executable_sha256','')) and
            outside(argv[1]).name=='session-worker-bounded.py' and
            argv[2:7]==['--request',phase_pin['path'],'--sha256',phase_pin['sha256'],'--out'] and
            all(p in step['inputs'] for p in (phase_pin,endpoint['mount_spec'],dict(path=argv[1],sha256=bounded_sha))),
            'Phase must invoke the pinned original bounded worker with the exact request: '+phase)
    expected = {'info.id':s['session_id']} if action=='export' else (
        {'passed':True,'original_mount_stopped':True,'all_exited':True,'mount_absent':True}
        if action=='verify-stopped' else {'passed':True,'action':'resume','session_id':s['session_id']})
    result = str(outside(argv[7])/('export.json' if action=='export' else 'result.json'))
    require(job['result']==dict(path=result,equals=expected), 'Exact native result predicates required: '+phase)
    require((not mounted or native['binary'] in step['inputs']) and
            (phase!='resume' or native.get('checkpoint') in step['inputs']), 'Native release/checkpoint not pinned: '+phase)
    output = outside(value['output'])
    require(output!=outside(argv[7]), 'Worker and phase results need separate outputs')
    row = dict(id=w['worker_id'],node_id=endpoint['node_id'],slot_id=s[direction]['slot_id'],
               peer=endpoint['peer'],spec=worker_pin,output=str(output))
    return row, (endpoint['node_id'],str(output)), (endpoint['node_id'],argv[7])


def observers(rows, metrics):
    contract = load(pinned(metrics))
    result = []
    for r in rows:
        require(set(r)=={'id','node_id','peer','directory','spec','required_live_targets'},
                'Exact observer row required')
        pin, value = remote(r['spec'])
        require(value.get('schema')=='experiment-observer-v1' and value.get('reviewed') is True and
                value.get('observer_id')==r['id'] and value.get('metric_contract_sha256')==metrics['sha256'] and
                set(r['required_live_targets'])<={t['id'] for t in value['targets']} and
                set(r['required_live_targets'])<={b['target_id'] for b in contract['bindings'] if b['observer_id']==r['id']},
                'Observer readback must bind the exact metric contract and live targets')
        outside(r['directory'])
        result.append(dict(r,spec=pin,peer=load(pinned(r['peer']))))
    return result


def draft(request, out):
    s = load(pinned(request))
    require(set(s)=={'schema','cell_id','session_id','export_transfer_sha256','opencode_sha256',
                     'relay_url','source','target','gates','phases'} and s['schema']==SCHEMA and
            set(s['gates'])==GATES and set(s['phases'])==set(PHASES) and
            re.fullmatch(r'ses_[A-Za-z0-9]+',s['session_id']) and all(
                re.fullmatch('[0-9a-f]{64}',s[k]) for k in ('export_transfer_sha256','opencode_sha256')),
            'Exact five-phase C4 handoff draft input required')
    relay = re.fullmatch(r'http://127\.0\.0\.1:(\d{2,5})/v1',s['relay_url'])
    require(relay and 1024<=int(relay[1])<=65535 and int(relay[1])!=18443,
            'Original independent loopback relay required')
    inventory = load(pinned(s['gates']['inventory']))
    require(inventory.get('schema')=='experiment-inventory-v3', 'Frozen SSH route inventory required')
    fleet = {n['id']:n for n in inventory['nodes']}
    require(set(fleet)==ROUTE_NODES and len({n['cluster_id'] for n in fleet.values()})==1,
            'Route inventory must cover both clients and only the A backend')
    endpoints = {name:side(s[name],fleet,name,s['cell_id']) for name in ('source','target')}
    a,b = endpoints.values()
    require(a['anchor']==b['anchor'] and fleet[a['node_id']]['host_id']!=fleet[b['node_id']]['host_id'],
            'Two separate ECS clients must open the same JuiceFS anchor')
    bounded_sha = digest(Path(__file__).with_name('session-worker-bounded.py'))
    cohorts, used = {},set()
    for phase,(direction,_,_) in PHASES.items():
        v = s['phases'][phase]
        row, outer, inner = worker(v,phase,s,endpoints[direction],bounded_sha)
        require(not {outer,inner}&used, 'Phase worker or native result output is reused')
        used.update((outer,inner))
        telemetry = observers(v['observers'],s['gates']['metric_contract'])
        value = dict(schema='experiment-cohort-v1',reviewed=False,purpose='experiment',provider='ssh',
                     agents=1,workers=[row],observers=telemetry,**s['gates'])
        cohort.validate(value)
        live_clients = {o['node_id'] for o in telemetry if o['node_id'] in CLIENTS and
                        'filesystem' in o['required_live_targets']}
        require(live_clients==(set() if phase=='lease_check' else {row['node_id']}),
                'Only the mounted client may require live filesystem metrics: '+phase)
        cohorts[phase] = value
    out = Path(out)
    require(out.is_absolute() and out.resolve()==out, 'Fresh canonical output directory required')
    out.mkdir(mode=0o700)
    pins = {}
    for phase,value in cohorts.items():
        path = out/(phase+'.draft.json')
        write_new(path,value)
        pins[phase] = dict(path=str(path),sha256=digest(path))
    value = dict(schema='experiment-handoff-v1',reviewed=False,mode='move',filesystem='juicefs',
                 restore_method='shared-workspace-db',session_id=s['session_id'],
                 export_transfer_sha256=s['export_transfer_sha256'],**endpoints,**pins)
    handoff.validate(value)
    path = out/'handoff.draft.json'
    write_new(path,value)
    return dict(reviewed=False,execution_enabled=False,cohorts=pins,
                handoff=dict(path=str(path),sha256=digest(path)),
                remaining=['independent observer lifecycle and live targets','cold-before-first-export boundary',
                           'after-run zero-debt, normal stop and archive','operator review and re-pin'])


if __name__=='__main__':
    p = argparse.ArgumentParser(description=__doc__,epilog=
        'Request schema: '+SCHEMA+'. Set cell_id, session_id, export_transfer_sha256, opencode_sha256, relay_url, '
        'source, target, gates and phases. Each phase needs worker, step, phase, output and observers. '
        'Remote spec readbacks use {"remote_path":"/path/on/worker","local":{"path":"/local/readback","sha256":"..."}}.')
    p.add_argument('--request',required=True)
    p.add_argument('--sha256',required=True)
    p.add_argument('--out',required=True)
    a = p.parse_args()
    print(json.dumps(draft(dict(path=a.request,sha256=a.sha256),a.out),indent=2))
