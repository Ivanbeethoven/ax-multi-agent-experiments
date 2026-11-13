from pathlib import Path
import json,hashlib,re,statistics,math,struct
from datetime import datetime,timezone
B=Path('/home/dev/ax-perf-issues-validation');O=B/'independent-review-7b844';R3=B/'runtime-review-03';R4=B/'runtime-review-04';RS=B/'runtime-sealed-01';RT=B/'runtime-timestamp-01';CP='home/dev/ax-perf-issues-validation/compose'
def j(p):return json.loads(p.read_text())
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def cp(r,p):return r/p.lstrip('/')
def canon(d):return json.dumps(d,sort_keys=True,separators=(',',':')).encode()
checks=[]
def check(n,v,d=None):
 z={'name':n,'passed':bool(v)}
 if d is not None:z['detail']=d
 checks.append(z)
archives=[]
for r in [R3,R4,RS]:
 m=j(r/'archive-manifest.json');files=m.get('files',m.get('members'));v=j(r/'verified.json')
 for f in files:
  p=r/f['path'];check(r.name+':hash:'+f['path'],p.stat().st_size==f['bytes'] and sha(p)==f['sha256'])
 archives.append({'path':str(r),'members':len(files),'bytes':sum(f['bytes'] for f in files),'receipt':v})
for p,h in j(RT/'sha256.json').items():check('timestamp:hash:'+p,sha(RT/p)==h)
pins={'baseline':'448ac21de0aadb5d82e5d00e9c41df8cbf616ee9539964aeb104dd70114ef5de','candidate':'94d1d39d34360cf3cb4d20a9f4e37fe7d25c1062a0eee3f71082fa35e0145ee6'}
def native(p):
 result={}
 for l in p.read_text().splitlines():
  if not l or l.startswith('#'):continue
  q=l.rsplit(None,1)
  if len(q)==2:
   try:result[q[0]]=float(q[1])
   except ValueError:pass
 return result

def gate(tag,p,arm,passed):
 g=j(p/'receipt.json');e=j(p/'expected.json');v=j(p/'observed.json')
 check(tag+':declared_gate',g['passed'] is passed)
 check(tag+':mount_lifecycle',g['new_cache_was_empty'] and g['old_cache_preserved'] and g['new_cache_root']!=g['old_cache_root'] and g['unmount']['old_process_terminated'] and not g['unmount']['forced'])
 check(tag+':binary_and_pid',g['old_mount']['pid']!=g['new_mount']['pid'] and g['old_mount']['executable_sha256']==g['new_mount']['executable_sha256']==pins[arm] and g['old_mount']['argv']==g['new_mount']['argv'])
 before=(p/'config-before.yaml').read_text();after=(p/'config-after.yaml').read_text()
 check(tag+':only_cache_root_changed',after==before.replace(g['old_cache_root'],g['new_cache_root']) and sha(p/'config-before.yaml')==g['config_sha256_before'] and sha(p/'config-after.yaml')==g['config_sha256_after'])
 check(tag+':no_logged_failure_before',not g['log_failures_before'])
 if passed:check(tag+':no_logged_failure_after',not g['log_failures_after'] and g['reasons']==[])
 check(tag+':size_preserved',[(x['path'],x['bytes']) for x in e]==[(x['path'],x['bytes']) for x in v] and sum(x['bytes'] for x in e)==g['logical_bytes'])
 diffs=[{'path':x['path'],'bytes':x['bytes'],'warm_sha256':x['sha256'],'cold_sha256':y['sha256']} for x,y in zip(e,v) if x['sha256']!=y['sha256']]
 check(tag+':full_sha_decision',bool(diffs) is (not passed))
 return g,e,v,diffs
