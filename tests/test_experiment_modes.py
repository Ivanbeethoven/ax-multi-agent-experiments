import json
import unittest

from bench.experiment_modes import CONFIG, MODES, resolve


class ModeContract(unittest.TestCase):
    def setUp(self):
        self.design = json.loads(CONFIG.read_text())

    def test_every_pair_resolves_without_dispatch(self):
        for experiment, modes in MODES.items():
            for mode in modes:
                with self.subTest(experiment=experiment, mode=mode):
                    result = resolve(self.design, experiment, mode,
                                     'move' if experiment == 'C4-handoff' else None)
                    self.assertFalse(result['dispatch_enabled'])
                    self.assertIn('observability', result['brewfs_cargo_features'])

    def test_c1_dynamic_off_stays_flat_and_does_not_hint(self):
        row = resolve(self.design, 'C1-layout', 'dynamic-off')
        self.assertEqual((row['engine'], row['brewfs_hint'], row['rustfs_layout_profile']),
                         ('flat-v1', 'off', 'disabled'))

    def test_overlay_off_cannot_silently_turn_off_packed(self):
        row = resolve(self.design, 'C2-runtime', 'overlay-off')
        self.assertEqual(row['engine'], 'workspace-v1')
        self.assertEqual(row['mechanisms'],
                         {'overlay': False, 'packed_metadata': True, 'dynamic_block': True})
        self.assertIn('read-control-qualified-candidate-required', row['runtime_qualification_blockers'])
        self.assertTrue(row['read_only_control'])

    def test_fork_reports_correctness_blocker_while_juicefs_selects_clone(self):
        self.assertTrue(resolve(self.design, 'C4-fork', 'brewfs-full')['runtime_qualification_blockers'])
        self.assertEqual(resolve(self.design, 'C4-fork', 'juicefs')['native_action'], 'clone')
        with self.assertRaises(ValueError):
            resolve(self.design, 'C4-fork', 'brewfs-basic')
        with self.assertRaises(ValueError):
            resolve(self.design, 'C4-handoff', 'juicefs')
