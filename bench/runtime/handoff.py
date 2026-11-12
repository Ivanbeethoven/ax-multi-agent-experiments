"""One clean C4 stay/move, preserving original session and shared data anchor.

All native argv are reviewed beforehand. An interrupted phase is unknown, never
automatically retried. No DELETE, data-tree copy, process-memory migration or
claim that a Manager journal lease is a client writer lease.

The separate v2 shared-workspace spec may be used for a fresh single-pool
route: after target-start, a supervised full tree read must pass before the
first target native export. V1 does not run this extra phase.
"""
import argparse
import base64
import copy
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import time

from .common import digest, load, pinned, private_dir, spec, write_new
from . import cohort, transport
from ..session_check import checkpoint, check_after

V1 = 'experiment-handoff-v1'
V2 = 'experiment-handoff-v2'
COLD = 'target-cold-check'
PHASES = ('quiesce','lease_check','restore','resume','export_after')
COLD_SCRIPTS = 'experiments/unified-suite/pilot-aliyun/scripts'


def cold_step(step, peer, mount_spec, result_path):
    """Bind the reviewed step to the actual reader and request, not an arbitrary emitter."""
    argv = step.get('argv')
    if not isinstance(argv,list) or len(argv)!=9 or not all(isinstance(a,str) for a in argv):
        return False
    scripts = PurePosixPath(peer['repo'])/COLD_SCRIPTS
    python, allowed = PurePosixPath(argv[0]), PurePosixPath(peer['python'])
    leaf = scripts/'check-c4-target-cold-tree.py'
    request = dict(path=argv[4],sha256=argv[6])
    if (python.parent!=allowed.parent or not re.fullmatch(re.escape(allowed.name)+r'(?:\.\d+)?',python.name) or
            argv[1:4]!=[str(leaf),'check','--request'] or argv[5]!='--sha256' or
            argv[7:]!=['--out',str(result_path)] or not PurePosixPath(argv[4]).is_absolute() or
            not re.fullmatch('[0-9a-f]{64}',argv[6]) or
            not re.fullmatch('[0-9a-f]{64}',step.get('executable_sha256',''))):
        return False
    # The coordinator pins remote bytes from its reviewed copy of each script;
    # the existing worker verifies these input pins again on the target.
    local = Path(__file__).resolve().parents[2]/COLD_SCRIPTS
    pins = [dict(path=str(scripts/name),sha256=digest(local/name))
            for name in ('check-c4-target-cold-tree.py','prepare-session-input.py')]
    return all(p in step.get('inputs',[]) for p in (mount_spec,request,*pins))


def cold_inputs(s):
    """V2 alone runs a reviewed, supervised target-only cold tree read."""
    target = s['target']
    c = s.get('target_cold_check')
    if (s.get('restore_method') != 'shared-workspace-db' or not isinstance(c,dict) or
            set(c) != {'cohort','worker_readback','step_readback','expected_tree_sha256'} or
            not isinstance(c['expected_tree_sha256'],str) or
            not re.fullmatch('[0-9a-f]{64}',c['expected_tree_sha256'])):
        raise ValueError('V2 requires a pinned shared-workspace target cold check and source tree SHA')
    cs = spec(c['cohort'],'experiment-cohort-v1')
    if (cs.get('purpose') != 'experiment' or
            cs.get('provider') != 'ssh' or cs.get('agents') != 1 or len(cs['workers']) != 1):
        raise ValueError('Cold check requires one supervised SSH target worker')
    cohort.validate(cs)
    row = cs['workers'][0]
    inv = load(pinned(cs['inventory']))
    nodes = {n['id']:n for n in inv['nodes']}
    if (inv.get('schema') != 'experiment-inventory-v3' or row['node_id'] != target['node_id'] or
            row['peer'] != target['peer'] or
            nodes[target['node_id']]['cluster_id'] != nodes[s['source']['node_id']]['cluster_id'] or
            any(load(pinned(s[n])).get('inventory') != cs['inventory'] for n in PHASES)):
        raise ValueError('Cold check and handoff phases must bind the same single-pool target slot and inventory')
    w = spec(c['worker_readback'],'experiment-worker-v1')
    mount = dict(spec=target['mount_spec'],directory=target['mount_directory'])
    if (c['worker_readback']['sha256'] != row['spec']['sha256'] or w.get('mount') != mount or
            w.get('requires_mount') is not True or w.get('worker_id') != row['id'] or
            w.get('purpose') != cs['purpose'] or w.get('kind') != 'control' or len(w['jobs']) != 1):
        raise ValueError('Cold worker must be the exact mounted target worker with one job')
    job = w['jobs'][0]
    step = spec(c['step_readback'],'experiment-step-v1')
    result_path = Path(job['result']['path'])
    expected = dict(schema='c4-target-cold-tree-v1',passed=True,full_tree_read=True,read_only=True,
        native_app_opened=False,workspace_copy_bytes=0,workspace=target['session_state']['workspace'],
        anchor=target['anchor'],mount_spec=target['mount_spec'],mount_directory=target['mount_directory'],
        observed_tree_sha256=c['expected_tree_sha256'])
    if (job['step']['sha256'] != c['step_readback']['sha256'] or job['id'] != COLD or
            step.get('stage') != COLD or step.get('inherit_environment') != [] or job.get('observe_opencode_tools') or
            any(job['result']['equals'].get(k) != v for k,v in expected.items()) or
            not result_path.is_absolute() or '..' in result_path.parts or
            result_path.is_relative_to(Path(expected['workspace']).parent) or
            not cold_step(step,target['peer'],target['mount_spec'],result_path)):
        raise ValueError('Cold job must pin the reviewed read-only tree check and exact result predicates')
    return cs, row, job, expected


