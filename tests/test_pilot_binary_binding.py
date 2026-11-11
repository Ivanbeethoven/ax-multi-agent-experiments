"""A new asset must keep its selected executable, including after old-path changes."""
import copy
import json
from pathlib import Path
import runpy
import tempfile
import unittest
from unittest.mock import patch

REPO=Path(__file__).resolve().parents[1]
MODULE=runpy.run_path(str(REPO/'experiments/unified-suite/pilot-aliyun/scripts/pilot-node.py'))
select=MODULE['filesystem_binary']
admission=runpy.run_path(str(REPO/'experiments/unified-suite/pilot-aliyun/scripts/admit-c1.py'))


class BinaryBinding(unittest.TestCase):
    def test_new_asset_keeps_its_binary_and_rejects_later_replacement(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'brewfs';path.write_bytes(b'new build')
            ref=dict(path=str(path),sha256=MODULE['digest'](path))
            asset=dict(product='brewfs',filesystem_binary=ref);original=copy.deepcopy(asset)
            with patch.dict(select.__globals__,ref=lambda p:(_ for _ in ()).throw(AssertionError('legacy binary accessed'))):
                self.assertEqual(select(asset),ref)
            path.write_bytes(b'different build')
            with self.assertRaises(ValueError):select(asset)
            self.assertEqual(asset,original)

    def test_legacy_asset_is_read_without_rewriting_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'original';path.write_bytes(b'original build')
            ref=dict(path=str(path),sha256=MODULE['digest'](path));asset=dict(product='brewfs')
            with patch.dict(select.__globals__,ref=lambda p:ref):self.assertEqual(select(asset),ref)
            self.assertEqual(asset,dict(product='brewfs'))

    def test_external_juicefs_release_is_separate_from_self_built_manifest(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)/'juicefs';root.mkdir();expected='6d450d3c151b6affede364d9a198d2505111b0612a5fb4d31613444c635174cc'
            binary={'path':'/srv/ax-client/assets/bin/juicefs','sha256':expected};asset={'product':'juicefs','filesystem_binary':binary}
            def put(name,value):
                path=root/name;path.write_text(json.dumps(value));return admission['ref'](path)
            release=put('release.json',dict(tag_name='v1.4.2',draft=False,prerelease=False,html_url='https://github.com/juicedata/juicefs/releases/tag/v1.4.2'))
            route=put('original-route.json',dict(passed=True,normally_stopped=True))
            (root.parent/'transfers.json').write_text(json.dumps([dict(source='/srv/route/result.json',**route)]))
            put('filesystem.json',dict(passed=True,native_checks=route))
            command=put('original-command.json',[binary['path'],'mount','redis://metadata/61','/mount'])
            proof=dict(schema='pilot-external-client-binary-v1',passed=True,product='juicefs',binary=binary,
                version_output='juicefs version 1.4.2+2026-10-22.46af7af\n',release=release,qualified_route=route,mount_command=command,
                mount_command_source='/srv/route/mount-1/command.json')
            put('client-binary.json',proof)
            build={'files':[{'file':'brewfs','sha256':'a'*64}]};original=copy.deepcopy(build)
            result=admission['asset_binary_proof'](asset,'juicefs',build,root)
            self.assertEqual(result['source'],'fixed external release');self.assertEqual(build,original)
            Path(release['path']).write_text('{}')
            with self.assertRaises(ValueError):admission['asset_binary_proof'](asset,'juicefs',build,root)

    def test_wrong_external_binary_version_or_route_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)/'juicefs';root.mkdir();expected='6d450d3c151b6affede364d9a198d2505111b0612a5fb4d31613444c635174cc'
            def put(name,value):
                path=root/name;path.write_text(json.dumps(value));return admission['ref'](path)
            release=put('release.json',dict(tag_name='v1.4.2',draft=False,prerelease=False,html_url='https://github.com/juicedata/juicefs/releases/tag/v1.4.2'))
            route=put('route.json',dict(passed=True,binary_sha256=expected))
            (root.parent/'transfers.json').write_text(json.dumps([dict(source='/srv/route/result.json',**route)]))
            put('filesystem.json',dict(passed=True,complete_base=route))
            binary=dict(path='/srv/ax-client/assets/bin/juicefs',sha256=expected)
            command=put('original-command.json',dict(argv=[binary['path'],'mount','redis://metadata/60','/mount']))
            proof=dict(schema='pilot-external-client-binary-v1',passed=True,product='juicefs',binary=binary,
                version_output='juicefs version 1.4.2+release',release=release,qualified_route=route,mount_command=command,
                mount_command_source='/srv/route/mount/command.reviewed.json')
            for field,value in [('version_output','juicefs version 1.2.1'),('binary',dict(binary,sha256='0'*64)),('qualified_route',dict(route,sha256='0'*64))]:
                broken=dict(proof,**{field:value});put('client-binary.json',broken)
                with self.assertRaises(ValueError):admission['asset_binary_proof']({'filesystem_binary':binary},'juicefs',{'files':[]},root)

    def test_c1_c2_and_c3_routes_accept_their_actual_mount_commands(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)/'juicefs';root.mkdir();expected='6d450d3c151b6affede364d9a198d2505111b0612a5fb4d31613444c635174cc'
            def put(name,value):
                path=root/name;path.write_text(json.dumps(value));return admission['ref'](path)
            binary=dict(path='/srv/ax-client/assets/bin/juicefs',sha256=expected)
            release=put('release.json',dict(tag_name='v1.4.2',draft=False,prerelease=False,html_url='https://github.com/juicedata/juicefs/releases/tag/v1.4.2'))
            route=put('route.json',dict(passed=True))
            original=dict(source='/srv/preparation/c3/run/result.json',**route)
            (root.parent/'transfers.json').write_text(json.dumps([original]))
            alternate=put('other-route.json',dict(passed=True,other=True))
            command=put('c3-original-command.reviewed.json',dict(argv=[binary['path'],'mount','redis://metadata/60','/mount'],reviewed=True))
            proof=dict(schema='pilot-external-client-binary-v1',passed=True,product='juicefs',binary=binary,
                version_output='juicefs version 1.4.2+release',release=release,qualified_route=route,mount_command=command,
                mount_command_source='/srv/preparation/c3/run/mount/command.reviewed.json')
            for key in ('native_checks','complete_route','complete_base'):
                put('filesystem.json',dict(passed=True,**{key:route}))
                put('client-binary.json',proof)
                result=admission['asset_binary_proof']({'filesystem_binary':binary},'juicefs',{'files':[]},root)
                self.assertEqual(result['qualified_route'],route)
            put('filesystem.json',dict(passed=True,native_checks=alternate,complete_base=route))
            admission['asset_binary_proof']({'filesystem_binary':binary},'juicefs',{'files':[]},root)
            for argv in [['/old/juicefs','mount','/mount'],[binary['path'],'sync','/mount'],[]]:
                badcommand=put('wrong-command.json',dict(argv=argv))
                put('client-binary.json',dict(proof,mount_command=badcommand))
                with self.assertRaises(ValueError):
                    admission['asset_binary_proof']({'filesystem_binary':binary},'juicefs',{'files':[]},root)
            for source in ['/srv/unrelated/mount/command.json','/srv/preparation/c3/run/../other/command.json','relative/command.json']:
                put('client-binary.json',dict(proof,mount_command_source=source))
                with self.assertRaises(ValueError):
                    admission['asset_binary_proof']({'filesystem_binary':binary},'juicefs',{'files':[]},root)
            put('client-binary.json',proof)
            for transfers in [[],[original,original]]:
                (root.parent/'transfers.json').write_text(json.dumps(transfers))
                with self.assertRaises(ValueError):
                    admission['asset_binary_proof']({'filesystem_binary':binary},'juicefs',{'files':[]},root)

    def test_missing_self_build_file_is_a_clear_rejection(self):
        with self.assertRaises(ValueError):
            admission['asset_binary_proof']({'filesystem_binary':{'path':'/brewfs','sha256':'a'*64}},'brewfs',{'files':[]},Path('/unused'))
