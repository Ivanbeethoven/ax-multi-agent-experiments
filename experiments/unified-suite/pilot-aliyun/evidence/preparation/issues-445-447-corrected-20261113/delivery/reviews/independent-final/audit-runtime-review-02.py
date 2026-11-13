from pathlib import Path
import json,hashlib,re,statistics,copy
from collections import defaultdict
from datetime import datetime,timezone
R=Path('/home/dev/ax-perf-issues-validation/runtime-review-02');C=R/'home/dev/ax-perf-issues-validation/compose';W=C/'workspace/ax447-workspace-ec4v2';O=Path('/home/dev/ax-perf-issues-validation/independent-review-7b844')
def j(p):return json.loads(p.read_text())
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def canonical(x):return json.dumps(x,sort_keys=True,separators=(',',':')).encode()
def at(p):return R/p.lstrip('/')
checks=[]
def check(n,b,d=None):
 x={'name':n,'passed':bool(b)}
 if d is not None:x['detail']=d
 checks.append(x)
a=j(R/'archive-manifest.json')
for m in a['files']:
 p=R/m['path'];check('hash:'+m['path'],p.stat().st_size==m['bytes'] and sha(p)==m['sha256'])
s=j(W/'summary.json');expected=j(W/'expected-files.json');targets=j(W/'inventory.json')['targets'];by_id={(t['object_key_sha256'],t['range']):t for t in targets};by_path={('/ax447-workspace-fixture/'+t['key'],t['range']):t for t in targets}
check('completed_counts',s['passed'] and len(s['waves'])==56 and len(s['row_cases'])==56 and len(s['mount_exits'])==114)
for path,h in expected.items():check('input:'+path,sha(W/'source'/path)==h)
check('input_manifest_sha',hashlib.sha256(json.dumps(expected,sort_keys=True).encode()).hexdigest()==s['input_sha256'])
check('manifest_sha',sha(W/'manifest.bin')==s['manifest_sha256'])
for p,h in s['harness_sha256'].items():check('harness:'+p,sha(at(p))==h)
for arm in ['baseline','candidate']:
 check('source:'+arm,(W/f'source-{arm}.stdout').read_text().strip()==s['source'][arm]['commit'] and (W/f'tree-{arm}.stdout').read_text().strip()==s['source'][arm]['tree'] and (W/f'clean-{arm}.stdout').read_text().strip()=='')
for k,v in s['images'].items():check('image:'+k,(W/f'preflight-image-{k}.stdout').read_text().strip()==v['id'])
check('client_budget',s['client_unit_limits']['cpu_max']=='300000 100000' and int(s['client_unit_limits']['memory_max_bytes'])==16<<30)
check('ec4_actual_topology',s['rustfs_runtime_pin']['topology_passed'] and s['rustfs_runtime_pin']['disk_check_bypass']=='true' and len(s['rustfs_runtime_pin']['logical_data_paths'])==4)
normalized=None;namespaces=set();mount_configs=[]
for cl in s['mount_exits']:
 label=cl['label'];p=W/label;conf=j(p/'mount-config.json');rec=j(p/'mount-receipt.json');env=rec['environment'];actual=j(p/'cleanup.json')
 check(label+':clean_unmount',actual==cl and cl['exit_code']==0 and not cl['forced'] and cl['unmount_exit_code']==0 and cl['mount_absent'] and cl['unmount_error'] is None)
 check(label+':fresh_workspace',conf['workspace_namespace'] not in namespaces and conf['workspace']==rec['workspace_id']);namespaces.add(conf['workspace_namespace'])
 arm='candidate' if 'candidate' in label else 'baseline'
 check(label+':binary_path',rec['argv'][0]==s['source'][arm]['worktree']+'/target/docker/brewfs')
 check(label+':row_budget',int(env['BREWFS_WORKSPACE_ROW_CACHE_BYTES'])==(524288 if label.startswith('row-memory') else 67108864) and env['BREWFS_WORKSPACE_ROW_CACHE_TTL_MS']=='600000')
 check(label+':packed_budgets',all(env['BREWFS_PACKED_V3_'+k.upper()+'_BUDGET_BYTES']==str(v) for k,v in s['packed_budget_limits_bytes'].items()))
 z=copy.deepcopy(conf)
 for k in ['workspace','workspace_namespace']:z.pop(k)
 z['cache'].pop('cache_root');z['data']['s3'].pop('endpoint')
 if normalized is None:normalized=z
 check(label+':same_config_except_owned_paths_endpoint',z==normalized)
 mount_configs.append({'label':label,'row_bytes':int(env['BREWFS_WORKSPACE_ROW_CACHE_BYTES']),'endpoint':conf['data']['s3']['endpoint']})