def target_identity(value):
    mount = value['mount']
    process = {k:value['process'][k] for k in ('pid','boot_id','start_ticks')}
    if (value.get('passed') is not True or
            type(mount['mount_id']) is not int or mount['mount_id']<=0 or not mount['filesystem'].startswith('fuse') or
            not isinstance(process['boot_id'],str) or not process['boot_id'] or
            any(type(process[k]) is not int or process[k]<=0 for k in ('pid','start_ticks'))):
        raise ValueError('Live target FUSE mount and process identity required')
    return dict(mount=mount,process=process,anchor=value['anchor'])


def cold_proof(c, config, completed, directory, mounted):
    cs,row,job,expected = config
    result_path, release_path = (directory/name for name in ('result.json','release-intent.json'))
    if (load(result_path) != completed or
            load(release_path) != dict(spec_sha256=c['cohort']['sha256'],workers=[row['id']]) or
            any(completed.get(k) is not True for k in ('passed','workload_passed','telemetry_live_check_passed')) or
            any(type(completed.get(k)) is not int or completed[k]!=1 for k in
                ('agents','ready_workers','completed_workers','completed_jobs')) or
            completed.get('purpose') != cs['purpose'] or len(completed.get('jobs',[])) != 1):
        raise ValueError('One actual successful target cold cohort and release receipt required')
    actual = completed['jobs'][0]
    if (actual.get('passed') is not True or actual.get('worker') != row['id'] or actual.get('job') != COLD or
            actual.get('step_sha256') != job['step']['sha256'] or actual.get('result_path') != job['result']['path'] or
            not re.fullmatch('[0-9a-f]{64}',actual.get('result_sha256','')) or
            type(actual.get('exit_code')) is not int or actual['exit_code']!=0 or actual.get('remaining_process_group') != []):
        raise ValueError('Cold proof must come from the exact successful target job')
    proof = actual['result']
    if (any(k not in proof or type(proof[k]) is not type(v) or proof[k]!=v for k,v in expected.items()) or
            any(type(proof.get(k)) is not int or proof[k]<=0 for k in ('files_hashed','bytes_hashed')) or
            target_identity(proof) != target_identity(mounted)):
        raise ValueError('Cold tree proof does not match the new target mount, process or pinned source tree')
    return dict(schema='c4-target-cold-check-v1',passed=True,cohort=c['cohort'],
        cohort_result=dict(path=str(result_path),sha256=digest(result_path)),
        release=dict(path=str(release_path),sha256=digest(release_path)),job_result_sha256=actual['result_sha256'],
        proof={**{k:proof[k] for k in expected},**target_identity(proof),
               **{k:proof[k] for k in ('files_hashed','bytes_hashed')}},
        before_first_target_export=True)


