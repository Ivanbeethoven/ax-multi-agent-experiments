from pathlib import Path
import json, hashlib, re, math, csv
from collections import defaultdict
from datetime import datetime, timezone
ROOT=Path('/home/dev/ax-perf-issues-validation/runtime-review-01')
OUT=Path('/home/dev/ax-perf-issues-validation/independent-review-7b844')
C=ROOT/'home/dev/ax-perf-issues-validation/compose'
checks=[]
def check(name,ok,detail=None):
    d={'name':name,'passed':bool(ok)}
    if detail is not None: d['detail']=detail
    checks.append(d)
def j(p): return json.loads(p.read_text())
def sha(p): return hashlib.sha256(p.read_bytes()).hexdigest()
def at(p): return ROOT/p.lstrip('/')
def stats(p):
    d={}
    for l in p.read_text().splitlines():
        if not l or l.startswith('#'): continue
        parts=l.rsplit(None,1)
        if len(parts)!=2: continue
        k,v=parts
        try: d[k]=float(v)
        except ValueError: pass
    return d
archive=j(ROOT/'archive-manifest.json')
for m in archive['members']:
    p=ROOT/m['path']; check('archive:'+m['path'],p.stat().st_size==m['bytes'] and sha(p)==m['sha256'])
pins={'baseline':('ca07159f3da4b670e4aeb878faef928e69bc1634','448ac21de0aadb5d82e5d00e9c41df8cbf616ee9539964aeb104dd70114ef5de'),'candidate':('7b8445042fda8e6cd5ec9a3bedc0eb606a45d8b3','94d1d39d34360cf3cb4d20a9f4e37fe7d25c1062a0eee3f71082fa35e0145ee6')}
rows=[]; common=None
for old in j(C/'matrices/cached-qualified/paired-summary.json')['rows']:
    arm=old['arm'];rep=old['repetition'];a=at(old['artifact']);prefix=f'{arm}{rep}:'
    t=j(C/'trials'/f'ax447-{arm}-cached-{rep}'/'receipt.json');m=j(a/'manifest.json')
    check(prefix+'identity',m['source']['revision']==pins[arm][0] and m['source']['binary_sha256']==pins[arm][1] and m['source']['dirty'] is False and t['binary']['sha256']==pins[arm][1])
    for field,file in [('container_harness_sha256','run-measured-perf-in-container.sh'),('harness_sha256','run-isolated-redis-perf.sh'),('compose_sha256','compose.json')]:check(prefix+field,t[field]==sha(C/file))
    check(prefix+'backend_config',sha(a/'backend.yml')=='03d5c15c584c413ef7ab9827dd58b880f48cf2f49ab3c1bdb26da8a78cc7c28c')
    if common is None:common=m['comparability']
    check(prefix+'same_comparability',common==m['comparability'])
    hz=int((a/'client-cpu-clock-ticks.txt').read_text());cpu={}
    for l in (a/'client-cpu-raw.tsv').read_text().splitlines():
        tool,phase,ns,proc=l.split('\t',3);v=proc[proc.rfind(')')+2:].split()
        cpu[phase]={'ns':int(ns),'pid':int(proc.split('(',1)[0].strip()),'start':int(v[19]),'ticks':int(v[11])+int(v[12])}
    phases={v[1]:int(v[2]) for l in (a/'phase-boundaries.tsv').read_text().splitlines() if (v:=l.split('\t'))}
    s=cpu['tool_start'];e=cpu['tool_return'];d=cpu['post-write_extended_drained']
    check(prefix+'same_process',len({(v['pid'],v['start']) for v in cpu.values()})==1)
    check(prefix+'monotonic_cpu',s['ticks']<=e['ticks']<=d['ticks'])
    check(prefix+'sample_boundaries',s['ns']<=phases['tool_start']<=phases['tool_return']<=e['ns']<=phases['post-write_extended_drained']<=d['ns'])
    vals={'cpu_tool_s':(e['ticks']-s['ticks'])/hz,'cpu_total_s':(d['ticks']-s['ticks'])/hz,'wall_tool_s':(phases['tool_return']-phases['tool_start'])/1e9,'wall_total_s':(phases['post-write_extended_drained']-phases['tool_start'])/1e9}
    for k,v in vals.items():check(prefix+'recompute:'+k,math.isclose(v,old[k],rel_tol=1e-12,abs_tol=1e-12),v)
    argv=j(a/'tools/fio-bigwrite.effective-argv.json');jobs=j(a/'results/fio-bigwrite.json')['jobs'];job=jobs[0]
    check(prefix+'one_job',sum(x=='--name' or x.startswith('--name=') for x in argv)==1 and len(jobs)==1 and job['job options']['numjobs']=='1')
    check(prefix+'local_tool_success',job['error']==0 and job['write']['io_bytes']==16777216 and job['read']['io_bytes']==16777216 and job['write']['total_ios']==4096 and job['read']['total_ios']==4096)
    b=stats(a/'diagnostics/stats-fio-bigwrite-before.txt');e=stats(a/'diagnostics/stats-fio-bigwrite-post-write-drained-after.txt')
    keys=['brewfs_s3_put_ops_total','brewfs_s3_put_bytes_total','brewfs_buffer_dirty_bytes','brewfs_writeback_dirty_bytes','brewfs_writeback_recent_pending_upload_bytes']
    st={k:{'before':b[k],'after':e[k],'delta':e[k]-b[k]} for k in keys}
    check(prefix+'dirty_drained',[e[k] for k in keys[2:]]==[0,0,0]);check(prefix+'only_32_put_bytes',e['brewfs_s3_put_bytes_total']==32)
    rows.append({'arm':arm,'repetition':rep,'artifact':str(a),'classification':'unqualified_diagnostic','user_hz':hz,**vals,'fio_raw_write_short_ios':job['write']['short_ios'],'fio_raw_sync_n':job['sync']['lat_ns']['N'],'stats':st})
