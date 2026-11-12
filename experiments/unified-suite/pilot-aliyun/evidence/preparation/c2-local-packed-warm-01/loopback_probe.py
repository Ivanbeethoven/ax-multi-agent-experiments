"""Observe actual RustFS reads in the tiny local fixture, without recording headers."""
from contextlib import contextmanager
import errno
from http.client import HTTPConnection
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
import re
from threading import Lock, Thread
import time


@contextmanager
def meter(rustfs_port):
    counters = dict(gets=0, ranges=0, heads=0, bytes=0, errors=0)
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
                    counters['heads'] += self.command == 'HEAD'
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


def extent_counters(raw, arm):
    modes = re.findall(r'^brewfs_workspace_read_extent_control_info\{mode="([a-z-]+)"\} 1$',raw,re.M)
    if modes != [arm]: raise ValueError('Mounted extent-control arm differs')
    counts = {}
    for label in ('queries','bypasses'):
        values = re.findall(r'^brewfs_workspace_read_extent_control_'+label+r'_total ([0-9]+)$',raw,re.M)
        if len(values)!=1: raise ValueError('Missing unique extent control counters')
        counts[label]=int(values[0])
    return counts


def extent_pass(first, second, arm):
    delta = {k:second[k]-first[k] for k in ('queries','bypasses')}
    return (delta['queries'] > 0 and delta['bypasses'] == 0 if arm in ('overlay','overlay-read') else
            delta['queries'] == 0 and delta['bypasses'] > 0)


def namespace_counters(raw):
    values = re.findall(
        r'^brewfs_workspace_read_namespace_control_(lookup|getattr|directory_probe)_(queries|cache_hits|bypasses)_total ([0-9]+)$',
        raw,re.M)
    if len(values) != 9 or len({(op,kind) for op,kind,_ in values}) != 9:
        raise ValueError('Missing unique read namespace control counters')
    return {op+'_'+kind:int(value) for op,kind,value in values}


def namespace_pass(first, second, arm):
    d = {k:second[k]-first[k] for k in second}
    return all(value >= 0 for value in d.values()) and all(
        (d[op+'_queries']+d[op+'_cache_hits'] > 0 and d[op+'_bypasses'] == 0)
        if arm=='overlay-read' else
        (d[op+'_queries'] == d[op+'_cache_hits'] == 0 and d[op+'_bypasses'] > 0)
        for op in ('lookup','getattr','directory_probe'))


def read_namespace(root):
    # Repeat exactly the non-root directory and file touched by the two-file fixture.
    start = time.monotonic_ns()
    sub = os.open('sub',os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=root)
    try:
        names = os.listdir(sub)
        listing = time.monotonic_ns()-start
        start = time.monotonic_ns()
        attr = os.stat('b.txt',dir_fd=sub,follow_symlinks=False)
        getattr = time.monotonic_ns()-start
    finally:
        os.close(sub)
    if names != ['b.txt'] or attr.st_size != 64:
        raise ValueError('Read namespace output differs from the fixture')
    return dict(open_and_list_ns=listing,lookup_and_stat_ns=getattr)


