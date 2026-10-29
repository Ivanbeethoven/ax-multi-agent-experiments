import sys
from pathlib import Path
import unittest
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'harness'))
import scale_plan as scale


class ScaleTests(unittest.TestCase):
    def test_counts_roles_no_actions_and_whole_paired_blocks(self):
        for profile,count,nodes in [('main',618,45),('expanded',990,77)]:
            p=scale.build(profile)
            self.assertEqual(p['total_cells'],count);self.assertEqual(p['parallel_node_count'],nodes)
            self.assertFalse(p['execution_enabled']);self.assertFalse(p['provisioning_enabled'])
            self.assertEqual(len({c['id'] for c in p['cells']}),count)
            for c in p['cells']:
                self.assertFalse(c['execution_enabled']);self.assertFalse(c['params']['simulate_llm'])
            for key in {c['paired_block'] for c in p['cells']}:
                rows=[c for c in p['cells'] if c['paired_block']==key]
                self.assertEqual(len(rows),len({c['method'] for c in rows}))
                self.assertEqual(sorted(c['order_in_block'] for c in rows),list(range(1,len(rows)+1)))

    def test_vm_is_not_docker_host_count(self):
        p=scale.build(isolation='vm');self.assertEqual(p['parallel_node_count'],269)
        self.assertEqual(p['clusters']['C4']['standby_environments'],64)
        self.assertEqual(p['clusters']['C4']['node_count'],131)
        self.assertIsNone(p['clusters']['C1']['physical_host_count'])
        self.assertEqual(scale.build('expanded','vm')['parallel_node_count'],525)

    def test_placement_source_target_unique_and_capacity(self):
        for mode in ('docker','vm'):
            rows=scale.placement(64,mode,True)
            self.assertEqual(len({r['environment_id'] for r in rows}),128)
            source={r['executor_node'] for r in rows if r['pool']=='source'}
            target={r['executor_node'] for r in rows if r['pool']=='target'}
            self.assertFalse(source & target)
            for host in source|target:self.assertLessEqual(sum(r['executor_node']==host for r in rows),8 if mode=='docker' else 1)
        cells=scale.build(cluster='C4')['cells']
        large=[c for c in cells if c['group']=='fork-fanout' and c['params']['dataset']=='mixed-400GiB' and c['params']['children']==64]
        self.assertTrue(all(c['capacity_floor']['EC2_plus2_with_25pct_headroom_bytes']>63*1024**4 for c in large))

    def test_simulation_only_explicit_multi_trace(self):
        p=scale.build(include_llm_simulation=True)
        extra=[c for c in p['cells'] if c['params']['simulate_llm']]
        self.assertTrue(extra)
        for c in extra:
            self.assertEqual(c['load_kind'],'trace');self.assertGreater(c['params']['agents'],1)
            self.assertIsNone(c['think_budget_ms_per_task'])
            self.assertIn('reviewed-tool-turn-boundaries',c['requires'])
        self.assertFalse(any(c['params']['simulate_llm'] for c in scale.build(cluster='C2',include_llm_simulation=True)['cells']))

    def test_determinism_provider_pairing_and_scope(self):
        self.assertEqual(scale.build(cluster='C1'),scale.build(cluster='C1'))
        for cluster in ('C1','C2','C3','C4'):
            p=scale.build(cluster=cluster);self.assertEqual(set(p['clusters']),{cluster})
            self.assertTrue(all(c['cluster_id']==cluster for c in p['cells']))
        self.assertNotEqual(scale.build(provider='aliyun')['cells'][0]['paired_block'],scale.build(provider='provided')['cells'][0]['paired_block'])


if __name__=='__main__':unittest.main()
