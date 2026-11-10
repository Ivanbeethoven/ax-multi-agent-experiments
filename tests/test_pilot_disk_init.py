"""New disk selection must fail before any formatting command is possible."""
import copy
import importlib.util
from pathlib import Path
import unittest

FILE=Path(__file__).resolve().parents[1]/'experiments/unified-suite/pilot-aliyun/scripts/provision-node.py'
spec=importlib.util.spec_from_file_location('pilot_disks',FILE)
module=importlib.util.module_from_spec(spec); spec.loader.exec_module(module)


class DiskSelection(unittest.TestCase):
    def setUp(self):
        self.spec=dict(campaign='ax-pilot-20261111',created_for_campaign=True,system_disk_serial='root-serial',
            data_disks=[dict(id='d-owned',serial='new-serial',gib=60,mount='/srv/ax-data')])
        self.devices=[dict(path='/dev/vda',type='disk',serial='root-serial',size=40*1024**3,fstype=None,
            children=[dict(path='/dev/vda1',type='part',mountpoints=['/'])]),
            dict(path='/dev/vdc',type='disk',serial='new-serial',size=60*1024**3,fstype=None,mountpoints=[None])]

    def test_selects_api_serial_regardless_of_device_order(self):
        self.assertEqual(module.select_disks(self.spec,list(reversed(self.devices)))[0][1]['path'],'/dev/vdc')

    def test_refuses_preexisting_or_system_disk(self):
        for change in ('owner','system'):
            s=copy.deepcopy(self.spec)
            if change=='owner':s['created_for_campaign']=False
            else:s['data_disks'][0]['serial']='root-serial'
            with self.subTest(change=change), self.assertRaises(ValueError): module.select_disks(s,self.devices)

    def test_refuses_filesystem_partition_mount_or_size_mismatch(self):
        for field,value in [('fstype','ext4'),('children',[{'type':'part'}]),('mountpoints',['/srv/other']),('size',1)]:
            devices=copy.deepcopy(self.devices);devices[1][field]=value
            with self.subTest(field=field), self.assertRaises(ValueError): module.select_disks(self.spec,devices)

    def test_refuses_duplicate_serial_device_or_mount(self):
        devices=copy.deepcopy(self.devices);devices.append(copy.deepcopy(devices[1]))
        with self.assertRaises(ValueError):module.select_disks(self.spec,devices)
        s=copy.deepcopy(self.spec);s['data_disks'].append(copy.deepcopy(s['data_disks'][0]))
        with self.assertRaises(ValueError):module.select_disks(s,self.devices)

    def test_refuses_path_escape(self):
        for mount in ['/','/srv/ax-data/../../home','/mnt/data','relative']:
            s=copy.deepcopy(self.spec);s['data_disks'][0]['mount']=mount
            with self.subTest(mount=mount), self.assertRaises(ValueError):module.select_disks(s,self.devices)


if __name__=='__main__':unittest.main()
