"""Check whether two read passes make a nonempty Packed lower working set hot."""
import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import time

from .runtime.common import load, mounts, pinned, private_dir, write_new

KEYS = dict(
    range_requests='brewfs_packed_v3_runtime_backend_range_gets_total',
    received_bytes='brewfs_packed_v3_runtime_backend_received_bytes_total',
    failures='brewfs_packed_v3_runtime_backend_failures_total',
    logical_bytes='brewfs_packed_v3_logical_bytes_total',
)
MAX_STATS_BYTES = 4 * 1024**2


def parse(raw):
    counters = {}
    names = {v:k for k,v in KEYS.items()}
    for line in raw.decode('utf-8').splitlines():
        fields = line.split()
        if not fields or fields[0] not in names:
            continue
        key = names[fields[0]]
        if key in counters or len(fields) != 2 or not re.fullmatch(r'[0-9]{1,20}',fields[1]):
            raise ValueError('Duplicate or malformed native counter: '+fields[0])
        counters[key] = int(fields[1])
    if counters.keys() != KEYS.keys():
        raise ValueError('Missing Packed wire005 native counters')
    return counters


def assess(rows, expected_bytes):
    if len(rows) != 3 or type(expected_bytes) is not int or expected_bytes <= 0:
        raise ValueError('Three snapshots and a nonempty working set required')
    changes = [{k:b[k]-a[k] for k in KEYS} for a,b in zip(rows,rows[1:])]
    first, warm = changes
    reasons = []
    if any(v < 0 for change in changes for v in change.values()):
        reasons.append('native counter reset')
    if any(change['failures'] != 0 for change in changes):
        reasons.append('native backend reported a failure')
    if any(change['logical_bytes'] != expected_bytes for change in changes):
        reasons.append('Packed logical bytes do not match the read working set on both passes')
    if first['range_requests'] <= 0 or first['received_bytes'] <= 0:
        reasons.append('cold pass did not show native backend reads')
    if warm['range_requests'] != 0 or warm['received_bytes'] != 0:
        reasons.append('second pass still reads the Packed lower backend')
    return dict(passed=not reasons,reasons=reasons,cold=first,second_pass=warm)


def check_ranges(rows):
    if not isinstance(rows,list) or not rows:
        raise ValueError('Nonempty frozen read ranges required')
    keys = set()
    for row in rows:
        if set(row) != {'path','offset','length','sha256'}:
            raise ValueError('Exact read range fields required')
        path = PurePosixPath(row['path'])
        if (path.is_absolute() or str(path) != row['path'] or not path.parts or
                any(p in ('..','.stats') for p in path.parts) or '\\' in row['path']):
            raise ValueError('Canonical ordinary relative lower path required')
        off, length = row['offset'],row['length']
        if (type(off) is not int or off < 0 or type(length) is not int or length <= 0 or
                off+length >= 2**63 or not re.fullmatch('[0-9a-f]{64}',row['sha256'])):
            raise ValueError('Invalid frozen read range')
        key = (path,off,length)
        if key in keys: raise ValueError('Duplicate warmup range')
        keys.add(key)


def read_range(root, row, *, timed=False):
    parts = PurePosixPath(row['path']).parts
    parent = os.dup(root)
    try:
        for component in parts[:-1]:
            child = os.open(component,os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,dir_fd=parent)
            os.close(parent)
            parent = child
        fd = os.open(parts[-1],os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,dir_fd=parent)
        try:
            st = os.fstat(fd)
            offset, remaining = row['offset'],row['length']
            if not stat.S_ISREG(st.st_mode) or offset+remaining > st.st_size:
                raise ValueError('Expected a regular file and complete read range')
            sha = hashlib.sha256()
            pread_ns = 0
            while remaining:
                count = min(remaining,1024**2)
                if timed:
                    started = time.monotonic_ns()
                    data = os.pread(fd,count,offset)
                    pread_ns += time.monotonic_ns() - started
                else:
                    data = os.pread(fd,count,offset)
                if not data: raise ValueError('Unexpected EOF in lower working set')
                sha.update(data); remaining -= len(data); offset += len(data)
            if sha.hexdigest() != row['sha256']: raise ValueError('Lower range SHA differs')
            if timed: return pread_ns
        finally:
            os.close(fd)
    finally:
        os.close(parent)


def probe(point, rows, output):
    """Read only; do not remount, start a model or release a timed workload."""
    check_ranges(rows)
    point, output = Path(point),Path(output)
    if (not point.is_absolute() or point == point.parent or point.resolve() != point or
            not output.is_absolute() or output.resolve() != output or output.is_relative_to(point)):
        raise ValueError('Canonical mount and separate output paths required')
    observed = mounts(point)
    if len(observed) != 1 or not observed[0]['filesystem'].startswith('fuse'):
        raise ValueError('One live FUSE mount required')
    out = private_dir(output)
    write_new(out/'working-set.json',rows)
    root = os.open(point,os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        def sample(label):
            if mounts(point) != observed: raise ValueError('Mount changed during warmup')
            fd = os.open('.stats',os.O_RDONLY | os.O_NOFOLLOW,dir_fd=root)
            with os.fdopen(fd,'rb') as stream: raw = stream.read(MAX_STATS_BYTES+1)
            if len(raw) > MAX_STATS_BYTES: raise ValueError('Native stats exceed bounded reader')
            with (out/(label+'.stats')).open('xb') as stream: stream.write(raw)
            return parse(raw)

        snapshots = [sample('before')]
        counts = []
        for label in ('pass-1','pass-2'):
            started = time.monotonic_ns()
            for row in rows: read_range(root,row)
            counts.append(dict(start_ns=started,end_ns=time.monotonic_ns(),ranges=len(rows)))
            snapshots.append(sample(label))
        expected = sum(row['length'] for row in rows)
        result = dict(schema='packed-warm-lower-probe-v1',mount=observed[0],passes=counts,
                      expected_read_bytes=expected,counters=snapshots,
                      **assess(snapshots,expected),
                      scope='Single Packed mount; cache qualification only, not an Overlay measurement')
        write_new(out/'result.json',result)
        return result
    finally:
        os.close(root)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    for name in ('mount','working-set','working-set-sha256','out'):
        p.add_argument('--'+name,required=True)
    a = p.parse_args()
    rows = load(pinned(dict(path=a.working_set,sha256=a.working_set_sha256)))
    result = probe(a.mount,rows,a.out)
    print(json.dumps(result))
    return 0 if result['passed'] else 1


if __name__=='__main__':
    raise SystemExit(main())
