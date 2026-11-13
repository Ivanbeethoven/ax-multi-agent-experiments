from pathlib import Path
import collections
import hashlib
import json
import re

R=Path('/home/dev/ax-perf-issues-validation')
ARCH=R/'corrected-runtime-review-09'
OUT=R/'independent-review-7b844'
DECODE=R/'private-profiles/offline-decode-605606-01'
TOOL='fio-randwrite-direct1'
PINS={'baseline':'0b0a2743aa968f497782d823cb068cd421eb840f82d5739a363d3bc3623c8679',
      'candidate':'905c4e4ca35162ae6a1ab2de03f4fa18194ef64d9afcbe20cff9bbefd570ce90'}
HEAD=re.compile(r'^(?P<comm>.*?)\s+(?P<pid>\d+)/(?P<tid>\d+)\s+\[(?P<cpu>\d+)\]\s+(?P<sec>\d+)\.(?P<ns>\d+):\s+(?P<period>\d+)\s+cpu-clock:\s*$')
checks=[];inputs={}
def ck(name,yes):
    checks.append({'name':name,'passed':bool(yes)});assert yes,name
def read(p):
    b=p.read_bytes();inputs[str(p)]=hashlib.sha256(b).hexdigest();return b.decode()
def obj(p):return json.loads(read(p))
def samples(p):
    result=[];current=None
    for line in read(p).splitlines():
        m=HEAD.match(line)
        if m:
            if current:result.append(current)
            z=m.groupdict();current={'pid':int(z['pid']),'tid':int(z['tid']),
                'ns':int(z['sec'])*10**9+int(z['ns'].ljust(9,'0')),'period':int(z['period']),
                'comm':z['comm'],'frames':[]}
        elif line.strip():
            assert current is not None,line
            current['frames'].append(line.strip())
    if current:result.append(current)
    return result
