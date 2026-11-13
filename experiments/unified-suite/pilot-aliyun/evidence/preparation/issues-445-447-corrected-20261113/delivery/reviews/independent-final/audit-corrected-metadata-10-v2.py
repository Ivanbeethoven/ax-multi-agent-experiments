from pathlib import Path
import hashlib,json,statistics
R=Path('/home/dev/ax-perf-issues-validation/corrected-runtime-review-10')
O=Path('/home/dev/ax-perf-issues-validation/independent-review-7b844')
old=(O/'audit-runtime-review-02.py').read_text().splitlines()
code='\n'.join(old[:116])
code=code.replace("R=Path('/home/dev/ax-perf-issues-validation/runtime-review-02');C=R/'home/dev/ax-perf-issues-validation/compose';W=C/'workspace/ax447-workspace-ec4v2'", "R=Path('/home/dev/ax-perf-issues-validation/corrected-runtime-review-10');C=R/'home/dev/ax-perf-issues-corrected-validation/compose';W=C/'workspace/ax447-corrected-workspace-corrected51201'")
code=code.replace("a=j(R/'archive-manifest.json')", "a=j(R/'manifest.json')")
n={};exec(compile(code,'reused-workspace-factual-audit','exec'),n)
checks=n['checks'];check=n['check'];s=n['s'];comparisons=n['comparisons']
workspace={'source':s['source'],'binary_pins':s['binary_pins'],'counts':{'mounts':len(s['mount_exits']),'fill':len(s['waves']),'row_hot':len(s['row_cases']),'memory':2},'comparisons':comparisons,'memory':n['memory']}
PINS={'baseline':('4f982f45c153a0c31e3e9cd8ff642ec7659c98fd','0b0a2743aa968f497782d823cb068cd421eb840f82d5739a363d3bc3623c8679'),
      'candidate':('c8f5f16aefe811540782b952481982179a1bfd6f','905c4e4ca35162ae6a1ab2de03f4fa18194ef64d9afcbe20cff9bbefd570ce90')}
for arm,(commit,sha) in PINS.items():
    check('workspace exact source/binary '+arm,s['source'][arm]['commit']==s['binary_pins'][arm]['commit']==commit and s['binary_pins'][arm]['sha256']==sha)
check('workspace no owned containers',(n['W']/'remaining-owned-containers.stdout').read_text().strip()=='')

long=n['C']/'workspace/ax447-corrected-rowlong-correctedlong51201'
n['W']=long;n['s']=json.loads((long/'summary.json').read_text());ls=n['s']
check('long counts',ls['passed'] and len(ls['mount_exits'])==24 and len(ls['row_cases'])==24)
for arm,(commit,sha) in PINS.items():
    check('long exact source/binary '+arm,ls['source'][arm]['commit']==ls['binary_pins'][arm]['commit']==commit and ls['binary_pins'][arm]['sha256']==sha)
check('long same input',ls['input_sha256']==s['input_sha256'])
check('long original default pools same',ls['packed_budget_limits_bytes']==s['packed_budget_limits_bytes'])
check('long fixed work',ls['fixed_plan']=={'keys':128,'threads':[1,8],'operations_per_thread':65536,'repetitions':6,'value_bytes':8,'row_ttl_ms':600000,'row_budget_bytes':67108864,'order':'AB for even repetitions, BA for odd'})
# Reuse the exact identity/configuration/mount/actual-native/value/hash checks,
# adjusting only the archived project root and the already-registered summary.
exec(compile('\n'.join(old[20:43]),'long-identity-mount-audit','exec'),n)
exec(compile('\n'.join(old[78:96]),'long-row-factual-audit','exec'),n)
longrows=n['row'];longpairs=[]
for threads in [1,8]:
    pairs=[]
    for repetition in range(6):
        rows={a:next(x for x in longrows if x['arm']==a and x['threads']==threads and x['repetition']==repetition) for a in ['baseline','candidate']}
        b,c=rows['baseline'],rows['candidate']
        check(f'long k128/t{threads}/r{repetition} fixed operation count',b['keys']==c['keys']==128 and b['ops_per_thread']==c['ops_per_thread']==65536)
        pair={'repetition':repetition,'baseline_ns':b['wall_ns'],'candidate_ns':c['wall_ns'],'ratio':c['wall_ns']/b['wall_ns'], 'native_misses_baseline':b['native_misses'],'native_misses_candidate':c['native_misses']}
        pairs.append(pair)
    bm=statistics.median(x['baseline_ns'] for x in pairs);cm=statistics.median(x['candidate_ns'] for x in pairs)
    longpairs.append({'keys':128,'threads':threads,'pairs':pairs,'baseline_median_ns':bm,'candidate_median_ns':cm,'median_percent_change':100*(cm/bm-1)})
