"""Regression tests for the parallel worker's registry and diagnostic guards."""
import ast
import json
from pathlib import Path
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

ROOT=Path(__file__).resolve().parents[3]
SOURCE=ROOT/'infra/alicloud/parallel/worker_bootstrap.py'


def helpers(out):
    tree=ast.parse(SOURCE.read_text(encoding='utf-8'))
    names={'save','redact_diagnostic','read_client_env','preparation_pin_diagnostics',
           'sanitize_trace2','registry_owned_ready','registry_upload_probe'}
    body=[n for n in tree.body if isinstance(n,(ast.Import,ast.ImportFrom)) or
          isinstance(n,ast.FunctionDef) and n.name in names]
    ns={'OUT':out,'ATTEMPT':'prep-test','registry_request':Mock(),'call':Mock()}
    exec(compile(ast.Module(body=body,type_ignores=[]),str(SOURCE),'exec'),ns)
    return ns


class WorkerPreparationTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.out=Path(self.tmp.name);self.ns=helpers(self.out)
    def tearDown(self):self.tmp.cleanup()

    def test_redaction_covers_url_userinfo_query_and_secrets(self):
        value='failure https://user:pass@private.example/path?Signature=abc access_key=xyz secret=abcdef\nAuthorization: Bearer opaque-token'
        got=self.ns['redact_diagnostic'](value,['abcdef'])
        for word in ('user:pass','Signature=abc','xyz','abcdef','opaque-token'):
            self.assertNotIn(word,got)

    def test_diagnostics_survive_failed_command_without_serializing_container_env(self):
        env=self.out/'env';env.write_text('AWS_ACCESS_KEY_ID=KEYPRIVATE\nAWS_SECRET_ACCESS_KEY=MYSECRET123\n')
        results=[SimpleNamespace(returncode=1,stdout='',stderr='failed https://user:password@private/x?Signature=x MYSECRET123'),
                 SimpleNamespace(returncode=0,stdout=json.dumps([{'Id':'sha256:abc','RepoDigests':['r@sha256:123'],
                     'Config':{'Labels':{'org.opencontainers.image.revision':'abc'},'Env':['AWS_SECRET_ACCESS_KEY=MYSECRET123']}}]),stderr=''),
                 SimpleNamespace(returncode=0,stdout='"sha256:abc"',stderr=''),
                 SimpleNamespace(returncode=0,stdout='true',stderr='')]
        with patch.object(self.ns['subprocess'],'run',side_effect=results):
            self.ns['preparation_pin_diagnostics']('pin','image','container',env,required=False)
        text=(self.out/'pin-preflight-diagnostics.json').read_text()
        self.assertNotIn('MYSECRET123',text);self.assertNotIn('user:password',text)
        self.assertNotIn('Config',text);self.assertNotIn('Signature=x',text)
        data=json.loads(text);self.assertEqual([r['returncode'] for r in data['commands']],[1,0,0,0])

    def test_required_pin_failure_is_saved_before_raising(self):
        env=self.out/'env';env.write_text('')
        result=SimpleNamespace(returncode=1,stdout='',stderr='error')
        with patch.object(self.ns['subprocess'],'run',return_value=result):
            with self.assertRaises(RuntimeError):self.ns['preparation_pin_diagnostics']('pin','image','container',env)
        self.assertTrue((self.out/'pin-preflight-diagnostics.json').exists())

    def test_trace2_only_whitelisted_fields_and_sanitized(self):
        src=self.out/'private';src.write_text(json.dumps({'event':'error','code':1,'argv':['supersecret'],
            'msg':'fatal: https://user:pass@host/?signature=xyz supersecret','sid':'safe-session'})+'\n')
        dest=self.out/'trace.json';self.ns['sanitize_trace2'](src,dest,['supersecret'])
        text=dest.read_text();self.assertNotIn('supersecret',text);self.assertNotIn('user:pass',text)
        self.assertEqual(json.loads(text)['events'][0]['code'],1)
        self.assertIn('fatal:',json.loads(text)['events'][0]['msg'])

    def test_healthy_registry_must_be_owned_by_our_unit(self):
        self.ns['registry_request'].return_value=(200,{},b'')
        self.ns['call'].side_effect=['123','LISTEN users:(("registry",pid=123,fd=5))']
        self.assertTrue(self.ns['registry_owned_ready']())
        self.ns['call'].side_effect=['123','LISTEN users:(("other",pid=456,fd=5))']
        with self.assertRaises(ValueError):self.ns['registry_owned_ready']()

    def test_registry_upload_probe_executes_post_and_cleans_upload(self):
        self.ns['registry_request'].side_effect=[(202,{'Location':'http://127.0.0.1:5000/v2/ax/rustfs/blobs/uploads/uuid?_state=s'},b''),(204,{},b'')]
        self.ns['registry_upload_probe']()
        self.assertEqual(self.ns['registry_request'].call_args_list[1].args,('DELETE','/v2/ax/rustfs/blobs/uploads/uuid?_state=s'))

    def test_registry_probe_rejects_unrelated_location_and_failed_delete(self):
        self.ns['registry_request'].return_value=(202,{'Location':'https://evil.example/v2/ax/rustfs/blobs/uploads/x'},b'')
        with self.assertRaises(ValueError):self.ns['registry_upload_probe']()
        self.ns['registry_request'].side_effect=[(202,{'Location':'/v2/ax/rustfs/blobs/uploads/x'},b''),(500,{},b'')]
        with self.assertRaises(ValueError):self.ns['registry_upload_probe']()

    def test_unhealthy_registry_is_not_success(self):
        self.ns['registry_request'].return_value=(401,{},b'')
        self.assertFalse(self.ns['registry_owned_ready']());self.ns['call'].assert_not_called()


if __name__=='__main__':unittest.main()
