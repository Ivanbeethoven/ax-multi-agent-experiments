#!/usr/bin/env python3
"""Summarize verified local trace archives; never talks to the cloud."""
import argparse
import csv
import hashlib
import json
import math
from pathlib import Path
import re
import tarfile

from cleanup_parallel import archive_cell_evidence

ARMS={'native-kernel-default':'native','overlay-kernel-default':'native-overlay',
      'packed-kernel-default':'native-overlay-pack','juicefs-kernel-default':'juicefs'}
STATUSES={'ok','failed','interrupted','unsupported','not_run'}
FIELDS=['node','method','status','source_kind','attempt','run_id','execution_seconds',
        'operations','read_bytes','write_bytes','remaining_handles','probe_errno_differences',
        'archive_sha256','artifact_path','strict_correctness','selected_for_comparison','supplement_run_id']


def sha(path):
    h=hashlib.sha256()
    with path.open('rb') as f:
        for block in iter(lambda:f.read(1024*1024),b''):h.update(block)
    return h.hexdigest()


def local_archive(root,name):
    if not isinstance(name,str) or not name or '\\' in name:
        raise ValueError('archive must be a batch-relative path')
    rel=Path(name)
    if rel.is_absolute() or '..' in rel.parts:
        raise ValueError('archive path escapes batch directory')
    path=(root/rel).resolve(strict=True)
    if not path.is_relative_to(root.resolve()) or not path.is_file():
        raise ValueError('archive path escapes batch directory')
    return path


def read_record(tar,path):
    value=json.load(tar.extractfile(path))
    if not isinstance(value,dict):raise ValueError('archive record must be an object')
    return value


def archive_rows(root,item,node,run_id,attempt,kind,expected_arms):
    receipt=item.get('receipt')
    if not isinstance(receipt,dict):raise ValueError('missing verified receipt for '+node)
    if receipt.get('worker_id')!=node or receipt.get('run_id')!=run_id or receipt.get('attempt_id')!=attempt:
        raise ValueError('receipt identity/attempt mismatch: '+node)
    if receipt.get('known_credential_scan')!='passed':raise ValueError('credential scan not verified')
    digest=receipt.get('sha256','')
    if not isinstance(digest,str) or not re.fullmatch('[0-9a-f]{64}',digest):raise ValueError('invalid archive SHA')
    path=local_archive(root,item.get('archive'))
    if type(receipt.get('bytes')) is not int or path.stat().st_size!=receipt['bytes'] or sha(path)!=digest:
        raise ValueError('archive size or SHA mismatch: '+node)
    rows=[]
    with tarfile.open(path,'r:*') as tar:
        seen=set()
        for member in tar.getmembers():
            name=member.name;parts=Path(name).parts
            if name in seen or not member.isfile() or Path(name).is_absolute() or '..' in parts or '\\' in name:
                raise ValueError('unsafe or duplicate archive member')
            seen.add(name)
        terminal=read_record(tar,'results/terminal.json')
        if terminal.get('worker_id')!=node or terminal.get('run_id')!=run_id or terminal.get('attempt_id')!=attempt:
            raise ValueError('terminal identity/attempt mismatch: '+node)
        if terminal.get('strict_correctness') is not False:raise ValueError('incorrect correctness claim')
        reason=terminal.get('terminal_reason')
        if reason not in ('completed','failed') or reason!=receipt.get('terminal_reason'):
            raise ValueError('terminal/receipt outcome mismatch')
        methods=terminal.get('methods')
        if not isinstance(methods,dict) or set(methods)!=set(expected_arms):raise ValueError('unexpected arm set')
        if any(not isinstance(v,dict) or v.get('status') not in STATUSES for v in methods.values()):
            raise ValueError('nonterminal arm')
        if reason=='completed' and any(v['status']!='ok' for v in methods.values()):
            raise ValueError('completed archive contains failed arm')
        archive_cell_evidence(tar,terminal)
        for arm in expected_arms:
            status=methods[arm]['status']
            artifact='results/'+arm+('/replay.json' if status=='ok' else '/result.json')
            row={'node':node,'method':ARMS[arm],'status':status,'source_kind':kind,'attempt':attempt,
                 'run_id':run_id,'execution_seconds':None,'operations':None,'read_bytes':None,'write_bytes':None,
                 'remaining_handles':None,'probe_errno_differences':None,'archive_sha256':digest,
                 'artifact_path':item['archive']+'::'+artifact if status!='not_run' else None,
                 'strict_correctness':False,'selected_for_comparison':False,'supplement_run_id':None}
            if status=='ok':
                replay=read_record(tar,artifact)
                if replay.get('mode')!='performance' or replay.get('valid') is not False:
                    raise ValueError('replay must be labelled performance-only')
                seconds=replay.get('execution_seconds')
                if type(seconds) not in (int,float) or not math.isfinite(seconds) or seconds<=0:
                    raise ValueError('invalid execution time')
                differences=replay.get('probe_errno_differences')
                if differences is not None and (type(differences) is not int or differences<0):
                    raise ValueError('invalid errno-difference count')
                row.update(execution_seconds=seconds,operations=replay['operations'],read_bytes=replay['actual_read_bytes'],
                           write_bytes=replay['actual_write_bytes'],remaining_handles=replay['remaining_handles'],
                           probe_errno_differences=differences)
            rows.append(row)
    return rows


