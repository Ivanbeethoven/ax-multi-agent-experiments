"""Check address propagation, experimental scope and network measurement retries."""
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import Mock, patch

from bench.cluster import suite
from infra.idcai import compare_network_paths as probe
from infra.rustfs.generate_rustfs import generate

ROOT = Path(__file__).resolve().parents[2]

def read(name):
    return json.loads((ROOT / name).read_text(encoding='utf-8'))

class DirectNetworkTests(unittest.TestCase):
    def test_inventory_and_experimental_counts(self):
        network = read('configs/network.json')
        design = read('configs/cluster.json')
        experiment = read('configs/experiment-suite.json')
        built = suite.build(experiment)
        self.assertEqual({h['name']: h['ssh'] for h in design['hosts']},
                         {name: h['ssh'] for name, h in network['hosts'].items()})
        self.assertEqual(network['formal_data_network'], 'direct')
        self.assertEqual(built['total_runs'], 52)
        self.assertEqual(len(built['jobs']), 52)
        self.assertEqual(sum(built['counts'].values()), 52)
        self.assertEqual(experiment['resource_limits']['maximum_agents'], 64)
        self.assertEqual(
            max(job['parameters'].get('agents', 1) for job in built['jobs']),
            64,
        )

    def test_rustfs_generator_uses_direct_addresses_for_listening_and_peers(self):
        network = read('configs/network.json')
        spec = {'image': 'rustfs:test-network', 'image_source_commit': 'a' * 40,
                'mode': 'four-node-single-disk', 'private_subnet': network['subnet'], 'nodes': []}
        for name in network['rustfs']['nodes']:
            spec['nodes'].append({'private_ip': network['hosts'][name]['direct_ip'],
                                 'data_mounts': ['/srv/network-test/data'],
                                 'log_dir': '/srv/network-test/logs',
                                 'credentials_file': '/srv/network-test/private/credentials',
                                 'cpus': 4, 'memory_bytes': 8 * 1024**3})
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / 'deployment'
            generate(spec, output)
            for index, name in enumerate(network['rustfs']['nodes'], 1):
                service = json.loads((output / f'rustfs{index}/compose.json').read_text())['services']['rustfs']
                self.assertEqual(service['network_mode'], 'host')
                self.assertEqual(service['extra_hosts'], network['rustfs']['extra_hosts'])
                self.assertEqual(service['environment']['RUSTFS_ADDRESS'], network['hosts'][name]['direct_ip'] + ':9000')
                self.assertNotIn('10.77.', json.dumps(service))

    def test_resume_requires_successful_measurement(self):
        row = {'source': 'A355', 'target': 'A366', 'route': 'direct', 'repetition': 1,
               'error': None, 'exit_code': 0, 'megabits_per_second': 9400}
        self.assertTrue(probe.completed([row], 'A355', 'A366', 'direct', 1))
        self.assertFalse(probe.completed([row], 'A355', 'A366', 'wireguard', 1))
        self.assertFalse(probe.completed([{**row, 'error': 'connection closed'}], 'A355', 'A366', 'direct', 1))

    def test_empty_ssh_output_is_recorded_and_retry_preserves_previous_files(self):
        route = subprocess.CompletedProcess([], 0, stdout='175.168.3.66 dev eth0 src 175.168.3.55', stderr='')
        client = subprocess.CompletedProcess([], 255, stdout='', stderr='connection closed')
        server = Mock()
        server.communicate.return_value = ('{}', '')
        with tempfile.TemporaryDirectory() as directory:
            out = Path(directory)
            with patch.object(probe.subprocess, 'run', side_effect=[route, client, route, client]), \
                    patch.object(probe.subprocess, 'Popen', return_value=server), \
                    patch.object(probe.time, 'sleep'):
                first = probe.measure(out, 'A355', 'A366', 'direct', 1, 5)
                second = probe.measure(out, 'A355', 'A366', 'direct', 1, 5)
            self.assertTrue(first['error'])
            self.assertEqual(first['exit_code'], 255)
            self.assertEqual(second['attempt'], 2)
            self.assertTrue((out / (first['raw_prefix'] + '.client.stderr.txt')).exists())
            self.assertTrue((out / (second['raw_prefix'] + '.client.stderr.txt')).exists())

if __name__ == '__main__':
    unittest.main()
