import importlib.util
from pathlib import Path
import unittest


class DynamicRepeatTests(unittest.TestCase):
    def test_waits_for_formal_results_and_other_diagnostics(self):
        path = Path(__file__).resolve().parents[2] / 'bench/cluster/diagnostics/dynamic_fio/repeat_when_idle.py'
        spec = importlib.util.spec_from_file_location('dynamic_repeat', path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        complete = {'jobs': {'one': {'state': 'passed'}, 'two': {'state': 'passed'}}}
        self.assertEqual(module.readiness(complete, True, False), 'formal-suite-running')
        self.assertEqual(module.readiness(complete, False, True), 'diagnostics-running')
        self.assertEqual(module.readiness(complete, False, False), 'ready')
        for state in ['running', 'failed', 'planned']:
            pending = {'jobs': {'one': {'state': 'passed'}, 'two': {'state': state}}}
            self.assertEqual(module.readiness(pending, False, False), 'formal-suite-incomplete')
        self.assertEqual(module.readiness({}, False, False), 'formal-suite-incomplete')


if __name__ == '__main__':
    unittest.main()
