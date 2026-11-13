from pathlib import Path
import hashlib, json, math, re, statistics, tarfile
import yaml

P=Path('/home/dev/ax-perf-issues-validation/corrected-runtime-review-07')
O=P.parent/'independent-review-7b844'
J=lambda p:json.loads(p.read_text())
H=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
checks=[]
def ck(name, value):
    checks.append(name)
    assert value,name
def met(p):
    return {m[1]:int(m[2]) for l in p.read_text().splitlines() if (m:=re.fullmatch(r'(brewfs_\w+) (\d+)',l))}
rec=J(P.with_suffix('.receipt.json'));man=J(P/'manifest.json')
ck('archive',H(P.with_suffix('.tar.gz'))==rec['sha256'])
ck('manifest',H(P/'manifest.json')==rec['manifest_sha256'])
for e in man['files']:
    q=P/e['path'];ck(e['path'],q.stat().st_size==e['bytes'] and H(q)==e['sha256'])
with tarfile.open(P.with_suffix('.tar.gz')) as t:ck('members',len(t.getmembers())==485 and len(man['files'])==484)
def mapped(path):
    for old,new in man['path_map'].items():
        if path==old or path.startswith(old+'/'):return P/new/path[len(old):].lstrip('/')
    raise ValueError(path)
pins={'baseline':('4f982f45c153a0c31e3e9cd8ff642ec7659c98fd','0b0a2743aa968f497782d823cb068cd421eb840f82d5739a363d3bc3623c8679'),'candidate':('c8f5f16aefe811540782b952481982179a1bfd6f','905c4e4ca35162ae6a1ab2de03f4fa18194ef64d9afcbe20cff9bbefd570ce90')}
expected_hash=hashlib.sha256(b'\x5a'*(16<<20)).hexdigest()
budget={'read_memory_bytes':4<<30,'write_memory_bytes':4<<30,'read_ssd_bytes':8<<30,'write_ssd_bytes':4<<30,'memory_budget_bytes':12<<30}
debt=['brewfs_writeback_recent_pending_upload_bytes','brewfs_writeback_dirty_bytes','brewfs_buffer_dirty_bytes','brewfs_writeback_live_dirty_bytes','brewfs_writeback_live_slices','brewfs_writeback_recent_pending_upload_slices','brewfs_writeback_stage_inflight_bytes','brewfs_writeback_remote_upload_inflight_bytes','brewfs_writeback_metadata_inflight']
runs={}; options=[]; image_ids=[]; configs=[];harness=[]
for arm,(commit,binary) in pins.items():
    for case,trial in [('cached-smoke',300),('cached',600),('cached',601),('cached',602)]:
        label=f'{arm}-{case}-{trial}';td=P/f'trials/ax447-corrected-{label}'
        r=J(td/'receipt.json');a=mapped(r['artifact_directories'][0]);d=a/'remote-durability/fio-bigwrite'
        ck(label+'qualified',r['qualified'] is True and r['status']==0)
        ck(label+'source',r['source']['commit']==commit and not r['source']['dirty'] and r['binary']==dict(commit=commit,sha256=binary))
        ck(label+'no leftover',not (td/'remaining-owned-containers.json').read_text().strip())
        ck(label+'no invalid',not list(a.glob('*.invalid')))
        gate=J(td/'services/error-gate.json');ck(label+'service gate',gate['passed'] and gate['error_count']==0 and H(td/'services/rustfs.log')==gate['log_sha256'])
        image_ids.append({k:v['id'] for k,v in r['images'].items()})
        harness.append({k:v for k,v in r.items() if k.endswith('_sha256')})
        cfg=yaml.safe_load((a/'backend.yml').read_text());ck(label+'budget',all(cfg['cache'][k]==v for k,v in budget.items()))
        cfg['cache'].pop('root');configs.append(cfg)
        fio=J(a/'results/fio-bigwrite.json');ck(label+'one group',len(fio['jobs'])==1)
        j=fio['jobs'][0];opt={k:v for k,v in j['job options'].items() if k!='write_lat_log'};options.append(opt)
        ck(label+'shape and verify',opt['bs']=='4k' and opt['size']=='16m' and opt['numjobs']=='1' and opt['direct']=='0' and opt['ioengine']=='psync' and opt['buffer_pattern']==opt['verify_pattern']=='0x5a' and opt['verify']=='pattern' and opt['do_verify']==opt['verify_fatal']=='1')
        ck(label+'4096 writes',j['error']==0 and j['write']['io_bytes']==16<<20 and j['write']['total_ios']==4096 and j['write']['drop_ios']==0)
        dr=J(d/'receipt.json');e=J(d/'expected.json');v=J(d/'observed.json')
        ck(label+'independent known bytes',len(e)==1 and e==v and e[0]['sha256']==expected_hash and e[0]['bytes']==16<<20)
        ck(label+'cold gate',dr['passed'] and not dr['reasons'] and not dr['log_failures_before'] and not dr['log_failures_after'] and dr['new_cache_was_empty'] and dr['old_cache_preserved'])
        ck(label+'normal samebin remount',dr['unmount']['old_process_terminated'] and not dr['unmount']['forced'] and dr['old_mount']['executable_sha256']==dr['new_mount']['executable_sha256']==binary and dr['old_mount']['argv']==dr['new_mount']['argv'] and dr['old_mount']['pid']!=dr['new_mount']['pid'])
        cb,ca=[yaml.safe_load((d/n).read_text()) for n in ['config-before.yaml','config-after.yaml']]
        cb['cache'].pop('root');ca['cache'].pop('root');ck(label+'only cache root changes',cb==ca)
        samples=sorted((a/'stats-timeseries/fio-bigwrite-post-write').glob('*.txt'));ss=[met(q) for q in samples[-3:]]
        ck(label+'strict three samples',len(ss)==3 and all(all(s[k]==0 for k in debt) for s in ss) and len({(s['brewfs_s3_put_ops_total'],s['brewfs_s3_put_bytes_total']) for s in ss})==1)
        ck(label+'actual PUT bytes',ss[-1]['brewfs_s3_put_bytes_total']>=16<<20)
        if case=='cached-smoke':
            ck(label+'logging explicit',r['environment']['BREWFS_FUSE_OP_LOG']=='1')
            groups={}
            for l in (a/'brewfs_fuse_ops.log').read_text().splitlines():
                if '[write] REQ' not in l:continue
                fields={k:int(v) for k,v in re.findall(r'\b(inode|fh|offset|data_len|write_flags|flags)=(\d+)',l)}
                fields['unique']=int(re.search(r'\bunique:\s*(\d+)',l)[1]);groups.setdefault(fields['inode'],[]).append(fields)
            proven=[]
            for ino,g in groups.items():
                if len(g)==4096 and sorted(x['offset'] for x in g)==list(range(0,16<<20,4096)) and all(x['data_len']==4096 and x['write_flags']&1 and x['unique']>0 for x in g) and len({x['unique'] for x in g})==4096:proven.append(ino)
            ck(label+'raw exact cached request path',len(proven)==1 and proven==J(a/'cached-write-touch-proof.json')['proven_inodes'])
        else:
            ck(label+'logging off',r['environment']['BREWFS_FUSE_OP_LOG']=='0' and not (a/'brewfs_fuse_ops.log').exists())
        phase={}
        for l in (a/'phase-boundaries.tsv').read_text().splitlines():
            tool,s,ns=l.split('\t');phase[s]=int(ns)
        obs={}
        for l in (a/'client-cpu-raw.tsv').read_text().splitlines():
            tool,s,ns,raw=l.split('\t',3);tail=raw.rsplit(') ',1)[1].split()
            obs[s]=dict(ns=int(ns),pid=int(raw.split(' (',1)[0]),start=int(tail[19]),ticks=int(tail[11])+int(tail[12]))
        names=['tool_start','tool_return','post-write_extended_drained'];x,y,z=[obs[k] for k in names]
        ck(label+'CPU process identity',len({(o['pid'],o['start']) for o in (x,y,z)})==1)
        ck(label+'CPU ticks monotonic',x['ticks']<=y['ticks']<=z['ticks'])
        ck(label+'all boundaries',x['ns']<=phase[names[0]]<=phase[names[1]]<=y['ns']<=phase[names[2]]<=z['ns'])
        ck(label+'cold after measurement',dr['started_unix_ns']>phase[names[2]] and dr['timing_outside_measured_tool_and_strict_drain'])
        hz=int((a/'client-cpu-clock-ticks.txt').read_text());ck(label+'100HZ',hz==100)
        runs[label]=dict(source=commit,binary_sha256=binary,receipt_sha256=H(td/'receipt.json'),started_at=r['started_at'],finished_at=r['finished_at'],cpu_s=(z['ticks']-x['ticks'])/hz,active_cpu_s=(y['ticks']-x['ticks'])/hz,wall_ns=phase[names[2]]-phase[names[0]],active_wall_ns=phase[names[1]]-phase[names[0]],cpu_observations=obs,cold_sha256=expected_hash,final_put_bytes=ss[-1]['brewfs_s3_put_bytes_total'],fio_write_short_ios=j['write']['short_ios'],fio_read_short_ios=j['read']['short_ios'])
