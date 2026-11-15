import ast
import json
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[2]


class BackendHintTests(unittest.TestCase):
    def test_client_policy_matches_selected_server(self):
        source = ROOT / 'bench/cluster/runtime/cluster_lib.py'
        tree = ast.parse(source.read_text(encoding='utf-8'))
        function = next(node for node in tree.body if isinstance(node, ast.FunctionDef)
                        and node.name == 'effective_ec_hint')
        backends = json.loads((ROOT / 'configs/rustfs-backends.json').read_text())
        namespace = {'RUSTFS_BACKENDS': backends}
        exec(compile(ast.Module(body=[function], type_ignores=[]), str(source), 'exec'), namespace)
        resolve = namespace['effective_ec_hint']
        for endpoint in backends['official']['endpoints']:
            for requested in ('auto', '65536', 'off'):
                self.assertEqual(resolve(endpoint, requested), 'off')
        for endpoint in backends['dynamic']['endpoints']:
            for requested in ('auto', '65536', '4194304'):
                self.assertEqual(resolve(endpoint, requested), requested)
        with self.assertRaises(ValueError):
            resolve('http://unconfigured:9000')


if __name__ == '__main__':
    unittest.main()