def summarize(state_path):
    state_path=Path(state_path).resolve(strict=True);root=state_path.parent
    state=json.loads(state_path.read_text(encoding='utf-8'))
    workers=state.get('workers',{});attempt=state.get('attempt_id');batch=state.get('batch_id')
    if set(workers)!={'node-a','node-b','node-c'} or not isinstance(attempt,str) or not batch:
        raise ValueError('three workers and current attempt/batch are required')
    rows=[]
    for node in sorted(workers):
        rows += archive_rows(root,workers[node],node,batch,attempt,'main',ARMS)
    for supplement in state.get('supplemental_replays',[]):
        if supplement.get('status')!='archived':raise ValueError('supplement is not archived')
        node=supplement.get('worker_id');arm=supplement.get('arm');run_id=supplement.get('run_id')
        if node not in workers or arm not in ARMS or not run_id or run_id==batch or supplement.get('parent_attempt_id')!=attempt:
            raise ValueError('supplement identity mismatch')
        rows += archive_rows(root,supplement,node,run_id,attempt,'supplement',[arm])
    successful={}
    for row in rows:
        if row['status']=='ok':
            key=(row['node'],row['method'])
            if key in successful:raise ValueError('multiple successful measurements for '+str(key))
            successful[key]=row;row['selected_for_comparison']=True
    for row in rows:
        if row['source_kind']=='main' and row['status']!='ok':
            other=successful.get((row['node'],row['method']))
            if other and other['source_kind']=='supplement':row['supplement_run_id']=other['run_id']
    totals={(r['operations'],r['read_bytes'],r['write_bytes']) for r in rows if r['selected_for_comparison']}
    if len(totals)>1:raise ValueError('successful cells differ in trace work counts')
    return {'batch_id':batch,'attempt':attempt,'rows':rows,'selected_count':len(successful),
            'scope':'application-level serialized trace performance; no strict correctness claim',
            'supplement_note':'supplement is a separately timed retry, not the original failure'}


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('state',type=Path);a=p.parse_args()
    value=summarize(a.state);root=a.state.resolve().parent
    out_json=root/'summary.json';out_csv=root/'summary.csv'
    if out_json.exists() or out_csv.exists():raise ValueError('summary exists; do not overwrite')
    with out_csv.open('x',newline='',encoding='utf-8') as f:
        writer=csv.DictWriter(f,fieldnames=FIELDS);writer.writeheader();writer.writerows(value['rows'])
    with out_json.open('x',encoding='utf-8') as f:json.dump(value,f,ensure_ascii=False,indent=2,allow_nan=False);f.write('\n')
    print(json.dumps({'rows':len(value['rows']),'selected':value['selected_count'],'outputs':[out_json.name,out_csv.name]}))


if __name__=='__main__':main()
