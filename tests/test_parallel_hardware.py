"""Measured cross-pool hardware checks must retain mismatches and unknowns."""
from copy import deepcopy
import inspect
import json
from pathlib import Path
import runpy
import subprocess
import sys
import unittest
if __package__:
    from .test_pilot_pools import cloud_inventory,SETUP
else:
    from test_pilot_pools import cloud_inventory,SETUP

ROOT=Path(__file__).resolve().parents[1]
MODULE=runpy.run_path(str(ROOT/'experiments/unified-suite/pilot-aliyun/scripts/qualify-parallel-hardware.py'))
evaluate=MODULE['evaluate'];FLAGS=MODULE['FLAGS']


def fixture():
    cloud=cloud_inventory();fleet=SETUP['actual_fleet'](cloud,'code-11')
    api={'instances':[],'disks':[]};samples={};specs={}
    for pool in cloud['pools']:
        pool_id=pool['pool_id'];specs[pool_id]={}
        for role,node in pool['roles'].items():
            cpu=8 if role=='objects' else 4;memory=32768 if role=='objects' else 16384
            api['instances'].append(dict(InstanceId=node['instance_id'],InstanceType='ecs.u1.test.'+role,
                Cpu=cpu,Memory=memory,ZoneId='cn-hangzhou-j',VpcAttributes={'VpcId':'vpc-unit-test'}))
            disks=[];native=[];mounts=[]
            for i,disk_id in enumerate(node['data_disk_ids']):
                size=60 if role!='client' else 100;root='/srv/ax-objects-'+str(i) if role=='objects' else '/srv/ax-'+role
                serial='actual-'+disk_id
                api['disks'].append(dict(DiskId=disk_id,InstanceId=node['instance_id'],Type='data',ZoneId='cn-hangzhou-j',
                    Category='cloud_essd',Size=size,PerformanceLevel='PL1',SerialNumber=serial))
                disks.append(dict(id=disk_id,serial=serial,mount=root))
                native.append(dict(type='disk',serial=serial,size=size*1024**3,mountpoints=[root]))
                mounts.append(dict(path=root,is_mount=True,device=100+i))
            specs[pool_id][role]=dict(instance_id=node['instance_id'],role=role,data_disks=disks)
            actual=dict(read_only=True,hostname=node['hostname'],instance_id=node['instance_id'],boot_id='boot-'+node['instance_id'],
                errors={},cpu_models=['Same actual CPU model'],affinity_cpu_count=cpu,memory_total_bytes=memory*1024**2,
                kernel='6.8.0-test',private_addresses=[{'addr_info':[{'local':node['private_ip']}]}],
                block_devices={'blockdevices':native},data_mounts=mounts)
            if role=='client':actual['slot']={'cpu.max':'200000 100000','memory.max':'8589934592','memory.swap.max':'0'}
            if role=='metadata':
                actual['redis']=dict(version='7.0.15',binary_sha256='e'*64,active='active',
                    version_output='Redis server v=7.0.15 sha=00000000:0 malloc=jemalloc-5.3.0 bits=64 build=test',
                    process_id='9779',systemd_pid='9779',process_executable_sha256='e'*64,io_accounting='yes',
                    settings={'appendonly':'yes','appendfsync':'always','maxmemory':'10737418240','maxmemory-policy':'noeviction','save':'','databases':'256'},
                    cgroup={'cpu.max':'300000 100000','memory.max':'12884901888','memory.swap.max':'0'},
                    source={'value':dict(passed=True,instance_id=node['instance_id'],action='redis')})
            if role=='objects':
                image='registry/rustfs@sha256:'+'f'*64
                proof=dict(source_commit='b'*40,image_digest='sha256:'+'f'*64,binary_sha256='c'*64)
                cid='actual-'+pool_id
                source=dict(passed=True,container_id=cid,image_id='sha256:'+'d'*64,compose_sha256='a'*64,
                            source_commit=proof['source_commit'],binary_sha256=proof['binary_sha256'])
                actual['rustfs']=dict(id=cid,image_id=source['image_id'],image_reference=image,running=True,
                    flags={flag:'true' for flag in FLAGS},nano_cpus=6000000000,memory_bytes=24*1024**3,memory_swap_bytes=24*1024**3,
                    source_commit=proof['source_commit'],repo_digests=[image],binary_sha256=proof['binary_sha256'],
                    compose_sha256='a'*64,source={'value':source},image_proof={'value':proof})
            samples[pool_id+'-'+role]=actual
    return dict(plan={'plan_sha256':'0'*64},fleet=fleet,cloud=cloud,api=api,observations=samples,node_specs=specs)


