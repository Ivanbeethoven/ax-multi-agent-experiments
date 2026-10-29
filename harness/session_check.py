#!/usr/bin/env python3
"""Check explicit OpenCode exports before/after handoff; never calls a model."""
import argparse
import hashlib
import json
from experiment_io import load, digest, write_new


def message_hash(message):
    return hashlib.sha256(json.dumps(message, sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode()).hexdigest()


def checkpoint(export):
    sid = export['info']['id']; messages = export['messages']
    if not isinstance(sid, str) or not sid or not messages:
        raise ValueError('nonempty native session export required')
    ids = [m['info']['id'] for m in messages]
    if len(ids) != len(set(ids)): raise ValueError('duplicate message IDs')
    tools = [p for m in messages for p in m['parts'] if p.get('type') == 'tool']
    if not tools or any(p.get('state', {}).get('status') not in ('completed','error') for p in tools):
        raise ValueError('all recorded tools must be terminal at checkpoint')
    return {'schema': 'opencode-checkpoint-v1', 'session_id': sid, 'message_ids': ids,
            'message_hashes': [message_hash(m) for m in messages],
            'scope': 'exported terminal tool history only; actual client exit, DB flush and lease release are separate gates'}


def check_after(before, export, resumed):
    if before.get('schema') != 'opencode-checkpoint-v1': raise ValueError('checkpoint schema mismatch')
    if export['info']['id'] != before['session_id']: raise ValueError('session identity changed')
    n = len(before['message_ids']); messages = export['messages']
    if len(messages) < n or [m['info']['id'] for m in messages[:n]] != before['message_ids']:
        raise ValueError('history prefix missing/reordered')
    if [message_hash(m) for m in messages[:n]] != before['message_hashes']:
        raise ValueError('checkpoint history changed')
    if len({m['info']['id'] for m in messages}) != len(messages): raise ValueError('duplicate message IDs')
    added = [p for m in messages[n:] for p in m['parts'] if p.get('type') == 'tool' and p.get('state', {}).get('status') == 'completed']
    if resumed and not added: raise ValueError('no completed new tool after resume')
    return {'passed': True, 'session_id': before['session_id'], 'prefix_messages': n,
            'new_completed_tools': len(added), 'scope': 'history and tool continuation; external side-effect duplication, task grading and file content require independent evidence'}


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__); p.add_argument('action', choices=['checkpoint','restored','resumed'])
    p.add_argument('--export', required=True); p.add_argument('--output', required=True)
    p.add_argument('--checkpoint'); p.add_argument('--checkpoint-sha256'); a = p.parse_args()
    if a.action == 'checkpoint': result = checkpoint(load(a.export)); result['export_sha256'] = digest(a.export)
    else:
        if not a.checkpoint or digest(a.checkpoint) != a.checkpoint_sha256: raise ValueError('pinned checkpoint required')
        result = check_after(load(a.checkpoint),load(a.export),a.action == 'resumed')
    write_new(a.output,result)
