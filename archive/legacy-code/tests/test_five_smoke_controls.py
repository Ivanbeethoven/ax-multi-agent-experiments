import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import subprocess
import sys

from bench.runtime.packed_metadata_probe import verify_off
from bench.runtime.unified_candidate import verify
from bench.runtime.move_target import different_host, mounted
from bench.runtime.tree_verify import verify_tree, write_probe
from bench.runtime.smoke_tree import file_tree


def stats(kv, objects=0, valid=1):
    return (f'brewfs_native_placement_kv_operations_succeeded_total{{phase="runtime"}} {kv}\n'
            f'brewfs_object_read_started_total{{phase="runtime",layer="backend_body",kind="group_metadata"}} {objects}\n'
            f'brewfs_object_read_counters_valid {valid}\n')


class MetadataWitness(unittest.TestCase):
    def test_many_directories_and_target_write_leave_tree_unchanged(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            for i in range(90):
                sub=root/str(i);sub.mkdir();(sub/'f').write_bytes(b'x')
            expected=file_tree(root)
            self.assertEqual(verify_tree(root,expected)['entries'],180)
            self.assertTrue(write_probe(root)['passed'])
            self.assertEqual(file_tree(root),expected)

    def test_move_dispatch_needs_no_agent_binary(self):
        from bench.experiment_smoke import main
        with tempfile.TemporaryDirectory() as folder:
            out=Path(folder)/'out'
            args=['smoke','--experiment','C4-handoff','--mode','juicefs','--placement','move',
                  '--brewfs','/brewfs','--producer','/producer','--smallfiles','/smallfiles',
                  '--juicefs','/juicefs','--brewfs-revision','a'*40,'--rustfs-image','image',
                  '--checkpoint','/checkpoint','--ssh-config','/ssh-config','--target-host','target',
                  '--target-repo','/repo','--target-tools','/tools','--out',str(out)]
            def child(command):
                self.assertNotIn('--opencode',command)
                result=Path(command[command.index('--out')+1]);result.mkdir()
                (result/'receipt.json').write_text('{"passed":true}')
                return subprocess.CompletedProcess(command,0)
            with patch.object(sys,'argv',args),patch('bench.experiment_smoke.subprocess.run',side_effect=child):
                self.assertEqual(main(),0)
            self.assertIn('filesystem-only',json.loads((out/'receipt.json').read_text())['runtime_scope'])

    def test_tree_does_not_swallow_deadline_as_missing_directory(self):
        with patch('os.scandir',side_effect=TimeoutError('deadline')):
            with self.assertRaises(TimeoutError): file_tree(Path('/workspace'))

    def test_disconnected_fuse_still_counts_as_mounted(self):
        with patch.object(Path,'read_text',return_value='1 2 0:1 / /srv/ax-pilot-session rw - fuse brewfs rw\n'),\
                patch('os.path.ismount',return_value=False):
            self.assertTrue(mounted(Path('/srv/ax-pilot-session')))

    def test_parallel_tree_checks_content_permissions_links_and_extra_names(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder); (root/'dir').mkdir(); (root/'dir/f').write_bytes(b'original')
            (root/'link').symlink_to('dir/f')
            expected=file_tree(root)
            self.assertTrue(verify_tree(root,expected)['passed'])
            (root/'extra').mkdir()
            with self.assertRaises(ValueError): verify_tree(root,expected)
            (root/'extra').rmdir(); (root/'dir/f').write_bytes(b'modified')
            with self.assertRaises(ValueError): verify_tree(root,expected)
            (root/'dir/f').write_bytes(b'original'); (root/'dir').chmod(0o700)
            with self.assertRaises(ValueError): verify_tree(root,expected)
            with self.assertRaises(ValueError): verify_tree(root,{'../escape':{'type':'directory'}})

    def test_kv_only_qualified(self):
        self.assertEqual(verify_off(stats(4),stats(9))['runtime_kv_operations'],5)

    def test_ignored_switch_or_resets_or_object_fallback_fail(self):
        for before,after in [('',stats(3)),(stats(4),stats(4)),(stats(4),stats(3)),
                             (stats(4),stats(5,1)),(stats(4,2),stats(5,1)),
                             (stats(4),stats(5,valid=0))]:
            with self.subTest(before=before,after=after),self.assertRaises(ValueError):
                verify_off(before,after)
        for kind in ('cold_index','cold_attributes','source_stats_index'):
            with self.assertRaises(ValueError):
                verify_off(stats(4),stats(5)+f'brewfs_object_read_started_total{{phase="runtime",layer="backend_body",kind="{kind}"}} 1\n')
        with self.assertRaises(ValueError):
            verify_off(stats(4),stats(5).splitlines()[0]+'\nbrewfs_object_read_counters_valid 1\n')


    def test_same_host_or_missing_identity_rejected(self):
        good={'machine':'a','boot':'b'}
        for target in ({},{'machine':'a','boot':'c'},{'machine':'c','boot':'b'}):
            with self.assertRaises(ValueError): different_host(good,target)
        different_host(good,{'machine':'c','boot':'d'})

    def test_provenance_requires_capability_and_actual_bytes(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder); binary=root/'brewfs'; binary.write_bytes(b'binary')
            proof=root/'build.json'; sha=hashlib.sha256(binary.read_bytes()).hexdigest()
            receipt=dict(schema='brewfs-unified-candidate-v1',build_exit_code=0,
                source_commit='a'*40,candidate_diff_sha256='b'*64,cargo_default_features=True,
                cargo_features=['workspace-overlay','observability','rustfs-ec-block-size-hint'],
                capabilities=['packed-metadata-off'],binary_sha256={'brewfs':sha})
            proof.write_text(json.dumps(receipt))
            self.assertEqual(verify(binary,None,proof,'a'*40,'packed-metadata-off')['brewfs'],sha)
            with self.assertRaises(ValueError): verify(binary,None,proof,'a'*40,'eager-workspace')
            binary.write_bytes(b'changed')
            with self.assertRaises(ValueError): verify(binary,None,proof,'a'*40,'packed-metadata-off')

    def test_all_off_stays_in_workspace_engine_and_disables_dynamic(self):
        from bench.experiment_smoke import main
        with tempfile.TemporaryDirectory() as folder:
            out=Path(folder)/'out'
            args=['smoke','--experiment','C2-replay','--mode','all-off',
                '--brewfs','/brewfs','--producer','/producer','--smallfiles','/smallfiles',
                '--juicefs','/juicefs','--brewfs-revision','a'*40,'--rustfs-image','image',
                '--brewfs-provenance','/proof','--trace','/trace','--replay','/replay','--out',str(out)]
            def child(command):
                for flag,expected in (('--engine','workspace-v1'),('--packed-metadata','off'),
                                      ('--layout','disabled'),('--hint','off')):
                    self.assertEqual(command[command.index(flag)+1],expected)
                self.assertIn('--eager-workspace',command)
                result=Path(command[command.index('--out')+1]);result.mkdir()
                (result/'receipt.json').write_text('{"passed":true}')
                return subprocess.CompletedProcess(command,0)
            with patch.object(sys,'argv',args),patch('bench.runtime.unified_candidate.verify',return_value={}),\
                    patch('bench.experiment_smoke.subprocess.run',side_effect=child):
                self.assertEqual(main(),0)