ratios=[]
for rep in range(3):
    b=next(x for x in rows if x['arm']=='baseline' and x['repetition']==rep);n=next(x for x in rows if x['arm']=='candidate' and x['repetition']==rep)
    ratios.append({'repetition':rep,**{k:n[k]/b[k] for k in ['cpu_tool_s','cpu_total_s','wall_tool_s','wall_total_s']}})
shape=[]
for arm in pins:
    t=j(C/'trials'/f'ax447-{arm}-cached-smoke-102/receipt.json');a=at(t['artifact_directories'][0]);groups=defaultdict(list)
    for l in (a/'brewfs_fuse_ops.log').read_text().splitlines():
        if '[write] REQ' not in l:continue
        f={k:int(v) for k,v in re.findall(r'\b(inode|fh|offset|data_len|write_flags|flags)=(\d+)',l)}
        f['unique']=int(re.search(r'\bunique:\s*(\d+)',l).group(1));groups[f['inode']].append(f)
    exact=[]
    for ino,writes in groups.items():
        w=[x for x in writes if x['write_flags']&1]
        if len(w)==4096 and sorted(x['offset'] for x in w)==list(range(0,16777216,4096)) and all(x['data_len']==4096 and x['unique']>0 for x in w) and len({x['unique'] for x in w})==4096:exact.append(ino)
    check(arm+':cached_FUSE_4096_shape',len(exact)==1,exact)
    matches=[{'line':i+1,'text':l} for i,l in enumerate((a/'brewfs.log').read_text().splitlines()) if re.search(r'pipeline upload failed|InternalError|RecoveryUploadFailed|recovery upload failed',l,re.I)]
    check(arm+':archived_upload_failure',any('pipeline upload failed' in x['text'] for x in matches) and any('InternalError' in x['text'] for x in matches))
    e=stats(a/'diagnostics/stats-fio-bigwrite-post-write-drained-after.txt')
    shape.append({'arm':arm,'artifact':str(a),'cached_inode':exact,'request_count':4096,'request_bytes':4096,'nonzero_distinct_request_ids':True,'timing_excluded':True,'harness_status':t['status'],'upload_error_evidence':matches,'after_put_bytes':e['brewfs_s3_put_bytes_total'],'after_dirty_bytes':e['brewfs_writeback_dirty_bytes']})