cached=[];c3=R3/CP
pattern=hashlib.sha256()
for _ in range(16):pattern.update(b'\x5a'*(1<<20))
for arm in pins:
 for rep in range(3):
  n=f'ax447-{arm}-cached-{300+rep}';t=c3/'trials'/n;rec=j(t/'receipt.json');a=cp(R3,rec['artifact_directories'][0]);tag=n
  check(tag+':success_pin',rec['status']==0 and rec['binary']['sha256']==pins[arm])
  for k,p in [('container_harness_sha256','run-measured-perf-in-container.sh'),('durability_helper_sha256','verify_remote_durability.py'),('harness_sha256','run-isolated-redis-perf.sh'),('compose_sha256','compose.json')]:check(tag+':'+k,rec[k]==sha(c3/p))
  service=j(t/'services/error-gate.json');check(tag+':server_gate',service['passed'] and service['error_count']==0 and service['log_sha256']==sha(t/'services/rustfs.log'))
  g,e,v,diff=gate(tag,a/'remote-durability/fio-bigwrite',arm,True)
  check(tag+':known_pattern_cold',len(v)==1 and v[0]['bytes']==16<<20 and v[0]['sha256']==pattern.hexdigest())
  hz=int((a/'client-cpu-clock-ticks.txt').read_text());cpu={}
  for l in (a/'client-cpu-raw.tsv').read_text().splitlines():
   tool,phase,ts,proc=l.split('\t',3);x=proc[proc.rfind(')')+2:].split();cpu[phase]={'pid':int(proc.split('(',1)[0]),'start':int(x[19]),'ticks':int(x[11])+int(x[12]),'ns':int(ts)}
  start=cpu['tool_start'];end=cpu['post-write_extended_drained'];ret=cpu['tool_return'];ph={q[1]:int(q[2]) for l in (a/'phase-boundaries.tsv').read_text().splitlines() if(q:=l.split('\t'))}
  check(tag+':cpu_identity',all(x['pid']==g['old_mount']['pid'] and x['start']==int(g['old_mount']['starttime']) for x in cpu.values()))
  check(tag+':cpu_bounds',start['ticks']<=ret['ticks']<=end['ticks'] and start['ns']<=ph['tool_start']<=ph['tool_return']<=ret['ns']<=ph['post-write_extended_drained']<=end['ns']<g['started_unix_ns'])
  st=native(a/'diagnostics/stats-fio-bigwrite-post-write-drained-after.txt')
  check(tag+':upload_and_drain',st['brewfs_s3_put_bytes_total']>=16<<20 and st['brewfs_writeback_dirty_bytes']==st['brewfs_buffer_dirty_bytes']==st['brewfs_writeback_recent_pending_upload_bytes']==0)
  fio=j(a/'results/fio-bigwrite.json')['jobs'];argv=j(a/'tools/fio-bigwrite.effective-argv.json')
  check(tag+':fio_shape',len(fio)==1 and fio[0]['error']==0 and fio[0]['write']['total_ios']==4096 and fio[0]['write']['io_bytes']==16<<20 and fio[0]['read']['io_bytes']==16<<20 and sum(x.startswith('--name=') for x in argv)==1)
  cached.append({'arm':arm,'rep':rep,'artifact':str(a),'cpu_s':(end['ticks']-start['ticks'])/hz,'active_ns':ph['tool_return']-ph['tool_start'],'total_ns':ph['post-write_extended_drained']-ph['tool_start'],'remote_validation_ns':g['total_validation_ns'],'put_bytes':st['brewfs_s3_put_bytes_total']})
cached_pairs=[]
for rep in range(3):
 b=next(x for x in cached if x['arm']=='baseline' and x['rep']==rep);n=next(x for x in cached if x['arm']=='candidate' and x['rep']==rep)
 cached_pairs.append({'pair':rep,**{k:{'baseline':b[k],'candidate':n[k],'ratio':n[k]/b[k]} for k in ['cpu_s','active_ns','total_ns']}})
for got,old in zip(cached_pairs,j(c3/'matrices/cached-ec4qualified/paired-summary.json')['pairs']):
 for k in ['cpu_s','active_ns','total_ns']:
  for arm in ['baseline','candidate','ratio']:check(f'cached-summary:{got["pair"]}:{k}:{arm}',math.isclose(got[k][arm],old[k][arm],rel_tol=1e-12))
