"""A selected pool owns all remote work and the exact versioned client asset."""
import json
from pathlib import Path
import runpy
import tempfile
import unittest
from unittest.mock import patch

REPO=Path(__file__).resolve().parents[1]
MOD=runpy.run_path(str(REPO/'experiments/unified-suite/pilot-aliyun/scripts/run-c1.py'))
Cell=MOD['Cell'];digest=MOD['digest']


class CellRouting(unittest.TestCase):
    def fixture(self,root):
        c=Cell.__new__(Cell);c.out=root/'out';c.out.mkdir()
        c.runtime=root/'runtime';c.runtime.mkdir();c.control=root/'control'
        (c.control/'suite').mkdir(parents=True);(c.control/'suite/plan.json').write_text('{}')
        (c.control/'provenance').mkdir()
        c.pool='pool-b';c.arm='native';c.product='brewfs';c.asset='/srv/ax-client/a/new-native'
        c.qualification=root/'qualification/native'
        folder=c.qualification.parent/'assets/native';folder.mkdir(parents=True)
        binary='/srv/ax-client/build-ca07159-01/bin/brewfs';sha='a'*64
        asset=dict(pool=c.pool,plan={'sha256':digest(c.control/'suite/plan.json')},
                   filesystem_binary={'path':binary,'sha256':sha})
        path=folder/'asset.json';path.write_text(json.dumps(asset))
        c.s={'asset_manifest':{'path':str(path),'sha256':digest(path)}}
        (c.control/'provenance/build-manifest.json').write_text(json.dumps({'files':[{'file':'brewfs','sha256':sha}]}))
        return c,path,binary,sha

    def test_remote_and_transfer_use_selected_pool_with_byte_verification(self):
        with tempfile.TemporaryDirectory() as tmp:
            c,_,_,_=self.fixture(Path(tmp));calls=[]
            def run(argv,**kw):
                calls.append(argv)
                if argv[0]=='ssh':kw['stdout'].write(b'actual remote output')
                if argv[0]=='scp':Path(argv[-1]).write_bytes(b'evidence')
                return type('Result',(),{'returncode':0})()
            with patch.object(MOD['subprocess'],'run',side_effect=run):
                self.assertEqual(c.remote('objects',['printf','actual remote output']),'actual remote output')
                with patch.object(c,'remote',return_value=__import__('hashlib').sha256(b'evidence').hexdigest()+'  evidence'):
                    self.assertEqual(c.get('metadata','/srv/proof','received').read_bytes(),b'evidence')
            self.assertEqual(calls[0][3],'pool-b-objects')
            self.assertEqual(calls[1][3],'pool-b-metadata:/srv/proof')
            self.assertFalse(any('pool-a' in value for cmd in calls for value in cmd))

    def test_versioned_asset_uses_new_binary_and_rejects_remote_replacement(self):
        with tempfile.TemporaryDirectory() as tmp:
            c,path,binary,sha=self.fixture(Path(tmp))
            def remote(role,argv):
                return (digest(path) if argv[1].endswith('/asset.json') else sha)+'  checked'
            with patch.object(c,'remote',side_effect=remote) as mock:
                self.assertEqual(c.check_asset_binary(),binary)
                self.assertEqual(mock.call_args.args[1],['sha256sum',binary])
            with patch.object(c,'remote',side_effect=[digest(path)+'  asset','b'*64+'  changed']):
                with self.assertRaisesRegex(ValueError,'Deployed client binary'):c.check_asset_binary()

    def test_asset_for_another_pool_or_plan_is_not_admitted(self):
        for field in ('pool','plan'):
            with self.subTest(field=field),tempfile.TemporaryDirectory() as tmp:
                c,path,_,_=self.fixture(Path(tmp));asset=json.loads(path.read_text())
                asset[field]='pool-a' if field=='pool' else {'sha256':'b'*64}
                path.write_text(json.dumps(asset));c.s['asset_manifest']['sha256']=digest(path)
                with patch.object(c,'remote',return_value=digest(path)+'  asset'):
                    with self.assertRaisesRegex(ValueError,'another pool or plan'):c.check_asset_binary()


if __name__=='__main__':unittest.main()
