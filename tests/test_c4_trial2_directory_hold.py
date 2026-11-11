"""Offline proof of the frozen C4 parent's trial-2 directory boundary."""
import ast
from pathlib import Path
import shlex
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from bench.experiment_io import digest, load, write_new
from bench.runtime import c4_verify
from bench.runtime.common import pinned
from bench.runtime.pilot_pools import resource_guard


REPO = Path(__file__).resolve().parents[1]
RUNNER = REPO/'experiments/unified-suite/pilot-aliyun/scripts/run-c4-batch.py'


def original_flow():
    """Use the exact frozen invoke/run, trial loop and failure handler."""
    tree = ast.parse(RUNNER.read_bytes(), filename=str(RUNNER))
    defs = {node.name: node for node in tree.body if isinstance(node, ast.FunctionDef)}
    guard = next(node for node in defs['main'].body if isinstance(node, ast.With)
                 and isinstance(node.items[0].context_expr.func, ast.Name)
                 and node.items[0].context_expr.func.id == 'resource_guard')
    pair = {node.name: node for node in guard.body if isinstance(node, ast.FunctionDef)}
    original_try = next(node for node in guard.body if isinstance(node, ast.Try))
    trials = next(node for node in original_try.body if isinstance(node, ast.For))
    attempt = ast.Try(body=[trials], handlers=original_try.handlers, orelse=[], finalbody=original_try.finalbody)
    test_tree = ast.Module(body=[defs['trial_verification'], defs['failure_release_code'],
                                 pair['run'], pair['invoke'], attempt], type_ignores=[])
    return compile(ast.fix_missing_locations(test_tree), str(RUNNER), 'exec')