# Actual cached FUSE path is separately logged with the same final arms.
for arm in pins:
 rec=j(c3/'trials'/f'ax447-{arm}-cached-smoke-300/receipt.json');a=cp(R3,rec['artifact_directories'][0]);groups={}
 for l in (a/'brewfs_fuse_ops.log').read_text().splitlines():
  if '[write] REQ' not in l:continue
  fields={k:int(v) for k,v in re.findall(r'\b(inode|offset|data_len|write_flags)=(\d+)',l)};fields['unique']=int(re.search(r'unique:\s*(\d+)',l).group(1));groups.setdefault(fields['inode'],[]).append(fields)
 proof=[ino for ino,allw in groups.items() if len(w:=[x for x in allw if x['write_flags']&1])==4096 and sorted(x['offset'] for x in w)==list(range(0,16<<20,4096)) and all(x['data_len']==4096 and x['unique']>0 for x in w) and len({x['unique'] for x in w})==4096]
 check(arm+':cached_actual_shape',len(proof)==1);gate(arm+':cached_smoke_durability',a/'remote-durability/fio-bigwrite',arm,True)
# Longer row actual cases retain prior short results, not overwrite them.
w=R4/CP/'workspace/ax447-rowlong-ec4long01';s=j(w/'summary.json');row=[];H='brewfs_workspace_row_cache_hits_total';M='brewfs_workspace_row_cache_misses_total';CAP='brewfs_workspace_row_cache_capacity_bytes';RET='brewfs_workspace_row_cache_retained_bytes'
check('longrow_complete',s['passed'] and len(s['row_cases'])==len(s['mount_exits'])==24)
for p,h in s['harness_sha256'].items():
 source=cp(R4,p)
 if not source.exists():source=cp(B/'runtime-review-02',p)
 check('longrow:harness:'+p,sha(source)==h,str(source))
for cl in s['mount_exits']:check('longrow:cleanup:'+cl['label'],cl['exit_code']==0 and not cl['forced'] and cl['mount_absent'] and cl['unmount_exit_code']==0)
for ref in s['row_cases']:
 p=cp(R4,ref['path']);v=j(p);arm=ref['arm'];tag=p.parent.name;ops=v['workload']['threads']*65536
 check(tag+':fixed_ops',v['workload']['keys']==128 and v['workload']['ops_per_thread']==65536 and v['successful_calls']==ops and v['passed'] and v['workload']['every_value_verified'])
 check(tag+':input',hashlib.sha256(canon(j(p.parent/'input.json'))).hexdigest()==v['canonical_input_sha256']=='eee075572b0375968904204eef501169a6463e5f3011f297a02da9cdaca1106b')
 env=j(p.parent/'mount-receipt.json')['environment'];check(tag+':same_row_config',env['BREWFS_WORKSPACE_ROW_CACHE_BYTES']=='67108864' and env['BREWFS_WORKSPACE_ROW_CACHE_TTL_MS']=='600000')
 for name in ['warm_before','before','after']:
  sample=v[name];raw={q[0]:q[1] for l in sample['raw'].splitlines() if l and not l.startswith('#') and len(q:=l.rsplit(None,1))==2}
  for k in [H,M]:check(tag+':'+name+':'+k,int(raw[k])==sample['row'][k])
  if arm=='candidate':check(tag+':'+name+':bytes',int(raw[CAP])==sample['row'][CAP]==67108864 and int(raw[RET])==sample['row'][RET]<=67108864)
  else:check(tag+':'+name+':unsupported',sample['row'][CAP] is None and sample['row'][RET] is None and CAP not in raw and RET not in raw)
 for key in [H,M]:
  check(tag+':delta:'+key,v['after']['row'][key]-v['before']['row'][key]==v['delta'][key]);check(tag+':warm_delta:'+key,v['before']['row'][key]-v['warm_before']['row'][key]==v['warm_delta'][key])
 check(tag+':stable_hits',v['delta'][M]==v['warm_delta'][M]==0 and v['warm_delta'][H]>=128 and v['delta'][H]>=ops)
 row.append({'arm':arm,'threads':v['workload']['threads'],'rep':ref['repetition'],'wall_ns':v['wall_ns'],'successful_calls':ops,'path':str(p)})
