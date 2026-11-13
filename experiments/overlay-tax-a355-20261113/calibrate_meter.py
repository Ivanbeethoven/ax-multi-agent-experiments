#!/usr/bin/env python3
"""Measure loopback HTTP proxy latency without BrewFS or a storage backend."""
import argparse
from http.client import HTTPConnection
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import importlib.util
import json
from pathlib import Path
import statistics
from threading import Thread
import time
from urllib.parse import urlsplit

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--runner', type=Path, required=True)
parser.add_argument('--out', type=Path, required=True)
args = parser.parse_args()
spec = importlib.util.spec_from_file_location('measured_runner', args.runner)
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)
payload = bytes(range(256)) * 16


class Backend(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'
    disable_nagle_algorithm = True

    def log_message(self, *_):
        pass

    def do_GET(self):
        self.send_response(200)
        self.send_header('Content-Length', str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


backend = ThreadingHTTPServer(('127.0.0.1', 0), Backend)
thread = Thread(target=backend.serve_forever, daemon=True)
thread.start()
rows = []
try:
    with runner.meter(backend.server_port) as (endpoint, counts):
        ports = dict(direct=backend.server_port, proxy=urlsplit(endpoint).port)
        for pair in range(3):
            for name in (('direct', 'proxy') if pair % 2 == 0 else ('proxy', 'direct')):
                connection = HTTPConnection('127.0.0.1', ports[name], timeout=5)
                samples = []
                for index in range(23):
                    started = time.monotonic_ns()
                    connection.request('GET', '/payload')
                    response = connection.getresponse()
                    assert response.status == 200 and response.read() == payload
                    elapsed = time.monotonic_ns() - started
                    if index >= 3:
                        samples.append(elapsed)
                connection.close()
                rows.append(dict(pair=pair + 1, path=name, samples_ns=samples,
                                 median_ms=statistics.median(samples) / 1e6))
        observed = counts()
        assert observed['gets'] == 69 and observed['received_bytes'] == 69 * len(payload)
        assert observed['errors'] == 0 and observed['inflight'] == 0
finally:
    backend.shutdown()
    backend.server_close()
    thread.join(timeout=5)
summary = dict(runner_sha256=runner.sha(args.runner), rows=rows, counts=observed,
               medians_ms={name: statistics.median([r['median_ms'] for r in rows if r['path'] == name]) for name in ports})
with args.out.open('x') as f:
    json.dump(summary, f, indent=2)
print(json.dumps(summary['medians_ms']))
