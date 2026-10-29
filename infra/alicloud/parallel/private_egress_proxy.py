#!/usr/bin/env python3
"""Small source-restricted public dependency proxy for the private worker."""
import http.server,json,select,socket,socketserver,sys,urllib.parse
from pathlib import Path
ALLOWED={'github.com','archive.ubuntu.com','security.ubuntu.com','mirrors.cloud.aliyuncs.com','mirrors.aliyun.com'}
def allowed(host,port,connect):
 return bool(host) and not any(c in host for c in ('@','/','\\')) and (host in ALLOWED or host.endswith('.archive.ubuntu.com')) and port==(443 if connect else 80)
class Handler(http.server.BaseHTTPRequestHandler):
 protocol_version='HTTP/1.1'
 def log_message(self,*args):pass
 def permitted(self):
  if self.client_address[0] not in self.server.clients:self.send_error(403);return False
  return True
 def tunnel(self,upstream):
  self.connection.settimeout(None);upstream.settimeout(None)
  while True:
   ready,_,_=select.select([self.connection,upstream],[],[],60)
   if not ready:return
   for src in ready:
    data=src.recv(65536)
    if not data:return
    (upstream if src is self.connection else self.connection).sendall(data)
 def do_CONNECT(self):
  if not self.permitted():return
  try:host,raw=self.path.rsplit(':',1);port=int(raw)
  except (ValueError,AttributeError):self.send_error(400);return
  if not allowed(host,port,True):self.send_error(403);return
  try:
   with socket.create_connection((host,port),timeout=120) as upstream:
    self.send_response(200,'Connection established');self.end_headers();self.tunnel(upstream)
  except OSError:self.close_connection=True
  finally:self.close_connection=True
 def do_GET(self):
  if not self.permitted():return
  u=urllib.parse.urlsplit(self.path)
  if u.scheme!='http' or u.username or u.password or not allowed(u.hostname,u.port or 80,False):self.send_error(403);return
  try:
   with socket.create_connection((u.hostname,u.port or 80),timeout=30) as upstream:
    target=u.path or '/'
    if u.query:target+='?'+u.query
    lines=['GET '+target+' HTTP/1.1','Host: '+u.netloc,'Connection: close']
    for key,value in self.headers.items():
     if key.lower() not in ('host','connection','proxy-connection','proxy-authorization'):lines.append(key+': '+value)
    upstream.sendall(('\r\n'.join(lines)+'\r\n\r\n').encode('iso-8859-1'))
    self.close_connection=True
    while True:
     data=upstream.recv(65536)
     if not data:break
     self.connection.sendall(data)
  except OSError:self.close_connection=True
class Server(socketserver.ThreadingMixIn,http.server.HTTPServer):
 daemon_threads=True
if __name__=='__main__':
 c=json.loads(Path(sys.argv[1]).read_text());assert c['bind_ip'].startswith(('10.','172.','192.168.')) and c['clients'];server=Server((c['bind_ip'],3128),Handler);server.clients=set(c['clients']);server.serve_forever()
