"""Checkpoint install exercises real SQLite, copying, sync and CLI export receipt parsing."""
from copy import deepcopy
from pathlib import Path
import runpy
import socket
import sqlite3
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from bench.runtime.common import digest, load, write_new
from bench.session_check import checkpoint


SCRIPT = Path(__file__).resolve().parents[1]/'experiments/unified-suite/pilot-aliyun/scripts/prepare-c4-native-handoff-source.py'
MODULE = runpy.run_path(str(SCRIPT))
M = MODULE['run'].__globals__
WORKER_FIXTURE = """
import argparse,json,pathlib
p=argparse.ArgumentParser()
for name in ('request','sha256','out'):p.add_argument('--'+name,required=True)
a=p.parse_args()
d=pathlib.Path(a.request)
assert __import__('hashlib').sha256(d.read_bytes()).hexdigest()==a.sha256
r=json.loads(d.read_bytes())
assert r['action']=='export' and r['total_timeout_seconds']==60
root=pathlib.Path(r['workspace'])
assert r['project']==str(root/'repo')
assert r['config']==str(root/'config/opencode.json')
settings=json.loads(pathlib.Path(r['config']).read_bytes())
assert settings['provider']['experiment']['models']['deepseek-v4-flash']['limit']=={'context':0,'output':512}
o=pathlib.Path(a.out);o.mkdir()
(o/'bounded.json').write_text(json.dumps(dict(passed=True,exit_code=0,descendants_cleared=True,timed_out=False,total_timeout_seconds=60)))
(o/'started.json').write_text(json.dumps(dict(action='export',binary=r['binary'],session_state=dict(
    home_empty_before_start=True,xdg=r['state'],config_sha256=__import__('hashlib').sha256(pathlib.Path(r['config']).read_bytes()).hexdigest()))))
(o/'export.json').write_bytes((root/'session-export.json').read_bytes())
"""


