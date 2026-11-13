from pathlib import Path
import hashlib,json,re,statistics
R=Path('/home/dev/ax-perf-issues-validation')
O=R/'independent-review-7b844'
E=Path('/home/dev/ax-randwrite-drain-diagnosis-01/profile09/events.json')
source=E.read_bytes();assert hashlib.sha256(source).hexdigest()=='fbde2a9ae148569693019a84a7489f7ab12444e4421a3ccfaddfc8085b58f644'
events=json.loads(source)
review=json.loads((O/'profile605606-sample-review-01.json').read_text())
namespace={}
exec((O/'audit-profile-samples.py').read_text().split('rows=[]')[0],namespace)
parse=namespace['samples'];checks=[]
def ck(n,b):checks.append({'name':n,'passed':bool(b)});assert b,n
rows=[]
for row in events['rows']:
    arm=row['arm'];trial=str(row['trial']);artifact=Path(row['artifact']);p=artifact/('profile'+trial)
    raw=(artifact/'brewfs.log').read_text().splitlines()
    inventory=json.loads((p/'start-request.json').read_text())['existing_scene_files']
    previous={x['ino']:x for x in inventory if x['relative_path'].startswith('.perf-fio-randwrite-direct0/')}
    ck(arm+': actual prior files',len(previous)==4 and set(previous)=={10,11,12,13})
    tasks=row['epoch_completed_tasks'];ck(arm+': all32 predecessor chunks',len(tasks)==32)
    for t in tasks:
        for endpoint in ['start','end']:
            event=t[endpoint];line=re.sub(r'\x1b\[[0-9;]*m','',raw[event['line']-1])
            ck(f'{arm}:{event["line"]}: exact raw event message',event['message'] in line and event['utc'] in line)
            ino,idx=divmod(event['chunk_id'],10**9)
            ck(f'{arm}:{event["line"]}: actual old inode+chunk',ino in previous and ino==event['ino'] and idx==event['chunk_index'] and 0<=idx<8)
            ck(f'{arm}:{event["line"]}: same prior file metadata',all(event['predecessor_file'][k]==previous[ino][k] for k in previous[ino]))
        ck(f'{arm}:{t["start"]["chunk_id"]}: positive duration',t['end']['unix_ns']>=t['start']['unix_ns'])
    ck(arm+': exact complete chunk set',{t['start']['chunk_id'] for t in tasks}=={ino*10**9+i for ino in previous for i in range(8)})
    samples=parse(O/('profile'+trial+'-noinline-demangled-01')/'script.demangled.txt')
    clocks=row['clocks'];offset=statistics.median((x['realtime_before_ns']+x['realtime_after_ns'])//2-x['monotonic_ns'] for x in clocks)
    boundaries=row['actual_cpu_realtime_ns'];reported=next(x for x in review['arms'] if x['arm']==arm)
    counts={phase:{'explicit_compaction_samples':0,'within_prior_task_window':0,'outside_prior_task_window':0} for phase in ['active','drain']}
    for s in samples:
        if not any(re.search(r'brewfs(?:\[[^\]]+\])?::chunk::compact::',f) for f in s['frames']):continue
        wall=s['ns']+offset
        phase='active' if boundaries['start']<=wall<boundaries['return'] else 'drain' if boundaries['return']<=wall<boundaries['strict_end'] else None
        if phase is None:continue
        counts[phase]['explicit_compaction_samples']+=1
        matched=any(t['start']['unix_ns']<=wall<=t['end']['unix_ns'] for t in tasks)
        counts[phase]['within_prior_task_window' if matched else 'outside_prior_task_window']+=1
    for phase in counts:
        ck(arm+': same sample phase count '+phase,counts[phase]['explicit_compaction_samples']==reported['stages'][phase]['explicit_compaction_stack_samples'])
    rows.append({'arm':arm,'trial':int(trial),'old_file_inodes':sorted(previous),'completed_chunks':32,
        'kind_counts':row['epoch_kind_counts'],'sample_event_window_crosscheck':counts,
        'all_snapshot_slice_entries':sum(t['start']['slice_count'] for t in tasks),
        'heavy_snapshot_slice_entries':sum(t['start']['slice_count'] for t in tasks if t['kind']=='Heavy'),
        'last_prior_completion_before_strict_end_s':(boundaries['strict_end']-row['last_predecessor_completion'])/1e9,
        's3_key_available':row['any_simple_put_span_key_present']})
out={'schema':'independent-profile-predecessor-join-v1','decision':'OLD_PREDECESSOR_COMPACTION_OVERLAP_SOURCE_LOG_AND_SAMPLES_CONCORDANT',
 'checks_passed':len(checks),'events_sha256':hashlib.sha256(source).hexdigest(),
 'sample_review_sha256':hashlib.sha256((O/'profile605606-sample-review-01.json').read_bytes()).hexdigest(),
 'rows':rows,'performance_accepted':False,'integrated_pr_qualified':False,
 'limits':['A sample in a logged task window plus explicit compaction frame supports real overlap; time matching does not identify each anonymous S3 request or account exact CPU.',
 'Old inode mapping proves previous-scene work is present. Different snapshot entry counts are not byte or CPU ratios.',
 'Original601/603 numbers and limitations remain. A separately matched fixed-work foreground guard may support a narrower conclusion; no universal maintenance claim follows.']}
f=O/'profile605606-predecessor-join-01.json';assert not f.exists();f.write_text(json.dumps(out,indent=2)+'\n')
(O/'profile605606-predecessor-join-checks-01.json').write_text(json.dumps(checks,indent=2)+'\n')
print(json.dumps({'path':str(f),'sha256':hashlib.sha256(f.read_bytes()).hexdigest(),'checks':len(checks),'rows':rows},indent=2))
