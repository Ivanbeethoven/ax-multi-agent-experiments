"""Pinned runtime inputs and Linux observations; no inferred readiness."""
import json
import os
from pathlib import Path
import re
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from experiment_io import digest, load, write_new
from experiment_step import prepare


def pinned(ref):
    p = Path(ref['path'])
    if not p.is_absolute() or p.is_symlink() or digest(p) != ref['sha256']:
        raise ValueError('changed or non-absolute pinned file')
    return p


def spec(ref, schema):
    value = load(pinned(ref))
    if value.get('schema') != schema or value.get('reviewed') is not True:
        raise ValueError('reviewed ' + schema + ' required')
    return value


def private_dir(path):
    p = Path(path)
    if not p.is_absolute() or p.exists() or p.is_symlink():
        raise ValueError('new absolute output directory required')
    p.mkdir(parents=True, mode=0o700)
    p.chmod(0o700)
    return p


def identity(pid):
    """PID + boot + start tick prevents PID reuse being mistaken for ownership."""
    try:
        raw = Path(f'/proc/{pid}/stat').read_text()
        fields = raw[raw.rindex(')')+2:].split()
        return {'pid': int(pid), 'start_ticks': int(fields[19]),
                'boot_id': Path('/proc/sys/kernel/random/boot_id').read_text().strip(),
                'state': fields[0]}
    except FileNotFoundError:
        return None


def alive(expected):
    now = identity(expected['pid'])
    return now is not None and now['state'] != 'Z' and all(
        now[k] == expected[k] for k in ('pid', 'boot_id', 'start_ticks'))


def mounts(path, text=None):
    """Exact mount point, including stacked mounts; never probe through FUSE."""
    text = Path('/proc/self/mountinfo').read_text() if text is None else text
    result = []
    for line in text.splitlines():
        left, right = line.split(' - ', 1)
        cols, tail = left.split(), right.split()
        point = re.sub(r'\\([0-7]{3})', lambda m: chr(int(m[1], 8)), cols[4])
        if point == str(path):
            result.append({'mount_id': int(cols[0]), 'point': point,
                           'filesystem': tail[0], 'source': tail[1], 'options': cols[5]})
    return result


def cgroup_limits(expected):
    """Read the current cgroup v2 and ancestors, not a declared Docker budget."""
    relative = [x[3:] for x in Path('/proc/self/cgroup').read_text().splitlines() if x.startswith('0::')]
    if len(relative) != 1:
        raise ValueError('cgroup v2 required')
    current = Path('/sys/fs/cgroup') / relative[0].lstrip('/')
    # In a private cgroup namespace /proc may name an inaccessible ancestor.
    if not (current/'cpu.max').exists():
        current = Path('/sys/fs/cgroup')
    cpu, memory, swap = [], [], []
    observed = []
    while current.is_relative_to('/sys/fs/cgroup'):
        if (current/'cpu.max').exists():
            quota, period = (current/'cpu.max').read_text().split()
            mem = (current/'memory.max').read_text().strip()
            sw = (current/'memory.swap.max').read_text().strip()
            observed.append({'path': str(current), 'cpu.max': f'{quota} {period}', 'memory.max': mem, 'memory.swap.max': sw})
            if quota != 'max': cpu.append(int(quota)/int(period))
            if mem != 'max': memory.append(int(mem))
            if sw != 'max': swap.append(int(sw))
        if current == Path('/sys/fs/cgroup'): break
        current = current.parent
    if not cpu or not memory or not swap:
        raise ValueError('finite CPU, memory and swap limits required')
    actual = {'cpus': min(cpu), 'memory_bytes': min(memory), 'swap_bytes': min(swap)}
    if actual != expected:
        raise ValueError('effective cgroup budget differs from reviewed budget')
    return {'effective': actual, 'observed': observed}


def isolation_identity():
    return {'boot_id': Path('/proc/sys/kernel/random/boot_id').read_text().strip(),
            'cgroup_namespace': os.readlink('/proc/self/ns/cgroup'),
            'cgroup_membership': Path('/proc/self/cgroup').read_text().strip()}


def emit(kind, **value):
    print(json.dumps({'type': kind, **value}, separators=(',', ':')), flush=True)


def result_check(path, expected):
    value = load(path)
    if not expected:
        raise ValueError('explicit result predicates required')
    for key, wanted in expected.items():
        current = value
        for part in key.split('.'):
            current = current[part]
        if type(current) is not type(wanted) or current != wanted:
            raise ValueError('result differs: ' + key)
    return value