def put(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(value, str):
        path.write_text(value)
    else:
        write_new(path, value)
    return path


class PrepareSource(unittest.TestCase):
    def setUp(self):
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name).resolve()
        self.point = self.root/'shared'
        self.point.mkdir()
        self.out = self.root/'evidence'
        self.source = self.root/'verified/workspace'
        self.original_export = self.source/'session-export.json'
        self.point_patch = patch.dict(M, POINT=self.point)
        self.point_patch.start()
        self.addCleanup(self.point_patch.stop)
        self.binary = put(self.root/'opencode','frozen binary test bytes')
        self.worker = put(self.root/'session-worker-bounded.py', WORKER_FIXTURE)
        export = {'info':{'id':'ses_source'},'messages':[
            {'info':{'id':'m0'},'parts':[{'type':'tool','state':{'status':'completed'}}]}]}
        put(self.original_export,export)
        db = self.source/'source/data/opencode/opencode.db'
        db.parent.mkdir(parents=True)
        with sqlite3.connect(db) as sql:
            sql.execute('create table session (id text)')
            sql.execute("insert into session values ('ses_source')")
        for directory in ('state','cache','config'):
            (self.source/'source'/directory).mkdir()
        put(self.source/'repo/checkpoint.txt','ready\n')
        (self.source/'repo/inside').symlink_to('checkpoint.txt')
        old = {'model':'experiment/deepseek-v4-flash','share':'disabled',
            'provider':{'experiment':{'npm':'fixed-sdk','options':{'baseURL':'http://127.0.0.1:8123/v1',
                'apiKey':MODULE['DUMMY_KEY']},'models':{'deepseek-v4-flash':{'name':'Frozen model'}}}}}
        self.old_config = put(self.source/'config/opencode.json',old)
        self.config = deepcopy(old)
        provider = self.config['provider']['experiment']
        self.url = 'http://127.0.0.1:18444/v1'
        self.key = 'a'*64
        provider['options'].update(baseURL=self.url, apiKey=self.key)
        provider['models']['deepseek-v4-flash']['limit'] = dict(context=0,output=512)
        self.private = put(self.root/'private/client.json',self.config)
        self.private.chmod(0o600)
        self.checkpoint = put(self.root/'verified/checkpoint.json',dict(session_id='ses_source',
            canonical_workspace=str(self.point/'workspace'),canonical_project=str(self.point/'workspace/repo'),
            session_export_sha256=digest(self.original_export),session_prefix=checkpoint(export),
            source_process_exited=True,sqlite_quick_check='ok',contains_provider_master_key=False,
            opencode_binary_sha256=digest(self.binary)))
        self.tree_tool = SCRIPT.with_name('prepare-session-input.py')
        tree = MODULE['source_tools'](self.pin(self.tree_tool)).tree(self.source)
        self.source_tree = put(self.root/'verified/source-tree.json',tree)
        self.receipt = put(self.root/'receipt.json',dict(passed=True,session_id='ses_source',
            checkpoint_sha256=digest(self.checkpoint),opencode_sha256=digest(self.binary),
            contains_provider_master_key=False,contains_active_relay_key=False,
            tree_reader_sha256=digest(self.tree_tool),files={f'workspace/{k}':v for k,v in tree.items()}))
        self.anchor = dict(volume='same-volume',workspace='same-workspace')
        self.mount = put(self.root/'mount.reviewed.json',dict(schema='experiment-mount-v1',reviewed=True,
            filesystem='juicefs',cluster='pool-a',purpose='experiment',mount_point=str(self.point),anchor=self.anchor))
        self.mounted = dict(point=str(self.point),mount_id=20,filesystem='fuse.juicefs',source='JuiceFS:same-volume')
        self.identity = dict(pid=20,boot_id='boot-a',start_ticks=172,state='S')
        self.ready = dict(passed=True,spec=self.pin(self.mount),anchor=self.anchor,mount=self.mounted,process=self.identity)
        put(self.root/'supervisor/ready.json',self.ready)
        self.control = patch.object(M['node'],'control',return_value=self.ready | dict(process=self.identity|dict(state='R')))
        self.node = self.control.start()
        self.addCleanup(self.control.stop)
        self.mounts = patch.dict(M,mounts=lambda point:[self.mounted])
        self.mounts.start()
        self.addCleanup(self.mounts.stop)
        self.req = dict(schema=M['SCHEMA'],reviewed=True,source=dict(role='client',side='source',pool='pool-a',
            hostname=socket.gethostname()),mount=dict(spec=self.pin(self.mount),directory=str(self.root/'supervisor')),
            checkpoint=self.pin(self.checkpoint),checkpoint_receipt=self.pin(self.receipt),source_tree=self.pin(self.source_tree),
            tree_reader=self.pin(self.tree_tool),worker=self.pin(self.worker),binary=self.pin(self.binary),
            private_client_config=self.pin(self.private),relay_url=self.url)

    @staticmethod
    def pin(path):
        return dict(path=str(path),sha256=digest(path))

    def execute(self):
        request = put(self.root/'source.request.json',self.req)
        return MODULE['run'](self.pin(request),self.out)

    def test_exact_export_and_no_secret_in_retained_evidence(self):
        before = self.old_config.read_bytes()
        result = self.execute()
        self.assertTrue(result['passed'])
        self.assertEqual(result['model_calls'],0)
        self.assertEqual(result['canonical_export']['sha256'],digest(self.original_export))
        self.assertEqual(result['session_state']['xdg']['data'],str(self.point/'workspace/source/data'))
        config = Path(result['session_state']['config'])
        self.assertEqual(config.stat().st_mode & 0o777,0o600)
        self.assertEqual(load(config),self.config)
        self.assertEqual(self.old_config.read_bytes(),before)
        self.assertEqual((self.point/'workspace/repo/inside').read_text(),'ready\n')
        self.assertNotIn(self.key.encode(), b''.join(p.read_bytes() for p in self.out.rglob('*') if p.is_file()))
        self.assertEqual(self.node.call_count,3)

    def test_refuses_unreviewed_request(self):
        self.req['reviewed']=False
        with self.assertRaisesRegex(ValueError,'reviewed'):
            self.execute()
        self.assertFalse((self.point/'workspace').exists())

    def test_refuses_wrong_executor_before_mount_probe(self):
        self.req['source']['hostname']='other-executor'
        with self.assertRaisesRegex(ValueError,'reviewed source executor'):
            self.execute()
        self.node.assert_not_called()
        self.assertFalse((self.point/'workspace').exists())

    def test_refuses_changed_mount_process_before_install(self):
        self.node.return_value = self.ready | dict(process=self.identity | dict(start_ticks=999))
        with self.assertRaisesRegex(ValueError,'mount instance changed'):
            self.execute()
        self.assertFalse((self.point/'workspace').exists())

    def test_refuses_changed_tree_before_install(self):
        (self.source/'repo/checkpoint.txt').write_text('changed\n')
        with self.assertRaisesRegex(ValueError,'tree differs'):
            self.execute()
        self.assertFalse(self.out.exists())
        self.assertFalse((self.point/'workspace').exists())

    def test_refuses_a_nonprovider_config_change_before_install(self):
        self.config['share']='enabled'
        self.private.unlink()
        put(self.private,self.config)
        self.private.chmod(0o600)
        self.req['private_client_config']=self.pin(self.private)
        with self.assertRaisesRegex(ValueError,'Only the client relay'):
            self.execute()
        self.assertFalse((self.point/'workspace').exists())

    def test_rejects_changed_export_and_preserves_first_failure(self):
        self.worker.write_text(WORKER_FIXTURE.replace("write_bytes((root/'session-export.json').read_bytes())",
            "write_bytes((root/'session-export.json').read_bytes()+b' ')"))
        self.req['worker']=self.pin(self.worker)
        with self.assertRaisesRegex(ValueError,'export bytes differ'):
            self.execute()
        self.assertTrue((self.out/'failed.json').is_file())
        self.assertFalse((self.out/'result.json').exists())
        self.assertFalse(load(self.out/'failed.json')['automatic_retry'])
        self.assertTrue((self.point/'workspace').exists())

    def test_refuses_old_relay(self):
        self.req['relay_url']='http://127.0.0.1:18443/v1'
        with self.assertRaisesRegex(ValueError,'Independent loopback'):
            self.execute()
        self.assertFalse((self.point/'workspace').exists())

    def test_no_overwrite_of_existing_workspace(self):
        put(self.point/'workspace/keep.txt','keep\n')
        with self.assertRaisesRegex(ValueError,'fresh shared workspace'):
            self.execute()
        self.assertEqual((self.point/'workspace/keep.txt').read_text(),'keep\n')
        self.assertFalse(self.out.exists())


if __name__ == '__main__':
    unittest.main()
