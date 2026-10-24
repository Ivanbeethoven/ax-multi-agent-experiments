#!/usr/bin/env python3
"""Ship small observation files in acknowledged, idempotent chunks over private HTTP.

No trace/blob/workspace transfer. Receiver fsyncs each acknowledged chunk.
Use a new worker/campaign namespace after truncating or replacing any source file.
"""
import argparse
import hashlib
import hmac
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import os
from pathlib import Path
import re
import time
import urllib.parse
import urllib.request

LIMIT = 1024 * 1024
NAMES = {'resources.jsonl', 'samples.jsonl', 'environment.json', 'observer-result.json',
         'stdout.log', 'stderr.log', 'replay.json', 'case-result.json', 'verify.json',
         'coordinator-events.jsonl', 'plan.json', 'allocation.json', 'probe.json', 'replay-resources.json',
         'juicefs-before.prom', 'juicefs-after.prom', 'redis-before.prom', 'redis-after.prom',
         'rustfs-before.prom', 'rustfs-after.prom', 'software-versions.json'}


def target_path(root, relative):
    rel = Path(relative)
    if rel.is_absolute() or '..' in rel.parts or not rel.parts or rel.name not in NAMES:
        raise ValueError('Invalid observation path')
    target = root / rel
    if any(p.is_symlink() for p in [target, *target.parents] if p != root.parent):
        raise ValueError('No symlinks in observation destination')
    if not target.resolve().is_relative_to(root.resolve()):
        raise ValueError('Outside observation destination')
    return target


def append_chunk(root, relative, offset, body, digest):
    if offset < 0 or len(body) > LIMIT or hashlib.sha256(body).hexdigest() != digest:
        raise ValueError('Invalid chunk')
    target = target_path(root, relative)
    target.parent.mkdir(parents=True, exist_ok=True)
    size = target.stat().st_size if target.exists() else 0
    if size < offset:
        raise ValueError('Missing preceding bytes')
    with target.open('r+b' if target.exists() else 'w+b') as f:
        f.seek(offset)
        overlap = min(size - offset, len(body))
        if overlap:
            if f.read(overlap) != body[:overlap]:
                raise ValueError('Conflicting retry; use a new attempt namespace')
        if overlap < len(body):
            f.write(body[overlap:])
        if overlap < len(body) or size == 0:
            f.flush()
            os.fsync(f.fileno())
    return {'ack_offset': offset + len(body), 'sha256': digest}


def serve(args, token):
    root = args.root.resolve()
    root.mkdir(parents=True, exist_ok=True)
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            if not hmac.compare_digest(self.headers.get('Authorization', ''), 'Bearer ' + token):
                self.send_error(403)
                return
            try:
                parsed = urllib.parse.urlparse(self.path)
                if parsed.path != '/append':
                    self.send_error(404)
                    return
                query = urllib.parse.parse_qs(parsed.query, strict_parsing=True)
                length = int(self.headers.get('Content-Length', '-1'))
                if length < 0 or length > LIMIT:
                    raise ValueError('Invalid length')
                self.connection.settimeout(10)
                body = self.rfile.read(length)
                if len(body) != length:
                    raise ValueError('Incomplete body')
                result = append_chunk(root, query['path'][0], int(query['offset'][0]), body,
                                      self.headers.get('X-Content-SHA256', ''))
                payload = json.dumps(result).encode()
                self.send_response(200)
                self.send_header('Content-Length', str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)
            except (ValueError, KeyError, OSError):
                self.send_error(409, 'Invalid or conflicting observation chunk')
        def log_message(self, *_args):
            pass
    HTTPServer((args.bind, args.port), Handler).serve_forever()


def ship_once(args, token, offsets):
    root = args.root.resolve(strict=True)
    sent = 0
    for path in sorted(root.rglob('*')):
        if path.is_symlink() or not path.is_file() or path.name not in NAMES:
            continue
        relative = path.relative_to(root).as_posix()
        key = args.worker_id + '/' + relative
        offset = offsets.get(key, 0)
        if path.stat().st_size < offset:
            raise ValueError('Source truncated; use a new campaign/attempt')
        with path.open('rb') as stream:
            stream.seek(offset)
            # Bound each pass so growing resource logs cannot starve other files.
            remaining = path.stat().st_size - offset
            # Empty logs are also results; preserve them at the receiver.
            empty_new_file = remaining == 0 and offset == 0 and key not in offsets
            while remaining or empty_new_file:
                empty_new_file = False
                body = stream.read(min(LIMIT, remaining))
                if remaining and not body:
                    raise ValueError('Source changed during transfer; use a new attempt')
                digest = hashlib.sha256(body).hexdigest()
                url = args.url.rstrip('/') + '/append?' + urllib.parse.urlencode({'path': key, 'offset': offset})
                request = urllib.request.Request(url, data=body, headers={
                    'Authorization': 'Bearer ' + token, 'X-Content-SHA256': digest}, method='POST')
                with urllib.request.urlopen(request, timeout=15) as response:
                    receipt = json.load(response)
                if receipt != {'ack_offset': offset + len(body), 'sha256': digest}:
                    raise ValueError('Bad receipt')
                offset += len(body)
                remaining -= len(body)
                sent += len(body)
                offsets[key] = offset
    return sent


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('mode', choices=['serve', 'ship'])
    p.add_argument('--root', type=Path, required=True)
    p.add_argument('--token-file', type=Path, required=True)
    p.add_argument('--bind', default='127.0.0.1')
    p.add_argument('--port', type=int, default=8088)
    p.add_argument('--url')
    p.add_argument('--worker-id')
    p.add_argument('--once', action='store_true')
    a = p.parse_args()
    token = a.token_file.read_text().strip()
    if len(token) < 32:
        p.error('Token must have at least 32 characters')
    if a.mode == 'serve':
        serve(a, token)
        return
    if not a.url or not a.worker_id or not re.fullmatch('[a-zA-Z0-9_-]+', a.worker_id):
        p.error('ship requires --url and a path-free --worker-id')
    offsets = {}  # Restart resends; receiver compares existing bytes before acknowledging.
    while True:
        try:
            count = ship_once(a, token, offsets)
            print(json.dumps({'utc_ns': time.time_ns(), 'worker': a.worker_id, 'acked_bytes': count}), flush=True)
        except Exception as exc:
            print(json.dumps({'utc_ns': time.time_ns(), 'error': type(exc).__name__}), flush=True)
            if a.once:
                raise
        if a.once:
            break
        time.sleep(10)


if __name__ == '__main__':
    main()