a=ROOT/'home/dev/ax-issue447-perf-baseline/docker/compose-xfstests/artifacts/perf-run-1794504480-5327';failures=[]
for direct in [0,1]:
    stem=f'fio-randrw-direct{direct}';pre=j(a/f'tools/{stem}-prefill.effective-argv.json');run=j(a/f'tools/{stem}.effective-argv.json')
    def arg(v,k):return [x.split('=',1)[1] for x in v if x.startswith('--'+k+'=')][-1]
    for k in ['name','directory','bs','size','numjobs','ioengine','iodepth','direct','randseed','randrepeat']:check(stem+':same_prefill_target:'+k,arg(pre,k)==arg(run,k))
    check(stem+':one_fio_job',sum(x.startswith('--name=') for x in pre)==1 and sum(x.startswith('--name=') for x in run)==1)
    prejob=j(a/f'results/{stem}-prefill.json')['jobs'][0];job=j(a/f'results/{stem}.json')['jobs'][0]
    check(stem+':prefill_local_success',prejob['error']==0 and prejob['write']['io_bytes']==134217728);check(stem+':read_EIO_present',job['error']==5)
    expected={l.split(None,1)[1] for l in (a/f'tools/{stem}-prepare.sha256').read_text().splitlines()}
    errors=re.findall(r'error on file (.+?): Input/output error: read offset=(\d+), buflen=(\d+)',(a/f'tools/{stem}.log').read_text())
    check(stem+':failed_files_prefilled',len(errors)==4 and {x[0] for x in errors}==expected)
    failures.append({'direct':direct,'artifact':str(a),'fio_error':job['error'],'file_errors':[{'file':f,'offset':int(o),'bytes':int(b)} for f,o,b in errors],'prefill_bytes':prejob['write']['io_bytes'],'completed_read_bytes':job['read']['io_bytes'],'write_bytes':job['write']['io_bytes'],'read_ios_including_failures':job['read']['total_ios'],'write_ios':job['write']['total_ios'],'fio_tool_runtime_ms':job['read']['runtime'],'remote_persistence_proven':False})
w=j(C/'workspace/ax447-workspace-qualified/summary.json')
check('workspace_failed_before_measurement',w['passed'] is False and w['waves']==[] and w['row_cases']==[] and w['mount_exits']==[])
check('plan_revised_before_formal','No formal regression case started yet' in j(C/'regression-plan-revision-01.json')['scope'])
report={'created_at':datetime.now(timezone.utc).isoformat(),'scope':'Independent read-only review of immutable rbm archive; no cloud access or workloads','candidate':pins['candidate'][0],'baseline':pins['baseline'][0],
 'decision':{'approve_one_integrated_pr':False,'status':'not_ready','reason':'Actual runtime acceptance incomplete and write persistence failures invalidate cached timing qualification; no attribution to candidate code established.'},
 'archive':{'root':str(ROOT),'archive_sha256':j(ROOT/'verified.json')['archive_sha256'],'members':len(archive['members']),'member_bytes':sum(x['bytes'] for x in archive['members']),'all_members_independently_rehashed':True},
 'audit_checks':{'total':len(checks),'passed':sum(x['passed'] for x in checks),'failed':sum(not x['passed'] for x in checks)},'source_ci_component_review':'preflight.json','proxy_protocol_review':'proxy-protocol-review.json','same_configuration':common,
 'cached':{'classification':'unqualified_diagnostic_not_publishable_performance_gain','paired_order':['AB','BA','AB'],'cpu_scope':'BrewFS process user+system ticks, all threads; excludes fio/RustFS','rows':rows,'ratios':ratios,'actual_path_proof':shape,'limitations':['FIO verification precedes post-write drain and can read local data.','All six final counters have only 32 PUT bytes for 16MiB workload; this does not prove remote durability.','Both logged shape runs contain object-store InternalError/upload failures while harness exits zero and dirty returns zero.','CPU arithmetic is reproducible; performance acceptance is not. Pair1 total wall also regresses12.31%.','FIO write.short_ios4095 with sync latency N4095 preserved raw; not asserted to mean lost writes or ignored.']},
 'baseline_regression_smoke':{'artifact':str(a),'scope':'32MiB per file,3s,4jobs; not formal512MiB/20s','overall_passed':False,'failures':failures,'post_write_drains':list(csv.DictReader((a/'post-write-drain.tsv').read_text().splitlines(),delimiter='\t')),'interpretation':'Prefill names and one-job protocol match; error follows strict drain/remount. Baseline failure does not qualify candidate.'},
 'workspace_actual_guards':{'passed':False,'phase':w['phase'],'waves_completed':len(w['waves']),'row_cases_completed':len(w['row_cases']),'reason':'Old proxy rejects init PUT; common proxy fix has local RED/GREEN tests, actual runs pending.','configured_packed_budgets':w['packed_budget_limits_bytes'],'budget_scope':w['packed_budget_source'],'packed_native_metrics':w['packed_native_metrics']},
 'remaining_acceptance':['Diagnose and fix backend/harness protocol for both unchanged release arms; preserve failures.','Fresh cached pairs must show full remote persistence and error-free cache-independent/remount readback, plus actual cached path and timing through drain.','Complete unchanged512MiB/20s six standard scenes per arm with randrw/direct guards and strict drain; reverse-order repeat only unresolved cases.','Complete actual Shared workspace different-group fill and xattr hot/capacity checks; native row hits and zero timed misses; baseline unsupported capacity fields missing, not zero.'],
 'owner_report_not_yet_independently_archived':'New candidate randrw EIO/InternalError reported by parent; this report relies on earlier independently archived failures, not that report.',
 'separation':'Packed7093 separate worker campaign acceptance remains separate; no results mixed.'}