row_cases=[]
for threads in [1,8]:
 pairs=[]
 for rep in range(6):
  b=next(x for x in row if x['arm']=='baseline' and x['threads']==threads and x['rep']==rep);n=next(x for x in row if x['arm']=='candidate' and x['threads']==threads and x['rep']==rep)
  pairs.append({'rep':rep,'baseline_ns':b['wall_ns'],'candidate_ns':n['wall_ns'],'ratio':n['wall_ns']/b['wall_ns']})
 bm=statistics.median(x['baseline_ns'] for x in pairs);nm=statistics.median(x['candidate_ns'] for x in pairs)
 old=next(x for x in j(w/'paired-summary.json')['cases'] if x['threads']==threads)
 check('longrow:summary:'+str(threads),bm==old['baseline_median_wall_ns'] and nm==old['candidate_median_wall_ns'])
 row_cases.append({'threads':threads,'pairs':pairs,'baseline_median_ns':bm,'candidate_median_ns':nm,'median_change_pct':100*(nm/bm-1),'max_paired_ratio':max(x['ratio'] for x in pairs),'all_pairs_faster':all(x['ratio']<1 for x in pairs)})
# Formal first failure and exact same-shape diagnostic pair.
failures=[];segment_data={}
for r,arm,trial in [(R3,'baseline',300),(R4,'candidate',402),(R4,'baseline',403)]:
 t=r/CP/'trials'/f'ax447-{arm}-regression-{trial}';rec=j(t/'receipt.json');a=cp(r,rec['artifact_directories'][0]);p=a/'remote-durability/fio-randwrite-direct0';g,e,v,diffs=gate(f'{arm}{trial}',p,arm,False)
 check(f'{arm}{trial}:two_files_differ',len(diffs)==2 and {x['path'] for x in diffs}==({'brewfs-randwrite.0.0','brewfs-randwrite.1.0'} if trial==300 else {'brewfs-randwrite.1.0','brewfs-randwrite.3.0'}) and all(x['bytes']==512<<20 for x in e))
 check(f'{arm}{trial}:invalid_marker',(a/'remote-durability.invalid').exists() and rec['status']!=0)
 argv=j(a/'tools/fio-randwrite-direct0.effective-argv.json');opts={x[2:].split('=',1)[0]:x.split('=',1)[1] for x in argv if x.startswith('--') and '=' in x}
 check(f'{arm}{trial}:same_workload',all(opts.get(k)==v for k,v in {'rw':'randwrite','bs':'4m','size':'512m','numjobs':'4','runtime':'20','direct':'0','iodepth':'1','randseed':'20261031','randrepeat':'1'}.items()))
 service=j(t/'services/error-gate.json');check(f'{arm}{trial}:server_no_errors',service['passed'] and service['error_count']==0 and sha(t/'services/rustfs.log')==service['log_sha256'])
 st=native(a/'diagnostics/stats-fio-randwrite-direct0-post-write-drained-after.txt');check(f'{arm}{trial}:drain_zero',all(st[k]==0 for k in ['brewfs_writeback_dirty_bytes','brewfs_buffer_dirty_bytes','brewfs_writeback_recent_pending_upload_bytes']))
 info={'arm':arm,'trial':trial,'artifact':str(a),'different_files':diffs,'full_file_count':len(e),'bytes':g['logical_bytes'],'gate_passed':False,'service_errors':0,'drain_zero':True}
 if trial!=300:
  em={x['path']:x for x in e};vm={x['path']:x for x in v};ds={}
  for name,x in em.items():
   y=vm[name];check(f'{arm}:segment_coverage:'+name,len(x['read_segments'])==len(y['read_segments'])==512 and [a['offset'] for a in x['read_segments']]==list(range(0,512<<20,1<<20)))
   for aa,bb in zip(x['read_segments'],y['read_segments']):
    if aa['sha256']!=bb['sha256']:ds[(name,aa['offset'])]={'warm':aa,'cold':bb,'inode':x['diagnostic_inode']}
  check(arm+':segment_count',len(ds)==(12 if arm=='candidate' else 8));segment_data[arm]=ds;info['different_segments_1MiB']=len(ds)
 else:
  for stem in ['fio-seqwrite-direct0','fio-seqwrite-direct1']:gate('formal_baseline:'+stem,a/'remote-durability'/stem,arm,True)
 failures.append(info)
