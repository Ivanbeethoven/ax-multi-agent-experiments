"""Streaming S3 probe. Never records credentials, URLs, or signed headers."""
from contextlib import contextmanager
from http.client import HTTPConnection
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Lock, Thread
import hashlib
import time
import socket

@contextmanager
def meter(port):
    lock = Lock()
    counters = dict(gets=0,ranges=0,heads=0,bytes=0,errors=0,active=0,duplicate_gets=0)
    seen = set()
    class Handler(BaseHTTPRequestHandler):
        protocol_version = 'HTTP/1.1'
        def setup(self):
            super().setup()
            self.connection.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        def log_message(self, *_): pass
        def forward(self):
            upstream = HTTPConnection('127.0.0.1',port,timeout=30)
            is_get = self.command == 'GET'
            with lock:
                if is_get:
                    counters['active'] += 1
                    identity = (hashlib.sha256(self.path.split('?')[0].encode()).digest(),self.headers.get('Range'))
                    counters['duplicate_gets'] += identity in seen
                    seen.add(identity)
            try:
                upstream.request(self.command,self.path,headers=dict(self.headers.items()))
                response = upstream.getresponse()
                with lock:
                    counters['errors'] += response.status >= 400
                    counters['heads'] += self.command == 'HEAD'
                    counters['gets'] += is_get
                    counters['ranges'] += is_get and response.status == 206
                length = response.getheader('Content-Length')
                if length is None: raise ValueError('Probe requires known response length')
                self.send_response_only(response.status)
                for name,value in response.getheaders():
                    if name.lower() not in ('connection','transfer-encoding'):
                        self.send_header(name,value)
                self.end_headers()
                if is_get:
                    while True:
                        data = response.read(64 << 10)
                        if not data: break
                        self.wfile.write(data)
                        with lock: counters['bytes'] += len(data)
            except Exception:
                with lock: counters['errors'] += 1
                self.close_connection = True
            finally:
                upstream.close()
                if is_get:
                    with lock: counters['active'] -= 1
        do_GET = do_HEAD = forward
    def snapshot():
        with lock: return dict(counters)
    def reset_duplicates():
        with lock:
            if counters['active']: raise RuntimeError('Previous reads are still active')
            seen.clear()
    snapshot.reset_duplicates = reset_duplicates
    with ThreadingHTTPServer(('127.0.0.1',0),Handler) as server:
        thread=Thread(target=server.serve_forever,daemon=True);thread.start()
        try: yield f'http://127.0.0.1:{server.server_port}',snapshot
        finally: server.shutdown();thread.join()