class C4DirectoryHold(unittest.TestCase):
    def simulate(self, probe_results):
        with TemporaryDirectory() as td:
            root = Path(td).resolve()
            control = root/'control'
            control.mkdir()
            out = root/'original-batch'
            out.mkdir()
            runtime = root/'runtime'
            runtime.mkdir()
            write_new(runtime/'fleet.json', {})
            tool = root/'original-calibrator.py'
            tool.write_text('# Stub; never executed\n', encoding='utf-8')
            asset = '/srv/ax-client/a/frozen'
            cell = dict(id='c4-cell', pool='pool-a', arm_id='native',
                        params=dict(files=100000), resource_ids=['host:pool-a-client'])
            plan = dict(config=dict(clusters=[dict(id='pool-a', resource_ids=cell['resource_ids'])]))
            collector = dict(collector_name='original-collector', collector_out='/original/collector')
            trials = [dict(mode=mode, attempt=f'c4-a-{i}', out=str(root/f'trial-data-{i}'), **collector)
                      for i, mode in enumerate(('off', 'on', 'on', 'off', 'off', 'on'), 1)]
            trials[1].update(new_collector_name='new-collector', new_collector_out='/new/collector')
            prep = dict(path='/srv/ax-client/prepared/result.json', sha256='pinned-preparation')
            contract = dict(FULL=c4_verify.FULL, TOUCHED=c4_verify.TOUCHED, REFERENCE=c4_verify.REFERENCE,
                            reference_identity=Mock())
            calls, probes = [], []
            clock = SimpleNamespace(sleep=Mock())

            def ref(path):
                return dict(path=str(path), sha256=digest(path))

            def subprocess_run(argv, **kwargs):
                if argv[0] == 'python3':
                    self.assertEqual(argv[:3], ['python3', str(tool), '--request'])
                    command = load(Path(argv[3]))
                    calls.append(command)
                    self.assertEqual(len(calls), 1, 'No original trial after Off1 may start')
                    self.assertEqual(command['attempt'], trials[0]['attempt'])
                    self.assertEqual(command['verification_policy'], c4_verify.REFERENCE)
                    first = Path(command['out'])
                    first.mkdir()
                    verified = first/'client-verify.json'
                    write_new(verified, dict(child_name=command['attempt'],
                              binding=dict(asset=dict(path=asset+'/asset.json'), source_tree={'tree': 'original'}),
                              expected_child={'state': 'reference'}))
                    write_new(first/'finished.json', dict(passed=True, client_verify=ref(verified), **collector))
                    kwargs['stdout'].write(b'original Off1 child stdout\n')
                    # Model the controller placing the empty fence after the
                    # child finished, before the stopped parent gets its status.
                    (out/'trial-2').mkdir(exist_ok=False)
                    return SimpleNamespace(returncode=0)
                self.assertEqual(argv[0:2], ['ssh', '-F'])
                self.assertEqual(kwargs, {'capture_output': True})
                self.assertEqual(shlex.split(argv[-1])[:2], ['python3', '-c'])
                self.assertIn('safe_for_release', shlex.split(argv[-1])[2])
                with self.assertRaisesRegex(ValueError, 'owns physical resource'):
                    with resource_guard(control, cell, plan):
                        self.fail('Original parent released its lock during failure probes')
                probes.append(argv[3])
                return SimpleNamespace(returncode=next(probe_results), stdout=b'probe\n', stderr=b'')

            context = dict(out=out, runtime=runtime, pool='pool-a', load=load, write_new=write_new,
                           pinned=pinned, digest=digest, shlex=shlex, Path=Path, time=clock,
                           subprocess=SimpleNamespace(run=subprocess_run),
                           s=dict(trials=trials, formal_attempt='c4-a-formal', asset=asset,
                                  tools=dict(calibrate=ref(tool))),
                           cell=cell, preparation=prep, base=dict(asset=asset),
                           current=collector.copy(), full_reference=None, verification=c4_verify.TOUCHED,
                           c4_verify=SimpleNamespace(**contract), alias=lambda pool, role: pool+'-'+role,
                           fleet_node=lambda *unused: {'repo': '/srv/ax-client/code-frozen'})
            with self.assertRaises(FileExistsError) as raised:
                with resource_guard(control, cell, plan):
                    exec(original_flow(), context)
            self.assertEqual(Path(raised.exception.filename), out/'trial-2')
            self.assertEqual(load(out/'trial-1'/'exit.json'), {'exit_code': 0})
            self.assertEqual((out/'trial-1'/'stdout').read_bytes(), b'original Off1 child stdout\n')
            self.assertTrue(load(Path(trials[0]['out'])/'finished.json')['passed'])
            planned = load(out/'trial-2-request.json')
            self.assertEqual(planned['attempt'], trials[1]['attempt'])
            self.assertEqual(planned['purpose'], 'observer-calibration-on')
            self.assertEqual(planned['verification_policy'], c4_verify.TOUCHED)
            self.assertEqual(planned['full_reference'], dict(
                path='/srv/ax-client/q/'+trials[0]['attempt']+'/verify/result.json',
                sha256=digest(Path(trials[0]['out'])/'client-verify.json')))
            self.assertEqual(list((out/'trial-2').iterdir()), [])
            self.assertFalse((out/'trial-3-request.json').exists())
            self.assertTrue(all(not Path(t['out']).exists() for t in trials[1:]))
            self.assertEqual(len(calls), 1)
            contract['reference_identity'].assert_called_once()
            failed = load(out/'failed.json')
            self.assertFalse(failed['passed'])
            self.assertFalse(failed['automatic_retry'])
            self.assertIn(str(out/'trial-2'), failed['error'])
            with resource_guard(control, cell, plan):
                pass
            return probes, clock

    def test_original_parent_reaps_off1_then_refuses_before_trial2_spawn(self):
        probes, clock = self.simulate(iter((0, 0, 0)))
        self.assertEqual(probes, ['pool-a-client', 'pool-a-metadata', 'pool-a-objects'])
        clock.sleep.assert_not_called()

    def test_unsafe_probe_keeps_original_lock_until_all_roles_are_safe(self):
        probes, clock = self.simulate(iter((2, 0, 0, 0, 0, 0)))
        self.assertEqual(probes, ['pool-a-client', 'pool-a-metadata', 'pool-a-objects']*2)
        clock.sleep.assert_called_once_with(5)


if __name__ == '__main__':
    unittest.main()