rows=[]
for arm,trial in [('baseline','605'),('candidate','606')]:
    p=next(ARCH.rglob('profile'+trial));a=p.parent;d=DECODE/arm
    dem=OUT/('profile'+trial+'-noinline-demangled-01')
    demrec=obj(dem/'receipt.json');dec=obj(d/'decode-receipt.json')
    ident=obj(p/'process-identity.json');start=obj(p/'perf-start.json');stop=obj(p/'perf-stop.json')
    quality=obj(d/'event-quality.json');events=read(d/'event-list.txt')
    ck(arm+': actual frozen executable',ident['exe_sha256']==dec['binary_sha256']==PINS[arm])
    ck(arm+': raw digest bound',dec['raw_sha256']==obj(p/'raw-identity.json')['sha256'])
    ck(arm+': decoder success no inline',dec['status']==0 and '--no-inline' in dec['command'])
    ck(arm+': all v0 labels resolved',demrec['unresolved_unique_labels']==[] and demrec['brewfs_labels_after']>0)
    ck(arm+': no lost/throttle records',quality['lost_or_throttled_lines']==[] and not any(k.startswith(('PERF_RECORD_LOST','PERF_RECORD_THROTTLE')) for k in quality['record_counts']))
    ck(arm+': event inheritance and mono', 'inherit: 1' in events and 'sample_stack_user: 8192' in events and 'clockid: 1' in events and 'sample_freq }: 49' in events)
    ck(arm+': requested stop only',stop['exit_code'] in [0,-2] and stop['requested_signal']=='SIGINT')
    ck(arm+': recorder wrote data','Captured and wrote' in read(p/'perf-record.stderr'))
    ck(arm+': stable process incarnation',ident['start']['starttime_ticks']==start['pid_stat']['starttime_ticks']==stop['pid_stat']['starttime_ticks'])
    clocks=[json.loads(x) for x in read(p/'clock-markers.jsonl').splitlines()]
    ck(arm+': three ordered boundary clock anchors',[x['phase'] for x in clocks]==['tool_start','tool_return','post-write_extended_drained'])
    ck(arm+': namespace pid matched',all(x['pid']==int(ident['namespace_pids'][-1]) for x in clocks))
    observations=[]
    for line in read(a/'client-cpu-raw.tsv').splitlines():
        tool,phase,observed_ns,raw=line.split('\t',3)
        if tool!=TOOL:continue
        fields=raw[raw.rfind(')')+2:].split()
        observations.append({'phase':phase,'observed_ns':int(observed_ns),'pid':int(raw.split(' ',1)[0]),
          'starttime_ticks':int(fields[19]),'ticks':int(fields[11])+int(fields[12])})
    ck(arm+': raw CPU three exact phases',[x['phase'] for x in observations]==['tool_start','tool_return','post-write_extended_drained'])
    hz=int(read(a/'client-cpu-clock-ticks.txt'))
    cpu={'pid':observations[0]['pid'],'starttime_ticks':observations[0]['starttime_ticks'],'observations':observations,
         'tool_cpu':{'total_s':(observations[1]['ticks']-observations[0]['ticks'])/hz},
         'post_tool_drain_cpu':{'total_s':(observations[2]['ticks']-observations[1]['ticks'])/hz}}
    ck(arm+': namespace and starttime match CPU',cpu['pid']==int(ident['namespace_pids'][-1]) and cpu['starttime_ticks']==ident['start']['starttime_ticks'])
    offsets=[(x['realtime_before_ns']+x['realtime_after_ns'])//2-x['monotonic_ns'] for x in clocks]
    widths=[x['realtime_after_ns']-x['realtime_before_ns'] for x in clocks]
    bounds=[obs['observed_ns']-offset for obs,offset in zip(cpu['observations'],offsets)]
    ck(arm+': monotonic CPU boundaries',bounds[0]<bounds[1]<bounds[2])
    ss=samples(dem/'script.demangled.txt')
    ck(arm+': exact sample record count',len(ss)==quality['record_counts']['PERF_RECORD_SAMPLE'])
    ck(arm+': one actual process',all(x['pid']==ident['host_pid'] for x in ss))
    ck(arm+': chronological samples',all(ss[i]['ns']<=ss[i+1]['ns'] for i in range(len(ss)-1)))
    known_tids=set(start['tasks'])|set(stop['tasks_after'])
    ck(arm+': every sampled task in process task inventories',set(s['tid'] for s in ss)<=known_tids)
    stages={k:[] for k in ['pre_cpu_start','active','drain','post_cpu_end']}
    for s in ss:
        phase='pre_cpu_start' if s['ns']<bounds[0] else 'active' if s['ns']<bounds[1] else 'drain' if s['ns']<bounds[2] else 'post_cpu_end'
        stages[phase].append(s)
    result={}
    for phase,group in stages.items():
        periods=sum(s['period'] for s in group)
        explicit=[s for s in group if any(re.search(r'brewfs(?:\[[^\]]+\])?::chunk::compact::',f) for f in s['frames'])]
        writer=[s for s in group if any(re.search(r'brewfs(?:\[[^\]]+\])?::vfs::io::writer',f) for f in s['frames'])]
        unknown=[s for s in group if any('[unknown]' in f for f in s['frames'])]
        allunknown=[s for s in group if s['frames'] and all('[unknown]' in f for f in s['frames'])]
        result[phase]={'samples':len(group),'sum_sample_period_ns':periods,
           'explicit_compaction_stack_samples':len(explicit),
           'explicit_compaction_stack_sample_fraction':len(explicit)/len(group) if group else None,
           'explicit_compaction_period_fraction':sum(s['period'] for s in explicit)/periods if periods else None,
           'explicit_writer_stack_samples':len(writer),
           'samples_with_any_unknown_frame':len(unknown),
           'samples_with_only_unknown_frames':len(allunknown),
           'leaf_unknown_samples':sum(bool(s['frames']) and '[unknown]' in s['frames'][0] for s in group),
           'samples_ending_at_unknown_sentinel':sum(bool(s['frames']) and 'ffffffffffffffff [unknown]' in s['frames'][-1] for s in group),
           'tid_count':len({s['tid'] for s in group}),
           'first_sample_ns':group[0]['ns'] if group else None,'last_sample_ns':group[-1]['ns'] if group else None}
    ck(arm+': explicit compaction overlaps active and drain',all(result[s]['explicit_compaction_stack_samples']>0 for s in ['active','drain']))
    cold=[]
    for f in sorted((a/'remote-durability').glob('*/receipt.json')):
        z=obj(f);ck(arm+'/'+f.parent.name+': runtime cold gate',z['passed'] and z['new_cache_was_empty'] and z['unmount']['old_process_terminated']);cold.append(f.parent.name)
    ck(arm+': four runtime correctness cells',len(cold)==4)
    row={'arm':arm,'trial':int(trial),'artifact':str(a),'host_pid':ident['host_pid'],
      'namespace_pid':cpu['pid'],'starttime_ticks':cpu['starttime_ticks'],'binary_sha256':PINS[arm],
      'recorded_samples':len(ss),'recorded_tid_count':len({s['tid'] for s in ss}),
      'recorded_new_task_tids':sorted(set(s['tid'] for s in ss)-set(start['tasks'])),
      'cpu_monotonic_bounds_ns':bounds,'clock_anchor_widths_ns':widths,
      'clock_offset_spread_ns':max(offsets)-min(offsets),'stages':result,
      'actual_process_cpu_active_s':cpu['tool_cpu']['total_s'],'actual_process_cpu_drain_s':cpu['post_tool_drain_cpu']['total_s'],
      'capture_total_process_cpu_s':(stop['pid_stat']['utime_ticks']+stop['pid_stat']['stime_ticks']-start['pid_stat']['utime_ticks']-start['pid_stat']['stime_ticks'])/100,
      'capture_sample_period_total_s':sum(s['period'] for s in ss)/1e9,
      'runtime_correctness':'four cells pass; diagnostic sampling/logging, not performance qualification',
      'original_decoder_outcome': 'complete script, literal-symbol quality check rejected; partial report preserved' if arm=='baseline' else 'deferred by protocol; original raw capture retained'}
    rows.append(row)

out={'schema':'independent-profile605606-sample-review-v1','decision':'EXPLICIT_COMPACTION_OVERLAP_CONFIRMED_LIMITED_SAMPLE_ATTRIBUTION',
  'performance_accepted':False,'integrated_pr_qualified':False,'checks_passed':len(checks),'arms':rows,
  'interpretation':[
    'Both actual processes contain explicit compaction callchains in both original active and strict-drain CPU intervals. This proves real overlap, not merely an inference from residual PUT bytes.',
    'Fractions use all captured samples/periods within each stated phase as denominators, including unknown stacks. They are observed sample fractions, not exact shares of process CPU or causal excess CPU.',
    'Period totals are reported alongside /proc CPU rather than forced equal. Sampling startup, per-thread quantization, kernel/unknown and unwind coverage limit total accounting.',
    'No recorded lost/throttle events and exact sample-record counts establish transport/decode integrity, not complete stacks. Unknown leaves/sentinels and missing inline frames remain disclosed.',
    'Mangled GNU crate disambiguators can render as brewfs[hash]::. A literal brewfs:: substring check falsely rejected readable 605 stacks; original failed quality report remains unchanged.',
    'Samples and metadata logs can establish which old chunks were being compacted; S3 spans lacking keys do not assign every PUT to those chunks.',
    'Profiling/logging/setup affect timing; no 605/606 timing or CPU comparison replaces 601/603 performance evidence. Original repeated-regression HOLD remains.',
  ],'input_sha256':inputs}
f=OUT/'profile605606-sample-review-01.json';assert not f.exists();f.write_text(json.dumps(out,indent=2,sort_keys=True)+'\n')
(OUT/'profile605606-sample-checks-01.json').write_text(json.dumps(checks,indent=2)+'\n')
print(json.dumps({'path':str(f),'sha256':hashlib.sha256(f.read_bytes()).hexdigest(),'checks':len(checks),'arms':rows},indent=2))