def validate(s):
    if s['mode'] not in ('stay','move'): raise ValueError('stay or move required')
    restore_method=s.get('restore_method','native-export-import')
    if restore_method not in ('native-export-import','shared-workspace-db'):
        raise ValueError('Explicit supported native state restore method required')
    source, target = s['source'], s['target']
    if source['anchor'] != target['anchor']: raise ValueError('handoff must reopen the same shared volume/workspace')
    if s['mode'] == 'move' and source['node_id'] == target['node_id']: raise ValueError('move requires a different execution node')
    if s['mode'] == 'stay' and source['node_id'] != target['node_id']: raise ValueError('stay must use original execution node')
    if s.get('claim_physical_move') and (not source.get('physical_host_id') or not target.get('physical_host_id') or source['physical_host_id']==target['physical_host_id']):
        raise ValueError('physical movement requires distinct verified physical hosts')
    if source['mount_directory'] == target['mount_directory'] and source['node_id'] == target['node_id']:
        raise ValueError('fresh target mount instance/evidence required even for stay')
    if restore_method=='shared-workspace-db':
        state=source.get('session_state')
        if not state or state!=target.get('session_state'):
            raise ValueError('The same workspace-local native database and XDG paths must reopen on the target')
        workspace=Path(state['workspace'])
        if not workspace.is_absolute() or Path(state['project'])!=workspace/'repo':
            raise ValueError('Original canonical workspace/project required')
        paths=[state['config']]+[state['xdg'][name] for name in ('data','state','cache','config')]
        if any(not Path(p).is_absolute() or not Path(p).is_relative_to(workspace) or Path(p)==workspace for p in paths):
            raise ValueError('All native configuration and XDG state must be inside the shared workspace')
    for name in PHASES:
        value = load(pinned(s[name]))
        if value.get('schema') != 'experiment-cohort-v1' or value.get('agents') != 1:
            raise ValueError('one migrating Agent per handoff; neighbors use a separate persistent cohort')
        peer = value['workers'][0]['peer']
        side = source if name in ('quiesce','lease_check') else target
        if peer != side['peer']: raise ValueError('phase routed to wrong peer')
    if s.get('schema') == V2:
        return cold_inputs(s)
    if 'target_cold_check' in s:
        raise ValueError('Target cold check requires experiment-handoff-v2')


def exported(result):
    if not result['passed'] or not result['jobs'] or not result['jobs'][-1].get('result'):
        raise ValueError('last job must return a successful native export result')
    return result['jobs'][-1]['result']


