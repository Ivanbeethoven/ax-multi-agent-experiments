"""Observe actual RustFS reads in the tiny local fixture, without recording headers."""
from contextlib import contextmanager
from http.client import HTTPConnection
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
import re
from threading import Lock, Thread


@contextmanager
def meter(rustfs_port):
    counters = dict(gets=0, ranges=0, bytes=0, errors=0)
    lock = Lock()

    class Handler(BaseHTTPRequestHandler):
        protocol_version = 'HTTP/1.1'

        def log_message(self, *_):
            pass  # Never retain signed requests or headers.

        def forward(self):
            upstream = HTTPConnection('127.0.0.1',rustfs_port,timeout=5)
            try:
                headers = dict(self.headers.items())
                # Preserve the signed Host and Range; RustFS verifies SigV4.
                upstream.request(self.command,self.path,headers=headers)
                response = upstream.getresponse()
                body = response.read(4*1024**2 + 1)
                if len(body) > 4*1024**2:
                    raise ValueError('Response too large for local fixture')
                with lock:
                    counters['errors'] += response.status >= 400
                    if self.command == 'GET' and response.status in (200,206):
                        counters['gets'] += 1
                        counters['ranges'] += response.status == 206
                        counters['bytes'] += len(body)
                self.send_response_only(response.status)
                for name,value in response.getheaders():
                    if name.lower() not in ('connection','transfer-encoding','content-length'):
                        self.send_header(name,value)
                length = response.getheader('Content-Length') if self.command == 'HEAD' else None
                self.send_header('Content-Length',length or str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            except Exception:
                with lock: counters['errors'] += 1
                self.close_connection = True
            finally:
                upstream.close()

        do_GET = do_HEAD = forward

    def snapshot():
        with lock: return counters.copy()

    with ThreadingHTTPServer(('127.0.0.1',0),Handler) as server:
        thread = Thread(target=server.serve_forever,daemon=True)
        thread.start()
        try:
            yield f'http://127.0.0.1:{server.server_port}',snapshot
        finally:
            server.shutdown()
            thread.join()


def probe(mount, rows, s3_snapshot, output, *, retained_bytes=None):
    from bench.warm_lower import assess, check_ranges, parse, read_range
    check_ranges(rows)
    output.mkdir(mode=0o700)
    stats_pattern = r'^(brewfs_fuse_read_(?:ops|bytes)_total) ([0-9]+)$'
    native_points = []
    retention_hits = []

    def collect(label):
        raw = (mount/'.stats').read_text()
        with (output/(label+'.stats')).open('x') as stream: stream.write(raw)
        found = re.findall(stats_pattern,raw,re.M)
        if len(found) != 2 or len(dict(found)) != 2:
            raise ValueError('Missing unique FUSE read counters')
        if retained_bytes is not None:
            hits = re.findall(r'^brewfs_packed_v3_payload_retention_hits_total ([0-9]+)$',raw,re.M)
            cap = re.findall(r'^brewfs_packed_v3_payload_retention_capacity_bytes ([0-9]+)$',raw,re.M)
            row_cache = re.findall(r'^brewfs_workspace_row_cache_hits_total [0-9]+$',raw,re.M)
            if len(hits) != 1 or len(row_cache) != 1:
                raise ValueError('Missing row cache or Packed frame retention metrics')
            if cap != [str(retained_bytes)]:
                raise ValueError('Native Packed frame retention capacity differs')
            retention_hits.append(int(hits[0]))
            native_points.append(parse(raw.encode()))
        return {**dict((k,int(v)) for k,v in found),**s3_snapshot()}

    points = [collect('before')]
    fd = os.open(mount,os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for label in ('pass-1','pass-2'):
            for row in rows: read_range(fd,row)
            points.append(collect(label))
    finally:
        os.close(fd)
    first, second = ({k:b[k]-a[k] for k in b} for a,b in zip(points,points[1:]))
    expected = sum(row['length'] for row in rows)
    qualified = all(p['brewfs_fuse_read_bytes_total'] == expected and
                    p['brewfs_fuse_read_ops_total'] >= len(rows) and p['errors'] == 0
                    for p in (first,second)) and first['ranges'] > 0 and first['bytes'] > 0
    out = dict(source='read-only loopback proxy before RustFS; successful GET body bytes',
               working_set=rows,points=points,first=first,second=second,qualified=qualified,
               hot=qualified and second['gets'] == 0 and second['bytes'] == 0)
    if retained_bytes is not None:
        native = assess(native_points,expected)
        retention_proved = retention_hits[0] <= retention_hits[1] < retention_hits[2]
        exact_fuse = all(delta['brewfs_fuse_read_ops_total'] == len(rows) for delta in (first,second))
        out.update(native=native,native_points=native_points,retention_hits=retention_hits,retained_bytes=retained_bytes,
                   hot=out['hot'] and native['passed'] and retention_proved and exact_fuse)
    with (output/'result.json').open('x') as stream: json.dump(out,stream,indent=2);stream.write('\n')
    return out
