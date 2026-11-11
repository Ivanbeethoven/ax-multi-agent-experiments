"""Signing is deterministic; public uploads reject private sources before transfer."""
import datetime
import importlib.util
from pathlib import Path
import unittest
import urllib.parse
import json
import sys
import tempfile
from unittest.mock import patch, MagicMock

FILE=Path(__file__).resolve().parents[1]/'experiments/unified-suite/pilot-aliyun/scripts/archive-cloud-to-oss.py'
SPEC=importlib.util.spec_from_file_location('cloud_oss_archive',FILE)
module=importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(module)


class CloudOSSArchive(unittest.TestCase):
    def test_cli_retains_receipt_without_credentials_or_signed_url(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);config=root/'credentials.json';out=root/'receipt.json'
            config.write_text(json.dumps(dict(current='test',profiles=[dict(name='test',mode='AK',
                access_key_id='fixture-id',access_key_secret='fixture-secret')])),encoding='utf-8')
            argv=['tool','--source','/srv/ax-pilot-control/proof.json','--sha256','a'*64,
                '--object','pilot-results/20261111/test/proof.json','--ssh-config','fixture',
                '--credentials',str(config),'--out',str(out)]
            response=MagicMock();response.__enter__.return_value=response
            response.status=200;response.headers={'Content-Length':'7'}
            with patch.object(sys,'argv',argv),patch.object(module,'cloud',side_effect=[
                dict(passed=True,bytes=7,content_md5='fixture',sha256='a'*64),
                dict(passed=True,bytes=7,sha256='a'*64)]),patch.object(module.urllib.request,'urlopen',return_value=response):
                module.main()
            data=out.read_text();value=json.loads(data)
            self.assertTrue(value['anonymous_public_head'])
            self.assertEqual(value['bytes_downloaded_to_local'],0)
            for secret in ('fixture-secret','fixture-id','x-oss-signature='):
                self.assertNotIn(secret,data)

    def test_put_binds_public_acl_md5_and_no_overwrite(self):
        now=datetime.datetime(2026,11,11,tzinfo=datetime.timezone.utc)
        profile=dict(access_key_id='fixture-id',access_key_secret='fixture-secret')
        headers={'content-length':'7','content-type':'application/octet-stream',
            'content-md5':'fixture','x-oss-object-acl':'public-read','x-oss-forbid-overwrite':'true'}
        first,actual=module.signed_put(profile,'fixture','pilot-results/20261111/a b.json',headers,now)
        self.assertEqual(first,module.signed_put(profile,'fixture','pilot-results/20261111/a b.json',headers,now)[0])
        self.assertIn('.oss-cn-hangzhou-internal.aliyuncs.com/',first)
        self.assertIn('/a%20b.json?',first)
        changed=dict(headers,**{'x-oss-forbid-overwrite':'false'})
        self.assertNotEqual(first,module.signed_put(profile,'fixture','pilot-results/20261111/a b.json',changed,now)[0])
        query=urllib.parse.parse_qs(urllib.parse.urlsplit(first).query)
        self.assertEqual(query['x-oss-expires'],['900'])
        self.assertEqual(query['x-oss-additional-headers'],['content-length;host'])
        self.assertNotIn(profile['access_key_secret'],first)
        self.assertEqual(actual['host'],'fixture.oss-cn-hangzhou-internal.aliyuncs.com')


if __name__=='__main__':unittest.main()
