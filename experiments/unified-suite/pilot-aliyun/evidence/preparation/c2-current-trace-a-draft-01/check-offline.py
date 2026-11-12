#!/usr/bin/env python3
"""Offline checks only: no SSH, FUSE, AWS SDK, or ECS calls."""
from datetime import datetime, timedelta, timezone
import base64
import hashlib
import json
from pathlib import Path
import re
import runpy
import sys
import tempfile

HERE=Path(__file__).resolve().parent
REPO=HERE.parents[5]
ctl=runpy.run_path(str(HERE/'run-c2-current-a.py'))
worker=runpy.run_path(str(HERE/'qualify-c2-current-a.py'))
for label,source in (('drives',ctl['CHECK_DRIVES']),('allowlist',ctl['READ_FILES']),('fsync',worker['FSYNC_TREE']),('cleanup',worker['CLEAN_SCRATCH'])):
    compile(source,label,'exec')

draft=json.loads((HERE/'request.draft.json').read_text())
assert draft['reviewed'] is False and draft['runnable'] is False
assert draft['instance_ids']==ctl['IDS']
assert draft['hard_window_seconds']==2700 and draft['work_deadline_since_first_start_request_seconds']==2400
assert draft['source']['case']==worker['CASE'].as_posix() and draft['source']['manifest']==worker['MANIFEST'].as_posix()
assert draft['source']['manifest_sha256']==worker['MAN_SHA']
for field,pin in [('brewfs','BREWFS'),('packed_producer','PRODUCER'),('replay','REPLAY')]:
    assert draft['build'][field]==dict(path=worker[pin].as_posix(),sha256=worker['PINS'][worker[pin]])
assert all(draft['source'][key]==value for key,value in worker['EXPECTED'].items())
for link in re.findall(r'\]\(([^)]+)\)',(HERE/'README.zh.md').read_text(encoding='utf-8')):
    assert (HERE/link).is_file(),link
archive=json.loads((REPO/'scenarios/k8s-containers/archives.json').read_text())
row=next(x for x in archive['archives'] if x['case']=='pytest-full-02')
assert (row['sha256'],row['bytes'],row['https_url'])==(draft['source']['archive_sha256'],
    draft['source']['archive_bytes'],draft['source']['archive_url'])
old=HERE.parent/'parallel-c2-native-03/raw/route'
worker['strict_proof'](old/'strict-proof/verification.json',cold=False)
worker['strict_proof'](old/'cold-proof/verification.json',cold=True)

# Exercise refusal on stale branches, incomplete StopCharging, wrong hashes, and unknown paths.
api=dict(RequestId='test',TotalCount=3,Instances=dict(Instance=[
    dict(InstanceId=i,Status='Stopped',StoppedMode='StopCharging',ZoneId='cn-hangzhou-j') for i in ctl['IDS'].values()]))
assert ctl['exact_stopped'](api)
api['Instances']['Instance'][0]['Status']='Running'
assert not ctl['exact_stopped'](api)
now=datetime.now(timezone.utc)
latest=dict(passed=True,at=now.isoformat(),sources=[
    dict(component=name,repository=repo,branch=branch,built_commit=commit,remote_commit=commit,
         matches=True,lookup_exit_code=0) for name,repo,branch,commit in (
       ('brewfs','https://github.com/brewfs/brewfs','main',ctl['BREWFS']),
       ('rustfs','https://github.com/Ivanbeethoven/rustfs','feat/dynamic-block-size-rebased',ctl['RUSTFS']))])
assert ctl['latest_ok'](latest,now)
assert not ctl['latest_ok'](latest,now+timedelta(seconds=901))
assert not ctl['review_ok'](draft,'tag',242,controller_sha='a',worker_sha='b',latest_sha='c',before_sha='d')
with tempfile.TemporaryDirectory(prefix='ax-c2-check-') as td:
    out=Path(td)
    raw=b'{"passed":false}\n'
    item=dict(sha256=hashlib.sha256(raw).hexdigest(),body=base64.b64encode(raw).decode())
    for bad in ({'../private.env':item},{'result.json':dict(item,sha256='0'*64)}):
        try:
            ctl['persist_payload'](out,bad)
        except ValueError:
            pass
        else:
            raise AssertionError('Disallowed receipt copied')
    ctl['persist_payload'](out,{'result.json':item})
    assert (out/'worker/result.json').read_bytes()==raw

if len(sys.argv)==2:
    initial=Path(sys.argv[1])
    assert worker['digest'](initial)==draft['source']['initial_tree_sha256']
    assert worker['verify_initial_summary'](initial)==dict(entries=714,files=614,directories=100,bytes=35526497)
print('Offline C2 checks passed; historical data used for schema validation only.')
