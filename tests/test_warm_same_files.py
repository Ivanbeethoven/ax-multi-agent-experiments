import copy
import json
import sys
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'harness'))
import warm_same_files as warm

class WarmGate(unittest.TestCase):
    def setUp(self):
        self.inventory=[{'path':'/mnt/test/file0','bytes':8192}]
        self.result={'jobs':[{'jobname':'file-0','error':0,
                            'read':{'io_bytes':8192,'bw_bytes':4096},'write':{'io_bytes':0}}]}
    def test_complete_read_and_exact_filename(self):
        self.assertEqual(warm.qualify_result(self.result,self.inventory,True),4096)
        config=warm.job_text(self.inventory,'randread',30)
        self.assertIn('filename=/mnt/test/file0',config)
        self.assertIn('allow_file_create=0',config)
        self.assertNotIn('directory=',config)
    def test_missing_zero_partial_error_or_write_is_rejected(self):
        cases=[]
        missing=copy.deepcopy(self.result);missing['jobs']=[];cases.append(missing)
        for field,value in [('error',5),('jobname','another-file')]:
            c=copy.deepcopy(self.result);c['jobs'][0][field]=value;cases.append(c)
        for field,value in [('io_bytes',0),('bw_bytes',0),('io_bytes',4096)]:
            c=copy.deepcopy(self.result);c['jobs'][0]['read'][field]=value;cases.append(c)
        c=copy.deepcopy(self.result);c['jobs'][0]['write']['io_bytes']=4096;cases.append(c)
        for c in cases:
            with self.subTest(c=c), self.assertRaises(ValueError):
                warm.qualify_result(c,self.inventory,True)

    def test_prepare_and_check_never_start_fio_or_next_phase(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(warm,'file_inventory',return_value=self.inventory), \
             patch('subprocess.run',side_effect=AssertionError('must not execute')):
            out=Path(tmp)/'coverage'
            receipt=warm.prepare_step([],out,'coverage')
            self.assertEqual(receipt['status'],'prepared_not_executed')
            self.assertNotIn('time_based=1',(out/'job.fio').read_text())
            (out/'fio.json').write_text(json.dumps(self.result))
            self.assertTrue(warm.check_step(out)['passed'])
            self.assertEqual(set(p.name for p in out.iterdir()),{'job.fio','step.json','fio.json','checked.json'})
            with self.assertRaises(FileExistsError):warm.check_step(out)
            with self.assertRaises(FileExistsError):warm.prepare_step([],out,'measure')

    def test_reject_changed_job_and_working_set(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(warm,'file_inventory',return_value=self.inventory) as inv:
            out=Path(tmp)/'measure';warm.prepare_step([],out,'measure')
            (out/'fio.json').write_text(json.dumps(self.result))
            original=(out/'job.fio').read_bytes()
            (out/'job.fio').write_bytes(original.replace(b'4k',b'8k'))
            with self.assertRaises(ValueError):warm.check_step(out)
            (out/'job.fio').write_bytes(original)
            inv.return_value=[{'path':'/mnt/test/file0','bytes':16384}]
            with self.assertRaises(ValueError):warm.check_step(out)
            self.assertFalse((out/'checked.json').exists())

    def test_partial_coverage_does_not_create_passed_receipt(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(warm,'file_inventory',return_value=self.inventory):
            out=Path(tmp)/'coverage';warm.prepare_step([],out,'coverage')
            bad=copy.deepcopy(self.result);bad['jobs'][0]['read']['io_bytes']=4096
            (out/'fio.json').write_text(json.dumps(bad))
            with self.assertRaises(ValueError):warm.check_step(out)
            self.assertFalse((out/'checked.json').exists())

if __name__=='__main__':unittest.main()
