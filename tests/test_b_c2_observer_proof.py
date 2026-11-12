import hashlib
from pathlib import Path
import runpy
from tempfile import TemporaryDirectory
import unittest

from bench.archive_check import pack


SCRIPT = (Path(__file__).resolve().parents[1]/
          'experiments/unified-suite/pilot-aliyun/scripts/plan-b-c2-observer-proof.py')
TOOL = runpy.run_path(str(SCRIPT))


class BC2Proof(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.formal = root/'objects.tar.gz'
        self.outer = root/'calibrations.tar.gz'
        self.cell = TOOL['CELL']
        formal = TOOL['FORMAL']
        members = []
        checks = []
        self.maps = {}
        self.groups = []
        for tag in TOOL['TAGS']:
            role = root/tag
            observer = 'o/'+tag+'/sample-000001/raw.txt'
            (role/observer).parent.mkdir(parents=True)
            data = tag.encode()
            (role/observer).write_bytes(data)
            unrelated = 'q/'+tag+'/result.json'
            (role/unrelated).parent.mkdir(parents=True)
            (role/unrelated).write_text('{}')
            self.maps[tag] = {'sample-000001/raw.txt':dict(bytes=len(data),sha256=hashlib.sha256(data).hexdigest())}
            raw = 'o/'+tag
            staged = ('evidence/'+tag+'/'+raw if tag==formal else
                      'retained-calibrations/pool-b-juicefs-c2-01/'+tag+'/raw/'+raw)
            self.groups.extend(dict(tag=tag,relative_path=path,files=1,allocated_bytes=4096)
                               for path in (raw,staged))
            if tag==formal:
                formal_check = pack(role,[observer,unrelated],self.formal,self.cell,'objects')
                continue
            member = tag+'/objects.tar.gz'
            nested = root/'outer'/member
            nested.parent.mkdir(parents=True)
            result = pack(role,[observer,unrelated],nested,self.cell,'calibration-'+tag+'-objects')
            members.append(member)
            checks.append(dict(member='calibrations.tar.gz!/'+member,depth=1,check=result))
        outer = pack(root/'outer',members,self.outer,self.cell,'six-calibration-trials')
        self.docs = dict(readback=dict(passed=True, anonymous_internal_get=True, objects={}),
                         candidates=dict(serial='bp1bhdngko7nq3c2i340',all_unmounted=True,groups=self.groups))
        for name,result,additional in (('objects',formal_check,[]),('calibrations',outer,checks)):
            archive = name+'.tar.gz'
            pin = {key:result[key] for key in ('sha256','bytes')}
            self.docs['readback']['objects'][archive] = dict(pin,verified=dict(passed=True,
                checks=[dict(member=archive,depth=0,check=result),*additional]))
            self.docs[name] = dict(anonymous_public_head=True,upload=dict(pin,passed=True,
                full_internal_readback=True,readback_sha256=result['sha256']))

    def make_proof(self):
        return TOOL['proof'](self.formal,self.outer,self.docs,{})

    def test_formal_and_nested_maps_exclude_non_observer_files(self):
        result = self.make_proof()
        self.assertEqual(result['schema'],'b-c2-observer-archive-proof-v1')
        self.assertEqual(result['files_by_tag'],self.maps)
        self.assertEqual(result['historical_allocated_bytes'],8*4096)
        self.assertFalse(result['removal_authorized'])

    def test_refuses_mutated_archive_or_unmatched_historical_census(self):
        self.groups[0]['files'] = 2
        with self.assertRaisesRegex(ValueError,'candidate count'):
            self.make_proof()
        self.groups[0]['files'] = 1
        with self.formal.open('ab') as stream:
            stream.write(b'changed')
        with self.assertRaisesRegex(ValueError,'verified OSS bytes'):
            self.make_proof()


if __name__=='__main__':
    unittest.main()