fill=[]
for wave in s['waves']:
 p=at(wave['path']);v=j(p);tag=p.parent.name
 check(tag+':embedded_result',all(wave[k]==v[k] for k in v))
 events=v['proxy']['events'];audit=v['proxy']['request_audit'];keys=v['distinct_groups'];threads=v['threads'];ids=[(e['object_key_sha256'],e['range']) for e in events]
 check(tag+':unique_targets',len(ids)==keys and len(set(ids))==keys and all(x in by_id and by_id[x]['frame_count']==1 for x in ids))
 for i,e in enumerate(events):
  t=by_id[(e['object_key_sha256'],e['range'])]
  check(tag+f':event{i}',e['status']==206 and e['bytes']==t['length'] and e['entered_ns']<=e['forward_started_ns']<=e['finished_ns'] and e['forward_started_ns']-e['entered_ns']>=50_000_000)
 edges=sorted([(e['entered_ns'],1) for e in events]+[(e['finished_ns'],-1) for e in events]);active=peak=0
 for _,delta in edges:active+=delta;peak=max(peak,active)
 check(tag+':recompute_peak',peak==v['proxy']['max_target_active'])
 sets={False:set(),True:set()};target_count=0
 for n,q in enumerate(audit):
  ident=(q['path'],q['range']);is_target=q['method']=='GET' and ident in by_path
  check(tag+f':audit{n}',q['target_match']==is_target and q['status']<400)
  if is_target:
   sets[q['wave_enabled']].add(ident)
   if q['wave_enabled']:target_count+=1
 check(tag+':no_leaf_warmed_in_setup',not (sets[False]&sets[True]) and all(by_path[x]['frame_count']==0 for x in sets[False]))
 check(tag+':audit_target_count',target_count==keys and len(sets[True])==keys and len(sets[False])==v['setup_target_groups'])
 check(tag+':payload_correct',len(v['reads'])==8 and all(x['bytes']==4096 and x['sha256']==expected[x['path']] and x['passed'] for x in v['reads']))
 check(tag+':path_shape',len({x['path'] for x in v['reads']})==keys)
 check(tag+':zero_errors',v['proxy']['errors']==0 and v['passed'] and v['reasons']==[] and v['proxy']['injected_delay_seconds']==.05)
 check(tag+':wall_scope',v['read_wall_ns']==v['leaf_open_pread_wall_ns'] and max(x['finished_ns'] for x in v['reads'])-min(x['started_ns'] for x in v['reads'])<=v['read_wall_ns'])
 fill.append({'arm':wave['arm'],'stage':wave['stage'],'repetition':wave['repetition'],'keys':keys,'threads':threads,'wall_ns':v['read_wall_ns'],'peak':peak,'target_fetches':len(events),'path':str(p)})
H='brewfs_workspace_row_cache_hits_total';M='brewfs_workspace_row_cache_misses_total';B='brewfs_workspace_row_cache_retained_bytes';CAP='brewfs_workspace_row_cache_capacity_bytes'
def sample(tag,x,arm,capacity):
 raw={}
 for line in x['raw'].splitlines():
  if line and not line.startswith('#') and len(v:=line.rsplit(None,1))==2:raw[v[0]]=v[1]
 for k in [H,M]:check(tag+':native:'+k,k in raw and int(raw[k])==x['row'][k])
 if arm=='candidate':
  check(tag+':retained_budget',not x['unsupported_row_fields'] and int(raw[CAP])==x['row'][CAP]==capacity and 0<=int(raw[B])==x['row'][B]<=capacity)
 else:check(tag+':baseline_unsupported_preserved',set(x['unsupported_row_fields'])=={B,CAP} and x['row'][B] is None and x['row'][CAP] is None and B not in raw and CAP not in raw)
