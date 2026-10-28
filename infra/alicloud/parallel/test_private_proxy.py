import socket,threading,unittest,types
from unittest.mock import patch
import private_egress_proxy as proxy
class ConnectClosureTests(unittest.TestCase):
 def test_real_client_gets_eof_after_tunnel_idle_return(self):
  upstream,peer=socket.socketpair();server=proxy.Server(('127.0.0.1',0),proxy.Handler);server.clients={'127.0.0.1'};client=socket.socket();client.settimeout(2)
  tunnel=types.FunctionType(proxy.Handler.tunnel.__code__,{**proxy.Handler.tunnel.__globals__,'select':types.SimpleNamespace(select=lambda *args:([],[],[]))})
  try:
   with patch.object(proxy.socket,'create_connection',return_value=upstream),patch.object(proxy.Handler,'tunnel',tunnel):
    thread=threading.Thread(target=server.handle_request,daemon=True);thread.start();client.connect(server.server_address);client.sendall(b'CONNECT github.com:443 HTTP/1.1\r\nHost: github.com:443\r\n\r\n');response=b''
    while True:
     chunk=client.recv(4096)
     if not chunk:break
     response+=chunk
    self.assertIn(b'200 Connection established',response);thread.join(2)
  finally:client.close();server.server_close();upstream.close();peer.close()
 def test_real_client_gets_eof_on_upstream_connection_failure(self):
  server=proxy.Server(('127.0.0.1',0),proxy.Handler);server.clients={'127.0.0.1'};client=socket.socket();client.settimeout(2)
  try:
   with patch.object(proxy.socket,'create_connection',side_effect=OSError('offline')):
    thread=threading.Thread(target=server.handle_request,daemon=True);thread.start();client.connect(server.server_address);client.sendall(b'CONNECT github.com:443 HTTP/1.1\r\nHost: github.com:443\r\n\r\n');self.assertEqual(client.recv(4096),b'');thread.join(2)
  finally:client.close();server.server_close()
if __name__=='__main__':unittest.main()
