import json
from pathlib import Path
import runpy
from tempfile import TemporaryDirectory
import unittest


SCRIPTS = Path(__file__).resolve().parents[1]/'experiments/unified-suite/pilot-aliyun/scripts'
SUMMARY = runpy.run_path(str(SCRIPTS/'summarize-c3-packed-cohort.py'))
WORKER = runpy.run_path(str(SUMMARY['WORKER']))


class CohortSummaryTests(unittest.TestCase):
    def setUp(self):
        scratch = TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        self.out = Path(scratch.name)
        tag = 'c3-current-base-20261112t080846z'
        self.write('stage.json',dict(tag=tag,database=249,workers=32,verify_seconds=120,
                   worker_sha256=SUMMARY['sha'](SUMMARY['WORKER'].read_bytes()),trace_packed_budget=True,
                   diagnostic_cohort=True,parallel_sha256=None,packed_output_mib=128,packed_control_mib=8))
        self.write('worker-intent.json',dict(tag=tag,redis_db=249,verifier_workers=32,verify_timeout_seconds=120,
                   formal_sample=False,automatic_retry=False,trace_packed_budget=True,diagnostic_cohort=True,
                   packed_output_budget_bytes=128*2**20,packed_control_budget_bytes=8*2**20))
        self.write('worker-ready.json',dict(pid=9,mount=dict(id=5,type='fuse')))
        self.write('worker-mount-exit.json',dict(unmount_exit=0,exit_code=0,mount_absent=True))
        self.unit(False)

    def write(self, name, value):
        (self.out/name).write_text(json.dumps(value))

    def unit(self, success):
        result = 'success' if success else 'exit-code'
        (self.out/'unit-result.txt').write_text(f'MainPID=0\nResult={result}\nExecMainStatus={0 if success else 1}\n')

    def events(self, rows, tail=b''):
        (self.out/'worker-cohort-events.ndjson').write_bytes(
            b''.join((json.dumps(row)+'\n').encode() for row in rows)+tail)

    def test_interrupted_call_keeps_partial_and_completed_separate(self):
        phase = 'serial-first-a'
        path = 'small/d0000/f0000000'
        self.events([
            dict(event='phase-start',phase=phase,monotonic_ns=1,workers=1),
            dict(event='start',phase=phase,monotonic_ns=2,path=path,operation='lstat'),
            dict(event='end',phase=phase,monotonic_ns=9,path=path,operation='lstat',elapsed_ns=6),
            dict(event='start',phase=phase,monotonic_ns=11,path=path,operation='open'),
        ], b'{"event":')
        result = SUMMARY['run'](self.out)
        self.assertFalse(result['native_complete'])
        self.assertEqual(result['ignored_partial_tail_bytes'],9)
        [record] = result['phases']
        self.assertEqual(record['operations']['lstat']['p95_ns'],6)
        self.assertEqual(record['operations']['open']['p95_ns'],None)
        self.assertEqual(record['unfinished'],[dict(path=path,operation='open',observed_wait_lower_bound_ns=0)])

    def test_end_without_start_is_not_a_sample(self):
        phase = 'serial-first-a'
        self.events([
            dict(event='phase-start',phase=phase,monotonic_ns=1,workers=1),
            dict(event='end',phase=phase,monotonic_ns=2,path='small/d0000/f0000000',operation='lstat',elapsed_ns=1),
        ])
        with self.assertRaisesRegex(ValueError,'Missing or repeated operation start'):
            SUMMARY['run'](self.out)

    def test_complete_receipt_requires_exact_native_and_content_results(self):
        rows, phases = [], []
        def event(name, phase, **kwargs):
            rows.append(dict(event=name,phase=phase,monotonic_ns=len(rows)*20,**kwargs))
        for phase,workers,offset in SUMMARY['PHASES']:
            begin = len(rows)*20
            event('phase-start',phase,workers=workers)
            for i in WORKER['cohort_indices'](offset):
                path = SUMMARY['expected_path'](i)
                for op in SUMMARY['OPS']:
                    event('start',phase,path=path,operation=op)
                    event('end',phase,path=path,operation=op,elapsed_ns=10)
                event('verified',phase,path=path,bytes=4096,sha256=SUMMARY['sha'](WORKER['base_payload'](i)))
            fields = dict(workers=workers,files=32,bytes=131072,elapsed_ns=len(rows)*20-begin)
            event('phase-end',phase,**fields)
            phases.append(dict(phase=phase,**fields))
        self.events(rows)
        self.unit(True)
        self.write('worker-cohort-result.json',dict(passed=True,formal_sample=False,phases=phases))
        result = SUMMARY['run'](self.out)
        self.assertTrue(result['native_complete'])
        self.assertEqual(sum(x['verified_files'] for x in result['phases']),128)
        next(row for row in rows if row['event']=='verified')['sha256'] = '0'*64
        self.events(rows)
        with self.assertRaisesRegex(ValueError,'exact payload'):
            SUMMARY['run'](self.out)
