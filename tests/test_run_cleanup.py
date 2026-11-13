import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

from bench import run_cleanup as C
from bench.runtime.smoke_cleanup import close_private_services, mounts_below


def passed_receipt():
    flags=dict(mounts_absent=True,rustfs_removed=True,redis_exited=True,filesystem_processes_exited=True,errors={})
    return dict(status='passed',evidence=dict(passed=True,cleanup=flags))


class Redis:
    def __init__(self): self.exited=False
    def poll(self): return 0 if self.exited else None
    def terminate(self): self.exited=True
    def wait(self,timeout): return 0


class PrivateServices(unittest.TestCase):
    def test_nested_mount_and_escaped_path_are_seen_without_stat(self):
        text='20 1 8:1 / /tmp/root/sub rw - fuse.test test rw\n21 1 8:1 / /tmp/with\\040space rw - fuse.test test rw\n'
        self.assertEqual(mounts_below(['/tmp/root'],text),['/tmp/root/sub'])
        self.assertEqual(mounts_below(['/tmp/with space'],text),['/tmp/with space'])

    def close(self,call,root):
        redis=Redis()
        with patch('bench.runtime.smoke_cleanup.mounts_below',return_value=[]):
            result=close_private_services(call=call,owner='owner',out=root,scratch=root/'tmp',
                points=[],redis=redis)
        self.assertTrue(redis.exited,'container error must not skip Redis termination')
        return result

    def test_docker_daemon_error_is_unknown_not_removed(self):
        with tempfile.TemporaryDirectory() as folder:
            def call(*args,**_): return subprocess.CompletedProcess(args,1,'','daemon unavailable')
            result=self.close(call,Path(folder))
            self.assertFalse(result['rustfs_removed'])
            self.assertFalse(result['passed'])
            self.assertIn('container',result['errors'])

    def test_log_error_still_removes_owned_container_by_full_id(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);ident='a'*64;removed=[]
            def call(*args,**_):
                if args[1:3]==('container','ls'):
                    return subprocess.CompletedProcess(args,0,'' if removed else ident+' owner\n','')
                if args[1:3]==('container','inspect'):
                    return subprocess.CompletedProcess(args,0,json.dumps([{'Id':ident,'Config':{'Labels':{
                        'ax.experiment.run':'owner','ax.experiment.attempt':str(root.resolve())}}}]),'')
                if args[1]=='logs':raise subprocess.TimeoutExpired(args,1)
                if args[1]=='rm':
                    self.assertEqual(args[-1],ident);removed.append(ident)
                    return subprocess.CompletedProcess(args,0,'','')
                self.fail(args)
            result=self.close(call,root)
            self.assertTrue(result['rustfs_removed'])
            self.assertFalse(result['passed'])
            self.assertIn('container-log',result['errors'])

    def test_wrong_owner_never_sends_remove(self):
        with tempfile.TemporaryDirectory() as folder:
            calls=[];ident='b'*64
            def call(*args,**_):
                calls.append(args)
                if args[1:3]==('container','ls'):
                    return subprocess.CompletedProcess(args,0,ident+' owner\n','')
                return subprocess.CompletedProcess(args,0,json.dumps([{'Id':ident,'Config':{'Labels':{}}}]),'')
            result=self.close(call,Path(folder))
            self.assertFalse(result['rustfs_removed'])
            self.assertFalse(any(call[1]=='rm' for call in calls))

    def test_successful_absence_query_and_private_process_exit(self):
        with tempfile.TemporaryDirectory() as folder:
            result=self.close(lambda *a,**k:subprocess.CompletedProcess(a,0,'',''),Path(folder))
            self.assertTrue(result['passed'])


class TargetCleanup(unittest.TestCase):
    def test_unmount_error_does_not_skip_process_exit_or_claim_success(self):
        from bench.runtime.move_target import close_target
        from unittest.mock import Mock
        proc=Mock();proc.wait.return_value=0
        with patch('bench.runtime.move_target.mounted',side_effect=[True,False]),patch(
                'bench.runtime.move_target.sp.run',side_effect=OSError('unmount unavailable')):
            proof=close_target(proc,Path('/mount'))
        proc.wait.assert_called_once_with(timeout=20)
        self.assertFalse(proof['passed'])
        self.assertTrue(proof['mount_absent'])
        self.assertIn('unmount',proof['errors'])

    def test_target_retains_failed_scratch_and_removes_only_passed(self):
        from bench.runtime.move_target import retain_or_remove_scratch
        with tempfile.TemporaryDirectory() as folder:
            scratch=Path(folder)/'private';scratch.mkdir();(scratch/'log').write_text('diagnosis')
            proof={'passed':False}
            retain_or_remove_scratch(proof,scratch)
            self.assertFalse(proof['scratch_removed'])
            proof={'passed':True}
            with patch('bench.runtime.move_target.mounts_below',return_value=[]):
                retain_or_remove_scratch(proof,scratch)
            self.assertTrue(proof['passed'])
            self.assertTrue(proof['scratch_removed'])


