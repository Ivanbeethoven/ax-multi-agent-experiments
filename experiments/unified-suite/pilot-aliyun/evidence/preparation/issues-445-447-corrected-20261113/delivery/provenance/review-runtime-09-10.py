import hashlib
import json
from pathlib import Path, PurePosixPath
import statistics
import tarfile

ROOT = Path(__file__).resolve().parent
sha = lambda b: hashlib.sha256(b).hexdigest()

for stage in ('09', '10'):
    name = 'corrected-runtime-review-' + stage
    receipt = json.loads((ROOT / (name + '-receipt.json')).read_text())
    archive = ROOT / (name + '.tar.gz')
    raw = archive.read_bytes()
    assert sha(raw) == receipt['sha256'] and len(raw) == receipt['bytes']
    with tarfile.open(archive) as tar:
        members = tar.getmembers()
        assert len(members) == receipt['members']
        assert len({m.name for m in members}) == len(members)
        for m in members:
            path = PurePosixPath(m.name)
            assert m.isfile() and not path.is_absolute() and '..' not in path.parts
        data = {m.name: tar.extractfile(m).read() for m in members}
    manifest = json.loads(data['manifest.json'])
    assert sha(data['manifest.json']) == receipt['manifest_sha256']
    assert set(data) == {'manifest.json'} | {x['path'] for x in manifest['files']}
    assert len(manifest['files']) == receipt['payload_files']
    for row in manifest['files']:
        assert len(data[row['path']]) == row['bytes']
        assert sha(data[row['path']]) == row['sha256']
    result = {'passed': True, 'archive_sha256': receipt['sha256'], 'members': len(members), 'payloads_hashed': len(manifest['files'])}
    if stage == '09':
        cold = [json.loads(v) for k,v in data.items() if '/remote-durability/' in k and k.endswith('/receipt.json')]
        assert len(cold) == 8
        for r in cold:
            assert r['passed'] and r['new_cache_was_empty']
            assert r['unmount']['old_process_terminated'] and not r['unmount']['forced']
            assert not r['log_failures_before'] and not r['log_failures_after']
        assert not any(k.endswith('/perf.data') for k in data)
        result.update(cold_receipts_checked=8, raw_perf_excluded=True,
                      scope='Diagnostic runtime content and archive integrity. Actual sample quality and attribution are independently reviewed; 605 outer decode rejection is preserved, not rewritten.')
    else:
        summaries = [json.loads(v) for k,v in data.items() if k.endswith('/summary.json')]
        assert sorted(len(r['mount_exits']) for r in summaries) == [24, 114]
        for r in summaries:
            assert r['passed']
            for x in r['mount_exits']:
                assert x['exit_code'] == 0 and x['unmount_exit_code'] == 0
                assert not x['forced'] and x['mount_absent'] and x['unmount_error'] is None
        fill = next(r for r in summaries if 'waves' in r)
        waves = [w for w in fill['waves'] if '/fill-compare-' in w['path'] and w['threads']==8 and w['distinct_groups']==8]
        values = {a:[w['read_wall_ns']/1e6 for w in waves if 'fill-compare-'+a+'-' in w['path']] for a in ('baseline','candidate')}
        assert all(len(v)==6 for v in values.values()) and all(w['passed'] for w in waves)
        medians = {a:statistics.median(v) for a,v in values.items()}
        result['fill_8_by_8'] = {'milliseconds': values, 'medians_ms': medians, 'reduction_percent': 100*(1-medians['candidate']/medians['baseline']), 'scope':'50 ms injected GroupMeta delay; six pairs of eight independent cold objects, eight threads'}
        long = next(r for r in summaries if len(r['mount_exits'])==24)
        result['row_long'] = []
        for threads in (1,8):
            values={a:{} for a in ('baseline','candidate')}
            for x in long['row_cases']:
                if x['threads'] != threads: continue
                j=json.loads(data[x['path'].lstrip('/')])
                assert j['passed'] and j['warm_stable']
                assert j['successful_calls'] == threads*65536
                values[x['arm']][x['repetition']] = j['wall_ns']/1e6
            assert all(set(v)==set(range(6)) for v in values.values())
            ratios=[values['candidate'][i]/values['baseline'][i] for i in range(6)]
            result['row_long'].append({'threads':threads,'milliseconds':values,'pair_ratios':ratios,'median_ratio':statistics.median(values['candidate'].values())/statistics.median(values['baseline'].values())})
        result['scope']='All 138 mount exits and raw reported paired arithmetic checked. Detailed cache admission/byte/content/path checks remain in the independent review. No inference of process RSS or distributed performance.'
    out=ROOT/('coordinator-runtime-'+stage+'-review.json')
    out.write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps({'stage':stage,'passed':True,'members':len(members),'receipt_sha256':sha(out.read_bytes())}))
