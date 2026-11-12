"""Current design boundaries: no cloud, model, mount or external service."""
from copy import deepcopy
import json
import re
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from bench.current_design import validate, cell_counts, parallel_resources
from bench.suite_plan import build

ROOT = Path(__file__).resolve().parents[1]
DESIGN = ROOT / 'experiments/unified-suite/current/design.json'


class CurrentDesignTests(unittest.TestCase):
    def setUp(self):
        self.config = json.loads(DESIGN.read_text(encoding='utf-8'))

    def rejected(self, change):
        value = deepcopy(self.config)
        change(value)
        with self.assertRaises(ValueError):
            validate(value)

    def test_count_whole_system_cells_and_keep_readiness_separate(self):
        counts = cell_counts(self.config)
        self.assertEqual(counts['formal_system_cells'],
                         {'C1': 30, 'C2': 99, 'C3': 56, 'C4-fork': 28, 'C4-handoff': 44})
        self.assertEqual(counts['formal_total'], 257)
        self.assertEqual(counts['pilot_total'], 21)
        self.assertFalse(self.config['execution_enabled'])
        self.assertFalse(any(self.config['readiness'].values()))

    def test_current_pilot_accepts_tenk_but_not_legacy_100k_inputs(self):
        self.assertIs(validate(self.config), self.config)
        for workload in ('C3', 'C4-fork'):
            with self.subTest(workload=workload):
                self.rejected(lambda c: c['pilot'][workload].update(files=100_000))

    def test_design_never_becomes_execution_authorization(self):
        self.rejected(lambda c: c.update(execution_enabled=True))
        self.rejected(lambda c: c.update(status='qualified'))
        self.rejected(lambda c: c.update(execution_blockers=[]))
        with self.assertRaises(ValueError):
            build(self.config)

    def test_no_deprecated_route_or_disguised_basic_all_off(self):
        for engine in ('workspace-entity-v1', 'workspace-native-v2'):
            self.rejected(lambda c: c['arms']['brewfs-full'].update(engine=engine))
        self.rejected(lambda c: c['arms']['brewfs-basic'].update(dynamic_block=False))

    def test_ablation_cannot_swap_engine_or_change_two_designs(self):
        self.rejected(lambda c: c['ablations'][0].update(engine='flat-v1'))
        self.rejected(lambda c: c['ablations'][0]['switches'].update(packed_metadata=False))
        self.rejected(lambda c: c['ablations'][1].update(requires=[]))

    def test_no_full_copy_disguised_as_a_fork_or_clone(self):
        self.rejected(lambda c: c['workloads']['C4-fork']['arms'].insert(0, 'brewfs-basic'))
        self.rejected(lambda c: c['workloads']['C4-fork']['creation_methods'].update({'brewfs-full':'full-copy'}))
        self.rejected(lambda c: c['workloads']['C4-fork'].update(basic_copy='cp -a'))
        self.rejected(lambda c: c['workloads']['C4-fork']['required_evidence'].remove('brewfs-child-inherits-packed-lower'))
        self.rejected(lambda c: c['workloads']['C3']['arms'].insert(0, 'brewfs-basic'))

    def test_overlay_run_requires_verified_warm_data_and_metadata_in_both_arms(self):
        self.rejected(lambda c: c['ablations'][0]['workloads'].append('C4-fork'))
        self.rejected(lambda c: c['workloads']['C2']['overlay_runtime'].update(warmup_scope=['metadata']))
        self.rejected(lambda c: c['workloads']['C2']['overlay_runtime'].update(max_lower_backend_read_bytes=1))
        self.rejected(lambda c: c['workloads']['C2']['overlay_runtime'].update(userspace_path_required=False))
        self.rejected(lambda c: c['workloads']['C2']['overlay_runtime'].update(prepare_and_warmup_outside_timer=False))

    def test_no_silent_scale_reduction_or_growth(self):
        self.rejected(lambda c: c['workloads']['C2'].update(scale_agents=[8, 16, 64]))
        self.rejected(lambda c: c['workloads']['C3'].update(files=100000))
        self.rejected(lambda c: c['workloads']['C2']['mixed_32'].update(django=3))
        self.rejected(lambda c: c['workloads']['C4-handoff'].update(moved_agents=[2, 4]))
        self.rejected(lambda c: c['workloads']['C2'].update(simulate_llm=True))

    def test_resource_and_disabled_profile_must_be_complete(self):
        self.rejected(lambda c: c['resources']['rustfs'].update(nodes=1))
        self.rejected(lambda c: c['resources']['client'].update(vcpu=16))
        self.rejected(lambda c: c['resources'].update(disk_free_fraction_min=0.1))
        self.rejected(lambda c: c['service_settings']['rustfs']['disabled'].pop('RUSTFS_EC_READ_QUANTUM_ENABLE'))

    def test_parallel_counts_include_duplicated_backends_and_migration_target(self):
        single = parallel_resources(self.config, [('C2', 32)])
        self.assertEqual(single, dict(active_slots=32, business_nodes=9, vcpu=208,
                                      memory_gib=832, data_gib=2380))
        paired = parallel_resources(self.config, [('C2', 16), ('C3', 16)])
        self.assertEqual(paired, dict(active_slots=32, business_nodes=14, vcpu=288,
                                      memory_gib=1152, data_gib=4360))
        moved = parallel_resources(self.config, [('C4-handoff', 32)])
        self.assertEqual(moved['active_slots'], 32)
        self.assertEqual(moved['business_nodes'], 10)
        self.assertEqual(moved['data_gib'], 2480)

    def test_two_pools_cannot_silently_double_concurrency(self):
        for pairs in ([('C2', 32), ('C3', 32)], [('C2', 32), ('C1', 1)],
                      [('C1', 1), ('C2', 8), ('C3', 8)]):
            with self.subTest(pairs=pairs), self.assertRaises(ValueError):
                parallel_resources(self.config, pairs)
        self.rejected(lambda c: c['controls'].update(max_active_workload_slots_total=64))

    def test_pair_identity_and_freeze_survive_parallel_scheduling(self):
        self.rejected(lambda c: c['scheduling'].update(all_arms_same_pool=False))
        self.rejected(lambda c: c['scheduling'].update(prepare_and_measure_same_resource='parallel'))
        self.rejected(lambda c: c['versions']['brewfs'].update(revision_policy='branch-latest-per-batch'))

    def test_legacy_template_requires_explicit_cli_selection(self):
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder) / 'legacy.json'
            argv = [sys.executable, '-m', 'bench.suite', 'template', '--output', str(output)]
            result = subprocess.run(argv, cwd=ROOT, capture_output=True, text=True)
            self.assertEqual(result.returncode, 2, result.stderr)
            self.assertIn('--legacy-catalog', result.stderr)
            self.assertFalse(output.exists())
            result = subprocess.run(argv + ['--legacy-catalog'], cwd=ROOT, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            config = json.loads(output.read_text(encoding='utf-8'))
            self.assertTrue(build(config)['cells'])
            engines = {arm['engine'] for case in config['cases'] for arm in case['arms'].values()}
            self.assertIn('workspace-entity-v1', engines)  # Preserve historical identity.

    def test_current_document_links_resolve_in_repository(self):
        pages = [ROOT / 'README.md', ROOT / 'experiments/unified-suite/README.md',
                 ROOT / 'experiments/unified-suite/MAIN-SCALE.zh.md',
                 ROOT / 'experiments/unified-suite/DESIGN-SWITCHES.zh.md']
        pages += list(DESIGN.parent.glob('*.md'))
        for page in pages:
            for target in re.findall(r'\[[^\]]+\]\(([^)\s]+)\)', page.read_text(encoding='utf-8')):
                if '://' in target or target.startswith('#'):
                    continue
                target = target.split('#', 1)[0]
                with self.subTest(page=page.name, target=target):
                    self.assertTrue((page.parent / target).resolve().exists())


if __name__ == '__main__':
    unittest.main()
