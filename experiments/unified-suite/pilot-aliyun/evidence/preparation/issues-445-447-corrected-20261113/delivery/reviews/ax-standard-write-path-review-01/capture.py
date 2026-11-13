"""Read-only capture of corrected standard601 write-path facts."""
import datetime
import hashlib
import json
import os
from pathlib import Path
import subprocess

os.environ['GIT_OPTIONAL_LOCKS']='0'
ROOT=Path('/home/dev/ax-issue447-corrected-candidate')
RAW=Path('/home/dev/ax-perf-issues-validation/corrected-runtime-review-06')
OUT=Path('/home/dev/ax-standard-write-path-review-01')
BASE='4f982f45c153a0c31e3e9cd8ff642ec7659c98fd'
CAND='c8f5f16aefe811540782b952481982179a1bfd6f'
def git(*args):return subprocess.check_output(['git','-C',str(ROOT),*args])
def sha(data):return hashlib.sha256(data).hexdigest()
def source(commit,path):return git('show',commit+':'+path).decode()
def span(text,start,end):
    assert text.count(start)==1,(start,text.count(start))
    a=text.index(start);b=text.index(end,a)
    assert b>a
    return text[a:b]

OUT.mkdir(exist_ok=True)
assert git('rev-parse','HEAD').decode().strip()==CAND and not git('status','--porcelain').strip()
writer='src/vfs/io/writer.rs'
before,after=(source(c,writer) for c in (BASE,CAND))
pairs=[
    ('ordinary_and_cached_entry_methods','    pub(crate) async fn write_at_with_unique(','    fn overlay_slices_for_chunk('),
    ('strict_flush_wait','    async fn flush_with_deadline_inner(','    pub(crate) async fn clear('),
    ('stage_and_upload_task','    fn spawn_flush_slice(','    async fn pop_front_slice('),
    ('cleanup_helpers','    async fn remove_writeback_record_if_uploaded_committed(','    fn keep_recently_committed_if_overlay_needed('),
    ('cbu_handoff','    async fn try_commit_before_upload_front(','    async fn try_commit_before_upload_if_front('),
    ('commit_task','    async fn commit_chunk(','    async fn auto_flush('),
]
receipt={'schema':'ax.standard-write-path-review.v1','captured_at_utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),
    'baseline':BASE,'candidate':CAND,'runtime_archive':str(RAW),'source_inference_not_runtime_branch_trace':True,
    'runtime_creation_unique_value':'unobserved: no per-request WRITE_CACHE/creation_unique trace in601',
    'unchanged_source_spans':[],'whole_file_identity':[],'arms':{},'finding':'No source-level explanation of the observed regression has been established. This does not dismiss the measured regression or waive603.'}
for label,start,end in pairs:
    a,b=span(before,start,end),span(after,start,end)
    assert a==b,label
    receipt['unchanged_source_spans'].append({'label':label,'file':writer,'start':start.strip(),'end_exclusive':end.strip(),'sha256':sha(a.encode()),'bytes':len(a.encode())})
for path in ['src/fuse/mod.rs','src/fuse/mount.rs','src/vfs/handles.rs','src/vfs/fs/mod.rs']:
    a,b=(git('show',c+':'+path) for c in (BASE,CAND));assert a==b,path
    receipt['whole_file_identity'].append({'file':path,'sha256':sha(a),'bytes':len(a)})
comparison=json.loads((RAW/'runtime/regression-corrected512-01/comparison.json').read_text())
for arm in ['baseline','candidate']:
    art=next((RAW/'artifacts'/arm).glob('perf-run-*'))
    manifest=json.loads((art/'manifest.json').read_text())
    actual=manifest['comparability']['filesystem']
    assert actual['BREWFS_FUSE_WRITEBACK']=='0'
    assert actual['BREWFS_VOLUME_FORMAT']=='' and actual['BREWFS_WORKSPACE_ID']==''
    trial=json.loads((RAW/'trials'/f'ax447-corrected-{arm}-regression-601/receipt.json').read_text())
    assert trial['environment']['BREWFS_FUSE_OP_LOG']=='0'
    fields={}; inputs=[]
    for p in sorted((art/'diagnostics').glob('stats-*direct1*.txt')):
        vals={}
        for line in p.read_text().splitlines():
            parts=line.split()
            if len(parts)==2 and ('cached_only' in parts[0] or 'cached_sub_block' in parts[0] or 'reject_older_unique' in parts[0]):
                vals[parts[0]]=int(parts[1])
        assert vals,p
        assert all(x==0 for x in vals.values()),(p,vals)
        fields[str(p.relative_to(art))]=vals
        inputs.append({'path':str(p),'sha256':sha(p.read_bytes())})
    receipt['arms'][arm]={'artifact':str(art),'actual_manifest_filesystem':actual,
        'manifest_sha256':sha((art/'manifest.json').read_bytes()),'trial_sha256':sha((RAW/'trials'/f'ax447-corrected-{arm}-regression-601/receipt.json').read_bytes()),
        'fuse_op_log':'0','sampled_cached_specific_metrics':fields,'stats_inputs':inputs,
        'metric_limit':'These counters cover specific events or current gauges, not every WRITE opcode. Their zero values do not prove absence of all cached writes.'}
row=next(p for p in comparison['pairs'] if p['tool']=='fio-randwrite-direct1')
receipt['randwrite_direct1_cpu_and_bytes']={arm:{'active_cpu_s':row[arm]['active_cpu_s'],'drain_cpu_s':row[arm]['active_plus_drain_cpu_s']-row[arm]['active_cpu_s'],
    'total_cpu_s':row[arm]['active_plus_drain_cpu_s'],'write_bytes':row[arm]['write_bytes'],'active_plus_drain_write_MiB_s':row[arm]['active_plus_drain_write_MiB_s'],
    'total_cpu_s_per_GiB':row[arm]['active_plus_drain_cpu_s_per_GiB']} for arm in ['baseline','candidate']}
receipt['original601_comparison_sha256']=sha((RAW/'runtime/regression-corrected512-01/comparison.json').read_bytes())
(OUT/'source-and-runtime-facts.json').write_text(json.dumps(receipt,indent=2)+'\n')
(OUT/'writer-4f-to-c8.diff').write_bytes(git('diff',BASE,CAND,'--',writer))
print(json.dumps({'receipt':str(OUT/'source-and-runtime-facts.json'),'sha256':sha((OUT/'source-and-runtime-facts.json').read_bytes()),'unchanged_spans':len(pairs),'sample_counts':{arm:len(v['stats_inputs']) for arm,v in receipt['arms'].items()},'randwrite':receipt['randwrite_direct1_cpu_and_bytes']}))
