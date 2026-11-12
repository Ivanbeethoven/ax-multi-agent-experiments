"""Check that the traffic meter observes real loopback HTTP traffic."""
from http.client import HTTPConnection
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Event, Thread
import unittest
from urllib.parse import urlsplit

from run_local import meter


class MeterTest(unittest.TestCase):
    def test_requests_bodies_head_and_errors_are_counted(self):
        class Upstream(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass

            def reply(self):
                length = int(self.headers.get('Content-Length', '0'))
                request = self.rfile.read(length)
                if self.path == '/missing':
                    code, body = 404, b'missing'
                elif self.command == 'DELETE':
                    code, body = 204, b''
                elif self.command == 'PUT':
                    code, body = 200, request
                else:
                    code, body = 200, b'payload'
                self.send_response(code)
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                if self.command != 'HEAD':
                    self.wfile.write(body)

            do_GET = do_HEAD = do_PUT = do_DELETE = reply

        with ThreadingHTTPServer(('127.0.0.1', 0), Upstream) as upstream:
            worker = Thread(target=upstream.serve_forever, daemon=True)
            worker.start()
            try:
                with meter(upstream.server_port) as (endpoint, snapshot):
                    port = urlsplit(endpoint).port
                    for method, path, body, code, expected in [
                        ('GET', '/', None, 200, b'payload'),
                        ('HEAD', '/', None, 200, b''),
                        ('PUT', '/', b'new', 200, b'new'),
                        ('DELETE', '/', None, 204, b''),
                        ('GET', '/missing', None, 404, b'missing'),
                    ]:
                        connection = HTTPConnection('127.0.0.1', port, timeout=3)
                        try:
                            connection.request(method, path, body=body)
                            response = connection.getresponse()
                            self.assertEqual(response.status, code)
                            self.assertEqual(response.read(), expected)
                            if method == 'HEAD':
                                self.assertEqual(response.getheader('Content-Length'), '7')
                        finally:
                            connection.close()
                    self.assertEqual(snapshot(), dict(requests=5, gets=2, heads=1, puts=1,
                                                      deletes=1, received_bytes=17,
                                                      sent_bytes=3, errors=1, inflight=0))
            finally:
                upstream.shutdown()
                worker.join(5)

    def test_request_is_visible_before_its_response_completes(self):
        entered, release = Event(), Event()

        class Upstream(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass

            def do_GET(self):
                entered.set()
                release.wait(3)
                self.send_response(200)
                self.send_header('Content-Length', '2')
                self.end_headers()
                self.wfile.write(b'ok')

        with ThreadingHTTPServer(('127.0.0.1', 0), Upstream) as upstream:
            worker = Thread(target=upstream.serve_forever, daemon=True)
            worker.start()
            try:
                with meter(upstream.server_port) as (endpoint, snapshot):
                    output = []
                    def request():
                        connection = HTTPConnection('127.0.0.1', urlsplit(endpoint).port, timeout=5)
                        try:
                            connection.request('GET', '/')
                            output.append(connection.getresponse().read())
                        finally:
                            connection.close()
                    client = Thread(target=request)
                    client.start()
                    try:
                        self.assertTrue(entered.wait(3))
                        observed = snapshot()
                        self.assertEqual(observed['requests'], 1)
                        self.assertEqual(observed['inflight'], 1)
                        self.assertEqual(observed['received_bytes'], 0)
                    finally:
                        release.set()
                        client.join(5)
                    self.assertEqual(output, [b'ok'])
                    self.assertEqual(snapshot()['inflight'], 0)
            finally:
                release.set()
                upstream.shutdown()
                worker.join(5)


if __name__ == '__main__':
    unittest.main()
