import os
from pathlib import Path
import tempfile
import unittest

from bench.cluster.c4_workload import apply,make_changes,prepare,verify,read_tree


@unittest.skipUnless(os.name=='posix','File modes and directory fsync use Linux semantics')
class WorkloadTests(unittest.TestCase):
    def test_mixed_changes_and_independent_children(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            prepare(root,80,4096,2)
            changes=make_changes(20,child=0)+make_changes(20,child=1,start_index=40)
            result=apply(root,changes)
            self.assertEqual(result['operations'],40)
            checked=verify(root,80,4096,changes,2)
            self.assertTrue(checked['passed'])
            self.assertEqual(checked['files'],84)
            (root/'files/d0000/f0000000').write_bytes(b'wrong')
            with self.assertRaises(RuntimeError):verify(root,80,4096,changes,2)

    def test_root_level_juicefs_virtual_files_are_ignored_but_nested_files_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            prepare(root, 8, 4096, 1)
            for name in (".accesslog", ".config", ".stats"):
                (root / name).write_text("JuiceFS virtual file", encoding="utf-8")

            checked = verify(root, 8, 4096, [], 1)
            self.assertTrue(checked["passed"])

            nested = root / "files" / ".stats"
            nested.write_text("unexpected nested file", encoding="utf-8")
            with self.assertRaises(RuntimeError):
                verify(root, 8, 4096, [], 1)

    def test_range_and_full_file_have_same_result(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            changes=make_changes(2,kind='range-overwrite',write_bytes=4096)
            changes[0]['offset']=1024*1024-2048
            amounts=[]
            for name,full_file in (('range',False),('whole',True)):
                target=root/name;prepare(target,2,2*1024*1024,1)
                result=apply(target,changes,full_file)
                amounts.append(result['logical_write_bytes'])
                self.assertTrue(verify(target,2,2*1024*1024,changes,1)['passed'])
            self.assertEqual(amounts,[8192,4*1024*1024])
            for p in (root/'range').rglob('*'):
                if p.is_file():self.assertEqual(p.read_bytes(),(root/'whole'/p.relative_to(root/'range')).read_bytes())

    def test_read_bytes_and_first_available_sample(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);prepare(root,3,2*1024*1024,1)
            read=read_tree(root,3,2*1024*1024)
            self.assertEqual(read['logical_read_bytes'],6*1024*1024)
            changes=make_changes(1,kind='range-overwrite')
            apply(root,changes)
            sample=verify(root,3,2*1024*1024,changes,1,True)
            self.assertTrue(sample['passed'])
            self.assertEqual(sample['read_bytes'],8192)


if __name__=='__main__':unittest.main()
