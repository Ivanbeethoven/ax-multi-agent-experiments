#!/usr/bin/env python3
"""Own-cgroup Prometheus endpoint and append-only raw samples. Linux, stdlib only."""
import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import re
import threading
import time
from observe import own_cgroup, sample
from summarize import kv


def prometheus(row):
    lines = []
    def metric(name, value, labels=''):
        if value is not None:
            lines.append(f'{name}{labels} {value}')
    def raw(name):
        field = row['cgroup'][name]
        metric('ax_source_available', int(field['value'] is not None), '{source=' + json.dumps(name) + '}')
        return field['value']
    cpu = kv(raw('cpu.stat'))
    for key, name, scale in [('usage_usec', 'ax_cpu_seconds_total', 1e6),
                             ('throttled_usec', 'ax_cpu_throttled_seconds_total', 1e6),
                             ('nr_periods', 'ax_cpu_periods_total', 1),
                             ('nr_throttled', 'ax_cpu_throttled_periods_total', 1)]:
        if key in cpu:
            metric(name, cpu[key] / scale)
    for key, name in [('memory.current', 'ax_memory_current_bytes'), ('memory.max', 'ax_memory_limit_bytes')]:
        value = raw(key)
        if value is not None and value.isdigit():
            metric(name, int(value))
    for key, value in kv(raw('memory.events')).items():
        metric('ax_memory_events_total', value, '{event=' + json.dumps(key) + '}')
    for line in (raw('io.stat') or '').splitlines():
        fields = line.split()
        for field in fields[1:]:
            key, sep, value = field.partition('=')
            if sep and key in ('rbytes', 'wbytes') and value.isdigit():
                labels = '{device=' + json.dumps(fields[0]) + ',direction=' + json.dumps(key) + '}'
                metric('ax_cgroup_block_bytes_total', int(value), labels)
    network = row['network_dev']['value']
    metric('ax_source_available', int(network is not None), '{source="network_namespace"}')
    for line in (network or '').splitlines():
        interface, sep, data = line.partition(':')
        fields = data.split()
        if sep and len(fields) == 16:
            for direction, position in [('rx', 0), ('tx', 8)]:
                metric('ax_netns_bytes_total', int(fields[position]),
                       '{interface=' + json.dumps(interface.strip()) + ',direction=' + json.dumps(direction) + '}')
    metric('ax_sample_timestamp_seconds', row['utc_ns'] / 1e9)
    metric('ax_collector_read_seconds', row['collector_read_ns'] / 1e9)
    return '\n'.join(lines) + '\n'


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--bind', required=True)
    p.add_argument('--port', type=int, default=9108)
    p.add_argument('--worker-id', required=True)
    p.add_argument('--out', type=Path, required=True)
    a = p.parse_args()
    if not re.fullmatch('[a-zA-Z0-9_-]+', a.worker_id):
        p.error('Invalid worker ID')
    cgroup = own_cgroup()
    latest = {'text': None}
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path != '/metrics':
                self.send_error(404)
                return
            content = latest['text']
            if content is None:
                self.send_error(503)
                return
            payload = content.encode()
            self.send_response(200)
            self.send_header('Content-Type', 'text/plain; version=0.0.4')
            self.send_header('Content-Length', str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
        def log_message(self, *_args):
            pass
    server = ThreadingHTTPServer((a.bind, a.port), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    a.out.parent.mkdir(parents=True, exist_ok=True)
    with a.out.open('x') as stream:
        index = 0
        while True:
            row = sample(cgroup)
            row.update({'worker_id': a.worker_id, 'index': index, 'scope': cgroup['scope']})
            stream.write(json.dumps(row) + '\n')
            stream.flush()
            latest['text'] = prometheus(row)
            index += 1
            time.sleep(1)


if __name__ == '__main__':
    main()