class ParallelHardware(unittest.TestCase):
    def test_actual_matching_values_pass_without_claiming_equal_speed(self):
        result=evaluate(**fixture())
        self.assertTrue(result['passed'],result['unknown_or_failed'])
        self.assertIn('does not establish equal speed',result['scope'])
        self.assertEqual(result['details']['pool-b']['objects']['cpu_models'],['Same actual CPU model'])
    def test_same_cloud_sku_does_not_hide_a_different_real_cpu(self):
        value=fixture();value['observations']['pool-b-client']['cpu_models']=['Different actual CPU']
        result=evaluate(**value)
        self.assertFalse(result['passed']);self.assertIn('client/matching_actual_cpu_model',result['unknown_or_failed'])
    def test_disk_capacity_type_pl_attachments_and_native_mount_mapping_are_checked(self):
        for field,replacement in [('Size',61),('Category','cloud_efficiency'),('PerformanceLevel','PL0'),('InstanceId','i-other')]:
            with self.subTest(field=field):
                value=fixture();value['api']['disks'][-1][field]=replacement
                self.assertFalse(evaluate(**value)['passed'])
        value=fixture();value['observations']['pool-b-objects']['block_devices']['blockdevices'][0]['mountpoints']=['/wrong']
        result=evaluate(**value)
        self.assertIn('pool-b/objects/native_disk_api_mapping',result['unknown_or_failed'])
    def test_missing_values_and_probe_errors_are_failed_not_invented(self):
        value=fixture();value['observations']['pool-b-metadata'].pop('redis')
        result=evaluate(**value)
        self.assertFalse(result['passed']);self.assertIn('pool-b/metadata/redis_actual_configuration',result['unknown_or_failed'])
        value=fixture();value['api']['instances'][0].pop('Memory')
        self.assertFalse(evaluate(**value)['passed'])
    def test_original_redis_io_receipt_is_accepted_only_with_current_native_identity(self):
        value=fixture()
        value['observations']['pool-a-metadata']['redis']['source']['value']={
            'passed':True,'previous':'MainPID=9779\nIOAccounting=no\n','after':'MainPID=9779\nIOAccounting=yes\n','errors':[]}
        self.assertTrue(evaluate(**value)['passed'])
        value['observations']['pool-a-metadata']['redis']['process_id']='9888'
        self.assertFalse(evaluate(**value)['passed'])
    def test_false_layout_flag_changed_image_or_wrong_resource_budget_blocks(self):
        for mutate in [lambda x:x['flags'].update({FLAGS[0]:'false'}),lambda x:x.update(memory_bytes=16*1024**3),
                       lambda x:x.update(image_reference='registry/rustfs@sha256:'+'0'*64),lambda x:x.update(binary_sha256='0'*64)]:
            value=fixture();mutate(value['observations']['pool-b-objects']['rustfs'])
            self.assertFalse(evaluate(**value)['passed'])
        value=fixture();value['observations']['pool-b-client']['slot']['memory.swap.max']='max'
        self.assertFalse(evaluate(**value)['passed'])
    def test_pinned_node_spec_can_bind_serial_when_api_omits_it(self):
        value=fixture()
        for disk in value['api']['disks']:disk.pop('SerialNumber')
        self.assertTrue(evaluate(**value)['passed'])
        value['node_specs']={}
        self.assertFalse(evaluate(**value)['passed'])
    def test_shared_host_or_data_disk_cannot_be_called_independent(self):
        value=fixture();value['cloud']['pools'][1]['roles']['client']['instance_id']=value['cloud']['pools'][0]['roles']['client']['instance_id']
        self.assertFalse(evaluate(**value)['passed'])
        value=fixture();value['cloud']['pools'][1]['roles']['client']['data_disk_ids']=value['cloud']['pools'][0]['roles']['client']['data_disk_ids']
        self.assertFalse(evaluate(**value)['passed'])
    def test_standalone_probe_payload_is_valid_and_does_not_dump_environment(self):
        source=inspect.getsource(MODULE['remote_probe'])
        self.assertNotIn('rustfs.env',source)
        self.assertNotIn('IAM',source)
        program='from unittest.mock import patch\n'+source+'\nimport json\nwith patch("urllib.request.build_opener",side_effect=OSError), patch("subprocess.check_output",side_effect=OSError):\n print(json.dumps(remote_probe({"role":"client","work_root":"/tmp"})))\n'
        result=subprocess.run([sys.executable,'-c',program],capture_output=True,text=True,check=True)
        value=json.loads(result.stdout);self.assertTrue(value['read_only']);self.assertIn('instance_id',value['errors'])


if __name__=='__main__':unittest.main()
