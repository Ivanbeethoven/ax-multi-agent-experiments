import importlib.util
import io
import json
from pathlib import Path
import tempfile
import threading
import unittest
import subprocess
import sys
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer


SCRIPT = Path(__file__).resolve().parents[1]/'experiments/unified-suite/pilot-aliyun/scripts/session-relay.py'
spec = importlib.util.spec_from_file_location('pilot_session_relay', SCRIPT)
relay = importlib.util.module_from_spec(spec)
spec.loader.exec_module(relay)


class Response:
    status = 200
    headers = {'Content-Type': 'text/event-stream'}

    def __init__(self):
        self.stream = io.BytesIO(b'data: {"completed":true}\n\n')

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.stream.close()

    def read1(self, size):
        return self.stream.read(size)


class RelayTests(unittest.TestCase):
    def setUp(self):
        self.config = dict(bind='127.0.0.1',port=18443,base_url='https://provider.invalid/v1',
            api_key='master-private-key',relay_key='relay-only-'+('x'*32),model='deepseek-v4-flash',max_requests=2)

    def test_private_config_and_explicit_private_bind(self):
        with tempfile.TemporaryDirectory() as temporary:
            path=Path(temporary)/'private.json'
            path.write_text(json.dumps(self.config));path.chmod(0o600)
            self.assertEqual(relay.configuration(path),self.config)
            path.chmod(0o644)
            with self.assertRaises(ValueError):relay.configuration(path)
            path.chmod(0o600)
            for changes in ({'bind':'0.0.0.0'},{'bind':'8.8.8.8'},{'base_url':'http://provider.invalid'},
                            {'base_url':'https://user:password@provider.invalid'},{'relay_key':'short'}):
                path.write_text(json.dumps(self.config|changes))
                with self.subTest(changes=changes),self.assertRaises(ValueError):relay.configuration(path)

    def exercise(self, upstream):
        records=[];calls=[]
        class Opener:
            def open(self, request, timeout):
                calls.append(request)
                return upstream(request)
        def reserve():return len(calls)<2
        server=ThreadingHTTPServer(('127.0.0.1',0),relay.handler(self.config,records.append,reserve,Opener()))
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        def post(model='deepseek-v4-flash',key=None,path='/v1/chat/completions'):
            raw=json.dumps(dict(model=model,messages=[dict(role='user',content='private prompt')])).encode()
            request=urllib.request.Request(f'http://127.0.0.1:{server.server_port}'+path,data=raw,
                headers={'Authorization':'Bearer '+(key or self.config['relay_key'])})
            try:
                with urllib.request.urlopen(request,timeout=5) as response:return response.status,response.read()
            except urllib.error.HTTPError as error:
                with error:return error.code,error.read()
        return server,thread,records,calls,post

    def test_stream_auth_model_allowlist_and_request_limit(self):
        server,thread,records,calls,post=self.exercise(lambda request:Response())
        try:
            self.assertEqual(post(key='invalid')[0],401)
            self.assertEqual(post(model='another-model')[0],400)
            self.assertEqual(post(path='/v1/chat/completions?redirect=other')[0],404)
            self.assertEqual(len(calls),0)
            self.assertEqual(post(),(200,b'data: {"completed":true}\n\n'))
            self.assertEqual(post()[0],200)
            self.assertEqual(post()[0],429)
            self.assertEqual(len(calls),2)
            self.assertEqual(calls[0].full_url,'https://provider.invalid/v1/chat/completions')
            self.assertEqual(calls[0].get_header('Authorization'),'Bearer master-private-key')
            self.assertEqual(len(records),2)
            raw=json.dumps(records)
            for secret in ('master-private-key','private prompt','relay-only-'):
                self.assertNotIn(secret,raw)
            self.assertTrue(all(row['outcome']=='complete' and row['automatic_retry'] is False for row in records))
        finally:
            server.shutdown();server.server_close();thread.join()

    def test_provider_error_body_is_withheld(self):
        def upstream(request):
            raise urllib.error.HTTPError(request.full_url,401,'private master-private-key',{},io.BytesIO(b'master-private-key'))
        server,thread,records,calls,post=self.exercise(upstream)
        try:
            code,body=post()
            self.assertEqual(code,502)
            self.assertNotIn(b'master-private-key',body)
            self.assertEqual(records[0]['outcome'],'upstream-rejected')
        finally:
            server.shutdown();server.server_close();thread.join()

    def test_explicit_nonthinking_policy_preserves_task_and_model_without_logging_body(self):
        self.config['thinking_mode']='disabled'
        server,thread,records,calls,post=self.exercise(lambda request:Response())
        try:
            self.assertEqual(post()[0],200)
            forwarded=json.loads(calls[0].data)
            self.assertEqual(forwarded['thinking'],{'type':'disabled'})
            self.assertEqual(forwarded['model'],'deepseek-v4-flash')
            self.assertEqual(forwarded['messages'],[dict(role='user',content='private prompt')])
            self.assertEqual(records[0]['thinking_policy'],'disabled')
            self.assertEqual(records[0]['forwarded_request_bytes'],len(calls[0].data))
            self.assertNotIn('private prompt',json.dumps(records))
        finally:
            server.shutdown();server.server_close();thread.join()

    def test_upstream_redirect_is_disabled(self):
        self.assertIsNone(relay.NoRedirect().redirect_request(None,None,302,'',{},'https://other.invalid'))

    def test_initialize_does_not_put_master_key_in_client_config(self):
        provider=dict(npm='@ai-sdk/openai-compatible',models={'deepseek-v4-flash':dict(name='DeepSeek')},
                      options=dict(baseURL=self.config['base_url'],apiKey=self.config['api_key']))
        with tempfile.TemporaryDirectory() as temporary:
            private=Path(temporary)/'private/relay.json';client=Path(temporary)/'client.json'
            argv=[sys.executable,str(SCRIPT),'--initialize-provider-stdin','--bind','10.20.0.10',
                  '--private-config',str(private),'--client-config',str(client)]
            result=subprocess.run(argv,input=json.dumps(provider),text=True,capture_output=True)
            self.assertEqual(result.returncode,0,result.stderr)
            for raw in (result.stdout,result.stderr,client.read_text()):
                self.assertNotIn('master-private-key',raw)
            actual=relay.configuration(private)
            self.assertEqual(actual['api_key'],'master-private-key')
            self.assertNotEqual(actual['relay_key'],actual['api_key'])
            self.assertEqual(json.loads(client.read_text())['provider']['experiment']['options']['apiKey'],actual['relay_key'])
            before=private.read_bytes(),client.read_bytes()
            result=subprocess.run(argv,input=json.dumps(provider),text=True,capture_output=True)
            self.assertNotEqual(result.returncode,0)
            self.assertEqual(before,(private.read_bytes(),client.read_bytes()))


if __name__=='__main__':unittest.main()
