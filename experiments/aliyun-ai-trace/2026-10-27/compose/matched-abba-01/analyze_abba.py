"""Read collected evidence only; never contact cloud/WSL or execute a workload."""
import argparse
import csv
import hashlib
import json
import os
from pathlib import Path
import re

SIZES = {'fio-seqwrite-direct0': 5326766080, 'fio-seqwrite-direct1': 6337593344,
         'fio-randwrite-direct0': 5062524928, 'fio-randwrite-direct1': 3028287488}
ORDER = ['baseline', 'candidate', 'candidate', 'baseline']
BASELINE = '765bce82ad5b971c4d24fc989f5a6ca201c8c7c3638c5a7c91501a0a1e4e994a'
CANDIDATE = 'c1f6de4e5fa06873cbd2e281c697a65470ad602b9c5fc4b20b43b9fe74e3c3d2'
SOURCE = 'd609a429dfce0943114717495a76c164598df36a4564768fc346156548ea2783'
CALL = re.compile(r'^(\d+\.\d+) (\w+)\((.*)\) = (.*?) <([\d.]+)>$')
UNFINISHED = re.compile(r'^(\d+\.\d+) (\w+)\((.*)<unfinished \.\.\.>$')
RESUMED = re.compile(r'^\d+\.\d+ <\.\.\. (\w+) resumed>(.*)$')


def longpath(path):
    value = str(Path(path).resolve())
    return Path('\\\\?\\' + value) if os.name == 'nt' and not value.startswith('\\\\?\\') else Path(value)


def read(path):
    return json.loads(path.read_text(encoding='utf-8-sig'))