def run(ref, output):
    s = load(pinned(ref))
    schema = s.get('schema')
    if schema not in (V1,V2) or s.get('reviewed') is not True:
        raise ValueError('reviewed ' + (V2 if schema == V2 else V1) + ' required')
    cold = validate(s)
    output = private_dir(output); write_new(output/'spec.json',s)
    source, target = s['source'], s['target']; phase = 'quiesce'
    times = {}; begin = time.monotonic_ns(); tool_events = []
    def observe(row):
        m = row['message']
        if m['type'] == 'tool_completed' and m.get('session_id') == s['session_id'] and m.get('call_id'):
            tool_events.append((row['coordinator_ns'],m['call_id']))
    def measured(name, call):
        t = time.monotonic_ns(); result = call()
        times[name] = {'start_ns':t,'end_ns':time.monotonic_ns()}
        write_new(output/(name+'-timing.json'),times[name]); return result
    def node(side, action, name, extra=None, payload=None):
        directory = private_dir(output/name)
        args = [action,'--output',side['mount_directory']]
        if extra: args = extra
        return transport.one(side['peer'], args, directory, payload=payload)
    try:
        # Quiesce must be a supervised completed tool boundary + native export;
        # the mount supervisor rejects stop if an earlier worker is still alive.
        before = exported(measured(phase,lambda:cohort.run(s[phase],output/phase)))
        check = checkpoint(before)
        if check['session_id'] != s['session_id']: raise ValueError('wrong checkpoint session')
        write_new(output/'checkpoint.json',check)
        phase = 'source-stop'
        stopped = measured(phase,lambda:node(source,'stop',phase))
        if stopped.get('anchor') != source['anchor'] or not stopped.get('mount_absent') or stopped.get('exit_code') != 0:
            raise ValueError('source native drain/unmount did not complete')
        phase = 'lease_check'
        lease = measured(phase,lambda:cohort.run(s[phase],output/phase))
        if not lease['passed']: raise ValueError('source writer release check failed')
        if s['filesystem'] == 'brewfs':
            inspection = exported(lease)
            if inspection['lease_id'] is not None or inspection['holder_generation'] is not None or inspection['pending_writeback_bytes'] != 0 or inspection['workspace_id'] != source['anchor']['workspace']:
                raise ValueError('BrewFS writer lease still present or wrong workspace')
        # Import mode transfers native JSON only. Shared-database mode opens
        # the existing workspace state directly and transfers no session file.
        phase = 'checkpoint-transfer-check'
        raw = (json.dumps(before,ensure_ascii=False)+'\n').encode()
        payload = {'base64':base64.b64encode(raw).decode(),'sha256':hashlib.sha256(raw).hexdigest()}
        if payload['sha256'] != s['export_transfer_sha256']:
            raise ValueError('checkpoint differs from the reviewed native export; retain and re-prepare outside measurement')
        restore_method=s.get('restore_method','native-export-import')
        if restore_method=='native-export-import':
            phase='stage-export';stage = s['stage_export']
            measured(phase,lambda:node(target,'stage-json',phase,
                ['stage-json','--spec',stage['path'],'--sha256',stage['sha256'],'--output',s['import_path']],payload))
        else:
            write_new(output/'shared-state-restore.json',dict(restore_method=restore_method,
                session_state=source['session_state'],checkpoint_sha256=payload['sha256'],
                native_session_json_transferred_bytes=0,workspace_copy_bytes=0,
                scope='Target opens the original shared native database; restore cohort exports it for prefix verification'))
        # Import command input SHA is fixed in the reviewed restore template to
        # this checkpoint. If a different checkpoint arrives, do not rewrite it.
        phase = 'target-start'
        mounted = measured(phase,lambda:node(target,'start',phase,
            ['start','--spec',target['mount_spec']['path'],'--sha256',target['mount_spec']['sha256'],
             '--output',target['mount_directory']]))
        if mounted.get('anchor') != target['anchor']: raise ValueError('target anchor changed')
        if schema == V2:
            phase = COLD
            cold_spec = s['target_cold_check']
            identity = target_identity(mounted)
            if Path(target['session_state']['workspace']) != Path(identity['mount']['point'])/'workspace':
                raise ValueError('Target workspace is not inside the newly mounted shared volume')
            checked = measured(phase,lambda:cohort.run(cold_spec['cohort'],output/phase))
            proof = cold_proof(cold_spec,cold,checked,output/phase,mounted)
            status = measured('target-cold-status',lambda:node(target,'status','target-cold-status'))
            if target_identity(status) != identity:
                raise ValueError('Target mount changed between the cold read and first native export')
            status_path = output/'target-cold-status'/'protocol.jsonl'
            proof['post_check_status'] = dict(path=str(status_path),sha256=digest(status_path))
            cold_path = output/(COLD+'.json')
            write_new(cold_path,proof)
        phase = 'restore'
        restored = exported(measured(phase,lambda:cohort.run(s[phase],output/phase)))
        check_after(check,restored,False)
        state_ready_ns=time.monotonic_ns()
        write_new(output/'state-ready.json',dict(start_ns=begin,end_ns=state_ready_ns,
            original_native_history_verified=True,model_resume_started=False,
            scope='Storage/session state ready through original history verification; subsequent model resume is separate'))
        phase = 'resume'
        resumed = measured(phase,lambda:cohort.run(s[phase],output/phase,observe))
        if not resumed['passed']: raise ValueError('native resume failed')
        phase = 'export_after'
        after = exported(measured(phase,lambda:cohort.run(s[phase],output/phase)))
        validation = check_after(check,after,True)
        old_calls={p.get('callID') for m in before['messages'] for p in m['parts'] if p.get('type')=='tool'}
        new_calls={p.get('callID') for m in after['messages'][len(before['messages']):] for p in m['parts']
                   if p.get('type')=='tool' and p.get('state',{}).get('status')=='completed'}-old_calls-{None}
        first_tool=min((t for t,call in tool_events if call in new_calls),default=None)
        if first_tool is None: raise ValueError('missing observed new completed OpenCode tool event')
        result = {'passed':True,'mode':s['mode'],'session_id':s['session_id'],
                  'restore_method':restore_method,
                  'native_session_json_transferred_bytes':len(raw) if restore_method=='native-export-import' else 0,
                  'workspace_copy_bytes':0,
                  'state_ready_seconds':(state_ready_ns-begin)/1e9,
                  'state_ready_includes_model_resume':False,
                  'first_completed_tool_ack_seconds':(first_tool-begin)/1e9,
                  'phases':times,'session_validation':validation,
                  'scope':'clean native session handoff; coordinator ACK times include transport; task grading and neighbor effects separate',
                  'target_mount_retained':True,'automatic_retry':False}
        if schema == V2:
            result.update(schema='experiment-handoff-result-v2',
                target_cold_check=dict(path=str(cold_path),sha256=digest(cold_path)),
                target_cold_check_seconds=(times[COLD]['end_ns']-times[COLD]['start_ns'])/1e9,
                state_ready_includes_target_cold_check=True)
        write_new(output/'result.json',result); return result
    except BaseException as error:
        write_new(output/'failed.json',{'phase':phase,'error_type':type(error).__name__,
                                      'source_and_target_state':'inspect original receipts before any action','automatic_retry':False})
        raise


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--spec',required=True);p.add_argument('--sha256',required=True);p.add_argument('--output',required=True)
    a=p.parse_args();print(json.dumps(run({'path':a.spec,'sha256':a.sha256},a.output)))
