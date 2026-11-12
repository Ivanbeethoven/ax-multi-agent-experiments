"""C4 Flat dispatch retirement, with real frozen plans and local SQLite only."""
from pathlib import Path
import runpy
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from bench import suite_state as state
from bench.c4_dispatch import retired_flat_fork, require_allowed
from bench.experiment_io import write_new
from bench.suite_catalog import select_pilot_files, template
from bench.suite_plan import build, check_plan
from bench.suite_report import report

SCRIPTS = Path(__file__).resolve().parents[1]/'experiments/unified-suite/pilot-aliyun/scripts'


class FlatRetirement(unittest.TestCase):
    def setUp(self):
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.db = self.root/'state.sqlite'

    def put(self, name, body):
        path = self.root/(name+'.json')
        write_new(path, body)
        return state.reference(path)

    def freeze(self, parallel, engine='flat'):
        config = select_pilot_files(template(), 100000)
        fork = next(c for c in config['cases'] if c['workload']=='fork')
        fork['arms'] = {name:fork['arms'][name] for name in ('native','juicefs')}
        fork['arms']['native']['engine']=engine
        config['cases'] = [fork]
        config.update(input_manifest_sha256='a'*64, build_manifest_sha256='b'*64)
        for pool in config['clusters']:
            pool['inventory_sha256'] = 'c'*64
        if not parallel:
            config.pop('placement')
        self.plan = build(config)
        self.native = next(c for c in self.plan['cells'] if c['arm_id']=='native')
        self.juicefs = next(c for c in self.plan['cells'] if c['arm_id']=='juicefs')
        state.initialize(self.db,self.plan)
        # The retirement gate must hold even when original qualification is present.
        evidence = self.put('test-only-proof',{'passed':True})
        check = {'evidence':evidence,'equals':{'passed':True}}
        for cell in self.plan['cells']:
            identity = self.plan['plan_sha256']
            state.qualify(self.db,self.put('q-'+cell['id'],dict(
                schema='suite-qualification-v1',plan_sha256=identity,cell_id=cell['id'],
                checks=[dict(check,gate=gate) for gate in cell['required_gates']])))
        for asset in self.plan['assets']:
            state.register_asset(self.db,self.put('a-'+asset['id'],dict(
                schema='suite-asset-v1',plan_sha256=identity,asset_id=asset['id'],
                native_binding='test-only',timings_seconds={'import':0},
                stages={stage:check for stage in asset['required_stages']})))

    def test_parallel_skips_only_retired_arm_and_preserves_report(self):
        self.freeze(parallel=True)
        self.assertEqual(check_plan(self.plan),self.plan)
        before = state.status(self.db)
        blocked = state.claim(self.db,self.native['pool'],'tester')
        self.assertIsNone(blocked['cell'])
        self.assertIn('retired',blocked['reason'])
        self.assertEqual(state.status(self.db),before)
        lease = state.claim(self.db,self.juicefs['pool'],'tester')
        self.assertEqual(lease['cell'],self.juicefs)
        self.assertEqual(len(report(self.db)['samples']),2)

    def test_paired_block_cannot_omit_retired_arm(self):
        self.freeze(parallel=False)
        before = state.status(self.db)
        result = state.claim(self.db,self.native['pool'],'tester')
        self.assertIsNone(result['cell'])
        self.assertIn('frozen pair',result['reason'])
        self.assertEqual(state.status(self.db),before)

    def test_original_flat_label_stays_retired_without_rewriting_frozen_plan(self):
        self.freeze(parallel=True,engine='flat-v1')
        before=state.status(self.db);identity=self.plan['plan_sha256']
        self.assertEqual(check_plan(self.plan),self.plan)
        self.assertIsNone(state.claim(self.db,self.native['pool'],'tester')['cell'])
        self.assertEqual(state.status(self.db),before)
        reject=runpy.run_path(str(SCRIPTS/'run-c4-fork-baseline.py'))['require_dispatch']
        with self.assertRaisesRegex(ValueError,'retired'):reject(self.native)
        for stage in (None,'observe-start','create','mount','measure'):
            with self.subTest(stage=stage),self.assertRaisesRegex(ValueError,'retired'):
                require_allowed(self.native,stage)
        for stage in ('drain','unmount','verify','observe-stop','archive'):
            require_allowed(self.native,stage)
        self.assertEqual(check_plan(self.plan)['plan_sha256'],identity)

    def test_existing_lease_is_visible_but_cannot_restart_work(self):
        self.freeze(parallel=True)
        cell = self.native
        with state.transaction(self.db) as db:
            # A lease that predates retirement; no new approval is manufactured.
            db.execute('UPDATE cells SET status=? WHERE id=?',('claimed',cell['id']))
            db.execute('INSERT INTO cell_leases VALUES (?,?,?,?)',(cell['id'],cell['pool'],'original','token'))
        lease = state.claim(self.db,cell['pool'],'original')
        self.assertTrue(lease['already_claimed'])
        before = state.status(self.db)
        for stage in ('observe-start','create','mount','measure'):
            with self.subTest(stage=stage), patch('bench.runtime.common.prepare',return_value=(
                    {'cell_id':cell['id'],'stage':stage},None)), patch('bench.experiment_step.execute') as execute:
                with self.assertRaisesRegex(ValueError,'full-copy dispatch is retired'):
                    state.run_stage(self.db,lease,stage,{'path':'test-only','sha256':'test-only'},self.root/stage)
                execute.assert_not_called()
        self.assertEqual(state.status(self.db),before)
        for stage in ('drain','unmount','verify','observe-stop','archive'):
            require_allowed(cell,stage)

    def test_exact_scope_and_standalone_worker_dispatch(self):
        self.freeze(parallel=True)
        worker = runpy.run_path(str(SCRIPTS/'run-c4-fork-baseline.py'))
        reject = worker['require_dispatch']
        with self.assertRaisesRegex(ValueError,'retired'):
            reject(self.native)
        selected = worker['select_cell'](self.plan,self.native['id'],self.native['pool'],'brewfs')
        self.assertEqual(selected,self.native)
        for allowed in (self.juicefs,
                        dict(self.native,suite='C3',workload='smallfiles'),
                        dict(self.native,workload='handoff'),
                        dict(self.native,arm_id='brewfs-basic'),
                        dict(self.native,arm=dict(self.native['arm'],engine='workspace'))):
            self.assertFalse(retired_flat_fork(allowed))
            require_allowed(allowed)
            reject(allowed)


if __name__ == '__main__':
    unittest.main()