@unittest.skipUnless(sys.platform=='linux','Linux ownership, mount and fd-safe deletion')
class OwnedScratch(unittest.TestCase):
    def fixture(self,root):
        parent=root/'temporary';parent.mkdir()
        record=root/'record';receipt=root/'receipt.json'
        receipt.write_text(json.dumps(passed_receipt()))
        scratch=C.create(record,parent,receipt)
        return scratch,record,receipt

    def close_plan(self,record):
        C.write_new(record/'child-exit.json',{'exit_code':0})
        plan=C.preview(record,_allow_current_owner=True)
        C.write_new(record/'preview.json',plan)
        return plan

    def apply(self,record):
        return C.apply(record/'preview.json',C.digest(record/'preview.json'),
                       record/'applied.json',_allow_current_owner=True)

    def test_success_removes_owned_temp_only_and_does_not_follow_links(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);scratch,record,receipt=self.fixture(root)
            keep=root/'shared-input';keep.write_bytes(b'preserve')
            (scratch/'symlink').symlink_to(keep)
            (scratch/'data').write_bytes(b'temporary')
            result=C.finish(record,0)
            self.assertTrue(result['passed'])
            self.assertFalse(scratch.exists())
            self.assertEqual(keep.read_bytes(),b'preserve')
            self.assertTrue(receipt.exists())
            self.assertTrue((record/'applied.json').is_file())

    def test_failed_missing_or_nonzero_receipt_is_retained(self):
        for kind in ('failed','missing','nonzero','missing-closure'):
            with self.subTest(kind=kind),tempfile.TemporaryDirectory() as folder:
                scratch,record,receipt=self.fixture(Path(folder))
                if kind=='missing':receipt.unlink()
                elif kind=='failed':receipt.write_text('{"status":"failed"}')
                elif kind=='missing-closure':receipt.write_text('{"status":"passed","evidence":{"passed":true}}')
                result=C.finish(record,3 if kind=='nonzero' else 0)
                self.assertTrue(result['retained'])
                self.assertTrue(scratch.exists())
                self.assertFalse((record/'applied.json').exists())

    def test_live_creator_and_new_mount_block_external_cleanup(self):
        with tempfile.TemporaryDirectory() as folder:
            scratch,record,_=self.fixture(Path(folder));self.close_plan(record)
            self.assertIn('still running',' '.join(C.preview(record)['reasons']))
            with patch('bench.run_cleanup.mounts_below',return_value=[str(scratch/'mnt')]):
                self.assertFalse(C.preview(record,_allow_current_owner=True)['eligible'])
                with self.assertRaises(ValueError):self.apply(record)
            self.assertTrue(scratch.exists())

    def test_changed_scratch_or_closure_after_preview_is_not_deleted(self):
        for kind in ('content','receipt','marker','wrong-sha'):
            with self.subTest(kind=kind),tempfile.TemporaryDirectory() as folder:
                scratch,record,receipt=self.fixture(Path(folder));self.close_plan(record)
                if kind=='content':(scratch/'new').write_text('unexpected writer')
                elif kind=='receipt':receipt.write_text(json.dumps(passed_receipt())+' ')
                elif kind=='marker':(scratch/C.MARKER).write_text('{}')
                with self.assertRaises(ValueError):
                    if kind=='wrong-sha':C.apply(record/'preview.json','0'*64,record/'apply.json',_allow_current_owner=True)
                    else:self.apply(record)
                self.assertTrue(scratch.exists())

    def test_root_replacement_symlink_and_special_file_are_rejected(self):
        for kind in ('replacement','symlink','fifo'):
            with self.subTest(kind=kind),tempfile.TemporaryDirectory() as folder:
                root=Path(folder);scratch,record,_=self.fixture(root);self.close_plan(record)
                if kind=='fifo':os.mkfifo(scratch/'unknown-pipe')
                else:
                    moved=root/'original';scratch.rename(moved)
                    if kind=='symlink':scratch.symlink_to(moved,target_is_directory=True)
                    else:scratch.mkdir();(scratch/C.MARKER).write_bytes((moved/C.MARKER).read_bytes())
                self.assertFalse(C.preview(record,_allow_current_owner=True)['eligible'])
                with self.assertRaises(ValueError):self.apply(record)

    def test_external_cli_can_clean_after_creator_exits(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);(root/'parent').mkdir()
            code="from pathlib import Path; from bench.run_cleanup import create,write_new; import sys,json; r=Path(sys.argv[1]); s=create(r/'record',r/'parent',r/'receipt.json'); (s/'file').write_text('test'); write_new(r/'record/child-exit.json',{'exit_code':0})"
            (root/'receipt.json').write_text(json.dumps(passed_receipt()))
            subprocess.run([sys.executable,'-c',code,str(root)],check=True)
            command=[sys.executable,'-m','bench.run_cleanup']
            subprocess.run(command+['preview','--directory',str(root/'record'),'--out',str(root/'preview.json')],check=True,capture_output=True)
            value=json.loads((root/'preview.json').read_text())
            self.assertTrue(value['eligible'])
            subprocess.run(command+['apply','--plan',str(root/'preview.json'),'--sha256',C.digest(root/'preview.json'),
                                    '--out',str(root/'applied.json')],check=True,capture_output=True)
            self.assertFalse(Path(value['root']).exists())

    def test_capacity_reserves_bytes_and_inodes_before_creation(self):
        low=dict(total_bytes=1000,available_bytes=250,total_inodes=100,available_inodes=25)
        with patch('bench.run_cleanup.capacity',return_value=low):
            C.admit('/unused',50,5)
            for b,i in ((51,0),(0,6)):
                with self.assertRaisesRegex(ValueError,'20%'):C.admit('/unused',b,i)

    def test_matrix_routes_temp_and_cleans_between_cases(self):
        from bench.experiment_smoke_matrix import main
        from test_smoke_matrix_selection import COMMON
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);parent=root/'temporary';parent.mkdir();output=root/'results';seen=[]
            def run(cmd,**kwargs):
                temp=Path(kwargs['env']['TMPDIR']);seen.append(temp)
                self.assertTrue(temp.is_relative_to(parent))
                (temp/'fixture').write_text('temporary')
                out=Path(cmd[cmd.index('--out')+1]);out.mkdir()
                (out/'receipt.json').write_text(json.dumps(passed_receipt()))
                return subprocess.CompletedProcess(cmd,0)
            argv=['matrix','--block','c1-io','--out',str(output),'--scratch-parent',str(parent),*COMMON]
            with patch.object(sys,'argv',argv),patch('bench.experiment_smoke_matrix.subprocess.run',side_effect=run),redirect_stdout(io.StringIO()):
                self.assertEqual(main(),0)
            self.assertEqual(len(set(seen)),2)
            self.assertTrue(all(not path.exists() for path in seen))
            rows=json.loads((output/'summary.json').read_text())
            self.assertTrue(all(row['scratch_cleanup']['passed'] for row in rows))

    def test_low_capacity_prevents_child_and_failed_case_retains_temp(self):
        from bench.experiment_smoke_matrix import main
        from test_smoke_matrix_selection import COMMON
        for kind in ('capacity','failed'):
            with self.subTest(kind=kind),tempfile.TemporaryDirectory() as folder:
                root=Path(folder);parent=root/'temporary';parent.mkdir();output=root/'results';seen=[]
                def run(cmd,**kwargs):
                    temp=Path(kwargs['env']['TMPDIR']);seen.append(temp)
                    (temp/'diagnosis').write_text('failure')
                    out=Path(cmd[cmd.index('--out')+1]);out.mkdir()
                    (out/'receipt.json').write_text('{"status":"failed","evidence":{"passed":false}}')
                    return subprocess.CompletedProcess(cmd,2)
                argv=['matrix','--block','c1-io','--out',str(output),'--scratch-parent',str(parent),*COMMON]
                row=dict(total_bytes=1000,available_bytes=100 if kind=='capacity' else 1000,
                         total_inodes=100,available_inodes=100)
                with patch.object(sys,'argv',argv),patch('bench.run_cleanup.capacity',return_value=row),patch(
                        'bench.experiment_smoke_matrix.subprocess.run',side_effect=run) as launch,redirect_stdout(io.StringIO()):
                    self.assertEqual(main(),2)
                if kind=='capacity':
                    launch.assert_not_called()
                    self.assertEqual(list(parent.iterdir()),[])
                else:
                    self.assertEqual(len(seen),1)
                    self.assertEqual((seen[0]/'diagnosis').read_text(),'failure')
                self.assertEqual(len(json.loads((output/'summary.json').read_text())),1)