check('long no owned containers',(long/'remaining-owned-containers.stdout').read_text().strip()=='')
check('both full value inputs resident',all(x['native_misses']==0 for x in longrows))
failed=[x for x in checks if not x['passed']]
report={'schema':'independent-corrected-metadata10-review-v2','reviewer_correction':'Review01 incorrectly retained the earlier short-row list when evaluating long rows; the fixed-operation assertions rejected that reviewer list-selection error. Original review01 remains preserved. This version resets the list before reading all24 long results. No raw/runtime/harness changed.','archive':{'sha256':'47f7fe3d43efdcf29e2544c71dcf4d53207db5b8bcd13fab0f183f7c2ed7425e','payload_files':2233,'total_members':2234,'manifest_sha256':hashlib.sha256((R/'manifest.json').read_bytes()).hexdigest()},
  'checks':{'total':len(checks),'failed':failed},'workspace':workspace,
  'long_row':{'mounts':24,'cases':24,'fixed_plan':ls['fixed_plan'],'pairs':longpairs},
  'decision':'CORRECTED_445_CONCURRENCY_AND_446_RETAINED_BYTES_TARGETS_VERIFIED_HOT_GUARD_PASSES' if not failed else 'FACTUAL_CHECK_FAILURE',
  'integrated_pr_qualified':False,
  'interpretation':[
    'Corrected 4f/c8 binaries were actually rerun. Publication e2 remains covered only by the separately proven test-only child equivalence; no old 7b timing relabelled.',
    '445 benefit is six matched 8-group/8-thread cold leaf lookup/open+pread waves with a controlled 50ms per-GroupMeta delay. Exact object/range/206/length and SHA checks plus one request per group and peak1→8 establish concurrency. No undelayed general throughput claim.',
    '446 baseline implements an entry cap only. Passing the same byte-budget environment does not make baseline enforce a byte budget. Candidate large-value samples prove retained-owner bytes<=512KiB; these are not process RSS or a continuous memory peak.',
    'Row hot comparisons separately use the same resident small-value dataset, request counts, TTL and configured64MiB on both arms. Every response is verified and warm/timed native misses are0. Long128-key results replace no raw short record; both remain separately reported.',
    'No broad whole-lifecycle or all-default-workload improvement claimed. Existing601/603 historical-state CPU regression observations remain; fixed-work foreground guard610–612 still pending independent review.',
  ]}
f=O/'corrected-metadata10-review-02.json';assert not f.exists();f.write_text(json.dumps(report,indent=2,sort_keys=True)+'\n')
(O/'corrected-metadata10-checks-02.json').write_text(json.dumps(checks,indent=2)+'\n')
print(json.dumps({'path':str(f),'sha256':hashlib.sha256(f.read_bytes()).hexdigest(),'check_count':len(checks),'failed_count':len(failed),'failed':failed[:12],'long_pairs':longpairs,'comparisons':[{k:v for k,v in x.items() if k!='pairs'} for x in comparisons],'memory':workspace['memory']},indent=2))
if failed:raise SystemExit(1)