def sha(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def elapsed(start, finish, clock='monotonic_ns'):
    return (finish[clock] - start[clock]) / 1e9


def trace_calls(paths, target, root):
    calls = []
    for path in paths:
        pending = None
        for number, text in enumerate(path.read_text().splitlines(), 1):
            original_line = number
            resumed = RESUMED.match(text)
            if resumed:
                assert pending is not None and pending[1] == resumed.group(1), 'Unpaired resumed syscall'
                stamp, name, arguments, original_line = pending
                text = f'{stamp} {name}({arguments}{resumed.group(2)}'
                pending = None
            unfinished = UNFINISHED.match(text)
            if unfinished:
                pending = (*unfinished.groups(), number)
                continue
            matched = CALL.match(text)
            if matched:
                stamp, name, arguments, outcome, duration = matched.groups()
                if target in arguments:
                    calls.append({'name': name, 'strace_realtime_timestamp': stamp,
                                  'arguments': arguments,
                                  'duration_seconds': float(duration), 'outcome': outcome,
                                  'evidence': str(path.relative_to(root)).replace('\\', '/') + ':' + str(original_line)})
            elif target in text and any(word + '(' in text for word in ('close', 'fsync', 'fdatasync')):
                raise AssertionError('Unparsed target close/sync evidence: ' + str(path))
        assert pending is None, 'Unfinished syscall in completed evidence'
    return calls


def analyze_session(directory, version, evidence_root):
    state = read(directory / 'status.json')
    assert not state.get('fatal_error') and state['cleanup_errors'] == []
    row = state['compose'][version]
    for name in ('command', 'cleanup'):
        assert row[name]['exit_code'] == 0 and row[name]['timed_out'] is False
    assert row['remaining_project_containers'] == []
    assert state['source']['candidate_sha256'] == CANDIDATE
    assert state['source']['baseline_binaries']['brewfs'] == BASELINE
    artifact = directory / ('compose-' + version) / 'run'
    data = artifact / 'results'
    drains = list(csv.DictReader((artifact / 'post-write-drain.tsv').open(), delimiter='\t'))
    summary = list(csv.DictReader((artifact / 'perf-summary.tsv').open(), delimiter='\t'))
    assert [item['tool'] for item in drains] == list(SIZES)
    assert [item['tool'] for item in summary] == list(SIZES)
    assert all(item['status'] == 'pass' for item in summary)
    for item in drains:
        assert float(item['post_write_drain_s']) >= 0
        assert all(float(item[key]) == 0 for key in ('pending_bytes', 'dirty_bytes', 'buffer_dirty_bytes'))
    lifecycle = []
    for path in sorted((artifact / 'mount-lifecycle').glob('*.child.json')):
        child = read(path)
        assert child['phase'] == 'exited' and child['exit_code'] == 0
        unmount = read(path.with_name(path.name.replace('.child.json', '.unmount.json')))
        assert unmount['phase'] == 'finished'
        assert all(unmount[key] == 0 for key in ('exit_code', 'child_exit_code', 'last_unmount_exit_code'))
        lifecycle.append({'generation': child['generation'], 'child_exit_code': 0,
                          'normal_unmount_seconds_realtime_based': unmount['normal_unmount_seconds'],
                          'child_exit_wait_seconds_realtime_based': unmount['child_exit_wait_seconds']})
    assert lifecycle
    cases = {}
    shell_points = []
    for label, expected in SIZES.items():
        fio = read(data / (label + '.json'))
        assert len(fio['jobs']) == 1
        job = fio['jobs'][0]
        assert job['error'] == 0 and job['write']['io_bytes'] == expected
        options = job['job options']
        required = {'bs': '4m', 'size': '512m', 'numjobs': '1', 'ioengine': 'io_uring',
                    'iodepth': '1', 'io_size': str(expected), 'randseed': '354',
                    'direct': label[-1], 'rw': 'randwrite' if 'randwrite' in label else 'write'}
        assert all(str(options[key]) == value for key, value in required.items())
        assert 'time_based' not in options and 'runtime' not in options
        timing = read(data / (label + '.timing.json'))
        assert timing['exit_code'] == 0 and timing['exact_bytes_verified'] is True
        assert timing['actual_write_bytes'] == timing['expected_write_bytes'] == expected
        assert timing['fixed_seed'] == 354
        assert '--randseed=354' in timing['command'] and '--io_size=' + str(expected) in timing['command']
        assert not any(arg == '--time_based' or arg.startswith('--runtime=') for arg in timing['command'])
        mono = elapsed(timing['before_spawn'], timing['after_wait'])
        assert abs(mono - timing['wall_seconds']) < 1e-9 and mono > 0
        realtime = elapsed(timing['before_spawn'], timing['after_wait'], 'realtime_ns')
        raw = elapsed(timing['before_spawn'], timing['after_wait'], 'monotonic_raw_ns')
        markers = [json.loads(line) for line in (data / (label + '.shell-timing.jsonl')).read_text().splitlines()]
        assert [item['phase'] for item in markers] == ['tool_start', 'before_command', 'after_command', 'after_sampler', 'tool_end']
        assert all(b['monotonic_ns'] >= a['monotonic_ns'] for a, b in zip(markers, markers[1:]))
        shell = elapsed(markers[0], markers[-1])
        shell_points.append((markers[0], markers[-1]))
        traces = list(data.glob(label + '.strace.*'))
        assert traces
        calls = trace_calls(traces, '/mnt/brewfs/.perf-' + label + '/', evidence_root)
        closes = sorted((call for call in calls if call['name'] == 'close'),
                        key=lambda call: float(call['strace_realtime_timestamp']))
        expected_closes = 1 + (expected + 512 * 1048576 - 1) // (512 * 1048576)
        assert len(closes) == expected_closes and all(call['outcome'] == '0' for call in closes)
        writes = job['write']
        cases[label] = {
            'write_bytes': expected, 'seed': 354, 'fio_active_write_seconds': writes['runtime'] / 1000,
            'fio_job_runtime_seconds': job['job_runtime'] / 1000,
            'fio_reported_active_mib_s': writes['bw_bytes'] / 1048576,
            'command_wall_monotonic_seconds': mono, 'case_wall_mib_s': expected / mono / 1048576,
            'command_wall_monotonic_raw_seconds': raw, 'command_wall_realtime_seconds': realtime,
            'realtime_minus_monotonic_elapsed_seconds': realtime - mono,
            'raw_minus_monotonic_elapsed_seconds': raw - mono,
            'shell_wall_monotonic_seconds': shell, 'shell_outside_command_seconds': shell - mono,
            'command_minus_fio_active_seconds': mono - writes['runtime'] / 1000,
            'prepare_close_seconds': closes[0]['duration_seconds'],
            'final_close_seconds': closes[-1]['duration_seconds'],
            'final_close_evidence': closes[-1]['evidence'],
            'maximum_close_seconds': max(call['duration_seconds'] for call in closes),
            'maximum_close_evidence': max(closes, key=lambda call: call['duration_seconds'])['evidence'],
            'strace_close_count': len(closes),
            'target_close_calls': closes,
            'all_target_close_seconds': sum(call['duration_seconds'] for call in closes),
            'intermediate_close_seconds': sum(call['duration_seconds'] for call in closes[1:-1]),
            'target_fsync_fdatasync_calls': [call for call in calls if call['name'] in ('fsync', 'fdatasync')],
            'write_total_lat_mean_ms': writes['lat_ns']['mean'] / 1e6,
            'write_slat_mean_ms': writes['slat_ns']['mean'] / 1e6,
            'write_clat_mean_ms': writes['clat_ns']['mean'] / 1e6,
            'write_clat_percentiles_ms': {key: value / 1e6 for key, value in writes['clat_ns'].get('percentile', {}).items()},
            'total_ios_not_independent_replicates': writes['total_ios'],
            'post_write_drain': next(item for item in drains if item['tool'] == label),
            'fio_exit_code': 0, 'timing_exit_code': 0,
            'evidence': str((data / (label + '.json')).relative_to(evidence_root)).replace('\\', '/'),
        }
    return {'version': version, 'cases': cases, 'natural_mount_exits': lifecycle,
            'compose_command_wall_seconds': row['command']['wall_seconds'],
            'sum_four_command_wall_seconds': sum(case['command_wall_monotonic_seconds'] for case in cases.values()),
            'sum_four_active_write_seconds': sum(case['fio_active_write_seconds'] for case in cases.values()),
            'sum_four_final_close_seconds': sum(case['final_close_seconds'] for case in cases.values()),
            'first_tool_to_last_tool_end_monotonic_seconds': elapsed(shell_points[0][0], shell_points[-1][1]),
            'tool_window_note': 'Contains gaps between tools, but excludes the final post-tool drain and final unmount.',
            'fixed_sequence_write_bytes': sum(SIZES.values()), 'cleanup_errors': [],
            'status_sha256': sha(directory / 'status.json')}


def ranges(rows, keys):
    return {key: {'values_in_order': [row[key] for row in rows],
                  'min': min(row[key] for row in rows), 'max': max(row[key] for row in rows)} for key in keys}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--self-check-fixture', type=Path)
    parser.add_argument('--public-root', type=Path, help='Verify a published evidence subset and write reproduced/ only')
    args = parser.parse_args()
    output = longpath(Path(__file__).parent)
    if args.self_check_fixture:
        fixture = longpath(args.self_check_fixture)
        parsed = analyze_session(fixture, 'baseline', fixture)
        assert len(parsed['cases']) == 4
        # Exercise the parser on historical evidence without changing fixture files.
        record = {'passed': True, 'historical_fixture_only': True,
                  'cases_verified': list(parsed['cases']), 'natural_mount_exits': len(parsed['natural_mount_exits'])}
        (output / 'parser-self-check.json').write_text(json.dumps(record, indent=2) + '\n')
        print(json.dumps(record))
        return
    if args.public_root:
        public = longpath(args.public_root)
        publication = read(public / 'publication-manifest.json')
        for item in publication['files']:
            path = public / item['path']
            assert path.resolve().is_relative_to(public.resolve())
            assert path.stat().st_size == item['bytes'] and sha(path) == item['sha256']
        output = public / 'reproduced'
        output.mkdir(exist_ok=True)
        receipt = read(public / 'provenance.json')['original_download_receipt']
        evidence = public / 'raw'
    else:
        attempt = output.parent
        receipt = read(attempt / 'verified-download.json')
        assert sha(attempt / 'results.tar.gz') == receipt['sha256']
        assert (attempt / 'results.tar.gz').stat().st_size == receipt['bytes']
        evidence = attempt / 'retrieved'
    assert receipt['finalized'] and not receipt['preempted']
    assert receipt['checkpoint_archive_match'] and receipt['known_credential_scan'] == 'passed' and receipt['hits'] == []
    assert receipt['sequence_phase'] == 'complete'
    manifest = read(evidence / 'archive-manifest.json')
    for item in manifest['files']:
        path = evidence / item['path']
        assert path.resolve().is_relative_to(evidence.resolve())
        assert path.stat().st_size == item['bytes'] and sha(path) == item['sha256']
    sequence = read(evidence / 'results/sequence.json')
    assert sequence['phase'] == 'complete' and not sequence.get('fatal_error')
    assert sequence['order'] == ORDER and sequence['fixed_write_bytes'] == SIZES and sequence['fixed_seed'] == 354
    assert sequence['baseline_sha256'] == BASELINE and sequence['candidate_sha256'] == CANDIDATE
    assert sequence['source_archive_sha256'] == SOURCE
    assert sequence['overall_timeout'] is None and sequence['diagnostic_only'] is True
    assert len(sequence['sessions']) == 4
    assert len({row['root'] for row in sequence['sessions']}) == 4
    assert all(row['image_ids'] == sequence['sessions'][0]['image_ids'] for row in sequence['sessions'])
    sessions = []
    for index, (version, row) in enumerate(zip(ORDER, sequence['sessions']), 1):
        assert row['index'] == index and row['version'] == version and row['exit_code'] == 0
        session = analyze_session(evidence / 'results' / f'session-{index:02d}-{version}', version, evidence)
        assert session['status_sha256'] == row['verification']['status_sha256']
        session.update(index=index, session_end_to_end_monotonic_seconds=(row['after_wait_monotonic_ns'] - row['before_spawn_monotonic_ns']) / 1e9)
        sessions.append(session)
    summary = {'scope': 'One fixed-work B-C-C-B diagnostic on one host; descriptive evidence, not acceptance or population inference',
               'accepted': None, 'archive_sha256': receipt['sha256'], 'manifest_files_rehashed': len(manifest['files']),
               'source_archive_sha256': SOURCE, 'baseline_sha256': BASELINE, 'candidate_sha256': CANDIDATE,
               'image_ids': sequence['sessions'][0]['image_ids'],
               'sessions': sessions,
               'version_ranges': {}, 'limits': [
                   'Each version has two sequential sessions from one ABBA, not independent experimental batches. I/O counts are not sample size.',
                   'The command timer includes filtered strace overhead. Close duration is strace -T itself; it does not isolate internal FUSE flush from other close work.',
                   'No write/io_uring calls were traced, so the exact last-write boundary is unavailable. Command minus fio-active duration is not an isolated flush measurement.',
                   'Realtime, monotonic and monotonic_raw are reported separately. No epoch/monotonic cross-source subtraction is used.',
                   'fio total lat mean and clat percentiles are distinct measures. clat percentiles are not total latency percentiles.',
                   'The fixed sequence has the same bytes and seed in each session. Session end-to-end includes fresh backend preparation and cleanup; four-command sums exclude those costs.',
                   'io_size exceeds the 512 MiB working set, so fio reopens the file across passes. All close calls are retained separately; their duration sum must not be added again to active or command time.',
                   'Earlier broad compose and CI evidence remain separate; this one ABBA cannot alone establish general performance or acceptance.'],
               'whole_abba_host_monotonic_span_seconds': (sequence['sessions'][-1]['after_wait_monotonic_ns'] - sequence['sessions'][0]['before_spawn_monotonic_ns']) / 1e9}
    case_keys = ['fio_active_write_seconds', 'command_wall_monotonic_seconds', 'case_wall_mib_s', 'final_close_seconds', 'all_target_close_seconds', 'write_total_lat_mean_ms']
    for version in ('baseline', 'candidate'):
        rows = [row for row in sessions if row['version'] == version]
        summary['version_ranges'][version] = {'sessions': ranges(rows, ['session_end_to_end_monotonic_seconds', 'compose_command_wall_seconds', 'sum_four_command_wall_seconds', 'sum_four_final_close_seconds']),
            'cases': {label: ranges([row['cases'][label] for row in rows], case_keys) for label in SIZES}}
    (output / 'summary.json').write_text(json.dumps(summary, indent=2) + '\n')
    columns = ['session', 'version', 'case', *case_keys, 'write_bytes', 'seed']
    with (output / 'cases.csv').open('w', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=columns)
        writer.writeheader()
        for session in sessions:
            for label, case in session['cases'].items():
                writer.writerow({'session': session['index'], 'version': session['version'], 'case': label,
                                 **{key: case[key] for key in columns if key in case}})
    print(json.dumps({'parsed_sessions': len(sessions), 'parsed_cases': 16, 'accepted': None,
                      'whole_abba_host_monotonic_span_seconds': summary['whole_abba_host_monotonic_span_seconds']}))


if __name__ == '__main__':
    main()