ck('identical actual FIO options',all(v==options[0] for v in options))
ck('identical images',all(v==image_ids[0] for v in image_ids))
ck('identical complete effective config except cache root',all(v==configs[0] for v in configs))
ck('identical harness',all(v==harness[0] for v in harness))
owner=J(P/'runtime/cached-corrected51201/paired-summary.json');pairs=[]
for i,order in enumerate([['baseline','candidate'],['candidate','baseline'],['baseline','candidate']]):
    b=runs[f'baseline-cached-{600+i}'];c=runs[f'candidate-cached-{600+i}']
    earlier,later=[runs[f'{arm}-cached-{600+i}'] for arm in order]
    ck('serial actual order'+str(i),earlier['finished_at']<later['started_at'])
    ck('prior smokes finish'+str(i),all(runs[f'{arm}-cached-smoke-300']['finished_at']<earlier['started_at'] for arm in pins))
    own=owner['pairs'][i]
    for arm,data in [('baseline',b),('candidate',c)]:
        for k in ('cpu_s','active_cpu_s','wall_ns','active_wall_ns'):
            ck(f'owner raw matches {i}/{arm}/{k}',math.isclose(data[k],own[arm][k],rel_tol=1e-12))
        ck(f'owner receipt matches {i}/{arm}',data['receipt_sha256']==own[arm]['receipt_sha256'])
    pairs.append(dict(trial=600+i,order=order,baseline=b,candidate=c,cpu_reduction_percent=100*(1-c['cpu_s']/b['cpu_s']),cpu_difference_ticks=round(100*(b['cpu_s']-c['cpu_s'])),wall_change_percent=100*(c['wall_ns']/b['wall_ns']-1)))
