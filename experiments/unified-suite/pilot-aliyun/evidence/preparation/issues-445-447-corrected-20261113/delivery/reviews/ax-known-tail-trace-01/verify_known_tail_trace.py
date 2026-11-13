#!/usr/bin/env python3
"""Fail-closed actual FUSE evidence for the non-timed known-tail oracle v4."""
import argparse
import datetime
import functools
import hashlib
import json
from pathlib import Path
import re
import struct
import sys

FILE_BYTES = 64 << 20
TAIL_BYTES = 4 << 20
TIME_RESOLUTION_NS = 1000
REQUIRED_MASK = 0x343
STAMP = re.compile(r'^(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d)(?:\.(\d{1,9}))?Z ')
REQUEST = re.compile(r'ID: (\d+) \| \[(open|getattr|read|release)\] REQ Request \{ unique: (\d+),.*?pid: (\d+) \} - Call_arg: (.*)$')
REPLY = re.compile(r'ID: (\d+) \| \[(open|getattr|read|release)\] - (Success|Error): (.*)$')
READ_REPLY = re.compile(r'ID: (\d+) \[.*\] read - Read (\d+) bytes$')


def require(value, message):
    if not value:
        raise ValueError(message)


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def timestamp(line):
    match = STAMP.match(line)
    require(match is not None, 'missing UTC timestamp on relevant FUSE log record')
    seconds = int(datetime.datetime.fromisoformat(match[1]).replace(tzinfo=datetime.timezone.utc).timestamp())
    return seconds * 1_000_000_000 + int((match[2] or '').ljust(9, '0'))


def parse_trace(path):
    events, pending, orphan_replies = [], {}, []
    for number, line in enumerate(path.read_text().splitlines(), 1):
        request = REQUEST.search(line)
        if request:
            ident, operation, unique, pid, args = request.groups()
            event = {'id': int(ident), 'operation': operation, 'unique': int(unique),
                     'pid': int(pid), 'request_line': number, 'request_ns': timestamp(line),
                     'arguments': {k: int(v) for k, v in re.findall(r'\b(inode|fh|offset|size|flags)=(\d+)', args)}}
            key = (ident, operation)
            # Consume replies in physical log order. IDs may restart after a mount.
            pending.setdefault(key, []).append(event)
            events.append(event)
            continue
        read = READ_REPLY.search(line)
        reply = REPLY.search(line)
        if read:
            ident, returned = read.groups()
            operation, successful, payload = 'read', True, {'returned': int(returned)}
        elif reply:
            ident, operation, result, body = reply.groups()
            successful, payload = result == 'Success', {'body': body}
            if successful and operation == 'open':
                match = re.search(r'\bfh: (\d+)', body)
                if match:
                    payload['fh'] = int(match[1])
            if successful and operation == 'getattr':
                identity = re.search(r'FileAttr \{ ino: (\d+), size: (\d+),', body)
                mtime = re.search(r'mtime: Timestamp \{ sec: (-?\d+), nsec: (\d+) \}', body)
                if identity and mtime:
                    require(0 <= int(mtime[2]) < 1_000_000_000, 'invalid reply mtime nanoseconds')
                    payload['stat'] = {'ino': int(identity[1]), 'size': int(identity[2]),
                                       'mtime_ns': int(mtime[1]) * 1_000_000_000 + int(mtime[2])}
        else:
            continue
        queue = pending.get((ident, operation), [])
        response = {'response_line': number, 'response_ns': timestamp(line),
                    'successful': successful, **payload}
        if queue:
            event = queue.pop(0)
            event.update(response)
        else:
            orphan_replies.append({'id': int(ident), 'operation': operation, **response})
    return events, orphan_replies


@functools.lru_cache(maxsize=2)
def expected_sha(value):
    state = hashlib.sha256()
    for _ in range((FILE_BYTES - TAIL_BYTES) >> 20):
        state.update(bytes(1 << 20))
    for _ in range(TAIL_BYTES >> 20):
        state.update(bytes([value]) * (1 << 20))
    return state.hexdigest()


def inside(value, start, end):
    return start - TIME_RESOLUTION_NS <= value <= end + TIME_RESOLUTION_NS


def completed(event):
    return event.get('successful') is True and event.get('response_line', 0) > event['request_line'] and event['response_ns'] >= event['request_ns']


