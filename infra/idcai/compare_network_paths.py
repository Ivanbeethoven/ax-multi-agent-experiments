"""Compare direct and WireGuard TCP throughput sequentially on six hosts."""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
NETWORK = json.loads((ROOT / 'configs/network.json').read_text(encoding='utf-8'))
HOSTS = {name: (host['ssh'], host['direct_ip'], host['wireguard_ip'])
         for name, host in NETWORK['hosts'].items()}
PORT = 35220

def ssh(host, command):
    if os.name == 'posix' and Path('/srv/ax-cluster/direct/scripts/cluster_lib.py').is_file():
        sys.path.insert(0, '/srv/ax-cluster/direct/scripts')
        import cluster_lib as cl
        if host == 'A366':
            return ['bash', '-c', command]
        target = cl.HOST_BY_NAME[host]
        return ['ssh', *cl.ssh_options(target), 'root@' + target['ip'], command]
    return ['ssh', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=10',
            HOSTS[host][0], command]

def completed(rows, source, target, route, repetition):
    return any(row['source'] == source and row['target'] == target
               and row['route'] == route and row['repetition'] == repetition
               and not row.get('error') and row.get('exit_code') == 0
               and row.get('megabits_per_second', 0) > 0 for row in rows)

def measure(out, source, target, route, repetition, seconds):
    index = 1 if route == 'direct' else 2
    src, dst = HOSTS[source][index], HOSTS[target][index]
    stem = f'{source}-{target}-{route}-r{repetition}'
    attempt = 1
    prefix = out / stem
    while prefix.with_suffix('.route.txt').exists():
        attempt += 1
        prefix = out / (stem + f'-attempt{attempt:02d}')
    row = {'source': source, 'target': target, 'route': route, 'repetition': repetition,
           'attempt': attempt, 'raw_prefix': prefix.name,
           'seconds': seconds, 'streams': 4, 'source_address': src, 'target_address': dst,
           'started_utc': datetime.now(timezone.utc).isoformat()}
    try:
        route_result = subprocess.run(ssh(source, f'ip route get {dst} from {src}'),
                                      capture_output=True, text=True, timeout=15)
    except subprocess.TimeoutExpired:
        prefix.with_suffix('.route.txt').write_text('SSH route query timed out\n', encoding='utf-8')
        return {**row, 'error': 'SSH route query timed out', 'exit_code': None}
    prefix.with_suffix('.route.txt').write_text(route_result.stdout + route_result.stderr, encoding='utf-8')
    expected = 'eth0' if route == 'direct' else 'axwg0'
    if route_result.returncode or f'dev {expected}' not in route_result.stdout:
        return {**row, 'error': 'Route query failed or unexpected interface', 'exit_code': route_result.returncode}
    server = subprocess.Popen(ssh(target, f'timeout 25s iperf3 -s -1 -B {dst} -p {PORT} -J'),
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        time.sleep(1)
        client = subprocess.run(ssh(source, f'iperf3 -c {dst} -B {src} -p {PORT} -t {seconds} -P 4 -J'),
                                capture_output=True, text=True, timeout=20)
        prefix.with_suffix('.client.json').write_text(client.stdout, encoding='utf-8')
        prefix.with_suffix('.client.stderr.txt').write_text(client.stderr, encoding='utf-8')
        try:
            data = json.loads(client.stdout)
        except json.JSONDecodeError:
            return {**row, 'error': 'No valid iperf3 JSON; see client stderr', 'exit_code': client.returncode}
        row['error'] = data.get('error')
        row['exit_code'] = client.returncode
        if 'sum_received' in data.get('end', {}):
            row['megabits_per_second'] = data['end']['sum_received']['bits_per_second'] / 1e6
            row['megabytes_per_second'] = row['megabits_per_second'] / 8
            row['retransmits'] = data['end'].get('sum_sent', {}).get('retransmits')
            row['cpu_utilization_percent'] = data['end'].get('cpu_utilization_percent')
        return row
    except subprocess.TimeoutExpired:
        return {**row, 'error': 'SSH client measurement timed out', 'exit_code': None}
    finally:
        try:
            output, error = server.communicate(timeout=30)
        except subprocess.TimeoutExpired:
            server.kill()
            output, error = server.communicate()
        prefix.with_suffix('.server.json').write_text(output, encoding='utf-8')
        prefix.with_suffix('.server.stderr.txt').write_text(error, encoding='utf-8')

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--receivers', nargs='+', choices=HOSTS, default=list(HOSTS))
    parser.add_argument('--routes', nargs='+', choices=['direct', 'wireguard'], default=['direct', 'wireguard'])
    parser.add_argument('--repetitions', type=int, default=1)
    parser.add_argument('--seconds', type=int, choices=range(1, 11), default=5)
    parser.add_argument('--resume', action='store_true')
    opts = parser.parse_args()
    if opts.repetitions < 1:
        parser.error('--repetitions must be positive')
    settings = {'receivers': opts.receivers, 'routes': opts.routes,
                'repetitions': opts.repetitions, 'seconds': opts.seconds, 'network': NETWORK}
    if opts.resume:
        previous = json.loads((opts.out / 'settings.json').read_text(encoding='utf-8'))
        if previous != settings:
            parser.error('Resume with the original measurement settings')
        summary = opts.out / 'summary.json'
        rows = json.loads(summary.read_text(encoding='utf-8')) if summary.exists() else []
    else:
        opts.out.mkdir(parents=True, exist_ok=False)
        (opts.out / 'settings.json').write_text(json.dumps(settings, indent=2), encoding='utf-8')
        rows = []
    for host in HOSTS:
        if (opts.out / (host + '.inventory.txt')).exists():
            continue
        result = subprocess.run(ssh(host, 'hostname; ip -br address; cat /sys/class/net/eth0/speed; uptime; iperf3 --version'), capture_output=True, text=True, timeout=15)
        (opts.out / (host + '.inventory.txt')).write_text(result.stdout + result.stderr, encoding='utf-8')
    for repetition in range(1, opts.repetitions + 1):
        routes = opts.routes if repetition % 2 else list(reversed(opts.routes))
        for target in opts.receivers:
            for source in HOSTS:
                if source == target:
                    continue
                for route in routes:
                    if completed(rows, source, target, route, repetition):
                        continue
                    row = measure(opts.out, source, target, route, repetition, opts.seconds)
                    rows.append(row)
                    (opts.out / 'summary.json').write_text(json.dumps(rows, indent=2), encoding='utf-8')
                    print(json.dumps(row), flush=True)
    for host in HOSTS:
        result = subprocess.run(ssh(host, f'hostname; ss -ltn sport = :{PORT}; uptime'), capture_output=True, text=True, timeout=15)
        (opts.out / (host + '.finished.txt')).write_text(result.stdout + result.stderr, encoding='utf-8')
    remaining = sum(not completed(rows, source, target, route, repetition)
                    for repetition in range(1, opts.repetitions + 1)
                    for target in opts.receivers for source in HOSTS if source != target
                    for route in opts.routes)
    print(json.dumps({'remaining_measurements': remaining}), flush=True)
    if remaining:
        raise SystemExit(1)

if __name__ == '__main__':
    main()
