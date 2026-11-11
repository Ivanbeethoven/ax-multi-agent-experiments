import importlib.util
from pathlib import Path
import unittest


path = (Path(__file__).resolve().parents[1] /
        'experiments/unified-suite/pilot-aliyun/scripts/observer-scratch-cloud.py')
spec = importlib.util.spec_from_file_location('observer_scratch_cloud', path)
scratch = importlib.util.module_from_spec(spec)
spec.loader.exec_module(scratch)


class Ownership(unittest.TestCase):
    def setUp(self):
        self.disk = dict(DiskId='d-scratch1', Type='data', Size=100,
                         Category='cloud_essd', PerformanceLevel='PL0',
                         DiskChargeType='PostPaid', ZoneId='cn-hangzhou-j',
                         Status='In_use', InstanceId='i-objects1', DeleteWithInstance=True,
                         Tags={'Tag': [{'TagKey': 'campaign', 'TagValue': 'ax-pilot-20261111'},
                                       {'TagKey': 'role', 'TagValue': scratch.ROLE}]})

    def validate(self, row, attached=True):
        return scratch.scratch_disk(row, 'd-scratch1', 'i-objects1',
                                    'ax-pilot-20261111', 'cn-hangzhou-j', attached)

    def test_attached_and_unattached_exact_owned_disk(self):
        self.assertIs(self.validate(self.disk), self.disk)
        empty = dict(self.disk, Status='Available', InstanceId='', DeleteWithInstance=False)
        self.assertIs(self.validate(empty, False), empty)

    def test_refuses_other_disk_or_wrong_attachment(self):
        for change in (dict(DiskId='d-other'), dict(InstanceId='i-other'), dict(Type='system'),
                       dict(Size=60), dict(PerformanceLevel='PL1'), dict(Status='Available'),
                       dict(DeleteWithInstance=False), dict(MultiAttach='Enabled'),
                       dict(Tags={'Tag': []}), dict(ZoneId='cn-hangzhou-i')):
            with self.subTest(change=change), self.assertRaises(ValueError):
                self.validate(dict(self.disk, **change))
        with self.assertRaises(ValueError):
            self.validate(self.disk, False)

    def test_truncated_cloud_response_cannot_support_identity(self):
        response = dict(TotalCount=2, Disks={'Disk': [self.disk]})
        with self.assertRaises(ValueError):
            scratch.exact(response, 'Disks', 'Disk')

    def test_embedded_guard_is_valid_python(self):
        compile(scratch.GUARD, '<guard>', 'exec')
