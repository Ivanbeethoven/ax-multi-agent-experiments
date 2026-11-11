"""Exercise actual pilot placement and locks without cloud calls or FUSE mounts."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import json
import os
from pathlib import Path
import runpy
import subprocess
import sys
import tempfile
import unittest

from bench import suite_state as state
from bench.runtime.common import digest,write_new
from bench.runtime.pilot_pools import (inventories,pool_inventory,endpoints,endpoint_binding,
    pool_of,alias,resource_ids,fleet_node,active_conflicts,resource_guard,cross_pool_proof)
from bench.suite_catalog import template
from bench.suite_plan import build

REPO=Path(__file__).resolve().parents[1]
SCRIPTS=REPO/'experiments/unified-suite/pilot-aliyun/scripts'
SETUP=runpy.run_path(str(SCRIPTS/'setup-controller.py'))


def cloud_inventory():
    result={'campaign_id':'parallel-control-01','pools':[]}
    for p,subnet in [('a',1),('b',2)]:
        roles={}
        for index,role in enumerate(('client','metadata','objects'),10):
            roles[role]=dict(instance_id='i-'+p+'-'+role,private_ip=f'10.20.{subnet}.{index}',
                hostname='ax-pilot-20261111-'+('' if p=='a' else 'b-')+role,
                data_disk_ids=['d-'+p+'-'+role+'-'+str(i) for i in range(4 if role=='objects' else 1)])
        result['pools'].append(dict(pool_id='pool-'+p,roles=roles))
    return result


class PilotPools(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
    def tearDown(self):self.tmp.cleanup()
    def put(self,name,value):
        path=self.root/name;write_new(path,value);return state.reference(path)
    def plan(self,shared=False):
        config=template();config['cases']=config['cases'][:1]
        config.update(input_manifest_sha256='a'*64,build_manifest_sha256='b'*64)
        for pool in config['clusters']:
            actual=pool_inventory(cloud_inventory(),pool['id'])
            pool.update(resource_ids=resource_ids(actual),inventory_sha256='c'*64)
            if shared:pool['resource_ids'].append('service:deliberately-shared-unit-test')
        return build(config)
    def ready(self,plan):
        proof=self.put('unit-test-only-proof.json',dict(passed=True,purpose='unit-test only'))
        for cell in plan['cells']:
            qualification=self.put('qualification-'+cell['id']+'.json',dict(schema='suite-qualification-v1',
                plan_sha256=plan['plan_sha256'],cell_id=cell['id'],
                checks=[dict(gate=g,evidence=proof,equals={'passed':True}) for g in cell['required_gates']]))
            state.qualify(self.root/'state.sqlite',qualification)
        for asset in plan['assets']:
            value=self.put('asset-'+asset['id']+'.json',dict(schema='suite-asset-v1',
                plan_sha256=plan['plan_sha256'],asset_id=asset['id'],native_binding='unit-test only',
                timings_seconds={'prepare':0},stages={s:dict(evidence=proof,equals={'passed':True}) for s in asset['required_stages']}))
            state.register_asset(self.root/'state.sqlite',value)
    def test_old_inventory_still_resolves_and_fleet_has_six_distinct_roles(self):
        document=cloud_inventory();old=deepcopy(document['pools'][0])
        self.assertEqual(inventories(old),[old])
        self.assertEqual(endpoints(old,'pool-a')['objects'],'http://10.20.1.12:9000')
        fleet=SETUP['actual_fleet'](document,'code-11')
        self.assertEqual(len(fleet['nodes']),6)
        self.assertEqual(len({n['host_id'] for n in fleet['nodes']}),6)
        self.assertEqual(fleet_node(fleet,'pool-b','client')['hostname'],'ax-pilot-20261111-b-client')
        self.assertEqual(fleet_node(fleet,'pool-b','client')['repo'],'/srv/ax-client/code-11')
        self.assertEqual(fleet_node(fleet,'pool-b','client')['slots'][0]['cgroup'],'/sys/fs/cgroup/ax-slot-1')
        self.assertEqual(alias('pool-b','objects'),'pool-b-objects')
    def test_actual_two_pool_cli_binds_19_cells_and_pins_new_endpoints(self):
        cloud=self.root/'cloud.json';write_new(cloud,cloud_inventory())
        build_manifest=self.root/'build.json';write_new(build_manifest,{'purpose':'unit test digest input'})
        inputs=self.root/'inputs.json';write_new(inputs,{'purpose':'unit test digest input'})
        out=self.root/'parallel'
        subprocess.run([sys.executable,str(SCRIPTS/'configure-pilot.py'),'--phase','c123','--files','100000',
            '--campaign','parallel-control-01','--inventory',str(cloud),'--build-manifest',str(build_manifest),
            '--input-manifest',str(inputs),'--out',str(out)],capture_output=True,check=True)
        plan=json.loads((out/'plan.json').read_text())
        self.assertEqual(len(plan['cells']),19);self.assertTrue(plan['provenance_bound'])
        self.assertEqual({c['params']['files'] for c in plan['cells'] if c['suite']=='C3'},{100000})
        for cell in plan['cells']:
            expected='pool-b' if cell['arm']['product']=='juicefs' else 'pool-a'
            self.assertEqual(pool_of(cell),expected)
            self.assertEqual(cell['resource_ids'],resource_ids(pool_inventory(cloud_inventory(),expected)))
            self.assertIn('cross-pool-hardware-equivalence',cell['required_gates'])
        cell=next(c for c in plan['cells'] if c['pool']=='pool-b')
        binding=endpoint_binding(plan,cell,cloud)
        self.assertEqual(binding['metadata'],'redis://10.20.2.11:6379')
        self.assertEqual(binding['objects'],'http://10.20.2.12:9000')
        self.assertEqual(binding['inventory']['sha256'],digest(cloud))
        original={'product':'brewfs','config':{'original':'immutable'}};saved=deepcopy(original)
        with self.assertRaises(ValueError):endpoint_binding(plan,cell)
        self.assertEqual(original,saved)
        cloud.write_text(cloud.read_text()+'\n')
        with self.assertRaises(ValueError):endpoint_binding(plan,cell,cloud)
    def test_one_state_database_claims_independent_pools_concurrently(self):
        plan=self.plan();write_new(self.root/'plan.json',plan)
        state.initialize(self.root/'state.sqlite',plan);self.ready(plan)
        with ThreadPoolExecutor(max_workers=2) as workers:
            leases=list(workers.map(lambda p:state.claim(self.root/'state.sqlite',p,p),('pool-a','pool-b')))
        self.assertTrue(all(lease['cell'] for lease in leases))
        self.assertEqual(len(state.status(self.root/'state.sqlite')['active_leases']),2)
        for lease in leases:
            conflicts=active_conflicts(self.root,lease['cell'],plan)
            self.assertEqual([c['pool'] for c in conflicts],[lease['cell']['pool']])
    def test_shared_service_blocks_other_pool_without_global_lease_gate(self):
        plan=self.plan(shared=True);state.initialize(self.root/'state.sqlite',plan);self.ready(plan)
        a=state.claim(self.root/'state.sqlite','pool-a','a')
        b=next(c for c in plan['cells'] if c['pool']=='pool-b')
        self.assertEqual(active_conflicts(self.root,b,plan)[0]['cell_id'],a['cell']['id'])
        self.assertIsNone(state.claim(self.root/'state.sqlite','pool-b','b')['cell'])
    @unittest.skipUnless(os.name=='posix','Linux coordinator resource locks')
    def test_calibration_guard_is_pool_local_and_shared_resource_exclusive(self):
        plan=self.plan();a,b=plan['cells']
        with resource_guard(self.root,a,plan):
            with resource_guard(self.root,b,plan):pass
            with self.assertRaises(ValueError):
                with resource_guard(self.root,a,plan):pass
        shared=self.plan(shared=True);a,b=shared['cells']
        with resource_guard(self.root,a,shared):
            with self.assertRaises(ValueError):
                with resource_guard(self.root,b,shared):pass
    def test_cross_pool_gate_rehashes_actual_evidence_and_refuses_other_plan(self):
        plan=self.plan();fleet=self.root/'fleet.json';write_new(fleet,SETUP['actual_fleet'](cloud_inventory(),'code-11'))
        pin=self.put('actual-comparison.json',{'purpose':'unit test proof only'})
        proof=self.root/'comparison.json'
        value=dict(schema='pilot-cross-pool-equivalence-v1',passed=True,plan_sha256=plan['plan_sha256'],
            inventory_sha256=digest(fleet),pools=['pool-a','pool-b'],checks={'same_hardware':True},source_pins=[pin])
        write_new(proof,value);self.assertTrue(cross_pool_proof(proof,plan,fleet)['passed'])
        wrong=deepcopy(plan);wrong['plan_sha256']='d'*64
        with self.assertRaises(ValueError):cross_pool_proof(proof,wrong,fleet)
        Path(pin['path']).write_text('changed')
        with self.assertRaises(ValueError):cross_pool_proof(proof,plan,fleet)


if __name__=='__main__':unittest.main()