def verify_read(label, data, phase, value, events):
    require(data.get('passed') is True, label + ': helper read did not pass')
    require(data.get('failure_conditions') == [], label + ': helper reports failure conditions')
    require(data.get('mismatches') == [] and data.get('first_mismatch') is None, label + ': content mismatch')
    require(data.get('changed_identity_fields') == [], label + ': identity changed')
    require(data.get('bytes') == FILE_BYTES and data.get('expected_bytes') == FILE_BYTES and data.get('eof_observed') is True, label + ': incomplete read or missing EOF')
    require(data.get('tail_byte') == value and data.get('sha256') == data.get('expected_sha256') == expected_sha(value), label + ': known-value SHA differs')
    before, after = data['before'], data['after']
    require(all(before[k] == after[k] for k in ('ino', 'size', 'mtime_ns')), label + ': original identity gate failed')
    ino, pid = before['ino'], data['reader_pid']
    require(ino > 0 and pid > 0 and data['inode'] == ino and before['size'] == FILE_BYTES, label + ': invalid inode/PID/size')
    pre, post = data['statx_before'], data['statx_after']
    require(pre.get('fd') == post.get('fd') and isinstance(pre.get('fd'), int) and pre['fd'] >= 0, label + ': statx did not use one file descriptor')
    boundaries = [data['opened_unix_ns'], pre['started_unix_ns'], pre['finished_unix_ns'],
                  data['first_read_started_unix_ns'], data['last_read_finished_unix_ns'],
                  post['started_unix_ns'], post['finished_unix_ns']]
    require(boundaries == sorted(boundaries), label + ': helper phase boundaries are unordered')
    selected_getattrs = []
    for stage, fresh, ordinary in [('before', pre, before), ('after', post, after)]:
        require(fresh.get('passed') is True and fresh.get('return_code') == 0 and fresh.get('errno') == 0, label + ': statx ' + stage + ' failed')
        require(fresh.get('flags') == 0x3000 and fresh.get('path') == '' and fresh.get('requested_mask') == 0x7ff and fresh.get('required_mask') == REQUIRED_MASK, label + ': wrong statx flags/mask/path')
        require(fresh.get('missing_required_mask') == 0 and fresh['values']['stx_mask'] & REQUIRED_MASK == REQUIRED_MASK, label + ': missing statx result fields')
        raw = bytes.fromhex(fresh['raw_struct_hex'])
        require(len(raw) == 256, label + ': missing full statx structure')
        require(struct.unpack_from('<I', raw, 0)[0] == fresh['values']['stx_mask'], label + ': raw statx mask differs')
        require(struct.unpack_from('<Q', raw, 32)[0] == ordinary['ino']
                and struct.unpack_from('<Q', raw, 40)[0] == ordinary['size']
                and struct.unpack_from('<H', raw, 28)[0] == ordinary['mode'], label + ': raw statx identity differs')
        sec, nsec = struct.unpack_from('<qI', raw, 112)
        require(0 <= nsec < 1_000_000_000 and sec * 1_000_000_000 + nsec == ordinary['mtime_ns'], label + ': raw statx mtime differs')
        require(data.get('statx_' + stage + '_fstat_mismatches') == [], label + ': statx/fstat mismatch')
        require(all(fresh['stat'][k] == ordinary[k] for k in ('ino', 'size', 'mtime_ns', 'mode', 'dev')), label + ': statx/fstat values differ')
        matches = [event for event in events if event['operation'] == 'getattr' and event['pid'] == pid
                   and event['arguments'].get('inode') == ino
                   and inside(event['request_ns'], fresh['started_unix_ns'], fresh['finished_unix_ns'])]
        require(len(matches) == 1, label + ': statx ' + stage + ' lacks one unambiguous FUSE GETATTR')
        event = matches[0]
        require(completed(event) and inside(event['response_ns'], fresh['started_unix_ns'], fresh['finished_unix_ns']), label + ': GETATTR did not succeed inside statx ' + stage)
        require(event.get('stat') == {k: ordinary[k] for k in ('ino', 'size', 'mtime_ns')}, label + ': GETATTR reply differs from statx/fstat ' + stage)
        selected_getattrs.append(event)
    opens = [event for event in events if event['operation'] == 'open' and event['pid'] == pid
             and event['arguments'].get('inode') == ino and completed(event) and 'fh' in event
             and event['request_ns'] >= phase['started_unix_ns'] - TIME_RESOLUTION_NS
             and event['response_ns'] <= data['opened_unix_ns'] + TIME_RESOLUTION_NS
             and event['response_line'] < selected_getattrs[0]['request_line']]
    require(opens, label + ': matching successful OPEN missing')
    opened = max(opens, key=lambda event: event['response_line'])
    fh = opened['fh']
    reads = [event for event in events if event['operation'] == 'read' and event['pid'] == pid
             and event['arguments'].get('inode') == ino
             and selected_getattrs[0]['response_line'] < event['request_line'] < selected_getattrs[1]['request_line']]
    require(reads, label + ': no FUSE READs between forced GETATTRs')
    cursor, eof = 0, False
    for index, event in enumerate(reads):
        require(event['arguments'].get('fh') == fh, label + ': READ belongs to another file handle')
        require(completed(event) and 'returned' in event, label + ': READ missing successful response')
        require(inside(event['request_ns'], data['first_read_started_unix_ns'], data['last_read_finished_unix_ns'])
                and inside(event['response_ns'], data['first_read_started_unix_ns'], data['last_read_finished_unix_ns']), label + ': READ outside recorded data-read window')
        require(event['arguments'].get('offset') == cursor, label + ': gap/overlap in READ offsets')
        returned = event['returned']
        require(0 <= returned <= event['arguments'].get('size', -1), label + ': invalid returned byte count')
        if returned == 0:
            require(index == len(reads) - 1 and cursor == FILE_BYTES, label + ': early/multiple EOF')
            eof = True
        else:
            require(not eof, label + ': data after EOF')
            cursor += returned
            require(cursor <= FILE_BYTES, label + ': READ exceeds full file')
    require(cursor == FILE_BYTES and eof, label + ': FUSE trace lacks full read plus EOF')
    pre_event, post_event = selected_getattrs
    require(opened['response_line'] < pre_event['request_line'] < pre_event['response_line'] < reads[0]['request_line'], label + ': OPEN/GETATTR/READ order invalid')
    require(reads[-1]['response_line'] < post_event['request_line'] < post_event['response_line'], label + ': EOF/post-GETATTR order invalid')
    releases = [event for event in events if event['operation'] == 'release'
                and event['arguments'].get('inode') == ino and event['arguments'].get('fh') == fh
                and opened['response_line'] < event['request_line'] < post_event['response_line']]
    require(not releases, label + ': selected handle was released before the read proof finished')
    return {'label': label, 'passed': True, 'reader_pid': pid, 'inode': ino, 'fh': fh,
            'open': opened, 'getattr_before': pre_event, 'reads': reads, 'getattr_after': post_event,
            'returned_bytes': cursor, 'eof': eof, 'known_sha256': expected_sha(value)}