ck('all pairs CPU lower',all(p['cpu_reduction_percent']>0 for p in pairs))
report=dict(schema='independent-corrected-cached-review-v1',decision='CPU_REDUCTION_RECOMPUTED_FIO_SHORT_IOS_INTERPRETATION_PENDING',integrated_pr_qualified=False,
            evidence=dict(directory=str(P),tar_sha256=rec['sha256'],manifest_sha256=rec['manifest_sha256'],payload_files=484,total_tar_members=485),
            checks_passed=len(checks),runtime_source={a:p[0] for a,p in pins.items()},publication_source='e2c964c55fafdcced34af58e840ebddda1cf9ea4',known16MiB_5a_sha256=expected_hash,pairs=pairs,
            median_cpu_reduction_percent=statistics.median(p['cpu_reduction_percent'] for p in pairs),
            measurement_scope='4096 sequential cached 4KiB writes with sync_file_range and final fsync/pattern verification, 16MiB known 0x5a, same binary per arm and same explicit configuration on expanded512GiB host. Smoke operation logging is excluded from timing; timed arms disable it.',
            benefit='All three AB/BA/AB pairs use fewer BrewFS process CPU ticks through strict drain: reductions 8,13,10 ticks at100HZ, or15.09,24.07,18.87 percent. This is repeated measured client CPU benefit for this workload, not a universal guarantee or statistical confidence interval.',
            wall_boundary='No wall-throughput improvement claim. Full walls include about4.3 seconds of fixed quiet-observation overhead; two pairs are nearly equal and one shorter. CPU excludes FIO and RustFS. Cold verification is outside timed boundaries.',
            correctness='All eight smoke/timed arms have known-content full cold SHA after normal old-process exit and empty-cache remount, three zero-debt stable PUT samples, no server/client failure and matching resource/cache identities.',
            raw_short_io_boundary='All eight arms report write.short_ios=4095, read.short_ios=0. Preserve this raw field. Full known content and exact logged cached requests pass, but precise fio3.39 psync/sync_file_range accounting interpretation is pending independent source review; do not call it zero or infer data loss without that evidence.',
            pending='Integrated PR remains HOLD for short_ios interpretation, standard direct1 nonregression resolution and corrected workspace/long-row gates; do not use this target gain to cancel another path regression.')
out=O/'corrected-cached-review-07.json';out.write_text(json.dumps(report,indent=2)+'\n');(O/'corrected-cached-checks-07.json').write_text(json.dumps(checks,indent=2)+'\n')
print(json.dumps(dict(checks=len(checks),sha256=H(out),decision=report['decision'],pairs=[{k:p[k] for k in ['trial','order','cpu_reduction_percent','cpu_difference_ticks','wall_change_percent']} for p in pairs]),indent=2))
