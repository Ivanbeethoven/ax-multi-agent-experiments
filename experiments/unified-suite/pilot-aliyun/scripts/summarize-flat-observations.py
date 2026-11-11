#!/usr/bin/env python3
"""Derive phase counter deltas from an accepted flat cell's retained observations."""
import argparse
from decimal import Decimal, InvalidOperation
import hashlib
import json
from pathlib import Path


def read(path):
    data = path.read_bytes()
    return data, dict(path=str(path), bytes=len(data), sha256=hashlib.sha256(data).hexdigest())


def document(path):
    data, ref = read(path)
    return json.loads(data), ref


def delta(before, after):
    values = {}
    for key in sorted(before.keys() | after.keys()):
        if key not in before or key not in after:
            values[key] = dict(delta=None, status='missing-start' if key not in before else 'missing-end')
            continue
        try:
            a, b = Decimal(str(before[key])), Decimal(str(after[key]))
        except InvalidOperation as exc:
            raise ValueError(f'Invalid counter: {key}') from exc
        if not (a.is_finite() and b.is_finite()) or a < 0 or b < a:
            raise ValueError(f'Negative, nonfinite or reset counter: {key}')
        d = b - a
        values[key] = dict(delta=int(d) if d == d.to_integral_value() else str(d), status='measured')
    return values


def diskstats(raw):
    rows = {}
    for line in raw.decode().splitlines():
        columns = line.split()
        if len(columns) < 14:
            raise ValueError('Malformed Linux diskstats row')
        major, minor, name = columns[:3]
        key = f'{major}:{minor} {name}'
        if key in rows:
            raise ValueError('Duplicate block device')
        rows[key] = dict(read_ios=int(columns[3]), write_ios=int(columns[7]),
                         read_bytes=int(columns[5]) * 512, write_bytes=int(columns[9]) * 512)
    if not rows:
        raise ValueError('Empty diskstats')
    return rows


def resolve_mark(directory, mark):
    path = directory / f"sample-{mark['sample_index']:06}" / 'sample.json'
    sample, pin = document(path)
    if sample.get('passed') is not True or sample['index'] != mark['sample_index']:
        raise ValueError(f'Invalid marked sample: {path}')
    path_disk = path.parent / 'host-diskstats.txt'
    raw, pin_disk = read(path_disk)
    if {k: pin_disk[k] for k in ('bytes','sha256')} != sample['files'][path_disk.name]:
        raise ValueError(f'Raw diskstats changed: {path_disk}')
    return sample, dict(sample=pin, diskstats=pin_disk), diskstats(raw)


def dotted(value, path):
    for key in path.split('.'):
        value = value[key]
    return value


def report(cell, close):
    result, result_pin = document(cell / 'sample.json')
    closure, closure_pin = document(close)
    if (result['schema'] != 'suite-sample-v1' or not result['uninterrupted'] or result['recovered']
        or closure['schema'] != 'suite-cell-close-v1' or closure['outcome'] != 'complete'
        or closure['cell_id'] != result['cell_id'] or closure['plan_sha256'] != result['plan_sha256']
        or closure['sample']['sha256'] != result_pin['sha256']):
        raise ValueError('Accepted, uninterrupted flat sample required')
    measurements = {}
    for name, entry in result['metrics'].items():
        source, pin = document(Path(entry['evidence']['path']))
        if pin['sha256'] != entry['evidence']['sha256']:
            raise ValueError(f'Metric source changed: {name}')
        measurements[name] = dict(value=dotted(source, entry['field']), unit=entry['unit'], source=pin)
    roles = {}
    names = dict(client=('filesystem',), metadata=('redis',), objects=('rustfs','collector'))
    for row in result['observers']:
        role = row['id'].rsplit('-', 1)[-1]
        if role not in names or role in roles:
            raise ValueError('Expected one client, metadata and objects observer')
        directory = Path(row['directory']).resolve(strict=True)
        if not directory.is_relative_to((cell / 'raw' / role).resolve(strict=True)):
            raise ValueError('Observer is outside the retained role archive')
        finished, finished_pin = document(directory / 'finished.json')
        marks = {m['label']:m for m in finished['marks']}
        if finished.get('passed') is not True or len(marks) != len(finished['marks']):
            raise ValueError('Observer did not finish cleanly or repeated marks')
        start, end, drain = (next(m for m in row['marks'] if m.startswith(label))
                             for label in ('measure-start','measure-end','drain-end'))
        windows = dict(measure=(start,end), drain=(end,drain))
        if {'measurement-start','application-finished'} <= marks.keys():
            windows['worker'] = ('measurement-start','application-finished')
        selected = {m:resolve_mark(directory,marks[m]) for endpoints in windows.values() for m in endpoints}
        reports = {}
        for name,(start,end) in windows.items():
            a, pin_a, disk_a = selected[start]
            b, pin_b, disk_b = selected[end]
            ns = b['monotonic_start_ns'] - a['monotonic_start_ns']
            if b['index'] <= a['index'] or ns <= 0:
                raise ValueError('Invalid same-host sample chronology')
            counters = {}
            for target in names[role]:
                if not all(s['target_states'][target]['state']=='live' for s in (a,b)):
                    raise ValueError(f'Required {target} counter endpoint is not live')
                counters[target] = delta(a['counters'][target],b['counters'][target])
            disks = {k:delta(disk_a[k],disk_b[k]) if k in disk_a and k in disk_b else None
                     for k in sorted(disk_a.keys() | disk_b.keys())}
            reports[name] = dict(start=start,end=end,host_interval_ns=ns,start_source=pin_a,end_source=pin_b,
                native_counter_deltas=counters,host_block_device_deltas=disks)
        roles[role] = dict(id=row['id'],finished=finished_pin,windows=reports)
    if roles.keys() != names.keys():
        raise ValueError('Required observer role absent')
    return dict(schema='pilot-flat-observation-deltas-v1',cell_id=result['cell_id'],plan_sha256=result['plan_sha256'],
        sample=result_pin,closure=closure_pin,formal_metrics=measurements,roles=roles,
        scope=['All intervals use one host clock. Worker, full measurement and drain are separate.',
               'Collector export lag can move RustFS increments across marked boundaries.',
               'Host device I/O includes other processes. Diskstats may include both disks and partitions; do not sum all rows.',
               'Native FUSE counter semantics differ by filesystem. Missing series remain null.',
               'Single accepted repeat. Backend bytes and operations are not logical trace bytes or operations.'])


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--cell',type=Path,required=True)
    p.add_argument('--close',type=Path,required=True)
    p.add_argument('--out',type=Path,required=True)
    a=p.parse_args()
    result=report(a.cell.resolve(strict=True),a.close.resolve(strict=True))
    with a.out.open('x',encoding='utf-8') as f:
        json.dump(result,f,indent=2,ensure_ascii=False);f.write('\n')
    print(json.dumps(dict(cell_id=result['cell_id'],roles=list(result['roles']),out=str(a.out))))


if __name__=='__main__':
    main()