OUT.mkdir(exist_ok=True)
(OUT/'runtime-review-01-checks.json').write_text(json.dumps(checks,indent=2)+'\n')
(OUT/'runtime-review-01.json').write_text(json.dumps(report,indent=2)+'\n')
md='''Issue #445-447 independent runtime review at 7b844504: NOT READY for one integrated PR.

All {members} immutable runtime-review-01 members were independently rehashed. {passed}/{count} factual audit checks passed; this is not runtime acceptance.

Both frozen release identities, mounted cache configuration, resource limits and common harness match. The single --name fix applies to both arms. Separate operation logs prove exactly 4096 distinct cached FUSE 4KiB writes with nonzero request IDs per arm. Source/CI/component evidence remains valid in preflight.json.

The old three cached AB/BA/AB pairs are unqualified diagnostics. CPU totals reproduce from /proc ticks, but all six finish with only32 uploaded bytes for16MiB work. BOTH logged shape runs show chunks-v2/3/0 upload failures/InternalError while the harness returns0 and dirty becomes0. Local fio verification and dirty/pending0 do not prove remote durability. These runs cannot support publishable CPU improvement. One pair also has12.31% higher fully drained wall time.

The baseline standard smoke fails randrw direct0 and direct1 after four-file prefill, strict drain and remount. Each has four EIO reads. Prefill/measured names, directories, sizes, job counts and seeds match, and each argv has one --name. This failed32MiB/3s smoke does not satisfy512MiB/20s guards and does not identify the causal layer. A baseline failure does not waive candidate correctness.

Actual workspace guards have no completed waves or row cases: old helper init PUT fails. Its common protocol fix has local RED/GREEN evidence but actual fill and row hot/capacity checks remain pending.

Require fresh remotely durable cached pairs, full512MiB/20s six-scene baseline/candidate guards (repeat unresolved cases only), and actual Shared workspace fill/row checks before accepting ONE PR. Retain failures. Packed7093 acceptance is separate and no results are mixed.
'''.format(members=len(archive['members']),passed=report['audit_checks']['passed'],count=len(checks))
(OUT/'runtime-review-01.md').write_text(md)
receipt={p.name:sha(p) for p in [OUT/'runtime-review-01.json',OUT/'runtime-review-01.md',OUT/'runtime-review-01-checks.json',OUT/'audit-runtime-review-01.py']}
(OUT/'runtime-review-01-sha256.json').write_text(json.dumps(receipt,indent=2)+'\n')
print(json.dumps({'checks':report['audit_checks'],'decision':report['decision'],'sha256':receipt},indent=2))
if report['audit_checks']['failed']:
    print(json.dumps([x for x in checks if not x['passed']],indent=2));raise SystemExit(1)
