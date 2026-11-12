import importlib.util
from pathlib import Path
import unittest


source=Path(__file__).resolve().parents[1]/'experiments/unified-suite/pilot-aliyun/scripts/qualify-c3-workspace-base.py'
spec=importlib.util.spec_from_file_location('c3_probe_database',source)
module=importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class ProbeDatabase(unittest.TestCase):
    def test_occupied_databases_are_skipped_and_recorded(self):
        seen=[]
        def count(db):
            seen.append(db)
            return 0 if db==242 else 123
        db,receipt=module.select_database(0,count)
        self.assertEqual((db,seen),(242,[240,241,242]))
        self.assertTrue(receipt['no_deletions'])
        self.assertEqual(receipt['observations'][0],{'database':240,'keys':123})

    def test_explicit_populated_database_does_not_switch_silently(self):
        seen=[]
        def count(db): seen.append(db); return 1
        with self.assertRaisesRegex(ValueError,'existing data was preserved'):
            module.select_database(249,count)
        self.assertEqual(seen,[249])

    def test_exhaustion_and_original_asset_are_protected(self):
        seen=[]
        def count(db): seen.append(db); return 1
        with self.assertRaises(ValueError): module.select_database(0,count)
        self.assertNotIn(70,seen)
        self.assertEqual(len(seen),235)
        with self.assertRaises(ValueError): module.select_database(70,count)

    def test_backend_error_does_not_look_empty(self):
        def count(db): raise ConnectionError('Redis unavailable')
        with self.assertRaises(ConnectionError): module.select_database(0,count)