row=[];row_inputs=set();workload_map={}
for ref in s['row_cases']:
 p=at(ref['path']);v=j(p);tag=p.parent.name;arm=ref['arm'];work=v['workload'];ops=work['ops_per_thread']*work['threads'];inp=j(p.parent/'input.json')
 row_inputs.add(v['canonical_input_sha256']);check(tag+':input',hashlib.sha256(canonical(inp)).hexdigest()==v['canonical_input_sha256'])
 for x in inp['files']:
  value=(b'ax'+int(x['index']).to_bytes(6,'little'))
  check(tag+':value:'+str(x['index']),hashlib.sha256(value).hexdigest()==x['value_sha256'] and inp['value_bytes']==8)
 for name in ['warm_before','before','after']:sample(tag+':'+name,v[name],arm,67108864)
 for k in [H,M]:
  check(tag+':delta:'+k,v['after']['row'][k]-v['before']['row'][k]==v['delta'][k])
  check(tag+':warm_delta:'+k,v['before']['row'][k]-v['warm_before']['row'][k]==v['warm_delta'][k])
 check(tag+':native_hit_no_miss',v['delta'][M]==v['warm_delta'][M]==0 and v['warm_delta'][H]>=work['keys'] and v['delta'][H]>=ops)
 check(tag+':verified_ops',v['passed'] and v['warm_stable'] and v['successful_calls']==ops and sum(x['successful_calls'] for x in v['threads'])==ops and work['every_value_verified'])
 check(tag+':workload_hash',hashlib.sha256(canonical(work)).hexdigest()==v['workload_sha256'])
 shape=(work['keys'],work['threads'],work['ops_per_thread']);workload_map.setdefault(shape,set()).add(v['workload_sha256'])
 check(tag+':helper_hash',v['harness_sha256']==s['harness_sha256']['/home/dev/ax-issue-446-workspace-guard/workspace_row_guard.py'])
 row.append({'arm':arm,'stage':ref['stage'],'repetition':ref['repetition'],'keys':work['keys'],'threads':work['threads'],'ops_per_thread':work['ops_per_thread'],'wall_ns':v['wall_ns'],'native_hits':v['delta'][H],'native_misses':v['delta'][M],'path':str(p)})
check('row_shared_input',len(row_inputs)==1);check('row_shared_workloads',all(len(x)==1 for x in workload_map.values()))
memory=[]
for arm in ['baseline','candidate']:
 p=W/f'row-memory-{arm}/result.json';v=j(p);cap=524288
 for i,x in enumerate(v['samples']):sample(f'memory:{arm}:{i}',x,arm,cap)
 check('memory:'+arm+':correct_workload',v['passed'] and v['workload']=={'every_value_verified':True,'keys':32,'kind':'large-xattr-memory-smoke','passes':2,'performance_claim':False,'value_bytes':32768} and len(v['samples'])==65)
 check('memory:'+arm+':path_touched',v['delta'][H]+v['delta'][M]>=64)
 max_bytes=max((x['row'][B] for x in v['samples'] if x['row'][B] is not None),default=None)
 check('memory:'+arm+':max',max_bytes==v['byte_checks']['sampled_retained_max_bytes'])
 memory.append({'arm':arm,'path':str(p),'sample_count':65,'sampled_max_retained_bytes':max_bytes,'configured_capacity_bytes':cap,'baseline_metric_supported':arm=='candidate','delta':v['delta'],'input_sha256':v['canonical_input_sha256'],'workload_sha256':v['workload_sha256']})
check('memory:same_input',memory[0]['input_sha256']==memory[1]['input_sha256'] and memory[0]['workload_sha256']==memory[1]['workload_sha256'])
comparisons=[]
for kind,rows in [('fill',fill),('row',row)]:
 for keys in sorted({x['keys'] for x in rows}):
  for threads in [1,8]:
   pairs=[]
   for rep in range(6):
    b=next(x for x in rows if x['stage']=='compare' and x['keys']==keys and x['threads']==threads and x['repetition']==rep and x['arm']=='baseline');n=next(x for x in rows if x['stage']=='compare' and x['keys']==keys and x['threads']==threads and x['repetition']==rep and x['arm']=='candidate')
    pairs.append({'repetition':rep,'baseline_ns':b['wall_ns'],'candidate_ns':n['wall_ns'],'ratio':n['wall_ns']/b['wall_ns'],**({'baseline_peak':b['peak'],'candidate_peak':n['peak']} if kind=='fill' else {})})
   bm=statistics.median(x['baseline_ns'] for x in pairs);nm=statistics.median(x['candidate_ns'] for x in pairs)
   comparisons.append({'kind':kind,'keys':keys,'threads':threads,'pairs':pairs,'baseline_median_ns':bm,'candidate_median_ns':nm,'median_percent_change':100*(nm/bm-1),'all_pairs_faster':all(x['ratio']<1 for x in pairs)})
summary_pairs=j(W/'paired-summary.json')['cases']
for v in comparisons:
 old=next(x for x in summary_pairs if x['kind']==v['kind'] and x['keys']==v['keys'] and x['threads']==v['threads'])
 check('median:'+str((v['kind'],v['keys'],v['threads'])),old['baseline_median_wall_ns']==v['baseline_median_ns'] and old['candidate_median_wall_ns']==v['candidate_median_ns'])
