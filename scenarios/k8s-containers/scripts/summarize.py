#!/usr/bin/env python3
"""Derive phase-level resource totals, preserving gaps and counter resets."""
import argparse
import json
from pathlib import Path


def kv(text):
    result = {}
    for line in (text or '').splitlines():
        fields = line.split()
        if len(fields) == 2:
            try:
                result[fields[0]] = int(fields[1])
            except ValueError:
                pass
    return result


def raw(sample, name):
    return sample.get('cgroup', {}).get(name, {}).get('value')


def delta(samples, filename, key):
    values = [kv(raw(s, filename)).get(key) for s in samples]
    if not values or any(v is None for v in values):
        return {'value': None, 'reason': 'missing_sample_or_counter'}
    if any(b < a for a, b in zip(values, values[1:])):
        return {'value': None, 'reason': 'counter_reset'}
    return {'value': values[-1] - values[0], 'reason': None}


def summarize(folder):
    environment = json.loads((folder / 'environment.json').read_text())
    result_path = folder / 'observer-result.json'
    result = json.loads(result_path.read_text()) if result_path.exists() else None
    samples = []
    damaged = 0
    with (folder / 'samples.jsonl').open() as stream:
        for line in stream:
            try:
                samples.append(json.loads(line))
            except json.JSONDecodeError:
                damaged += 1
    if not samples:
        raise ValueError('No readable samples; do not report zero usage')
    times = [s['monotonic_ns'] for s in samples]
    monotonic = all(b > a for a, b in zip(times, times[1:]))
    contiguous = all(s.get('index') == i for i, s in enumerate(samples))
    span = (times[-1] - times[0]) / 1e9 if monotonic and len(times) > 1 else None
    memory = []
    for s in samples:
        value = raw(s, 'memory.current')
        try:
            memory.append(int(value))
        except (ValueError, TypeError):
            pass
    cpu = delta(samples, 'cpu.stat', 'usage_usec')
    periods = delta(samples, 'cpu.stat', 'nr_periods')
    throttle = delta(samples, 'cpu.stat', 'nr_throttled')
    gaps = [(b-a) / 1e9 for a, b in zip(times, times[1:])]
    return {'run_id': environment['run_id'], 'worker_id': environment['worker_id'],
            'phase': environment['phase'], 'observer_result': result,
            'complete': bool(result and damaged == 0 and monotonic and contiguous and result['samples'] == len(samples)),
            'sample_indices_contiguous': contiguous,
            'samples': len(samples), 'damaged_lines': damaged,
            'max_sample_gap_s': max(gaps) if gaps else None,
            'gaps_over_2x_interval': sum(g > 2 * environment['interval_s'] for g in gaps),
            'counter_window_seconds': span, 'cpu_usage_usec': cpu,
            'average_used_cores': cpu['value'] / 1e6 / span if span and cpu['value'] is not None else None,
            'throttled_usec': delta(samples, 'cpu.stat', 'throttled_usec'),
            'throttled_period_fraction': throttle['value'] / periods['value']
                if periods['value'] and throttle['value'] is not None else None,
            'sampled_memory_current_max_bytes': max(memory) if memory else None,
            'memory_samples_available': len(memory),
            'oom_kill_delta': delta(samples, 'memory.events', 'oom_kill'),
            'cgroup_scope': environment['cgroup']['scope'],
            'warning': 'Includes observer and all own-cgroup work; memory peak is sampled. '
                       'No backend physical I/O, latency percentiles or global network sum inferred.'}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('folder', type=Path)
    args = parser.parse_args()
    print(json.dumps(summarize(args.folder), ensure_ascii=False, indent=2))
