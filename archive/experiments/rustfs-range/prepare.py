"""Validate archived Range GET reports and export one observation per condition."""
import csv
import hashlib
import json
import math
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent
COMMIT = 'a00ad83aa555535ad6517e38bd9367d858b3bdc0'
SIZES = ['4KiB', '16KiB', '32KiB', '64KiB', '128KiB', '256KiB', '1MiB']


def size_bytes(value):
    return int(value[:-3]) * (1024 if value.endswith('KiB') else 1024 ** 2)


def milliseconds(value):
    return float(value[:-2]) if value.endswith('ms') else 1000 * float(value[:-1])


def main():
    source = ROOT / 'source'
    tree = json.loads((source / 'tree.json').read_text())
    for item in tree['tree']:
        if item['type'] != 'blob':
            continue
        raw = (source / item['path']).read_bytes()
        assert hashlib.sha1(b'blob ' + str(len(raw)).encode() + b'\0' + raw).hexdigest() == item['sha']
    with (source / 'summary.tsv').open(encoding='utf-8', newline='') as f:
        summary = list(csv.DictReader(f, delimiter='\t'))
    assert len(summary) == 35
    observations = []
    for r in summary:
        name = f"{r['suite']}_{r['series']}_range_{r['range_size']}.txt"
        text = (source / 'rounds' / name).read_text(encoding='utf-8')
        run = re.search(r'Report: GET\. Concurrency: (\d+)\. Ran: ([\d.]+)s', text)
        avg = re.search(r'\* Average: ([\d.]+) MiB/s, ([\d.]+) obj/s(?:, (\d+) errors)?', text)
        latency = re.search(r'\* Reqs: Avg: ([\d.]+m?s), 50%: ([\d.]+m?s), 90%: ([\d.]+m?s), 99%: ([\d.]+m?s)', text)
        assert run and avg and latency, name
        assert int(run[1]) == 64
        rate, mib = float(r['req_per_s']), float(r['throughput'].split()[0])
        assert math.isclose(mib, float(avg[1]), abs_tol=.005)
        assert math.isclose(rate, float(avg[2]), abs_tol=.005)
        for key, val in zip(['req_avg', 'req_p50', 'req_p90', 'req_p99'], latency.groups()):
            assert math.isclose(milliseconds(r[key]), milliseconds(val), abs_tol=.005)
        assert math.isclose(rate * size_bytes(r['range_size']) / 1024 ** 2, mib, abs_tol=.011)
        errors = int(avg[3] or 0)
        assert text.count('<ERROR>') == errors, name
        if errors:
            assert text.count('waiting for startup_finalization') == errors
        observations.append({'ec': r['ec_geometry'], 'series': r['series'],
            'layout': r['block_layout'], 'range_size': r['range_size'],
            'range_bytes': size_bytes(r['range_size']), 'req_per_s': rate, 'mib_per_s': mib,
            'p50_ms': milliseconds(r['req_p50']), 'p99_ms': milliseconds(r['req_p99']),
            'errors': errors, 'reported_duration_s': float(run[2]), 'report': 'rounds/' + name})
    groups = sorted({(r['ec'], r['series']) for r in observations})
    assert len(groups) == 5
    for ec, series in groups:
        assert [r['range_size'] for r in observations if (r['ec'], r['series']) == (ec, series)] == SIZES
    failures = [{k: r[k] for k in ['ec', 'series', 'range_size', 'errors']} for r in observations if r['errors']]
    assert failures == [
        {'ec': '12+4', 'series': '105p1', 'range_size': '4KiB', 'errors': 16},
        {'ec': '8+8', 'series': 'dyn64k', 'range_size': '4KiB', 'errors': 1}]
    data = {'schema': 1, 'commit': COMMIT,
        'environment': {'platform': 'Aliyun i5', 'storage_nodes': 4, 'cores_per_node': 6,
                        'memory_gib_per_node': 12, 'nvme_per_node': 4,
                        'objects_per_configuration': 3200, 'object_bytes': 16 * 1024 ** 2,
                        'concurrency': 64, 'requested_duration_s': 45, 'runs_per_condition': 1,
                        'cache_protocol': 'Author reports dropping page caches before each trial.'},
        'observations': observations}
    (ROOT / 'data.json').write_text(json.dumps(data, indent=2) + '\n')
    qa = ROOT / 'qa'; qa.mkdir(exist_ok=True)
    checks = {'conditions': len(observations), 'configurations': len(groups), 'sizes': SIZES,
              'all_blobs_verified': True, 'all_report_values_match_summary': True,
              'request_errors': failures, 'duration_range_s': [min(r['reported_duration_s'] for r in observations), max(r['reported_duration_s'] for r in observations)],
              'source_report_sha256': hashlib.sha256((source / 'author-report.md').read_bytes()).hexdigest()}
    (qa / 'data-checks.json').write_text(json.dumps(checks, indent=2) + '\n')
    print(json.dumps(checks, indent=2))


if __name__ == '__main__':
    main()
