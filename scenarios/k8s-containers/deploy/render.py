#!/usr/bin/env python3
"""Render plain-process monitoring configs from actual container IPv4 addresses."""
import argparse
import ipaddress
import json
from pathlib import Path
import re


def render(spec):
    if not re.fullmatch('[a-zA-Z0-9_-]+', spec['campaign']):
        raise ValueError('Invalid campaign ID')
    workers, storage = spec['workers'], spec['rustfs']
    if not workers or len(storage) not in (1, 4):
        raise ValueError('Provide workers and one or four RustFS containers')
    roles = [{'id': 'monitor', 'ip': spec['monitor']}, {'id': 'redis', 'ip': spec['redis']}, *workers, *storage]
    if len({n['id'] for n in roles}) != len(roles) or len({n['ip'] for n in roles}) != len(roles):
        raise ValueError('Use unique IDs and distinct container IPs for this topology')
    for node in roles:
        if not re.fullmatch('[a-zA-Z0-9_-]+', node['id']):
            raise ValueError('Invalid ID')
        address = ipaddress.IPv4Address(node['ip'])
        if not address.is_private or address.is_loopback or address.is_unspecified:
            raise ValueError('Use reachable private IPv4 addresses, not 0.0.0.0/loopback')
    def targets(nodes, port):
        return [{'targets': [f"{n['ip']}:{port}"], 'labels': {'worker': n['id']}} for n in nodes]
    prom = {'global': {'scrape_interval': '1s', 'scrape_timeout': '800ms', 'evaluation_interval': '5s',
                       'external_labels': {'campaign': spec['campaign']}},
            'scrape_configs': [
                {'job_name': 'juicefs', 'static_configs': targets(workers, 9567)},
                {'job_name': 'resources', 'static_configs': targets(roles, 9108)},
                {'job_name': 'redis', 'scrape_interval': '5s', 'scrape_timeout': '3s',
                 'static_configs': [{'targets': [f"{spec['redis']}:9121"]}]},
                {'job_name': 'rustfs', 'static_configs': [{'targets': ['127.0.0.1:8889']}]},
                {'job_name': 'prometheus', 'scrape_interval': '5s',
                 'static_configs': [{'targets': ['127.0.0.1:9090']}]}]}
    otel = {'receivers': {'otlp': {'protocols': {'http': {'endpoint': f"{spec['monitor']}:4318"}}}},
            'processors': {'memory_limiter': {'check_interval': '1s', 'limit_mib': 512, 'spike_limit_mib': 128},
                           'batch': {'timeout': '1s', 'send_batch_size': 1024}},
            'exporters': {'prometheus': {'endpoint': '127.0.0.1:8889',
                                         'metric_expiration': '15s',
                                         'resource_to_telemetry_conversion': {'enabled': True}}},
            'service': {'pipelines': {'metrics': {'receivers': ['otlp'],
                         'processors': ['memory_limiter', 'batch'], 'exporters': ['prometheus']}}}}
    return prom, otel


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('hosts', type=Path)
    p.add_argument('out', type=Path)
    a = p.parse_args()
    prom, otel = render(json.loads(a.hosts.read_text()))
    a.out.mkdir(parents=True, exist_ok=False)
    # JSON is a YAML subset accepted by Prometheus and the Collector.
    for filename, content in [('prometheus.yml', prom), ('otel.yml', otel)]:
        (a.out / filename).write_text(json.dumps(content, indent=2) + '\n')
