import hashlib
import importlib.util
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from bench.runtime.common import load, pinned


SCRIPT = Path(__file__).resolve().parents[1]/'experiments/unified-suite/pilot-aliyun/scripts/continue-c4-native-flat.py'
SPEC = importlib.util.spec_from_file_location('native_flat_continuation', SCRIPT)
CONTINUE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CONTINUE)
CONTINUE.load = load
CONTINUE.pinned = pinned


class C4NativeFlatHandoff(unittest.TestCase):
    def put(self, path, data):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data))
        return dict(path=str(path), sha256=hashlib.sha256(path.read_bytes()).hexdigest())

    def test_active_original_unit_refuses_before_any_worker_contact(self):
        active = 'InvocationID=original\nSubState=running\nMainPID=101\nResult=success\nExecMainStatus=0\n'
        release = dict(retention_decision='preserve-local-through-c4', unit='original-unit', invocation='original')
        with patch.object(CONTINUE.subprocess, 'check_output', return_value=active) as check:
            with self.assertRaisesRegex(ValueError, 'has not closed successfully'):
                CONTINUE.handoff(dict(c3=release), None, None, None, None, None)
            self.assertEqual(check.call_count, 1)
            self.assertEqual(check.call_args.args[0][:3], ['systemctl', 'show', 'original-unit'])

    def test_closed_original_binds_actual_receipts_and_refuses_new_lease(self):
        manager = 'InvocationID=original\nSubState=exited\nMainPID=0\nResult=success\nExecMainStatus=0\n'
        cell = CONTINUE.C3
        collector_name = 'ax-pilot-c3-on3-collector'
        with tempfile.TemporaryDirectory() as directory:
            canonical = Path(directory)
            c3 = canonical/'one-cell-runs'/cell
            close_path = canonical/'cells'/(cell+'.close.json')
            run = self.put(canonical/'run.json', dict(cell_id=cell, final_collector_name=collector_name,
                final_collector_out='/srv/ax-objects-0/q/c3-on3-collector'))
            self.put(c3/'started.json', dict(request=run))
            proof = self.put(c3/'closure.json', dict(cell_id=cell, passed=True, all_exited=True,
                all_unmounted=True, pending_upload_bytes=0, pending_upload_operations=0))
            retention = self.put(c3/'retention.json', {})
            sample = self.put(c3/'sample.json', {})
            self.put(canonical/'suite/plan.json', dict(plan_sha256='original-plan'))
            close = self.put(close_path, dict(cell_id=cell, outcome='complete', schema='suite-cell-close-v1',
                plan_sha256='original-plan', retention=dict(manifest=retention), sample=sample, closure={
                'processes':dict(evidence=proof, equals={'all_exited': True}),
                'mounts':dict(evidence=proof, equals={'all_unmounted': True}),
                'debt':dict(evidence=proof, equals={'pending_upload_bytes':0, 'pending_upload_operations':0})}))
            result = self.put(c3/'result.json', dict(cell_id=cell, passed=True, closing=close,
                final_collector_name=collector_name))
            with sqlite3.connect(canonical/'state.sqlite') as db:
                db.execute('CREATE TABLE cells(id TEXT, status TEXT)')
                db.execute("INSERT INTO cells VALUES (?, 'complete')", (cell,))
            draft = dict(required_handoff=dict(closure_path=str(close_path)))
            release = dict(retention_decision='preserve-local-through-c4', unit='original-unit', invocation='original',
                result=result, run_request=run, closing=close)
            with patch.object(CONTINUE.subprocess, 'check_output', return_value=manager), patch.object(
                    CONTINUE, 'active_conflicts', create=True, return_value=[]) as conflicts:
                c, _ = CONTINUE.handoff(dict(c3=release), draft, canonical, None, None, [canonical])
                self.assertEqual(c['collector_name'], collector_name)
                conflicts.return_value = [{'pool': 'pool-a'}]
                with self.assertRaisesRegex(ValueError, 'resource is leased'):
                    CONTINUE.handoff(dict(c3=release), draft, canonical, None, None, [canonical])


if __name__ == '__main__':
    unittest.main()
