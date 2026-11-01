"""One clean C4 stay/move, preserving original session and shared data anchor.

All native argv are reviewed beforehand. An interrupted phase is unknown, never
automatically retried. No DELETE, data-tree copy, process-memory migration or
claim that a Manager journal lease is a client writer lease.
"""
import argparse
import base64
import copy
import hashlib
import json
from pathlib import Path
import time

from .common import digest, load, pinned, private_dir, spec, write_new
from . import cohort, transport
from ..session_check import checkpoint, check_after


def validate(s):
    if s['mode'] not in ('stay','move'): raise ValueError('stay or move required')
    source, target = s['source'], s['target']
    if source['anchor'] != target['anchor']: raise ValueError('handoff must reopen the same shared volume/workspace')
    if s['mode'] == 'move' and source['node_id'] == target['node_id']: raise ValueError('move requires a different execution node')
    if s['mode'] == 'stay' and source['node_id'] != target['node_id']: raise ValueError('stay must use original execution node')
    if s.get('claim_physical_move') and (not source.get('physical_host_id') or not target.get('physical_host_id') or source['physical_host_id']==target['physical_host_id']):
        raise ValueError('physical movement requires distinct verified physical hosts')
    if source['mount_directory'] == target['mount_directory'] and source['node_id'] == target['node_id']:
        raise ValueError('fresh target mount instance/evidence required even for stay')
    for name in ('quiesce','lease_check','restore','resume','export_after'):
        value = load(pinned(s[name]))
        if value.get('schema') != 'experiment-cohort-v1' or value.get('agents') != 1:
            raise ValueError('one migrating Agent per handoff; neighbors use a separate persistent cohort')
        peer = value['workers'][0]['peer']
        side = source if name in ('quiesce','lease_check') else target
        if peer != side['peer']: raise ValueError('phase routed to wrong peer')


def exported(result):
    if not result['passed'] or not result['jobs'] or not result['jobs'][-1].get('result'):
        raise ValueError('last job must return a successful native export result')
    return result['jobs'][-1]['result']


def run(ref, output):
    s = spec(ref,'experiment-handoff-v1'); validate(s)
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
        # Transfer only native exported session JSON. The shared repo/HOME tree
        # stays in the shared filesystem; never cp it between execution nodes.
        phase = 'stage-export'
        raw = (json.dumps(before,ensure_ascii=False)+'\n').encode()
        payload = {'base64':base64.b64encode(raw).decode(),'sha256':hashlib.sha256(raw).hexdigest()}
        if payload['sha256'] != s['export_transfer_sha256']:
            raise ValueError('checkpoint differs from pre-reviewed import; retain and re-prepare outside measurement')
        stage = s['stage_export']
        measured(phase,lambda:node(target,'stage-json',phase,
            ['stage-json','--spec',stage['path'],'--sha256',stage['sha256'],'--output',s['import_path']],payload))
        # Import command input SHA is fixed in the reviewed restore template to
        # this checkpoint. If a different checkpoint arrives, do not rewrite it.
        phase = 'target-start'
        mounted = measured(phase,lambda:node(target,'start',phase,
            ['start','--spec',target['mount_spec']['path'],'--sha256',target['mount_spec']['sha256'],
             '--output',target['mount_directory']]))
        if mounted.get('anchor') != target['anchor']: raise ValueError('target anchor changed')
        phase = 'restore'
        restored = exported(measured(phase,lambda:cohort.run(s[phase],output/phase)))
        check_after(check,restored,False)
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
                  'first_completed_tool_ack_seconds':(first_tool-begin)/1e9,
                  'phases':times,'session_validation':validation,
                  'scope':'clean native session handoff; coordinator ACK times include transport; task grading and neighbor effects separate',
                  'target_mount_retained':True,'automatic_retry':False}
        write_new(output/'result.json',result); return result
    except BaseException as error:
        write_new(output/'failed.json',{'phase':phase,'error_type':type(error).__name__,
                                      'source_and_target_state':'inspect original receipts before any action','automatic_retry':False})
        raise


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--spec',required=True);p.add_argument('--sha256',required=True);p.add_argument('--output',required=True)
    a=p.parse_args();print(json.dumps(run({'path':a.spec,'sha256':a.sha256},a.output)))