sealed=[]
for armrec in j(RS/CP/'retained-sealed-content-comparison.json'):
 arm=armrec['arm'];ds=segment_data[arm];covered=set()
 for obj in armrec['retained_sealed']:
  p=cp(RS,obj['path']);data=p.read_bytes();parts=p.stem.split('_');ino=int(parts[0]);chunk=int(parts[1]);offset=int(parts[4]);length=int(parts[5]);absolute=(chunk%1_000_000_000)*(64<<20)+offset
  check(arm+':sealed_size:'+p.name,len(data)==length==4<<20 and offset==60<<20 and chunk//1_000_000_000==ino)
  for seg in obj['segments']:
   raw=data[seg['offset']:seg['offset']+(1<<20)];h=hashlib.sha256(raw).hexdigest();key=tuple(seg['matches_warm']);actual=ds[key]
   check(arm+':sealed_segment:'+str(key),h==seg['sha256']==actual['warm']['sha256'] and h!=actual['cold']['sha256'] and key[1]==absolute+seg['offset'] and actual['inode']==ino and seg['matches_cold'] is None and raw[:64].hex()==actual['warm']['prefix64_hex']);covered.add(key)
  sealed.append({'arm':arm,'path':str(p),'bytes':len(data),'inode':ino,'absolute_offset':absolute,'chunk_offset':offset,'matched_warm_segments':len(obj['segments'])})
 check(arm+':sealed_exact_diff_cover',covered==set(ds))
tr=j(RT/'fio-scramble-timestamp-comparison.json');timestamps=[]
for rec in tr['records']:
 actual=segment_data[rec['arm']][(rec['path'],rec['offset'])];wa=bytes.fromhex(actual['warm']['prefix64_hex']);co=bytes.fromhex(actual['cold']['prefix64_hex']);ws,wn=struct.unpack_from('<QQ',wa,48);cs,cn=struct.unpack_from('<QQ',co,48)
 check('timestamp:'+str((rec['arm'],rec['path'],rec['offset'])),[ws,wn,cs,cn]==[rec['warm_sec'],rec['warm_nsec'],rec['cold_sec'],rec['cold_nsec']] and wn<10**9 and cn<10**9 and (cs,cn)>(ws,wn));timestamps.append({'arm':rec['arm'],'file':rec['path'],'offset':rec['offset'],'warm_pair':[ws,wn],'cold_pair':[cs,cn]})
report={'created_at':datetime.now(timezone.utc).isoformat(),'scope':'Read-only independent rbm archive audit; no cloud access or workloads','candidate':'7b8445042fda8e6cd5ec9a3bedc0eb606a45d8b3','archives':archives,'checks':{'total':len(checks),'passed':sum(x['passed'] for x in checks),'failed':sum(not x['passed'] for x in checks)},'qualified_cached':{'rows':cached,'pairs':cached_pairs,'conclusion':'All3paired BrewFS process CPU totals lower with known-pattern full cold remount gates passed; no wall-time improvement, totals fractionally higher; excludes fio/server CPU and separately reported durability-validation cost.'},'long_row':{'rows':row,'cases':row_cases,'conclusion':'24arms correct, warm/timed misses0; longer fixed65536ops/thread gives lower medians and no paired slowdown>5%;1thread not all pairs faster. Prior short+11.66/+7.46% medians remain evidence, not overwritten.'},'failed_guards':failures,'sealed':{'objects':sealed,'exact_covers_all_different_1MiB_segments':True,'candidate_difference_bytes':12<<20,'baseline_difference_bytes':8<<20,'conclusion':'Retained original sealed bytes exactly reproduce all warm differing segments; cold differs. This locates warm overlay contribution, not which generation is correct.'},'timestamp_bytes':{'records':timestamps,'conclusion':'Independently decoded little-endian64-bit pairs at prefix offsets48/56 match owner timestamp report; cold pairs later for20/20segments. Semantics depend on reported fio3.39 scramble contract; generation ordering supports stale-overlay hypothesis, not independent final-data oracle or proven Rust race.'},'decision':{'approve_one_integrated_pr':False,'status':'hold','reason':'Both baseline and candidate actual512MiB*4 randwrite/direct0 fail full warm/cold SHA consistency despite clean normal remount, fresh local cache, zero dirty counters and no service errors. Remaining standard guards incomplete. Valid target benefits do not waive correctness.'},'separation':'Earlier single-path errors/timings remain invalid; Packed7093 campaign separate. No source modified.'}
(O/'runtime-final-hold-checks.json').write_text(json.dumps(checks,indent=2)+'\n');(O/'runtime-final-hold.json').write_text(json.dumps(report,indent=2)+'\n')
lines=['Issue445-447 final independent evidence review: HOLD ONE integrated PR at7b844504.','',f"Independently rehashed883+982+6 archive members and4 timestamp files; factual checks{report['checks']['passed']}/{len(checks)}.",'','Qualified EC4 cached writes now have complete16MiB known-pattern cold readback, same binary/args with new PID and empty cache, clean service gates and CPU measurements through strict drain. Three paired BrewFS CPU totals .54/.51/.50 -> .43/.43/.41s (20.37%,15.69%,18% lower). This is process CPU, not end-to-end wall throughput: fully drained wall is fractionally higher in all3pairs. Validation/remount costs are outside measured boundaries and separately recorded.','', 'Long hot-row guards:24arms correct, zero warm/timed misses,64MiB byte budget and600sTTL fixed.65536ops/thread:1thread median3.699990->3.643019s;8threads13.315117->12.843490s. No pair slower by more than5%;1thread has one marginal slower pair. Keep prior short-run regressions, do not claim every shape/round faster.','', 'Correctness remains blocking. Baseline formal third scene randwrite/direct0 fails warm/cold SHA; exact same-shape candidate402 and baseline403 also fail. Each has four512MiB files at20s/4MiB/4jobs/iodepth1/same seed. The original formal failure differs in files0 and1; both exact isolated diagnostics differ in files1 and3 after ordinary unmount with old process gone and fresh empty local cache, although service errors and dirty counters are zero.','', 'The five archived4MiB sealed files exactly cover every differing1MiB segment:12MiB candidate,8MiB baseline; all occur at60MiB offsets inside64MiB chunks. Their actual bytes match warm hashes/prefixes, not cold. This establishes overlay involvement, not warm correctness. Prefix48/56 decoded pairs match reported fio timestamps and cold is later in all20differences; this supports an older overlay hypothesis but does not prove the final application oracle or exact cleanup race.','', 'Do not publish/merge the integrated candidate until the inconsistency is resolved and required standard guards pass. Valid445/446/cached target evidence remains scoped and preserved; Packed7093 acceptance is separate.']
(O/'runtime-final-hold.md').write_text('\n'.join(lines)+'\n');receipt={p.name:sha(p) for p in [O/'runtime-final-hold.json',O/'runtime-final-hold.md',O/'runtime-final-hold-checks.json',O/'audit-runtime-final-hold.py']};(O/'runtime-final-hold-sha256.json').write_text(json.dumps(receipt,indent=2)+'\n')
print(json.dumps({'checks':report['checks'],'cached_pairs':cached_pairs,'row_cases':[{k:v for k,v in x.items() if k!='pairs'} for x in row_cases],'sha256':receipt},indent=2))
if report['checks']['failed']:
 print(json.dumps([x for x in checks if not x['passed']],indent=2));raise SystemExit(1)
