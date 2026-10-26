#!/usr/bin/env python3
"""Stream the supplied JSONL; retain counters/histograms, never an event list.

Python standard library only. Run with --source pointing at the original capture.
Outputs describe handler requests, not application syscalls or storage performance.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
META = ('lookup', 'getattr', 'readdirplus', 'opendir', 'releasedir', 'getxattr')


def load(path):
    return json.loads(path.read_text(encoding='utf-8-sig'))


def digest(path):
    h = hashlib.sha256()
    with path.open('rb') as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def new_stats():
    return dict(methods=Counter(), status=Counter(), method_status=Counter(),
                requests=Counter(), responses=Counter(), sizes=defaultdict(Counter),
                times=defaultdict(Counter), latency_missing=Counter(),
                latency_sum=Counter(), pre_agent=Counter(), post_run=Counter(),
                first=None, last=None, max_id=0, seen=bytearray(), duplicates=0)


def quantile(hist, p):
    """Exact empirical quantile: linear interpolation at (n-1)*p."""
    n = sum(hist.values())
    if not n:
        return None
    pos = (n - 1) * p
    lo, hi = math.floor(pos), math.ceil(pos)
    running, a, b = 0, None, None
    for value, count in sorted(hist.items()):
        if running <= lo < running + count:
            a = value
        if running <= hi < running + count:
            b = value
            break
        running += count
    return a + (b - a) * (pos - lo)


def size_summary(hist):
    n = sum(hist.values())
    return dict(n=n, request_bytes=sum(k*v for k, v in hist.items()),
                min_bytes=min(hist) if n else None, max_bytes=max(hist) if n else None,
                quantiles_bytes={str(p): quantile(hist, p) for p in (.5, .75, .9, .95, .99)},
                le_4KiB=sum(v for k, v in hist.items() if k <= 4096),
                gt_4_le_64KiB=sum(v for k, v in hist.items() if 4096 < k <= 65536),
                gt_64_le_128KiB=sum(v for k, v in hist.items() if 65536 < k <= 131072),
                gt_128KiB=sum(v for k, v in hist.items() if k > 131072))


def summarize(s):
    n = sum(s['methods'].values())
    return dict(events=n, methods=dict(s['methods']), statuses=dict(s['status']),
                method_status={m: {k: v for (mm, k), v in s['method_status'].items() if mm == m}
                               for m in s['methods']},
                request_bytes=dict(s['requests']), response_bytes=dict(s['responses']),
                size={m: size_summary(s['sizes'][m]) for m in ('read', 'write')},
                metadata6_count=sum(s['methods'][m] for m in META),
                metadata6_pct=100 * sum(s['methods'][m] for m in META) / n,
                read_write_ratio=s['methods']['read']/s['methods']['write'],
                latency_missing=dict(s['latency_missing']),
                pre_agent_events=sum(s['pre_agent'].values()),
                pre_agent_methods=dict(s['pre_agent']),
                post_run_events=sum(s['post_run'].values()),
                post_run_methods=dict(s['post_run']))


def write_csv(path, fields, rows):
    with path.open('w', newline='', encoding='utf-8') as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--source', type=Path, required=True)
    ap.add_argument('--output', type=Path, default=HERE/'derived')
    args = ap.parse_args()
    runs = load(args.source/'runs.json')
    tasks = load(args.source/'tasks.json')
    availability = load(args.source/'agent-availability.json')
    by_id = {r['task']['instance_id']: r for r in runs}
    assert len(by_id) == len(runs) == 8, 'Expected eight distinct task runs'
    core_ids = {t['instance_id'] for t in tasks}
    groups = {t: 'Exploratory' if r.get('workload') == 'exploratory' else 'Core'
              for t, r in by_id.items()}
    assert core_ids == {t for t, g in groups.items() if g == 'Core'}
    stats = {t: new_stats() for t in by_id}
    started = {t: datetime.fromisoformat(r['agent_started_at']) for t, r in by_id.items()}
    finished = {t: datetime.fromisoformat(r['finished_at']) for t, r in by_id.items()}
    event_hash, line_count, key_counts = hashlib.sha256(), 0, Counter()
    with (args.source/'events.jsonl').open('rb') as f:
        for line_count, raw in enumerate(f, 1):
            event_hash.update(raw)
            try:
                e = json.loads(raw)
                task, m, status = e['task'], e['method'], e['status']
                s, run = stats[task], by_id[task]
                assert e['agent'] == run['agent'] and e['model'] == run['model']
                assert e.get('workload', 'core') == run.get('workload', 'core')
                assert status in ('ok', 'error', 'incomplete')
                assert 'deepseek-v4-flash' in e['model']
                key_counts.update(e.keys())
                op = e['operation_id']
                assert isinstance(op, int) and 0 <= op < 10000000
                if op >= len(s['seen']):
                    s['seen'].extend(bytearray(op + 1 - len(s['seen'])))
                s['duplicates'] += bool(s['seen'][op])
                s['seen'][op] = 1
                s['max_id'] = max(s['max_id'], op)
                ts = datetime.fromisoformat(e['started_at'])
                rel = float(e['relative_ms'])
                assert rel >= 0
                if s['first'] is None:
                    s['first'] = ts
                assert abs((ts - s['first']).total_seconds()*1000 - rel) < .002
                s['last'] = max(s['last'] or ts, ts)
                req, resp, lat = e['request_bytes'], e['response_bytes'], e['latency_ms']
                assert isinstance(req, int) and req >= 0
                assert resp is None or (isinstance(resp, int) and resp >= 0)
                if status == 'incomplete':
                    assert lat is None and e['finished_at'] is None
                else:
                    assert lat is not None and lat >= 0 and e['finished_at'] is not None
                if lat is None:
                    s['latency_missing'][m] += 1
                else:
                    s['latency_sum'][m] += lat
                s['methods'][m] += 1
                s['status'][status] += 1
                s['method_status'][m, status] += 1
                s['requests'][m] += req
                s['responses'][m] += resp or 0
                if m in ('read', 'write'):
                    assert req == int(e['args'].get('size', e['args'].get('data_len', 0)))
                    if status == 'ok':
                        assert resp is not None and resp <= req
                    s['sizes'][m][req] += 1
                s['times'][int(rel // 1000)][m] += 1
                if ts < started[task]:
                    s['pre_agent'][m] += 1
                if ts > finished[task]:
                    s['post_run'][m] += 1
            except Exception as ex:
                raise ValueError(f'Invalid event at line {line_count}: {ex}') from ex

    aggregate = {g: new_stats() for g in ('Core', 'Exploratory', 'All')}
    out_tasks = {}
    for task, s in stats.items():
        r = by_id[task]
        assert s['duplicates'] == 0, f'Duplicate operation IDs in {task}'
        assert sum(s['seen']) == s['max_id'], f'Non-contiguous IDs in {task}'
        n = sum(s['methods'].values())
        assert n == r['fuse_events'] == r['fuse_summary']['total_events']
        assert set(s['methods']) == set(r['fuse_summary']['methods'])
        for m, rec in r['fuse_summary']['methods'].items():
            for field, actual in [('count', s['methods'][m]),
                                  ('ok', s['method_status'][m, 'ok']),
                                  ('error', s['method_status'][m, 'error']),
                                  ('request_bytes', s['requests'][m]),
                                  ('response_bytes', s['responses'][m])]:
                assert rec[field] == actual, (task, m, field, rec[field], actual)
        for g in (groups[task], 'All'):
            a = aggregate[g]
            for field in ('methods', 'status', 'method_status', 'requests', 'responses',
                          'latency_missing', 'pre_agent', 'post_run'):
                a[field].update(s[field])
            for m in ('read', 'write'):
                a['sizes'][m].update(s['sizes'][m])
        rec = summarize(s)
        span = (s['last']-s['first']).total_seconds()
        # All complete/partial one-second windows from first to last request.
        bins = [sum(s['times'][i].values()) for i in range(int(span)+1)]
        top_n = max(1, math.ceil(len(bins)*.1))
        rec.update(group=groups[task], run_id=r['run_id'], repo=r['task']['repo'],
                   base_commit=r['task']['base_commit'], agent=r['agent'], model=r['model'],
                   status=r['status'], agent_exit_code=r['agent_exit_code'],
                   daemon_exit_code=r['daemon_exit_code'],
                   exploration_budget_minutes=r.get('exploration_budget_minutes'),
                   first_request_at=s['first'].isoformat(), last_request_at=s['last'].isoformat(),
                   capture_span_s=span, agent_started_at=r['agent_started_at'],
                   run_finished_at=r['finished_at'],
                   elapsed_agent_start_to_run_finish_s=(finished[task]-started[task]).total_seconds(),
                   n_one_second_bins=len(bins), active_one_second_bins=sum(x > 0 for x in bins),
                   peak_one_second_calls=max(bins), top_decile_bin_count=top_n,
                   top_decile_bin_call_pct=100*sum(sorted(bins, reverse=True)[:top_n])/n,
                   zero_call_bin_pct=100*sum(x == 0 for x in bins)/len(bins),
                   mean_calls_per_capture_second=n/span, unique_operation_ids=n)
        out_tasks[task] = rec
    assert sum(s['methods'].total() for s in stats.values()) == line_count
    result = dict(schema_version=1, total_events=line_count,
                  metadata6_methods=list(META), tasks=out_tasks,
                  groups={g: summarize(s) for g, s in aggregate.items()},
                  event_key_counts=dict(key_counts),
                  validation=dict(all_run_summary_counts_and_bytes_match=True,
                                  duplicate_operation_ids=0, operation_ids_contiguous_per_task=True,
                                  malformed_events=0, relative_time_verified=True,
                                  tasks_json_core_ids_match=True,
                                  availability_measured_runs=availability['dsh']['measured_runs'],
                                  availability_count_scope='Core only; exploratory confirmed by runs.json'),
                  definitions=dict(group_aggregation='event-weighted, no task averaging',
                                   quantile='linear interpolation at (n-1)*p over integer-byte histogram',
                                   time_bins='[i,i+1) seconds since first request; retain zero bins and final partial bin',
                                   capture_span='last request start minus first request start, per task',
                                   top_decile='ceil(0.1 * number of 1-s bins) busiest bins / all calls',
                                   request_size='request_bytes for read/write, all statuses; not response length',
                                   scope='all captured events including setup/canary and teardown'),
                  sources={})
    for name in ('events.jsonl', 'runs.json', 'tasks.json', 'agent-availability.json',
                 'README.md', 'dynamic-3d-report.md', 'collect_logfs.py'):
        p = args.source/name
        result['sources'][name] = dict(bytes=p.stat().st_size,
                                       sha256=event_hash.hexdigest() if name == 'events.jsonl' else digest(p))
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output/'summary.json').write_text(json.dumps(result, indent=2)+'\n', encoding='utf-8')
    task_fields = ['task', 'group', 'events', 'read', 'write', 'metadata6_pct', 'capture_span_s',
                   'peak_one_second_calls', 'top_decile_bin_call_pct', 'zero_call_bin_pct',
                   'agent_exit_code', 'daemon_exit_code']
    write_csv(args.output/'tasks.csv', task_fields,
              [dict(task=t, read=r['methods']['read'], write=r['methods']['write'],
                    **{k: r[k] for k in task_fields if k not in ('task', 'read', 'write')})
               for t, r in out_tasks.items()])
    write_csv(args.output/'methods.csv', ['scope', 'method', 'count', 'ok', 'error', 'incomplete',
                                        'request_bytes', 'response_bytes'],
              [dict(scope=name, method=m, count=n,
                    **{status:s['method_status'][m,status] for status in ('ok','error','incomplete')},
                    request_bytes=s['requests'][m], response_bytes=s['responses'][m])
               for name, s in list(stats.items())+list(aggregate.items())
               for m, n in sorted(s['methods'].items())])
    write_csv(args.output/'request-sizes.csv', ['scope','method','request_bytes','count'],
              [dict(scope=name, method=m, request_bytes=size, count=n)
               for name, s in list(stats.items())+list(aggregate.items()) for m in ('read','write')
               for size,n in sorted(s['sizes'][m].items())])
    write_csv(args.output/'timeseries-1s.csv', ['task','group','bin_start_s','calls','read','write','metadata6'],
              [dict(task=t, group=groups[t], bin_start_s=i, calls=sum(s['times'][i].values()),
                    read=s['times'][i]['read'], write=s['times'][i]['write'],
                    metadata6=sum(s['times'][i][m] for m in META))
               for t,s in stats.items() for i in range(out_tasks[t]['n_one_second_bins'])])
    print(json.dumps(dict(total_events=line_count, validation=result['validation'],
                          groups={g: {k:r[k] for k in ('events','metadata6_pct','read_write_ratio','size')}
                                  for g,r in result['groups'].items()}), indent=2))


if __name__ == '__main__':
    main()
