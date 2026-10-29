import sys
from pathlib import Path
import unittest
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'bench'))
sys.path.insert(0, str(ROOT/'bench/reference'))
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
        expected=(400*1024**3*65*8+2)//3
        self.assertTrue(large)
        for cell in large:
            floor=cell['capacity_floor']
            self.assertEqual(floor['raw_data_only_with_25pct_free_bytes'],expected)
            self.assertFalse(floor['fits_4TiB_backend_data_only'])
            self.assertFalse(floor['complete_budget_verified'])

    def test_free_capacity_fraction_and_physical_overhead(self):
        # 300 raw used bytes require 400 total, not 375 (25% added to used).
        self.assertEqual(scale.ec22_capacity_floor(150),400)
        self.assertEqual(scale.ec22_capacity_floor(150,75),500)
        self.assertEqual(scale.ec22_capacity_floor(1),3)
        for args in [(-1,0),(1,-1),(1.5,0),(True,0)]:
            with self.assertRaises(ValueError):scale.ec22_capacity_floor(*args)
        cells=scale.build('expanded',cluster='C4')['cells']
        for c in cells:
            if c['group']!='fork-fanout' or c['params']['dataset']!='mixed-400GiB':continue
            self.assertEqual(c['capacity_floor']['fits_4TiB_backend_data_only'],c['params']['children']==1)

    def test_hardware_targets_are_not_ready_evidence(self):
        for isolation in ('docker','vm'):
            for profile,cpu,mem in [('main',1296,5184),('expanded',2320,9280)]:
                p=scale.build(profile,isolation)
                self.assertEqual(p['parallel_reference_compute'],{'vcpus':cpu,'memory_gib':mem})
                for t in p['clusters'].values():
                    self.assertFalse(t['hardware_verified'])
                    self.assertEqual(t['minimum_free_fraction'],.25)
                    self.assertTrue(t['required_hardware_evidence'])
                for c in p['cells']:
                    self.assertIn('role-hardware-and-physical-capacity-verified',c['requires'])
        pilot=scale.topology('C1',16,'docker')
        self.assertEqual(pilot['node_count'],5)
        self.assertEqual(scale.reference_compute({'C1':pilot}),{'vcpus':120,'memory_gib':480})
        self.assertEqual(pilot['executor_node_reference']['input_disk_gib'],1024)
        self.assertEqual(pilot['executor_node_reference']['scratch_disk_gib'],1024)

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

    def test_writeback_study_preserves_policy_pairs_and_scopes(self):
        p=scale.build(study='writeback')
        self.assertEqual(p['total_cells'],276)
        self.assertEqual(p['group_counts'],{'writeback-trace':162,'writeback-queue':24,
                                          'writeback-handoff':72,'writeback-copy-readiness':18})
        self.assertEqual(set(p['clusters']),{'C1','C4'})
        self.assertFalse(p['execution_enabled']);self.assertFalse(p['provisioning_enabled'])
        self.assertEqual(len({c['id'] for c in p['cells']}),276)
        for key in {c['paired_block'] for c in p['cells']}:
            rows=[c for c in p['cells'] if c['paired_block']==key]
            for method in {c['method'] for c in rows}:
                self.assertEqual({c['params']['write_policy'] for c in rows if c['method']==method},
                                 {'upload_before_commit','commit_before_upload'})
            self.assertEqual(sorted(c['order_in_block'] for c in rows),list(range(1,len(rows)+1)))
        for c in p['cells']:
            self.assertFalse(c['execution_enabled'])
            self.assertIn('same-build-and-volume-format-between-write-policies',c['requires'])
            self.assertIn('scoped-remote-drain-and-independent-read',c['requires'])
            self.assertFalse(c['writeback_qualification_verified'])
            asynchronous=c['params']['write_policy']=='commit_before_upload'
            conf=c['required_effective_config']
            if c['method'].startswith('juicefs'):
                self.assertEqual(conf['writeback'],asynchronous)
                if asynchronous:self.assertEqual(conf['upload_delay_seconds'],0)
            else:self.assertEqual(conf['cache.writeback_mode'],c['params']['write_policy'])
            if c['load_kind']=='handoff':
                self.assertIsNone(c['checkpoint_manifest_sha256'])
                self.assertIn('resumable-trace-checkpoint-state',c['requires'])
                self.assertIn('neighbor-writers-continue-outside-drain-scope',c['requires'])
        fork=[c for c in p['cells'] if c['load_kind']=='fork']
        self.assertTrue(all(not c['capacity_floor']['fits_4TiB_backend_data_only'] for c in fork))

    def test_writeback_pacing_is_one_predeclared_interaction(self):
        p=scale.build(study='writeback',include_llm_simulation=True)
        self.assertEqual(p['total_cells'],288)
        extra=[c for c in p['cells'] if c['params']['simulate_llm']]
        self.assertEqual(len(extra),12)
        for c in extra:
            self.assertEqual(c['group'],'writeback-trace')
            self.assertEqual(c['params']['agents'],64)
            self.assertEqual(c['params']['trace_case'],'archive-io-20261026')
            self.assertIn(c['method'],['native-overlay','juicefs'])
            self.assertIsNone(c['think_budget_ms_per_task'])
        self.assertFalse(any(c['params']['simulate_llm'] for c in scale.build(study='writeback')['cells']))

    def test_writeback_does_not_expand_unrelated_dimensions(self):
        for args in [dict(study='unknown'),dict(study='writeback',cluster='C2'),
                     dict(study='writeback',cluster='C3'),dict(study='writeback',profile='expanded')]:
            with self.assertRaises(ValueError):scale.build(**args)
        self.assertEqual(scale.build(study='writeback',cluster='C1')['total_cells'],186)
        self.assertEqual(scale.build(study='writeback',cluster='C4')['total_cells'],90)
        self.assertEqual(scale.build(study='writeback'),scale.build(study='writeback'))
        main_ids={c['id'] for c in scale.build()['cells']}
        self.assertFalse(main_ids & {c['id'] for c in scale.build(study='writeback')['cells']})


if __name__=='__main__':unittest.main()
