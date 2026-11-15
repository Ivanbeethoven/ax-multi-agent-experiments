import ast
import configparser
import json
from pathlib import Path
import unittest

ROOT=Path(__file__).resolve().parents[2]


class FioWorkTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        source=ast.parse((ROOT/'bench/cluster/runtime/run_c1.py').read_text(encoding='utf-8'))
        function=next(node for node in source.body if isinstance(node,ast.FunctionDef) and node.name=='build_fio_jobfile')
        namespace={'JOBS':8,'BYTES_PER_JOB':256*1024**2}
        exec(compile(ast.Module(body=[function],type_ignores=[]),'fio-builder','exec'),namespace)
        cls.build=staticmethod(namespace['build_fio_jobfile'])

    def test_random_sweep_total_is_64_mib(self):
        for family in ('block-random','reference-random'):
            for operation in ('read','write'):
                for size in (4,64,256,1024):
                    condition={'family':family,'parameters':{'operation':operation,'request_kib':size}}
                    text,reads,_,_=self.build(condition,'/test',100)
                    cfg=configparser.ConfigParser();cfg.read_string(text)
                    jobs=[name for name in cfg.sections() if name.startswith('job')]
                    self.assertEqual(len(jobs),8)
                    self.assertEqual(int(cfg['global']['io_size'])*len(jobs),64*1024**2)
                    self.assertEqual(reads,operation=='read')
                    self.assertEqual(len({cfg[job]['filename'] for job in jobs}),8)
                    self.assertEqual(cfg['global']['do_verify'],'0')

    def test_sequential_total_is_2_gib(self):
        for family in ('block-sequential','reference-sequential'):
            for operation in ('read','write'):
                text,_,_,written=self.build({'family':family,'parameters':{'operation':operation}},'/test',100)
                cfg=configparser.ConfigParser();cfg.read_string(text)
                self.assertEqual(int(cfg['global']['io_size'])*8,2*1024**3)
                self.assertEqual(int(cfg['global']['size'])*8,2*1024**3)
                self.assertEqual(written,256*1024**2 if operation=='write' else 0)

    def test_fsync_interval_follows_configuration(self):
        for family, request, expected in [('block-sequential',1024,'16'),('block-random',4,'4096')]:
            text,_,_,_=self.build({'family':family,'parameters':{'operation':'write',
                'request_kib':request,'fsync_bytes_all_jobs':128*1024**2}},'/test',100)
            cfg=configparser.ConfigParser();cfg.read_string(text)
            self.assertEqual(cfg['global']['fsync'],expected)
            self.assertEqual(cfg['global']['end_fsync'],'1')


if __name__=='__main__':unittest.main()
