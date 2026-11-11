"""Reuse original calibration records only with the actual pool's three observers."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

FILE = Path(__file__).resolve().parents[1]/'experiments/unified-suite/pilot-aliyun/scripts/check-calibration.py'
SPEC = importlib.util.spec_from_file_location('calibration_check_pool', FILE)
module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(module)


class CalibrationCheckPool(unittest.TestCase):
    def fixture(self, root, pool):
        def write(path, value):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(value), encoding='utf-8')
            return path
        metric = write(root/'metric.json', {})
        workload = write(root/'workload.json', {})
        pairs = []
        for i in range(3):
            pair = {}
            for mode in ('on', 'off'):
                trial = root/(mode+str(i))
                cohort = write(trial/'cohort/result.json', dict(
                    coordinator_makespan_seconds=1, jobs=[dict(seconds=1)]))
                proof = write(trial/'proof.json', dict(passed=True))
                write(trial/'finished.json', dict(passed=True, cohort=module.ref(cohort),
                    **{k: module.ref(proof) for k in ('client_verify', 'native_exit', 'verify_exit')}))
                ids = [pool+'-'+r for r in ('client', 'metadata', 'objects')]
                write(trial/'cohort.json', dict(workers=[dict(node_id=pool+'-client')],
                    observers=[dict(id=v) for v in ids] if mode == 'on' else []))
                if mode == 'on':
                    for name in ids:
                        write(trial/'observer-stop'/name/'raw-audit.json', dict(passed=True))
                pair[mode] = str(trial)
            pairs.append(pair)
        return pairs, metric, workload

    def test_b_uses_original_b_records_and_does_not_change_them(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            args = self.fixture(root, 'pool-b')
            before = {str(p): p.read_bytes() for p in root.rglob('*.json')}
            plan, details = module.check(*args, pool='pool-b')
            self.assertEqual(plan['pool'], 'pool-b')
            self.assertEqual(len(details), 6)
            self.assertEqual(before, {str(p): p.read_bytes() for p in root.rglob('*.json')})
            with self.assertRaisesRegex(ValueError, 'another pool'):
                module.check(*args)

    def test_default_a_remains_compatible(self):
        with tempfile.TemporaryDirectory() as tmp:
            plan, _ = module.check(*self.fixture(Path(tmp), 'pool-a'))
            self.assertEqual(plan['pool'], 'pool-a')

    def test_missing_role_or_failed_audit_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            args = self.fixture(root, 'pool-b')
            path = root/'on0/observer-stop/pool-b-objects/raw-audit.json'
            path.write_text('{"passed":false}', encoding='utf-8')
            with self.assertRaisesRegex(ValueError, 'raw observer'):
                module.check(*args, pool='pool-b')
            path.unlink()
            with self.assertRaises(FileNotFoundError):
                module.check(*args, pool='pool-b')


if __name__ == '__main__':
    unittest.main()
