#!/usr/bin/env python3
"""Start a pinned OTLP-to-Prometheus collector; preserve its source timestamps.

RustFS exports OTLP rather than hosting this Prometheus endpoint itself. On ECS
use host networking with loopback listeners. A local container network namespace
may be selected explicitly for functional qualification.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import subprocess


def capture(argv):
    return subprocess.check_output(argv, text=True).strip()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['start', 'stop'])
    parser.add_argument('--image-receipt', type=Path)
    parser.add_argument('--name', required=True)
    parser.add_argument('--network', default='host')
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    if not re.fullmatch(r'ax-pilot-[a-z0-9-]+', args.name):
        raise ValueError('Dedicated collector owner name required')
    if args.action == 'stop':
        original = json.loads((args.out / 'started.json').read_text())
        row = json.loads(capture(['docker', 'inspect', args.name]))[0]
        if row['Id'] != original['container_id'] or row['Config']['Labels'].get('ax.pilot.owner') != args.name:
            raise ValueError('Collector identity changed; preserve it')
        subprocess.run(['docker', 'stop', '--timeout', '-1', args.name], check=True)
        (args.out / 'container.log').write_text(subprocess.check_output(
            ['docker', 'logs', args.name], stderr=subprocess.STDOUT, text=True))
        (args.out / 'stopped.json').write_text(json.dumps(dict(at=datetime.now(timezone.utc).isoformat(),
            running=False, container_id=row['Id'])) + '\n')
        return
    if args.network != 'host' and not re.fullmatch(r'container:ax-pilot-[a-z0-9-]+', args.network):
        raise ValueError('Only host or an explicitly named local pilot namespace is supported')
    if subprocess.run(['docker', 'inspect', args.name], capture_output=True).returncode == 0:
        raise ValueError('Preserve existing collector')
    image_receipt = json.loads(args.image_receipt.read_text())
    image = image_receipt['image_reference']
    if not re.fullmatch(r'[^\s]+@sha256:[0-9a-f]{64}', image):
        raise ValueError('Collector digest required')
    runtime_image = image
    if subprocess.run(['docker','image','inspect',image],capture_output=True).returncode:
        # An offline Docker/containerd import can retain an OCI index by its
        # immutable ID without a RepoDigest name. Only accept that exact digest.
        expected_id=image_receipt.get('image_id')
        if expected_id != image.split('@')[1]:
            raise ValueError('Offline collector needs its exact attested OCI digest ID')
        loaded=json.loads(capture(['docker','image','inspect',expected_id]))[0]
        if loaded['Id'] != expected_id:
            raise ValueError('Loaded collector identity differs')
        runtime_image=expected_id
    args.out.mkdir(parents=True, exist_ok=False)
    # Container mode is private local qualification, accessible from its bridge
    # address. Cloud host mode binds only localhost, not the public jump or VPC.
    listen = '127.0.0.1' if args.network == 'host' else '0.0.0.0'
    config = dict(receivers={'otlp': {'protocols': {'http': {'endpoint': listen + ':4318'}}}},
        processors={'memory_limiter': {'check_interval': '1s', 'limit_mib': 256, 'spike_limit_mib': 64},
            'batch': {'timeout': '1s', 'send_batch_size': 1024}},
        exporters={'prometheus': {'endpoint': listen + ':18889', 'send_timestamps': True,
            'metric_expiration': '30s', 'resource_to_telemetry_conversion': {'enabled': True}}},
        service={'pipelines': {'metrics': {'receivers': ['otlp'],
            'processors': ['memory_limiter', 'batch'], 'exporters': ['prometheus']}}})
    config_path = args.out.resolve() / 'collector.json'
    config_path.write_text(json.dumps(config, indent=2) + '\n')
    argv = ['docker', 'run', '--pull', 'never', '-d', '--name', args.name,
        '--label', 'ax.pilot.owner=' + args.name, '--network', args.network,
        '--memory', '512m', '--cpus', '1', '--read-only',
        '-v', str(config_path) + ':/etc/otelcol-contrib/pilot.json:ro', runtime_image,
        '--config=/etc/otelcol-contrib/pilot.json']
    (args.out / 'intent.json').write_text(json.dumps(argv) + '\n')
    container = capture(argv)
    receipt = dict(at=datetime.now(timezone.utc).isoformat(), container_id=container,
        image_reference=image, runtime_image=runtime_image, network=args.network, config_sha256=hashlib.sha256(config_path.read_bytes()).hexdigest(),
        prometheus_port=18889, otlp_http_port=4318, source_timestamps=True,
        container_memory_bytes=512*1024**2, container_cpus=1, limiter_mib=256,
        scope='Collector startup only; actual native series, freshness, coverage and overhead still require qualification')
    (args.out / 'started.json').write_text(json.dumps(receipt, indent=2) + '\n')
    print(json.dumps(receipt))


if __name__ == '__main__':
    main()
