"""Separate physical service pools keep endpoint, disk and metric identities."""
import copy
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock
import io
import sys

REPO=Path(__file__).resolve().parents[1]
SCRIPTS=REPO/'experiments/unified-suite/pilot-aliyun/scripts'


def module(name,file):
    spec=importlib.util.spec_from_file_location(name,file)
    result=importlib.util.module_from_spec(spec);spec.loader.exec_module(result)
    return result


binding=module('pilot_service_binding_test',SCRIPTS/'pilot_service_binding.py')
generator=module('pilot_service_generator_test',REPO/'infra/rustfs/generate_rustfs.py')
maintenance=module('pilot_maintenance_test',SCRIPTS/'freeze-maintenance.py')
sys.path.insert(0,str(SCRIPTS))
objects_deploy=module('pilot_objects_deploy_test',SCRIPTS/'deploy-objects.py')


class ServicePools(unittest.TestCase):
    def test_actual_objects_entrypoint_refuses_other_instance_before_mutation(self):
        spec,inventory=self.fixture()
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'spec.json').write_text(json.dumps(spec));(root/'inventory.json').write_text(json.dumps(inventory))
            argv=['deploy-objects.py','--assets',str(root/'assets'),'--out',str(root/'out'),
                  '--node-spec',str(root/'spec.json'),'--inventory',str(root/'inventory.json'),'--pool','pool-b']
            opener=mock.Mock();opener.open.side_effect=[io.BytesIO(b'token'),io.BytesIO(b'i-wrong')]
            with mock.patch.object(sys,'argv',argv),mock.patch.object(objects_deploy.os,'geteuid',return_value=0), \
                    mock.patch.object(objects_deploy.socket,'gethostname',return_value=spec['hostname']), \
                    mock.patch('urllib.request.build_opener',return_value=opener), \
                    mock.patch.object(objects_deploy.subprocess,'check_output') as command:
                with self.assertRaisesRegex(ValueError,'not the object instance'):
                    objects_deploy.main()
                command.assert_not_called()
            self.assertFalse((root/'out').exists())

    def test_maintenance_requires_actual_owned_worker(self):
        spec,_=self.fixture('metadata')
        maintenance.check_spec(spec,spec['instance_id'],spec['hostname'])
        for key,value in [('created_for_campaign',False),('instance_id','i-other'),
                          ('hostname','ax-pilot-20261111-metadata'),('role','coordinator'),
                          ('campaign','../ax-pilot-20261111-b')]:
            changed=copy.deepcopy(spec);changed[key]=value
            with self.subTest(key=key),self.assertRaises(ValueError):
                maintenance.check_spec(changed,spec['instance_id'],spec['hostname'])

    def fixture(self,role='objects'):
        disks=['d-b-'+str(i) for i in range(4 if role=='objects' else 1)]
        spec=dict(campaign='ax-pilot-20261111-b',created_for_campaign=True,role=role,
                  instance_id='i-pool-b-'+role,hostname='ax-pilot-20261111-b-'+role,
                  data_disks=[dict(id=d,mount='/srv/ax-'+role+'-'+str(i)) for i,d in enumerate(disks)])
        node=dict(instance_id=spec['instance_id'],hostname=spec['hostname'],
                  private_ip='10.20.2.202' if role=='objects' else '10.20.2.201',data_disk_ids=disks)
        inventory=dict(pools=[dict(pool_id='pool-a',roles={role:dict(instance_id='i-pool-a-'+role,
            hostname='ax-pilot-20261111-'+role,private_ip='10.20.2.163',data_disk_ids=['d-original'])}),
            dict(pool_id='pool-b',roles={role:node})])
        return spec,inventory

    def test_pool_b_generates_its_own_listener_and_metric_identity(self):
        spec,inventory=self.fixture();original=copy.deepcopy(inventory)
        bound=binding.bind_service(spec,inventory,'pool-b','objects')
        image='10.20.0.10:15000/ax-pilot/rustfs@sha256:'+'a'*64
        data=binding.objects_deployment_spec(bound,image,
            ['/srv/ax-rustfs-data-'+str(i) for i in range(4)],'/srv/ax-objects-0/private/credentials')
        with tempfile.TemporaryDirectory() as tmp:
            generator.generate(data,Path(tmp)/'generated')
            compose=json.loads((Path(tmp)/'generated/rustfs1/compose.json').read_text())
        service=compose['services']['rustfs'];env=service['environment']
        self.assertEqual(env['RUSTFS_ADDRESS'],'10.20.2.202:9000')
        self.assertEqual(env['OTEL_RESOURCE_ATTRIBUTES'],'rustfs.cluster.id=ax-pilot-20261111-b-pool-b')
        self.assertEqual(compose['name'],'ax-pilot-20261111-b-pool-b-objects-01')
        self.assertEqual(service['image'],image)
        self.assertEqual((service['cpus'],service['mem_limit'],service['restart']),(6,24*1024**3,'no'))
        self.assertTrue(all(env[k]=='true' for k in ['RUSTFS_EC_BLOCK_SIZE_HINT_ENABLE',
            'RUSTFS_EC_BLOCK_SIZE_HINT_FLEET_CONFIRMED','RUSTFS_EC_READ_QUANTUM_ENABLE','RUSTFS_EC_READ_QUANTUM_FLEET_CONFIRMED']))
        self.assertEqual(env['RUSTFS_OBS_METRIC_ENDPOINT'],'http://127.0.0.1:4318/v1/metrics')
        self.assertEqual(inventory,original)

    def test_old_single_pool_inventory_is_accepted_without_rewriting_it(self):
        spec,inventory=self.fixture();single=inventory['pools'][1];original=copy.deepcopy(single)
        self.assertEqual(binding.bind_service(spec,single,'pool-b','objects')['private_ip'],'10.20.2.202')
        self.assertEqual(single,original)
        legacy=binding.legacy_objects_binding()
        self.assertEqual((legacy['hostname'],legacy['private_ip']),('ax-pilot-20261111-objects','10.20.2.163'))
        self.assertEqual(legacy['project_name'],'ax-pilot-20261111-objects-01')

    def test_wrong_instance_host_disks_or_pool_refused(self):
        for field in ('instance_id','hostname','data_disk_ids'):
            spec,inventory=self.fixture();node=inventory['pools'][1]['roles']['objects']
            node[field]=['d-wrong'] if field=='data_disk_ids' else 'wrong'
            with self.subTest(field=field),self.assertRaises(ValueError):
                binding.bind_service(spec,inventory,'pool-b','objects')
        spec,inventory=self.fixture()
        with self.assertRaises(ValueError):binding.bind_service(spec,inventory,'pool-c','objects')
        inventory['pools'].append(copy.deepcopy(inventory['pools'][1]))
        with self.assertRaises(ValueError):binding.bind_service(spec,inventory,'pool-b','objects')

    def test_public_loopback_or_spec_ip_mismatch_refused(self):
        for address in ('8.8.8.8','127.0.0.1','169.254.1.2'):
            spec,inventory=self.fixture();inventory['pools'][1]['roles']['objects']['private_ip']=address
            with self.subTest(address=address),self.assertRaises(ValueError):
                binding.bind_service(spec,inventory,'pool-b','objects')
        spec,inventory=self.fixture();spec['private_ip']='10.20.2.163'
        with self.assertRaises(ValueError):binding.bind_service(spec,inventory,'pool-b','objects')

    def test_redis_binds_only_its_inventory_and_actual_host_address(self):
        spec,inventory=self.fixture('metadata');bound=binding.bind_service(spec,inventory,'pool-b','metadata')
        interfaces=[dict(addr_info=[dict(family='inet',local='127.0.0.1'),dict(family='inet',local='10.20.2.201')])]
        self.assertEqual(binding.require_local_address(bound['private_ip'],interfaces),'10.20.2.201')
        with self.assertRaises(ValueError):binding.require_local_address('10.20.2.162',interfaces)


if __name__=='__main__':unittest.main()
