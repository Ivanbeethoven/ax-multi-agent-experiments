import hashlib
import json
from pathlib import Path, PurePosixPath
import tarfile

ROOT = Path(__file__).resolve().parent


def sha(data):
    return hashlib.sha256(data).hexdigest()


for stage in ('07', '08'):
    name = 'corrected-runtime-review-' + stage
    receipt = json.loads((ROOT / (name + '.receipt.json')).read_text())
    archive = ROOT / (name + '.tar.gz')
    raw = archive.read_bytes()
    assert sha(raw) == receipt['sha256']
    assert len(raw) == receipt['bytes']
    with tarfile.open(archive) as tar:
        members = tar.getmembers()
        assert len({m.name for m in members}) == len(members)
        for m in members:
            path = PurePosixPath(m.name)
            assert m.isfile() and not path.is_absolute() and '..' not in path.parts
        data = {m.name: tar.extractfile(m).read() for m in members}
    manifest = json.loads(data['manifest.json'])
    assert sha(data['manifest.json']) == receipt['manifest_sha256']
    assert set(data) == {'manifest.json'} | {x['path'] for x in manifest['files']}
    assert len(manifest['files']) == receipt['files']
    for row in manifest['files']:
        assert len(data[row['path']]) == row['bytes']
        assert sha(data[row['path']]) == row['sha256']
    cold = []
    cpu = []
    for path, value in data.items():
        if '/remote-durability/' in path and path.endswith('/receipt.json'):
            r = json.loads(value)
            assert r['passed'] and r['new_cache_was_empty']
            assert r['old_cache_preserved'] and r['unmount']['old_process_terminated']
            assert not r['unmount']['forced']
            assert r['timing_outside_measured_tool_and_strict_drain']
            assert not r['log_failures_before'] and not r['log_failures_after']
            assert r['old_mount']['executable_sha256'] == r['new_mount']['executable_sha256']
            cold.append({'path': path, 'bytes': r['logical_bytes'], 'files': r['file_count']})
        if path.endswith('/client-cpu-summary.json'):
            r = json.loads(value)
            for tool in r['tools']:
                observations = tool['observations']
                assert len(observations) == 3
                assert len({(x['pid'], x['starttime_ticks']) for x in observations}) == 1
                ticks = [(x['user_ticks'] + x['system_ticks']) for x in observations]
                assert ticks == sorted(ticks)
                secs = (ticks[-1] - ticks[0]) / r['user_hz']
                assert abs(secs - tool['tool_and_drain_cpu']['total_s']) < 1e-9
                cpu.append({'path': path, 'tool': tool['tool'], 'cpu_s': secs})
    assert len(cold) == (8 if stage == '07' else 12)
    recomputed_pairs = []
    if stage == '07':
        summary = json.loads(data['runtime/cached-corrected51201/paired-summary.json'])
        assert summary['passed_correctness'] and len(summary['pairs']) == 3
        for pair in summary['pairs']:
            actual = {}
            for arm in ('baseline', 'candidate'):
                r = pair[arm]
                path = 'artifacts/' + arm + '/' + r['artifact'].rsplit('/', 1)[-1]
                c = json.loads(data[path + '/client-cpu-summary.json'])['tools'][0]
                p = json.loads(data[path + '/precise-phase-summary.json'])['tools'][0]
                assert abs(r['cpu_s'] - c['tool_and_drain_cpu']['total_s']) < 1e-9
                assert r['wall_ns'] == p['strict_drained_ns'] - p['tool_start_ns']
                actual[arm] = r['cpu_s']
            ratio = actual['candidate'] / actual['baseline']
            assert abs(ratio - pair['candidate_over_baseline_cpu']) < 1e-9
            recomputed_pairs.append({'cpu_s': actual, 'reduction_percent': 100 * (1-ratio)})
    result = {
        'passed': True,
        'archive_sha256': receipt['sha256'],
        'members': len(members),
        'payloads_hashed': len(manifest['files']),
        'cold_receipts_checked': cold,
        'cpu_ticks_recomputed': cpu,
        'cached_pairs_recomputed': recomputed_pairs,
        'scope': 'Coordinator checks all archive paths, exact payload hashes, cold receipt identity/error gates and process CPU arithmetic. Cached reductions include internal FIO readback and strict drain; no whole-lifecycle or throughput claim. Independent detailed content/syscall/profile reviews remain separate. Standard CPU regression HOLD remains unchanged.'
    }
    output = ROOT / ('coordinator-runtime-' + stage + '-review.json')
    output.write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps({'stage': stage, 'passed': True, 'members': len(members), 'cold_checks': len(cold), 'cpu_windows': len(cpu), 'receipt_sha256': sha(output.read_bytes())}))
