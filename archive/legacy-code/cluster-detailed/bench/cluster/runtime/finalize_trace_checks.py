"""Summarize completed trace checks from stored replay reports and measured timings."""
import argparse
import json
import re
import statistics

import cluster_lib as cl


def build_report(timings, documents):
    expected = [(1, 'on'), (1, 'off'), (2, 'off'), (2, 'on'), (3, 'on'), (3, 'off')]
    if [(p, mode) for p, mode, *_ in timings] != expected:
        raise ValueError('Expected three complete ON/OFF pairs in execution order')
    arms = {}
    for pair, mode, seconds, active_logged, p99_logged in timings:
        tasks = [documents[f'cal-p{pair}-{mode}-m{idx}-replay.json'] for idx in range(8)]
        for task in tasks:
            if not (task.get('completed') and task.get('mode') == 'performance'
                    and task.get('operations') == 94130 and task.get('remaining_handles') == 0
                    and task.get('think_time', {}).get('all_boundaries_applied')):
                raise ValueError('Calibration replay did not complete successfully')
        hist = cl.merge_log2_histograms([task['latency_us']['all'] for task in tasks])
        active = statistics.mean(task['active_replay_seconds'] for task in tasks)
        if abs(active - active_logged) > 0.000501 or hist['p99_us'] != p99_logged:
            raise ValueError('Replay reports and logged timings refer to different measurements')
        arms[pair, mode] = {
            'collector_on': mode == 'on', 'makespan_seconds': seconds,
            'makespan_resolution_seconds': 0.001,
            'mean_active_replay_seconds': active,
            'mean_fs_operation_seconds': statistics.mean(t['fs_operation_seconds'] for t in tasks),
            'merged_op_p50_us': hist['p50_us'], 'merged_op_p95_us': hist['p95_us'],
            'merged_op_p99_us': hist['p99_us'], 'total_operations': hist['count'],
            'task_results': tasks,
        }
    pairs = []
    for pair in range(1, 4):
        on, off = arms[pair, 'on'], arms[pair, 'off']
        pairs.append({'pair': pair, 'order': ['on', 'off'] if pair % 2 else ['off', 'on'],
                      'on': on, 'off': off,
                      'relative_makespan_diff': (on['makespan_seconds'] - off['makespan_seconds']) / off['makespan_seconds'],
                      'relative_active_replay_diff': (on['mean_active_replay_seconds'] - off['mean_active_replay_seconds']) / off['mean_active_replay_seconds'],
                      'relative_p99_diff': (on['merged_op_p99_us'] - off['merged_op_p99_us']) / max(1, off['merged_op_p99_us'])})
    makespan = statistics.median(p['relative_makespan_diff'] for p in pairs)
    p99 = statistics.median(p['relative_p99_diff'] for p in pairs)
    return {'schema': 'ax-collector-overhead-calibration-v1', 'mounts': 8, 'pairs': pairs,
            'median_relative_makespan_diff': makespan, 'median_relative_p99_diff': p99,
            'threshold': 0.05, 'passed': abs(makespan) <= 0.05 and p99 <= 0.05,
            'makespan_source': 'trace-and-collector-checks.log',
            'makespan_resolution_seconds': 0.001, 'recorded_at': cl.utc_iso()}


def collect_reports():
    root = cl.RUN_ROOT
    log = (root / 'trace-and-collector-checks.log').read_text()
    current = log.rsplit('=== [2/3]', 1)[-1]
    pattern = r'Pair (\d) collector=(ON|OFF): makespan=([\d.]+)s active=([\d.]+)s p99=([\d.]+)us'
    timings = [(int(p), mode.lower(), float(seconds), float(active), float(p99))
               for p, mode, seconds, active, p99 in re.findall(pattern, current)]
    documents = {}
    for host in cl.ALL_HOST_NAMES:
        path = str(root / 'hosts' / host)
        command = ('import json,pathlib; p=pathlib.Path(' + repr(path) + '); '
                   "files=list(p.glob('cal-p[1-3]-*-m*-replay.json'))+list(p.glob('smoke-tr?-ver.json')); "
                   'print(json.dumps({f.name:json.loads(f.read_text()) for f in files}))')
        result = cl.run_on_host(host, ['python3', '-c', command], log_cmd=False)
        found = json.loads(result.stdout)
        if documents.keys() & found.keys():
            raise ValueError('Duplicate calibration replay filenames across hosts')
        documents.update(found)
    report = build_report(timings, documents)
    commands = [json.loads(line) for line in (root / 'commands.jsonl').read_text().splitlines()]
    stopped = set()
    for command in commands:
        argv = command['argv']
        if 'stop' in argv and '--id' in argv and command['returncode'] == 0:
            stopped.add(argv[argv.index('--id') + 1])
    for pair, mode, *_ in timings:
        for idx in range(8):
            if f'cal-p{pair}-{mode}-m{idx}' not in stopped:
                raise ValueError('Missing successful calibration unmount command')
    single = {}
    for engine, name, short in [('brewfs-native-overlay', 'brew', 'b'),
                                ('juicefs', 'jfs', 'j'), ('juicefs-overlay', 'jov', 'o')]:
        replay = json.loads((root / 'mounts/smoke' / f'replay-{name}.json').read_text())
        verify = documents[f'smoke-tr{short}-ver.json']
        if not (replay.get('completed') and replay.get('mode') == 'performance'
                and replay.get('operations') == 94130 and replay.get('remaining_handles') == 0
                and verify.get('passed')):
            raise ValueError('Single-task replay or cross-host read check did not pass')
        single[engine] = {'replay': replay, 'verify': verify}
    return report, single


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check-only', action='store_true')
    args = parser.parse_args()
    report, single = collect_reports()
    print(json.dumps({key: report[key] for key in
                      ['passed', 'median_relative_makespan_diff', 'median_relative_p99_diff']}), flush=True)
    if not report['passed']:
        raise RuntimeError('Collector overhead exceeds the planned threshold')
    if args.check_only:
        return
    flush = {host: cl.collector_rpc(host, {'action': 'flush'}) for host in cl.ALL_HOST_NAMES}
    summary = {'schema': 'ax-smoke-verification-v1', 'recorded_at': cl.utc_iso(),
               'thousand_file_smoke': json.loads((cl.RUN_ROOT / 'mounts/file-checks.json').read_text()),
               'single_task_trace_smoke': single, 'collector_flush': flush}
    cleanup = {'state': 'pending', 'buckets': ['axd1-smoke-tr-brew', 'axd1-smoke-tr-jfs', 'axd1-smoke-tr-jov'],
               'redis_db0_patterns': ['*axd1_smoke_tr_brew*', '{jfs-fork:axd1-smoke-tr-jov}:*'],
               'redis_databases': [14], 'recorded_at': cl.utc_iso()}
    for relative, value in [('hosts/collector-overhead-8mounts.json', report),
                            ('mounts/smoke-verification.json', summary),
                            ('mounts/smoke/trace-checks.json', single),
                            ('mounts/smoke/trace-cleanup.json', cleanup)]:
        path = cl.RUN_ROOT / relative
        temporary = path.with_suffix('.json.tmp')
        temporary.write_text(json.dumps(value, indent=2) + '\n')
        temporary.replace(path)
    print('Trace checks and collector measurements saved', flush=True)


if __name__ == '__main__':
    main()