def assess(artifact):
    result = {'passed': False, 'reasons': [], 'checks': [], 'artifact': str(artifact),
              'artifact_dir': str(artifact), 'artifact_realpath': str(artifact.resolve()),
              'verifier_sha256': digest(Path(__file__)),
              'scope': 'Actual FUSE trace gate for non-performance known-tail oracle v4; no performance claim',
              'timestamp_tolerance_ns': TIME_RESOLUTION_NS,
              'tolerance_scope': 'Log timestamps have microsecond precision; tolerance applies only to recorded syscall time windows. Physical log order, values and byte counts are exact.'}
    try:
        require(artifact.is_dir(), 'artifact directory missing')
        log = artifact / 'brewfs_fuse_ops.log'
        result['trace_sha256'] = digest(log)
        result['op_log_sha256'] = result['trace_sha256']
        result['inputs'] = {str(log): {'sha256': digest(log), 'bytes': log.stat().st_size}}
        events, orphans = parse_trace(log)
        result['parsed_events'] = len(events)
        result['orphan_replies'] = orphans
        check_a = artifact / 'known-tail-oracle/check-a.json'
        verify_b = artifact / 'known-tail-oracle/verify-b.json'
        sources = [('check-a.value', check_a, 'value', 0x41),
                   ('verify-b.warm_value', verify_b, 'warm_value', 0x42),
                   ('verify-b.same_cache_value', verify_b, 'same_cache_value', 0x42),
                   ('verify-b.fresh_cache_value', verify_b, 'fresh_cache_value', 0x42)]
        result['phase_sha256'] = {}
        result['phase_json_sha256'] = result['phase_sha256']
        for label, path, key, value in sources:
            require(path.is_file(), label + ': phase JSON missing')
            phase = json.loads(path.read_text())
            result['phase_sha256'][str(path.relative_to(artifact))] = digest(path)
            result['inputs'][str(path)] = {'sha256': digest(path), 'bytes': path.stat().st_size}
            require(phase.get('passed') is True and phase.get('reasons') == [], label + ': oracle phase did not pass')
            require(key in phase, label + ': read result missing')
            proof = verify_read(label, phase[key], phase, value, events)
            if result['checks']:
                require(result['checks'][-1]['getattr_after']['response_line'] < proof['open']['request_line'], label + ': read proofs overlap or reuse earlier events')
            result['checks'].append(proof)
        require(len(result['checks']) == 4, 'not all four oracle reads qualified')
        result['passed'] = True
    except (OSError, ValueError, KeyError, TypeError) as error:
        result['reasons'].append(type(error).__name__ + ': ' + str(error))
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('artifact_dir', type=Path)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    result = assess(args.artifact_dir)
    serialized = json.dumps(result, indent=2) + '\n'
    if args.output is not None:
        with args.output.open('x') as stream:
            stream.write(serialized)
    print(serialized, end='')
    return 0 if result['passed'] else 1


if __name__ == '__main__':
    sys.exit(main())