report={'created_at':datetime.now(timezone.utc).isoformat(),'scope':'Independent rbm-only archived actual-workspace review; no cloud or new workload','archive':{'path':str(R),'sha256':j(R/'verified.json')['archive_sha256'],'members':len(a['files']),'bytes':sum(x['bytes'] for x in a['files']),'all_rehashed':True},'source':s['source'],'binary_pins':s['binary_pins'],'harness_sha256':s['harness_sha256'],'checks':{'total':len(checks),'passed':sum(x['passed'] for x in checks),'failed':sum(not x['passed'] for x in checks)},'correctness':{'mounts_clean':114,'fill_waves':56,'fill_reads_correct':448,'row_hot_cases':56,'row_memory_cases':2,'row_warm_and_timed_misses_zero':True},'configuration':{'common_normalized_mount':normalized,'packed_pools_configured_only':s['packed_budget_limits_bytes'],'row_hot_and_fill_capacity_bytes':67108864,'row_memory_capacity_bytes':524288,'row_ttl_ms':600000,'packed_native_stats':s['packed_native_metrics'],'client_limits':s['client_unit_limits'],'service_limits':s['service_limits'],'ec4_topology_scope':s['rustfs_runtime_pin']['scope']},'comparisons':comparisons,'memory':memory,'fill_rows':fill,'row_rows':row,'decision':{'approve_integrated_pr':False,'issue445_actual_evidence_supports':'Six paired 8-group/8-thread cold leaf lookup/open+pread waves improve consistently under same controlled50ms per-GroupMeta delay; exact fetch count and peak1to8 prove corrected concurrency. No general undelayed throughput claim.','issue446_capacity':'All65 candidate large-xattr samples retained<=524288; maximum524046. Samples are retained cache owners, not continuous peak/RSS. Baseline new metrics unsupported, not0.','issue446_latency':'128-key short runs have higher candidate medians for1/8threads; preserve as unresolved regression pending requested fixed-op long paired runs.','issue447':'Earlier single-path cached timing remains invalid; new EC4 durable cached pairs and512MiB/20s standard guards not contained in this archive.'},'scope_boundary':['Parent setup excluded; measured operation includes leaf lookup/open+pread+close.','Proxy all-phase audit confirms measured GroupMeta was not fetched during setup, target responses206/exact length, each key one fetch.','EC4 uses four logical directories on one physical disk with disk-check bypass; supports controlled A/B, not independent-disk reliability.','No Packed7093 evidence mixed.']}
(O/'runtime-review-02-checks.json').write_text(json.dumps(checks,indent=2)+'\n');(O/'runtime-review-02.json').write_text(json.dumps(report,indent=2)+'\n')
lines=['Issue #445-447 independent workspace review: actual correctness verified; integrated PR remains NOT READY.','',f"Independently rehashed {len(a['files'])} archived files and passed {report['checks']['passed']}/{len(checks)} factual checks.",'','114 mounts clean,56 fill waves/448 SHA-correct reads,56 hot xattr cases,2 memory cases. Same frozen source/binaries and explicitly equal mount budgets. All hot warm/timed row misses are0.','']
for v in comparisons:lines.append(f"- {v['kind']} keys={v['keys']} threads={v['threads']}: median {v['baseline_median_ns']/1e6:.3f} -> {v['candidate_median_ns']/1e6:.3f} ms ({v['median_percent_change']:+.3f}%), all six pairs faster={v['all_pairs_faster']}.")
lines+=['','The8-group/8-thread fill result is a real-path controlled-delay concurrency improvement, with independently recomputed peak1->8 and one fetch per exact GroupMeta. It includes cold leaf lookup/open+pread+close; parent setup is excluded. No general undelayed speed claim.','', 'Row hot/fill uses64MiB; memory pressure separately uses512KiB. Candidate maximum sampled retained bytes524046<=524288 across65 samples. Baseline capacity/retained metrics are unsupported, not0; this is not RSS or a continuous peak.','', 'The128-key short hot measurements regress at the median. Keep these records; fixed-op longer paired measurements are pending. Earlier ordinary single-path cached timings remain unqualified, and fresh EC4 durability plus formal512MiB/20s standard guards remain pending.','', 'EC4 means four logical directories on the same physical disk with an explicit check bypass, not four independent disks. Packed7093 acceptance is separate.']
(O/'runtime-review-02.md').write_text('\n'.join(lines)+'\n')
receipt={p.name:sha(p) for p in [O/'runtime-review-02.json',O/'runtime-review-02.md',O/'runtime-review-02-checks.json',O/'audit-runtime-review-02.py']};(O/'runtime-review-02-sha256.json').write_text(json.dumps(receipt,indent=2)+'\n')
print(json.dumps({'checks':report['checks'],'comparisons':[{k:v for k,v in x.items() if k!='pairs'} for x in comparisons],'sha256':receipt},indent=2))
if report['checks']['failed']:
 print(json.dumps([x for x in checks if not x['passed']],indent=2));raise SystemExit(1)
