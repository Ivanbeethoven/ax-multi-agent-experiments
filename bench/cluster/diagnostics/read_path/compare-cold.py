import json, sys
from pathlib import Path

def delta(case, series, label):
    values = []
    for phase in ('before', 'after'):
        lines = (case / f'cold-{phase}.prom').read_text().splitlines()
        matches = [float(line.split()[-1]) for line in lines if line.startswith(series + '{') and label in line]
        values.append(sum(matches) if matches else None)
    if values[1] is None:
        raise ValueError(f'{case}: missing {series}')
    result = values[1] - (values[0] or 0)
    if result < 0:
        raise ValueError(f'{case}: {series} decreased')
    return int(result)

def report(root):
    for path in sorted(root.rglob('summary.json')):
        data = json.loads(path.read_text())
        if 'cold' not in data.get('passes', {}):
            continue
        case = path.parent
        if data['volume_format'] == 'juicefs':
            prefix, label = 'juicefs_object_', 'method="GET"'
            count, size = prefix+'request_durations_histogram_seconds_count', prefix+'request_data_bytes'
        else:
            label = 'kind="upper"'
            count, size = 'graftfs_object_get_total', 'graftfs_object_get_bytes_total'
        yield dict(case=str(case), verified=data['verified'], mode=data['volume_format'],
                   get_requests=delta(case,count,label), get_bytes=delta(case,size,label), **data['passes']['cold'])

if __name__ == '__main__':
    for arg in sys.argv[1:]:
        for row in report(Path(arg)):
            print(json.dumps(row))
