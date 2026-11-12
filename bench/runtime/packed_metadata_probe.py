"""Require measured KV reads and zero Packed namespace fetches in metadata-off."""
import re

NAMESPACE = {'group_index', 'group_metadata', 'inode_index', 'reverse_index',
             'cold_index', 'cold_attributes', 'source_stats_index'}


def counters(text):
    result = {}
    for line in text.splitlines():
        if not line or line.startswith('#'):
            continue
        match = re.fullmatch(r'(\w+)(?:\{([^}]*)\})?\s+(\d+)', line)
        if match:
            name, raw, value = match.groups()
            labels = tuple(sorted(re.findall(r'(\w+)="([^"]*)"', raw or '')))
            result[name, labels] = int(value)
    return result


def verify_off(before, after):
    start, end = counters(before), counters(after)
    key = ('brewfs_native_placement_kv_operations_succeeded_total', (('phase', 'runtime'),))
    if key not in start or key not in end:
        raise ValueError('Metadata-off KV counters missing; switch may have been ignored')
    operations = end[key] - start[key]
    if operations <= 0:
        raise ValueError('No runtime KV reads, or counter reset')
    reads = 0
    for key,value in start.items():
        if key[0] in ('brewfs_native_placement_kv_operations_succeeded_total',
                      'brewfs_object_read_started_total') and (key not in end or end[key]<value):
            raise ValueError('Counter disappeared or reset')
    for key, value in end.items():
        name, labels = key
        labels = dict(labels)
        if (name == 'brewfs_object_read_started_total' and labels.get('phase') == 'runtime'
                and labels.get('layer') == 'backend_body' and labels.get('kind') in NAMESPACE):
            delta = value - start.get(key, 0)
            if delta < 0:
                raise ValueError('Object counter reset')
            reads += delta
    if reads:
        raise ValueError('Metadata-off read Packed namespace objects during runtime')
    if any(sample.get(('brewfs_object_read_counters_valid', ())) != 1 for sample in (start,end)):
        raise ValueError('Object counter validity missing or false')
    return {'runtime_kv_operations': operations, 'runtime_namespace_object_reads': reads}
