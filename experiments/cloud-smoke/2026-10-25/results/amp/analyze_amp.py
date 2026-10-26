#!/usr/bin/env python3
"""Recompute all derived values from immutable raw Prometheus snapshots.

Also read (never write) the three archived smoke result directories.
"""
import argparse, hashlib, json, pathlib
import run_amp as amp
def load(path):
    return json.loads(path.read_text())
def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()
def recompute(snapshot):
    for c in snapshot['clients'].values():
        c['selected'] = amp.selected(amp.parse(c['raw']))
    return snapshot
def delta(a,b):
    return amp.diff(recompute(a),recompute(b))
def main():
    p=argparse.ArgumentParser()
    p.add_argument('--out',type=pathlib.Path,default=amp.OUT)
    p.add_argument('--smoke',type=pathlib.Path,default=pathlib.Path('/data/cloud-smoke/2026-10-25/results'))
    args=p.parse_args()
    rows=[]
    for path in sorted(args.out.glob('*/result.json')):
        r=load(path)
        directory=path.parent
        before=load(directory/'before.json')
        after=load(directory/'after.json')
        d=delta(before,after)
        primary=d[r['mount']]
        gauges={c:{k:amp.get(amp.parse(v['raw']),k) for k in (
            'juicefs_staging_blocks','juicefs_staging_block_bytes','juicefs_staging_writing_blocks',
            'juicefs_used_buffer_size_bytes')} for c,v in after['clients'].items()}
        other={c:v for c,v in d.items() if c!=r['mount'] and any(
            v[k] for k in ('PUT_bytes','GET_bytes','PUT_count','GET_count',
                          'juicefs_compact_size_histogram_bytes_count'))}
        assert not other, (r['case'],'other client changed',other)
        assert r['validation']['ok']
        assert primary['juicefs_object_request_errors']==0
        assert primary['juicefs_staging_block_errors']==0
        assert gauges[r['mount']]['juicefs_staging_blocks']==0
        assert gauges[r['mount']]['juicefs_staging_block_bytes']==0
        assert gauges[r['mount']]['juicefs_staging_writing_blocks']==0
        n=r['logical_write_bytes']
        cb=primary['juicefs_compact_size_histogram_bytes_sum']
        cc=primary['juicefs_compact_size_histogram_bytes_count']
        row={'case':r['case'],'client':r['mount'],'logical_write_bytes':n,
             'logical_read_bytes':r.get('logical_read_bytes',0),
             **primary,'PUT_amplification':primary['PUT_bytes']/n,
             'GET_per_logical_write':primary['GET_bytes']/n,
             'compact_average_bytes':cb/cc if cc else None,
             'PUT_minus_compact_bytes':primary['PUT_bytes']-cb,
             'after_gauges':gauges,'other_client_activity':other,
             'before_sha256':sha(directory/'before.json'),
             'after_sha256':sha(directory/'after.json'),
             'utc_before':before['utc'],'utc_after':after['utc'],'validation':r['validation']}
        if (directory/'cycle-first-after-writes.json').exists():
            s0=load(directory/'cycle-first-before.json')
            sw=load(directory/'cycle-first-after-writes.json')
            sr=load(directory/'cycle-first-after-read.json')
            row['first_cycle']={'write_phase':delta(s0,sw)[r['mount']],
                                'read_phase':delta(sw,sr)[r['mount']]}
        rows.append(row)
    amp.save(args.out/'summary.json',{'cases':rows})
    table=['| Case | logical write B | PUT B | PUT count | GET B | GET count | PUT/write | compaction count | compact B |',
           '|---|---:|---:|---:|---:|---:|---:|---:|---:|']
    for r in rows:
        table.append('| '+r['case']+' | '+ ' | '.join(f'{r[k]:,}' for k in (
            'logical_write_bytes','PUT_bytes','PUT_count','GET_bytes','GET_count'))+
            f" | {r['PUT_amplification']:.6f} | {r['juicefs_compact_size_histogram_bytes_count']:,} | "
            f"{r['juicefs_compact_size_histogram_bytes_sum']:,} |")
    (args.out/'tables.md').write_text('\n'.join(table)+'\n')
    old=[]
    for label,mode in (('initial','off'),('stable','off'),('cleandiag','op')):
        a=args.smoke/label/f'jfs-{mode}-before-backend.json'
        b=args.smoke/label/f'jfs-{mode}-after-backend.json'
        pa=amp.parse(load(a)['jfs_metrics'])
        pb=amp.parse(load(b)['jfs_metrics'])
        sa,sb=amp.selected(pa),amp.selected(pb)
        d={k:sb[k]-v for k,v in sa.items()}
        cb=d['juicefs_compact_size_histogram_bytes_sum']
        put=d['PUT_bytes']
        old.append({'run':label,'mode':mode,'before_file':str(a),'after_file':str(b),
                    'before_sha256':sha(a),'after_sha256':sha(b),'delta':d,
                    'compact_share_of_PUT':cb/put,'PUT_minus_compact_bytes':put-cb,
                    'PUT_per_FUSE_write':put/d['juicefs_fuse_written_size_bytes_sum']})
    amp.save(args.out/'smoke-recheck.json',old)
    print('\n'.join(table),flush=True)
    print(json.dumps(old,indent=2),flush=True)
if __name__=='__main__':
    main()
