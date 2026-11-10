#!/usr/bin/env python3
"""Private C4 model relay; the shared workspace receives only a relay credential.

No request/response bodies, authorization headers, upstream URLs or provider
errors are retained. This service makes no model call until a client asks.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import hmac
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import ipaddress
import json
import os
from pathlib import Path
import secrets
import stat
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def configuration(path):
    path = Path(path)
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077:
        raise ValueError('Owner-only regular private configuration required')
    return validate_config(json.loads(path.read_text()))


def validate_config(value):
    address = ipaddress.ip_address(value['bind'])
    if address.is_unspecified or address.is_global or address.is_multicast:
        raise ValueError('Explicit private or loopback listener required')
    upstream = urllib.parse.urlsplit(value['base_url'])
    if upstream.scheme != 'https' or not upstream.hostname or upstream.username or upstream.password or upstream.query or upstream.fragment:
        raise ValueError('An HTTPS provider base URL without embedded credentials is required')
    if not value['api_key'] or value['api_key'].startswith('{') or len(value['relay_key']) < 32:
        raise ValueError('Literal provider key and a separate random relay key required')
    if value['api_key'] == value['relay_key']:
        raise ValueError('Never give the provider key to a workspace')
    if value['model'] != 'deepseek-v4-flash':
        raise ValueError('This campaign uses deepseek-v4-flash')
    if not 1024 <= value['port'] <= 65535 or not 1 <= value['max_requests'] <= 1000:
        raise ValueError('Explicit unprivileged port and bounded request count required')
    return value


def handler(config, record, reserve, opener=None):
    opener = opener or urllib.request.build_opener(NoRedirect())

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def reply(self, code, message):
            body = json.dumps({'error': message}).encode()
            self.send_response(code)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if self.path != '/health':
                self.reply(404, 'unknown endpoint')
                return
            self.reply(200, 'ready')

        def do_POST(self):
            start = time.monotonic()
            if not hmac.compare_digest(self.headers.get('Authorization', ''), 'Bearer ' + config['relay_key']):
                self.reply(401, 'relay credential required')
                return
            if self.path != '/v1/chat/completions':
                self.reply(404, 'unknown endpoint')
                return
            if self.headers.get('Transfer-Encoding'):
                self.reply(400, 'content length required')
                return
            try:
                length = int(self.headers.get('Content-Length', '0'))
                if not 0 < length <= 16 * 1024 * 1024:
                    raise ValueError()
                raw = self.rfile.read(length)
                value = json.loads(raw)
                if len(raw) != length or value['model'] != config['model']:
                    raise ValueError()
            except (ValueError, KeyError, TypeError):
                self.reply(400, 'invalid model request')
                return
            if not reserve():
                self.reply(429, 'campaign request limit reached; inspect before continuing')
                return
            request = urllib.request.Request(config['base_url'].rstrip('/') + '/chat/completions',
                data=raw, headers={'Authorization': 'Bearer ' + config['api_key'],
                                   'Content-Type': 'application/json'}, method='POST')
            status = None
            output_bytes = 0
            outcome = 'transport-failed'
            try:
                with opener.open(request, timeout=180) as response:
                    status = response.status
                    if not 200 <= status < 300:
                        raise ValueError('upstream status')
                    self.send_response(status)
                    self.send_header('Content-Type', response.headers.get('Content-Type', 'application/json'))
                    self.end_headers()
                    while chunk := response.read1(65536):
                        self.wfile.write(chunk)
                        self.wfile.flush()
                        output_bytes += len(chunk)
                    outcome = 'complete'
            except urllib.error.HTTPError as error:
                status = error.code
                error.close()
                self.reply(502, 'provider rejected request; response body withheld')
                outcome = 'upstream-rejected'
            except (OSError, ValueError):
                # Errors never retain provider text, response bodies or headers.
                outcome = 'transport-failed'
                if status is None:
                    self.reply(502, 'provider transport failed')
            finally:
                record(dict(at=datetime.now(timezone.utc).isoformat(),seconds=time.monotonic()-start,
                    request_bytes=length,response_bytes=output_bytes,status=status,outcome=outcome,
                    automatic_retry=False))

    return Handler


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--private-config', type=Path, required=True)
    parser.add_argument('--out', type=Path)
    parser.add_argument('--initialize-provider-stdin', action='store_true')
    parser.add_argument('--client-config', type=Path)
    parser.add_argument('--bind')
    parser.add_argument('--port', type=int, default=18443)
    parser.add_argument('--max-requests', type=int, default=128)
    args = parser.parse_args()
    if args.initialize_provider_stdin:
        if not args.client_config or not args.bind:
            parser.error('Initialization requires --client-config and --bind')
        provider = json.loads(sys.stdin.read().lstrip('\ufeff'))
        config = validate_config(dict(bind=args.bind,port=args.port,base_url=provider['options']['baseURL'],
            api_key=provider['options']['apiKey'],relay_key=secrets.token_hex(32),
            model='deepseek-v4-flash',max_requests=args.max_requests))
        client = {'$schema':'https://opencode.ai/config.json','provider':{'experiment':{
            'npm':provider['npm'],'models':{'deepseek-v4-flash':provider['models']['deepseek-v4-flash']},
            'options':{'baseURL':f'http://{args.bind}:{args.port}/v1','apiKey':config['relay_key']}}},
            'model':'experiment/deepseek-v4-flash','small_model':'experiment/deepseek-v4-flash',
            'share':'disabled','permission':{'*':'allow','task':'deny','webfetch':'deny','websearch':'deny','codesearch':'deny'}}
        for path in (args.private_config,args.client_config):
            if path.exists() or path.is_symlink():
                raise FileExistsError('New private configuration paths required')
        for path,value in ((args.private_config,config),(args.client_config,client)):
            path.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
            with os.fdopen(os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600),'w') as stream:
                json.dump(value,stream);stream.write('\n')
        print(json.dumps(dict(initialized=True,provider_master_key_in_client_config=False,
                             model=config['model'],max_requests=config['max_requests'])))
        return
    if not args.out:
        parser.error('Serving requires --out')
    config = configuration(args.private_config)
    args.out.mkdir(parents=True, exist_ok=False, mode=0o700)
    log = (args.out/'calls.jsonl').open('x')
    lock = threading.Lock()
    count = 0

    def reserve():
        nonlocal count
        with lock:
            if count >= config['max_requests']:
                return False
            count += 1
            return True

    def record(row):
        with lock:
            log.write(json.dumps(row)+'\n')
            log.flush()
            os.fsync(log.fileno())

    server = ThreadingHTTPServer((config['bind'], config['port']), handler(config, record, reserve))
    server.daemon_threads = True
    server.timeout = 30
    (args.out/'ready.json').write_text(json.dumps(dict(pid=os.getpid(),bind=config['bind'],
        port=server.server_port,model=config['model'],max_requests=config['max_requests'],
        script_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        provider_master_key_in_workspace=False,request_bodies_logged=False,
        scope='Private relay availability only; not a model or handoff qualification'))+'\n')
    try:
        server.serve_forever()
    finally:
        server.server_close()
        log.close()


if __name__ == '__main__':
    main()
