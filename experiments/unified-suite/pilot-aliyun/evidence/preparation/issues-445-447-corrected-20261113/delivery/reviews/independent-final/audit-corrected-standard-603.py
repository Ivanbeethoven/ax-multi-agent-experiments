from pathlib import Path
import json,hashlib,re,shlex
O=Path('/home/dev/ax-perf-issues-validation/independent-review-7b844')
# Reuse the frozen raw-evidence checks; do not execute the old report writer.
validation=(O/'audit-corrected-standard-601.py').read_text().split("delta=P/'protocol/workspace-failure-retention-01'")[0]
validation=validation.replace('corrected-runtime-review-06','corrected-runtime-review-08').replace('regression-corrected512-01','regression-corrected512-reverse603').replace('regression-601','regression-603').replace('== 1236 and members == 1237','== 1238 and members == 1239')
ns={};exec(compile(validation,'reused-standard-raw-checks','exec'),ns)
P=ns['P'];check=ns['check'];J=ns['J'];SHA=ns['SHA'];pairs=ns['pairs'];checks=ns['checks']
runtime=J(P/'runtime/regression-corrected512-reverse603/receipt.json')
check('actual BA execution',runtime['passed'] and [x['arm'] for x in runtime['phases']]==['candidate','baseline'] and runtime['phases'][0]['finished_at']<runtime['phases'][1]['started_at'])
q=P/'protocol/full-reverse603-01/run-standard-full-reverse-603.py'
original=Path('/home/dev/ax-perf-issues-validation/corrected-runtime-review-05/harness/run-corrected-standard-guard-pair-512.py')
restored=q.read_text().replace('regression-corrected512-reverse603','regression-corrected512-01').replace("('candidate','baseline')","('baseline','candidate')").replace('regression-603','regression-601').replace("'regression','603'","'regression','601'").replace("'trial':603","'trial':601")
check('wrapper only order trial output',restored==original.read_text())
check('actual wrapper hash',runtime['script_sha256']==SHA(q)=='7d90c1ba80e97aa4c54f4960a460a951bb75298644a987d5745924ceef784445')
native={}
for arm in ['baseline','candidate']:
    a=next((P/'artifacts'/arm).iterdir());snap={}
    for phase in ['before','after','post-write-drained-after']:
        snap[phase]=ns['metrics'](a/f'diagnostics/stats-fio-randwrite-direct1-{phase}.txt')
    native[arm]={k:{phase:s[k] for phase,s in snap.items()} for k in snap['before'] if k in snap['after'] and k in snap['post-write-drained-after'] and any(x in k for x in ('s3_put_','writeback_stage','writeback_upload','writeback_freeze','writeback_commit','writeback_flush','meta_')) and len({s[k] for s in snap.values()})>1}
target=next(p for p in pairs if p['tool']=='fio-randwrite-direct1')
report=dict(schema='independent-standard603-review-v1',decision='CORRECTNESS_PASS_REPEATED_RANDWRITE_DIRECT1_CPU_REGRESSION_HOLD',integrated_pr_qualified=False,
    evidence=dict(directory=str(P),tar_sha256=ns['receipt']['sha256'],manifest_sha256=ns['receipt']['manifest_sha256'],payload_files=1238,total_tar_members=1239),checks_passed=len(checks),
    order='candidate then baseline; unchanged complete six-scene sequence on each arm',wrapper_sha256=SHA(q),runtime_sources={a:v[0] for a,v in ns['pins'].items()},publication_source='e2c964c55fafdcced34af58e840ebddda1cf9ea4',
    correctness='All twelve cells independently pass the same raw identity, FIO, three-boundary process CPU, strict drain, full empty-cache cold SHA, normal remount, configuration, resource and capacity checks.',
    capacities=ns['capacities'],pairs=pairs,randwrite_direct1_native_windows=native,
    interpretation='Trial603 repeats the full CPU/GiB increase for randwrite/direct1 after reversing the complete arm order. Compare this matched pair separately with trial601; do not average away the repeated regression. No process restart, negative CPU delta, or misplaced observation boundary was found. Different completed write bytes remain explicit denominators; active and post-return CPU are separately retained. Attribution remains open, not noise by assumption.',
    next='Read-only source/counter analysis with445, then only a concrete authorized diagnostic hypothesis run serially by447. Workspace114 and long-row expansion remain pending.')
out=O/'corrected-standard-603-review-01.json';out.write_text(json.dumps(report,indent=2)+'\n')
(O/'corrected-standard-603-checks-01.json').write_text(json.dumps(checks,indent=2)+'\n')
print(json.dumps({'review_sha256':SHA(out),'checks':len(checks),'target':target,'native':native},indent=2))
