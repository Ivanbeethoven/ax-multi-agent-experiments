#!/usr/bin/env python3
"""Pinned TLS stdio transport; stdout contains only the inner SSH byte stream."""
import hashlib,os,socket,ssl,sys,threading
host,port,certificate,fingerprint,server_name=sys.argv[1:]
if os.name=='nt':
 import msvcrt
 msvcrt.setmode(0,os.O_BINARY);msvcrt.setmode(1,os.O_BINARY)
ctx=ssl.create_default_context(cafile=certificate);ctx.minimum_version=ssl.TLSVersion.TLSv1_2
with socket.create_connection((host,int(port)),timeout=15) as raw,ctx.wrap_socket(raw,server_hostname=server_name) as remote:
 if hashlib.sha256(remote.getpeercert(binary_form=True)).hexdigest()!=fingerprint:raise SystemExit('TLS certificate fingerprint mismatch')
 remote.settimeout(None)
 def upload():
  try:
   while True:
    data=os.read(0,65536)
    if not data:break
    remote.sendall(data)
  except OSError:pass
  finally:
   try:remote.shutdown(socket.SHUT_WR)
   except OSError:pass
 thread=threading.Thread(target=upload,daemon=True);thread.start()
 while True:
  data=remote.recv(65536)
  if not data:break
  os.write(1,data)
