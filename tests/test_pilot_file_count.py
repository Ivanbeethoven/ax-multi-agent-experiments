"""Pilot scale changes cannot relabel old plans, inputs or capacity estimates."""
from copy import deepcopy
import json
import hashlib
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor

from bench.suite_catalog import select_pilot_files, template
from bench.suite_plan import build
from bench.suite_support import sizing

ROOT = Path(__file__).resolve().parents[1]


class PilotFileCount(unittest.TestCase):
    @unittest.skipUnless(os.name == 'posix', 'Bash snapshot publication runs on Linux')
    def test_parallel_preparation_publishes_one_complete_snapshot(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); build = root/'build'; tools = root/'tools'
            (build/'bin').mkdir(parents=True); tools.mkdir()
            stub = build/'bin/smallfiles'
            stub.write_text('#!/bin/sh\nexit 0\n');stub.chmod(0o755)
            # Hold both copies until both processes have seen the absent snapshot.
            copy = tools/'cp'
            copy.write_text('#!/usr/bin/env python3\nimport os,pathlib,subprocess,sys,time\n'
                'root=pathlib.Path(os.environ["SNAPSHOT_TEST_DIR"])\n'
                '(root/("copy-"+str(os.getpid()))).touch()\n'
                'deadline=time.monotonic()+10\n'
                'while len(list(root.glob("copy-*")))<2:\n'
                ' if time.monotonic()>deadline: sys.exit(90)\n'
                ' time.sleep(.01)\n'
                'sys.exit(subprocess.call(["/bin/cp",*sys.argv[1:]]))\n')
            copy.chmod(0o755)
            env = dict(os.environ, PATH=str(tools)+os.pathsep+os.environ['PATH'],
                SNAPSHOT_TEST_DIR=str(root))
            env.pop('AX_INPUT_SNAPSHOT', None)
            script = ROOT/'experiments/unified-suite/pilot-aliyun/scripts/prepare-inputs.sh'
            def run():
                return subprocess.run(['bash',str(script),'c3',str(build),'100000'],
                    env=env,capture_output=True,timeout=20)
            with ThreadPoolExecutor(max_workers=2) as pool:
                futures = [pool.submit(run) for _ in range(2)]
                results = [future.result() for future in futures]
            for result in results:
                self.assertEqual(result.returncode,0,result.stderr.decode())
            snapshots = list((build/'scripts').glob('prepare-inputs-*.sh'))
            self.assertEqual(len(snapshots),1)
            self.assertEqual(snapshots[0].read_bytes(),script.read_bytes())
            self.assertFalse(list((build/'scripts').glob('.prepare-inputs.*')))

    def test_100k_plan_keeps_all_28_routes_and_original_fio_trace_work(self):
        config = template()
        original = deepcopy([c for c in config['cases'] if c['suite'] in ('C1', 'C2')])
        select_pilot_files(config, 100000)
        self.assertEqual(original, [c for c in config['cases'] if c['suite'] in ('C1', 'C2')])
        plan = build(config)
        self.assertEqual(len(plan['cells']), 28)
        assets = {a['id']:a for a in plan['assets']}
        for cell in plan['cells']:
            if cell['workload'] in ('smallfiles', 'fork'):
                self.assertEqual(cell['params']['files'], 100000)
                self.assertIn('100k', assets[cell['asset_id']]['dataset'])
        for cell in sizing(plan)['cells']:
            if cell['workload'] == 'fork':
                self.assertEqual(cell['source_logical_bytes'], 409600000)
                self.assertEqual(cell['eager_base_plus_children_logical_bytes'], 819200000)

    def test_default_catalog_still_requires_full_million(self):
        select_pilot_files(template(), 100000)
        fresh = template()
        self.assertNotIn('pilot_file_count', fresh)
        self.assertEqual({c['params']['files'] for c in fresh['cases'] if c['workload'] == 'smallfiles'}, {1000000})
        wrong = deepcopy(fresh)
        next(c for c in wrong['cases'] if c['workload'] == 'smallfiles')['params']['files'] = 100000
        with self.assertRaises(ValueError):
            build(wrong)

    def test_wrong_fork_input_size_cannot_match_the_100k_label(self):
        config = select_pilot_files(template(), 100000)
        next(c for c in config['cases'] if c['workload'] == 'fork')['params']['files'] = 1000000
        with self.assertRaises(ValueError):
            build(config)

    def test_main_cannot_silently_use_the_smaller_pilot(self):
        with self.assertRaises(ValueError):
            select_pilot_files(template(profile='main'), 100000)
        config = select_pilot_files(template(), 100000)
        config['profile'] = 'main'
        with self.assertRaises(ValueError):
            build(config)

    def test_cli_generates_separate_four_cell_c3_phase(self):
        with tempfile.TemporaryDirectory() as temporary:
            out = Path(temporary)/'c3'
            subprocess.run([sys.executable, str(ROOT/'experiments/unified-suite/pilot-aliyun/scripts/configure-pilot.py'),
                '--phase', 'c3', '--files', '100000', '--campaign', 'pilot-100k', '--out', str(out)],
                check=True, capture_output=True)
            plan = json.loads((out/'plan.json').read_text())
            self.assertEqual(len(plan['cells']), 4)
            self.assertEqual({c['workload'] for c in plan['cells']}, {'smallfiles'})
            self.assertEqual(plan['config']['pilot_file_count'], 100000)
            self.assertFalse(plan['provenance_bound'])

    def test_old_million_file_proof_is_rejected_before_preparation(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config = select_pilot_files(template(), 100000)
            plan = root/'plan.json';plan.write_text(json.dumps(build(config)))
            proof = root/'old-proof.json'
            proof.write_text(json.dumps(dict(passed=True, files=1000000, bytes_checked=4096000000)))
            helper = root/'helper.py';helper.write_text('# unused preflight helper\n')
            common = ['--asset', str(root/'asset'), '--plan', str(plan), '--source', str(root/'source'),
                '--source-proof', str(proof), '--node-tool', str(helper), '--node-tool-sha256',
                hashlib.sha256(helper.read_bytes()).hexdigest(), '--database', '90']
            scripts = ROOT/'experiments/unified-suite/pilot-aliyun/scripts'
            for name, extra in [('prepare-smallfiles.py', common+['--product','brewfs','--attempt','check-100k']),
                    ('prepare-packed.py', common+['--mount-state',str(root/'mount-state')]),
                    ('smallfiles-oracle.py', ['--source',str(root/'source'),'--source-proof',str(proof),'--files','100000'])]:
                out = root/(name+'.out')
                result = subprocess.run([sys.executable,str(scripts/name),*extra,'--out',str(out)], capture_output=True)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(b'source qualification', result.stderr.lower())
                self.assertFalse(out.exists())

    def test_new_version_can_prepare_c1_c2_without_rewriting_old_state(self):
        with tempfile.TemporaryDirectory() as temporary:
            for phase, expected in [('c1',10),('c2',5)]:
                with self.subTest(phase=phase):
                    out=Path(temporary)/phase
                    subprocess.run([sys.executable,str(ROOT/'experiments/unified-suite/pilot-aliyun/scripts/configure-pilot.py'),
                        '--phase',phase,'--campaign','new-version-'+phase,'--out',str(out)],
                        check=True,capture_output=True)
                    plan=json.loads((out/'plan.json').read_text())
                    self.assertEqual(len(plan['cells']),expected)
                    self.assertEqual({c['suite'] for c in plan['cells']},{phase.upper()})
                    self.assertEqual(plan['config']['clusters'][0]['supported_suites'],[phase.upper()])
                    self.assertFalse(plan['provenance_bound'])
                    self.assertFalse((out/'state.sqlite').exists())


if __name__ == '__main__':
    unittest.main()
