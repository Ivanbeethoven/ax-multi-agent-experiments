#!/usr/bin/env python3
"""TLS transport for a fixed localhost SSH endpoint; SG and peer IP remain restricted."""
import time,json,socket,socketserver,ssl,sys,threading
from pathlib import Path
C=json.loads(Path(sys.argv[1]).read_text());CTX=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER);CTX.minimum_version=ssl.TLSVersion.TLSv1_2;CTX.load_cert_chain(C['certificate'],C['private_key'])
class Handler(socketserver.BaseRequestHandler):
 def record(self,stage,**extra):
  Path('/run/ax-backup-tls/last-connection.json').write_text(json.dumps({'epoch':time.time(),'stage':stage,'peer':self.client_address[0],**extra})+'\n')
 def handle(self):
  self.record('tcp_accepted')
  if self.client_address[0] not in C['allowed_sources']:
   self.record('source_rejected');return
  try:
   self.request.settimeout(15)
   with CTX.wrap_socket(self.request,server_side=True) as client,socket.create_connection(('127.0.0.1',22),timeout=15) as upstream:
    self.record('tls_accepted')
    client.settimeout(None);upstream.settimeout(None)
    def copy(src,dst):
     try:
      while True:
       data=src.recv(65536)
       if not data:break
       dst.sendall(data)
     except OSError:pass
     finally:
      try:dst.shutdown(socket.SHUT_WR)
      except OSError:pass
    t=threading.Thread(target=copy,args=(client,upstream),daemon=True);t.start();copy(upstream,client);t.join(timeout=2)
  except OSError as error:self.record('tls_or_transport_error',error_type=type(error).__name__)
class Server(socketserver.ThreadingMixIn,socketserver.TCPServer):
 allow_reuse_address=True;daemon_threads=True
with Server(('0.0.0.0',443),Handler) as server:server.serve_forever()