def probe(mount, rows, s3_snapshot, output, *, retained_bytes=None, extent_arm=None, timed_passes=0,
          mount_alive=None):
    from bench.warm_lower import assess, check_ranges, parse, read_range
    from bench.runtime.common import mounts
    check_ranges(rows)
    if timed_passes and (extent_arm is None or not 1 <= timed_passes * len(rows) <= 10000):
        raise ValueError('Timed diagnostic needs an extent arm and at most 10000 reads')
    output.mkdir(mode=0o700)
    stats_pattern = r'^(brewfs_fuse_read_(?:ops|bytes)_total) ([0-9]+)$'
    native_points = []
    retention_hits = []
    extent_points = []
    namespace_points = []
    compare_namespace = extent_arm in ('overlay-read','lower-read')
    original_mount = None
    if extent_arm is not None:
        entries = mounts(mount)
        if (extent_arm not in ('overlay','lower-only','overlay-read','lower-read') or retained_bytes is None or len(entries)!=1
                or 'ro' not in entries[0]['options'].split(',') or not entries[0]['filesystem'].startswith('fuse')):
            raise ValueError('Extent control requires one read-only FUSE mount and verified retention')
        original_mount = entries[0]
        try:
            # Opening the known regular file without O_TRUNC cannot modify it.
            fd = os.open(mount/rows[0]['path'],os.O_WRONLY|os.O_NOFOLLOW)
        except OSError as error:
            if error.errno!=errno.EROFS: raise
        else:
            os.close(fd)
            raise ValueError('Extent control did not reject a writable open')

    def collect(label):
        if mount_alive is not None and not mount_alive():
            raise ValueError('Original FUSE process exited before the counter sample')
        if original_mount is not None and mounts(mount)!=[original_mount]:
            raise ValueError('Extent control mount changed')
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
        if extent_arm is not None: extent_points.append(extent_counters(raw,extent_arm))
        if compare_namespace: namespace_points.append(namespace_counters(raw))
        return {**dict((k,int(v)) for k,v in found),**s3_snapshot()}

    points = [collect('before')]
    fd = os.open(mount,os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for label in ('pass-1','pass-2'):
            if compare_namespace: read_namespace(fd)
            for row in rows: read_range(fd,row)
            points.append(collect(label))
    finally:
        os.close(fd)
    first, second = ({k:b[k]-a[k] for k in b} for a,b in zip(points,points[1:]))
    expected = sum(row['length'] for row in rows)
    qualified = all(p['brewfs_fuse_read_bytes_total'] == expected and
                    p['brewfs_fuse_read_ops_total'] >= len(rows) and p['errors'] == 0
                    for p in (first,second)) and first['ranges'] > 0 and first['bytes'] > 0
    out = dict(source='read-only loopback proxy before RustFS; GET bytes and HEAD request count',
               working_set=rows,points=points,first=first,second=second,qualified=qualified,
               hot=qualified and all(second[k]==0 for k in ('gets','ranges','heads','bytes')))
    if retained_bytes is not None:
        native = assess(native_points,expected)
        retention_proved = retention_hits[0] <= retention_hits[1] < retention_hits[2]
        exact_fuse = all(delta['brewfs_fuse_read_ops_total'] == len(rows) for delta in (first,second))
        out.update(native=native,native_points=native_points.copy(),retention_hits=retention_hits.copy(),retained_bytes=retained_bytes,
                   hot=out['hot'] and native['passed'] and retention_proved and exact_fuse)
    if extent_arm is not None:
        active = all(extent_pass(a,b,extent_arm) for a,b in zip(extent_points,extent_points[1:]))
        out.update(extent_arm=extent_arm,extent_points=extent_points.copy(),readonly_mount=original_mount,
                   writable_open='EROFS',expected_extent_path=active,hot=out['hot'] and active)
    if compare_namespace:
        active = all(namespace_pass(a,b,extent_arm) for a,b in zip(namespace_points,namespace_points[1:]))
        out.update(namespace_points=namespace_points.copy(),expected_namespace_path=active,hot=out['hot'] and active)
    if timed_passes:
        out['timed_extent_reads'] = dict(passed=False,status='warmup_failed')
        if out['hot']:
            # The second warm snapshot is the start fence. Use the same mount and
            # working set, and verify backend, FUSE, and mode counters at the end.
            fd = os.open(mount,os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            try:
                started = time.monotonic_ns()
                latencies, namespace_latencies = [], []
                for _ in range(timed_passes):
                    if compare_namespace: namespace_latencies.append(read_namespace(fd))
                    latencies.extend(read_range(fd,row,timed=True) for row in rows)
                elapsed = time.monotonic_ns() - started
                last = collect('timed-end')
            finally:
                os.close(fd)
            timed = {k:last[k]-points[-1][k] for k in last}
            native_timed = {k:native_points[-1][k]-native_points[-2][k] for k in native_points[-1]}
            passed = (all(timed[k] == 0 for k in ('gets','ranges','heads','bytes','errors')) and
                      timed['brewfs_fuse_read_ops_total'] == len(latencies) and
                      timed['brewfs_fuse_read_bytes_total'] == expected * timed_passes and
                      all(native_timed[k] == 0 for k in ('range_requests','received_bytes','failures')) and
                      native_timed['logical_bytes'] == expected * timed_passes and
                      retention_hits[-1] > retention_hits[-2] and
                      extent_pass(extent_points[-2],extent_points[-1],extent_arm) and
                      (not compare_namespace or namespace_pass(namespace_points[-2],namespace_points[-1],extent_arm)))
            out['timed_extent_reads'] = dict(passed=passed,status='hot' if passed else 'rejected',
                scope=('Read-only data extent, lookup, non-root attributes and directory opening'
                       if compare_namespace else 'Read-only data extent resolution'),
                passes=timed_passes,elapsed_ns=elapsed,pread_ns=latencies,final_independent=last,
                final_native=native_points[-1],final_extent=extent_points[-1],final_retention_hits=retention_hits[-1],
                native_delta=native_timed,independent_delta=timed)
            if compare_namespace:
                out['timed_extent_reads'].update(namespace_ns=namespace_latencies,final_namespace=namespace_points[-1])
    with (output/'result.json').open('x') as stream: json.dump(out,stream,indent=2);stream.write('\n')
    return out
