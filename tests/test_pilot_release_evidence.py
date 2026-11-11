"""Stopping billing estimates requires absence of every original owned resource."""
import copy
import importlib.util
from pathlib import Path
import unittest

FILE = Path(__file__).resolve().parents[1]/'experiments/unified-suite/pilot-aliyun/scripts/release-evidence.py'
spec = importlib.util.spec_from_file_location('release_evidence', FILE)
module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)


def tags(**values):
    return {'Tag':[dict(TagKey=k,TagValue=v) for k,v in values.items()]}


class ReleaseEvidence(unittest.TestCase):
    def setUp(self):
        self.scope = dict(reuse_jump_instance='i-jump')
        self.creation = dict(preexisting=False,instance_id='i-owned',campaign='ax-pilot-20261111',role='client')
        self.node = dict(instance_id='i-owned',campaign='ax-pilot-20261111',role='client',
                         system_disk_serial='boot',data_disks=[dict(id='d-data',gib=100)])
        self.instances = [dict(InstanceId='i-owned',Status='Running',Tags=tags(campaign='ax-pilot-20261111',role='client'))]
        self.disks = [dict(DiskId='d-boot',InstanceId='i-owned',Type='system',Size=40,DeleteWithInstance=True,Status='In_use'),
                      dict(DiskId='d-data',InstanceId='i-owned',Type='data',Size=100,DeleteWithInstance=True,Status='In_use')]

    def original(self):
        return module.owned_set(self.creation,self.node,self.scope,self.instances,self.disks)

    def test_exact_owned_boot_and_data_are_both_frozen(self):
        self.assertEqual(self.original()['owned_disk_ids'],['d-boot','d-data'])
        grown=copy.deepcopy(self.disks); grown[1]['Size']=256
        self.assertEqual(module.owned_set(self.creation,self.node,self.scope,self.instances,grown)['owned_disk_ids'],['d-boot','d-data'])

    def test_unknown_attached_disk_wrong_boot_and_retained_disk_are_rejected(self):
        for change in ('extra','boot','retained'):
            disks=copy.deepcopy(self.disks)
            if change=='extra':disks.append(dict(DiskId='d-foreign'))
            elif change=='boot':disks[0]['DiskId']='d-another-boot'
            else:disks[1]['DeleteWithInstance']=False
            with self.subTest(change=change), self.assertRaises(ValueError):
                module.owned_set(self.creation,self.node,self.scope,self.instances,disks)

    def test_preexisting_jump_cannot_enter_instance_release_set(self):
        for change in ('preexisting','jump'):
            creation=copy.deepcopy(self.creation)
            if change=='preexisting':creation['preexisting']=True
            else:creation['instance_id']='i-jump'
            with self.subTest(change=change), self.assertRaises(ValueError):
                module.owned_set(creation,self.node,self.scope,self.instances,self.disks)

    def test_only_dedicated_coordinator_data_disk_is_allowed(self):
        creation=dict(preexisting=False,instance_id='i-jump',disk_id='d-control',campaign='ax-pilot-20261111')
        disk=dict(DiskId='d-control',InstanceId='i-jump',Type='data',Size=100,DeleteWithInstance=False,
                  Tags=tags(campaign='ax-pilot-20261111',role='coordinator-data'))
        original=module.owned_set(creation,None,self.scope,[],[disk])
        self.assertEqual(original['owned_disk_ids'],['d-control'])
        disk['Type']='system'
        with self.assertRaises(ValueError):module.owned_set(creation,None,self.scope,[],[disk])

    def test_instance_gone_but_one_disk_present_does_not_end_cost(self):
        checked=module.absence(self.original(),[],self.disks[1:],'i-jump')
        self.assertFalse(checked['passed']);self.assertTrue(checked['released'])
        self.assertFalse(checked['all_owned_disks_absent'])

    def test_stopped_instance_is_not_released_and_all_absent_is_released(self):
        stopped=copy.deepcopy(self.instances);stopped[0]['Status']='Stopped'
        self.assertFalse(module.absence(self.original(),stopped,[],'i-jump')['passed'])
        checked=module.absence(self.original(),[],[],'i-jump')
        self.assertTrue(checked['passed']);self.assertTrue(checked['all_owned_disks_absent'])

    def test_misfiltered_api_results_are_not_an_absence_proof(self):
        with self.assertRaisesRegex(ValueError,'another resource'):
            module.absence(self.original(),[dict(InstanceId='i-other')],[],'i-jump')
        with self.assertRaisesRegex(ValueError,'unknown/duplicate'):
            module.absence(self.original(),[],[dict(DiskId='d-foreign')],'i-jump')


if __name__=='__main__':
    unittest.main()
