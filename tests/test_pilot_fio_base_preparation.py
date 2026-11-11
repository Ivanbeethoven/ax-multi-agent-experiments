"""Prepare fixed CRC inputs with one clean reopen and no destructive retry."""
import importlib.util
import io
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from bench.fio_workload import arguments
from bench.suite_catalog import fio_params

REPO = Path(__file__).resolve().parents[1]
FILE = REPO/'experiments/unified-suite/pilot-aliyun/scripts/prepare-fio-base.py'
spec = importlib.util.spec_from_file_location('pilot_fio_base_preparation', FILE)
module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)


class FioBasePreparation(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.client = Path(self.tmp.name)/'client'; self.client.mkdir()
        self.slot = self.client/'cgroup.procs'; self.slot.write_text('')
        self.binary = self.client/'brewfs'; self.binary.write_bytes(b'test binary pin')
        self.plan = self.client/'plan.json'; self.inventory = self.client/'inventory.json'; self.inventory.write_text('{}')
        self.cells = [dict(id='fio-'+mode, case='fio-'+mode, workload='fio', pool='pool-a',
            arm_id='native', arm=dict(product='brewfs'), params=fio_params(mode, True))
            for mode in ('read', 'randread')]
        self.plan.write_text(json.dumps(dict(cells=self.cells, config=dict(seed=17))))
        tool = REPO/'experiments/unified-suite/pilot-aliyun/scripts/pilot-node.py'
        self.args = SimpleNamespace(product='brewfs', pool='pool-a', asset=self.client/'a/fio-inputs',
            out=self.client/'preparation/fio-inputs', plan=self.plan, inventory=self.inventory,
            node_tool=tool, node_tool_sha256=module.digest(tool), filesystem_binary=self.binary,
            filesystem_binary_sha256=module.digest(self.binary), attempt='fio-inputs', database=50)
        self.calls = []; self.dbsize = '0'; self.corrupt_cold = False; self.fail_stop = False

    def tearDown(self): self.tmp.cleanup()

    def put(self, path, value):
        path.parent.mkdir(parents=True, exist_ok=True); path.write_text(json.dumps(value)); return module.ref(path)

    def popen(self, argv, **kwargs):
        self.calls.append(argv)
        if argv[0]=='redis-cli': kwargs['stdout'].write((self.dbsize+'\n').encode())
        else:
            action = argv[2]
            def opt(name): return argv[argv.index('--'+name)+1]
            out = Path(opt('out'))
            if action=='asset-init':
                self.args.asset.mkdir(parents=True); point = self.args.asset/'mnt'; point.mkdir()
                self.put(self.args.asset/'asset.json', dict(plan=module.ref(self.plan), pool='pool-a',
                    database=50, filesystem_binary=module.ref(self.binary), mount_point=str(point)))
            elif action=='draft-mount': self.put(out/'mount.reviewed.json', {})
            elif action=='start':
                self.put(out/'ready.json', {})
                self.put(out/'process.json', dict(process=dict(pid=999999), supervisor=dict(pid=999998)))
            elif action in ('fio-base', 'fio-base-check'):
                block = int(opt('block-bytes')); phase = 'prepare' if action=='fio-base' else 'verify'
                params = next(cell['params'] for cell in self.cells if cell['params']['block_bytes']==block)
                request = dict(binary=dict(path='/usr/bin/fio'), root=str(self.args.asset/'mnt'/('base-'+str(block))),
                    params=params, seed=17)
                options = dict(option[2:].split('=', 1) for option in arguments(request, phase, out/'fio.json') if option.startswith('--'))
                raw = dict(jobs=[dict(jobname='job-0', error=0, **{'job options':options},
                    read=dict(io_bytes=module.BYTES if phase=='verify' else 0),
                    write=dict(io_bytes=module.BYTES if phase=='prepare' else 0))])
                raw_ref = self.put(out/'fio.json', raw); self.put(out/'exit.json', dict(exit_code=0))
                self.put(out/'result.json', dict(passed=True, completed=True, jobs=1, application_bytes=module.BYTES,
                    phase=phase, params=params, seed=17, wrapper_wall_seconds=.1,
                    raw_sha256='bad' if self.corrupt_cold and out.name.startswith('cold-') else raw_ref['sha256']))
            elif action=='debt':
                samples = [self.put(out/('sample-'+str(i)+'.json'), dict(passed=True, cell_id='fio-randread',
                    monotonic_ns=i*1050000000, pending_upload_bytes=0, pending_upload_operations=0)) for i in range(2)]
                self.put(out/'result.json', dict(passed=True, samples=samples))
            elif action=='stop':
                if self.fail_stop:
                    return SimpleNamespace(pid=999999, wait=lambda:1)
                value = dict(passed=True, exit_code=0, mount_absent=True)
                self.put(out, value); self.put(Path(opt('mount-run'))/'stopped.json', value)
            else: self.fail('Unexpected helper: '+action)
        return SimpleNamespace(pid=999999, wait=lambda:0)

    def execute(self):
        metrics = b'brewfs_fuse_op_total 1\nbrewfs_mount_info 1\nbrewfs_writeback_pending_upload_bytes 0\n'
        with patch.object(module, 'CLIENT', self.client), patch.object(module, 'SLOT', self.slot), \
                patch.object(module, 'unused_metrics'), patch.object(module.subprocess, 'Popen', side_effect=self.popen), \
                patch('bench.runtime.pilot_pools.endpoint_binding', return_value=dict(metadata='redis://10.1.1.1:6379')), \
                patch('bench.runtime.common.alive', return_value=False), \
                patch('bench.runtime.common.mounts', return_value=[]), \
                patch.object(module, 'build_opener', return_value=SimpleNamespace(open=lambda *a, **k:io.BytesIO(metrics))), \
                patch('builtins.print'):
            module.prepare(self.args)

    def test_both_geometries_warm_and_cold_and_original_receipt_names(self):
        self.execute(); result = module.load(self.args.out/'result.json')
        self.assertTrue(result['passed']); self.assertEqual(len(result['input_checks']), 6)
        expected = ['base-seq-prepare', 'base-seq-verify', 'base-random-prepare', 'base-random-verify',
                    'cold-base-verify-1048576', 'cold-base-verify-4096', 'base-debt']
        self.assertTrue(all((self.args.asset/path/'result.json').is_file() for path in expected))
        self.assertTrue((self.args.asset/'native-endpoint.prom').is_file())
        stops = [argv for argv in self.calls if len(argv)>2 and argv[2]=='stop']
        self.assertEqual(len(stops), 2)
        self.assertNotEqual(stops[0][stops[0].index('--mount-run')+1], stops[1][stops[1].index('--mount-run')+1])

    def test_nonempty_database_keeps_failure_and_never_initializes_or_flushes(self):
        self.dbsize = '5'
        with self.assertRaisesRegex(ValueError, 'never flush'): self.execute()
        self.assertFalse(self.args.asset.exists()); self.assertEqual(len(self.calls), 1)
        self.assertFalse(module.load(self.args.out/'failed.json')['passed'])

    def test_changed_binary_and_occupied_slot_refuse_before_any_command(self):
        self.args.filesystem_binary_sha256 = 'f'*64
        with self.assertRaisesRegex(ValueError, 'changed'): self.execute()
        self.args.filesystem_binary_sha256 = module.digest(self.binary); self.slot.write_text('42\n')
        with self.assertRaisesRegex(ValueError, 'slot remains occupied'): self.execute()
        self.assertEqual(self.calls, []); self.assertFalse(self.args.out.exists())

    def test_cold_crc_failure_is_preserved_and_only_original_mount_stops(self):
        self.corrupt_cold = True
        with self.assertRaisesRegex(ValueError, 'CRC fio result'): self.execute()
        self.assertFalse((self.args.out/'result.json').exists())
        self.assertTrue((self.args.out/'failed.json').is_file())
        self.assertTrue((self.args.out/'failure-normal-stop.json').is_file())
        self.assertEqual(sum(len(argv)>2 and argv[2]=='start' for argv in self.calls), 2)
        self.assertEqual(sum(len(argv)>2 and argv[2]=='fio-base' for argv in self.calls), 2)

    def test_failed_normal_stop_is_not_retried(self):
        self.fail_stop = True
        with self.assertRaisesRegex(RuntimeError, 'original failed stage'): self.execute()
        self.assertEqual(sum(len(argv)>2 and argv[2]=='stop' for argv in self.calls), 1)
        self.assertTrue(module.load(self.args.out/'failed.json')['stop_already_attempted'])


if __name__=='__main__': unittest.main()
