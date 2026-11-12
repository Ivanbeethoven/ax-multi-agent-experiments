from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from bench import fork_source as fs
from bench.experiment_io import digest, load, write_new
from bench.runtime.prepare import anchor
from scripts.full_trace_common import tree


LAYER = '10000000-0000-0000-0000-000000000001'
CHILD = '20000000-0000-0000-0000-000000000002'
SEALED = '30000000-0000-0000-0000-000000000003'
ROOT = '12'*32
REV = f'{LAYER}:1:{ROOT}'


class ForkSourceTest(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.point = self.root/'fuse'
        self.point.mkdir()
        reference = self.root/'fixture'
        reference.mkdir()
        self.payload(reference)
        self.manifest = self.record('fixture-tree.json', tree(reference))
        self.output = self.root/'qualified.json'
        self.mounts = {}
        self.processes = set()
        self.status = {}
        for key, kwargs in (
            ('mounts', {'side_effect': lambda point: self.mounts.get(str(point), [])}),
            ('alive', {'side_effect': lambda proc: proc['pid'] in self.processes}),
        ):
            edit = patch.object(fs, key, **kwargs)
            edit.start()
            self.addCleanup(edit.stop)
        edit = patch.object(fs.node, 'control', side_effect=lambda directory, _: self.status[directory])
        edit.start()
        self.addCleanup(edit.stop)

    def payload(self, where):
        (where/'dir').mkdir()
        (where/'dir'/'task').write_bytes(b'agent data\n')
        (where/'dir'/'empty').touch()

    def record(self, name, value):
        path = self.root/name
        path.parent.mkdir(exist_ok=True, parents=True)
        write_new(path, value)
        return self.ref(path)

    def ref(self, path):
        return dict(path=str(path), sha256=digest(path))

    def binary(self, name):
        path = self.root/name
        path.write_bytes(b'test binary identity only')
        return self.ref(path)

    def mount(self, directory, config, binary, filesystem, point, ident, pid=None):
        config_ref = self.record(directory+'/config.json', config)
        argv = [binary['path'], 'mount']
        argv += ['--config', config_ref['path']] if filesystem == 'brewfs' else [config['metadata_url']]
        argv.append(str(point))
        command = self.record(directory+'/mount-step.json', dict(schema='experiment-step-v1',
            reviewed=True, argv=argv, executable_sha256=binary['sha256'], inputs=[config_ref]))
        vol = anchor(filesystem, config)
        mount = dict(mount_id=ident, point=str(point), filesystem='fuse.'+filesystem,
                     source=filesystem, options='rw')
        mount_spec = self.record(directory+'/mount-spec.json', dict(schema='experiment-mount-v1',
            reviewed=True, filesystem=filesystem, mount_point=str(point), anchor=vol,
            allowed_fstypes=[mount['filesystem']], config=config_ref, command=command))
        pid = pid if pid is not None else ident+100
        value = dict(passed=True, mount=mount,
                     process=dict(pid=pid, boot_id='test-boot', start_ticks=9, state='S'),
                     anchor=vol, spec=mount_spec)
        self.status[self.root/directory] = value
        self.mounts[str(point)] = [mount]
        self.processes.add(pid)
        return mount_spec, self.record(directory+'/ready.json', value)

    def stop(self, ready):
        path = Path(ready['path'])
        original = load(path)
        self.mounts[str(original['mount']['point'])] = []
        self.processes.remove(original['process']['pid'])
        return self.record((path.parent/'stopped.json').relative_to(self.root),
                           dict(passed=True, exit_code=0, mount_absent=True, anchor=original['anchor'],
                                original_mount=original['mount'], active_workers=[]))

    def juice(self):
        source = self.point/'source'
        source.mkdir()
        (self.point/'children').mkdir()
        self.payload(source)
        writer_point = self.root/'writer-fuse'
        writer_point.mkdir()
        config, binary = dict(metadata_url='redis://127.0.0.1:6379/4', cache_dir='memory'), self.binary('juicefs')
        _, writer = self.mount('writer', config, binary, 'juicefs', writer_point, 70)
        stopped = self.stop(writer)
        mount, ready = self.mount('clone', config, binary, 'juicefs', self.point, 80)
        return dict(schema=fs.REQUEST, reviewed=True, cell_id='c4-source-1', method='juicefs-clone',
                    native=dict(binary=binary, mount=str(self.point), source=str(source),
                                destination_parent=str(self.point/'children'), threads=4),
                    mount_spec=mount, ready=ready, writer=dict(ready=writer, stopped=stopped),
                    input_tree=self.manifest)

    def brew(self, *, workspace=CHILD, base_layer=LAYER):
        self.payload(self.point)
        binary = self.binary('brewfs')
        data = dict(backend='s3', s3=dict(bucket='fixture-bucket'))
        native = dict(binary=binary, redis_url='redis://127.0.0.1:6379/5', namespace='c4-fixture',
                      revision=REV, data_config=self.record('data.json', dict(data=data)))
        config = dict(data=data, volume_format='workspace', workspace=workspace,
                      workspace_namespace=native['namespace'],
                      meta=dict(backend='redis', redis=dict(url=native['redis_url'])))
        mount, ready = self.mount('brew-mount', config, binary, 'brewfs', self.point, 90)
        publication = self.record('publication/stdout.log', dict(snapshot_id='probe-fixture', fork_source=REV,
            native_sealed_source=f'{SEALED}:2:{"34"*32}', manifest_key='source/packed/'+'56'*32,
            manifest_sha256='56'*32))
        step_data = dict(schema='experiment-step-v1', reviewed=True, cell_id='c4-qualification',
            stage='packed-carrier-probe', executable_sha256=binary['sha256'],
            inputs=[publication, self.manifest, native['data_config']],
            argv=[binary['path'], 'workspace', '--meta-backend', 'redis', '--meta-url', native['redis_url'],
                  '--workspace-namespace', native['namespace'], 'fork', REV, '--require-packed-carrier',
                  '--count', '1', '--data-config', native['data_config']['path']])
        step = self.record('probe-step.json', step_data)
        self.record('probe/spec.json', step_data)
        stdout = self.record('probe/stdout.log', [dict(workspace_id=CHILD, upper_bucket='qualification-child-upper',
            fork_base=dict(layer_id=base_layer, sealed_version=1, root_hash=[18]*32))])
        finished = self.record('probe/finished.json', dict(cell_id='c4-qualification',
            stage='packed-carrier-probe', spec_sha256=step['sha256'], passed=True, completed=True,
            automatic_retry=False, exit_code=0))
        return dict(schema=fs.REQUEST, reviewed=True, cell_id='c4-source-1', method='brewfs-fork',
                    native=native, mount_spec=mount, ready=ready, input_tree=self.manifest,
                    carrier=dict(publication=publication, step=step, stdout=stdout, finished=finished))

    def qualify(self, request):
        return fs.qualify(self.record('source-request.json', request), self.output)

    def preflight(self, request, **fields):
        expected = {k: request[k] for k in ('cell_id', 'method', 'native', 'input_tree')}
        expected['probe_stopped'] = None
        expected.update(fields)
        return fs.preflight(self.ref(self.output), **expected)

    def fresh_juicefs_mount(self, request):
        stopped = self.stop(request['ready'])
        original_mount = load(request['mount_spec']['path'])
        config = load(original_mount['config']['path'])
        mount, ready = self.mount('measurement', config, request['native']['binary'], 'juicefs', self.point, 81)
        return dict(probe_stopped=stopped, measurement=dict(mount_spec=mount, ready=ready))

    def test_juicefs_reads_exact_bytes_then_checks_live_source_without_another_tree_scan(self):
        request = self.juice()
        qualified = self.qualify(request)
        self.assertEqual((qualified['files'], qualified['bytes']), (2, 11))
        self.assertEqual(qualified['input_tree'], self.manifest)
        self.assertNotIn('reviewed', qualified)
        fresh = self.fresh_juicefs_mount(request)
        with patch.object(fs, 'tree', side_effect=AssertionError('no tree scan in preflight')):
            checked = self.preflight(request, **fresh)
            self.assertEqual(checked.pop('measurement_mount')['mount']['mount_id'], 81)
            self.assertEqual(checked, qualified)
            source = Path(request['native']['source'])
            source.rename(self.point/'old-source')
            source.mkdir()
            self.payload(source)
            with self.assertRaisesRegex(ValueError, 'source changed'):
                self.preflight(request, **fresh)

    def test_juicefs_refuses_replaced_live_mount(self):
        request = self.juice()
        self.qualify(request)
        fresh = self.fresh_juicefs_mount(request)
        self.mounts[str(self.point)] = [dict(self.mounts[str(self.point)][0], mount_id=190)]
        with self.assertRaisesRegex(ValueError, 'mount or foreground process changed'):
            self.preflight(request, **fresh)

    def test_juicefs_remount_can_reuse_kernel_mount_id_after_verified_stop(self):
        request = self.juice()
        original = self.qualify(request)['source_mount']
        stopped = self.stop(request['ready'])
        spec = load(request['mount_spec']['path'])
        config = load(spec['config']['path'])
        mount, ready = self.mount('measurement',config,request['native']['binary'],'juicefs',self.point,
                                  original['mount']['mount_id'],pid=181)
        checked = self.preflight(request,probe_stopped=stopped,measurement=dict(mount_spec=mount,ready=ready))
        self.assertEqual(checked['measurement_mount']['mount'],original['mount'])
        self.assertNotEqual(checked['measurement_mount']['process'],original['process'])

    def test_juicefs_must_not_measure_with_the_same_mount_used_for_readback(self):
        request = self.juice()
        self.qualify(request)
        with self.assertRaisesRegex(ValueError, 'normally closed qualification'):
            self.preflight(request, measurement={k: request[k] for k in ('mount_spec', 'ready')})
        stopped = self.stop(request['ready'])
        with self.assertRaisesRegex(ValueError, 'distinct live JuiceFS mount'):
            self.preflight(request, probe_stopped=stopped,
                           measurement={k: request[k] for k in ('mount_spec', 'ready')})

    def test_same_file_count_and_size_cannot_pass_different_bytes(self):
        request = self.juice()
        (self.point/'source'/'dir'/'task').write_bytes(b'other data\n')
        with self.assertRaisesRegex(ValueError, 'tree differs'):
            self.qualify(request)
        self.assertFalse(self.output.exists())

    def test_count_only_reference_rejected(self):
        request = self.juice()
        request['input_tree'] = self.record('unqualified.json', dict(files=100000))
        with self.assertRaisesRegex(ValueError, 'manifest path or entry'):
            self.qualify(request)

    def test_brewfs_binds_fork_returned_basis_and_requires_probe_shutdown(self):
        request = self.brew()
        qualified = self.qualify(request)
        self.assertEqual(qualified['backend']['revision'], REV)
        self.assertEqual(qualified['backend']['qualification_workspace'], CHILD)
        with self.assertRaisesRegex(ValueError, 'normally closed qualification'):
            self.preflight(request)
        stopped = self.stop(request['ready'])
        self.assertEqual(self.preflight(request, probe_stopped=stopped), qualified)
        with self.assertRaisesRegex(ValueError, 'does not bind this exact'):
            self.preflight(request, cell_id='other', probe_stopped=stopped)

    def test_brewfs_rejects_child_from_wrong_carrier(self):
        request = self.brew(base_layer=SEALED)
        with self.assertRaisesRegex(ValueError, 'not the native Packed probe child'):
            self.qualify(request)

    def test_brewfs_rejects_readback_of_wrong_workspace_even_if_all_bytes_match(self):
        request = self.brew(workspace=SEALED)
        with self.assertRaisesRegex(ValueError, 'not the native Packed probe child'):
            self.qualify(request)


if __name__ == '__main__':
    unittest.main()
