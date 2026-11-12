"""Check the lower hot gate, including failure modes that fake a speedup."""
import errno
import hashlib
import importlib.util
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from bench.warm_lower import KEYS, assess, check_ranges, parse, read_range


def counters(gets, byte_count, logical, failures=0, full=0, heads=0):
    return dict(range_requests=gets,received_bytes=byte_count,logical_bytes=logical,
                failures=failures,full_requests=full,head_requests=heads)


def fixture_meter():
    fixture = Path(__file__).resolve().parents[1]/('experiments/unified-suite/pilot-aliyun/'
              'evidence/preparation/c2-local-packed-warm-01/loopback_probe.py')
    loader = importlib.util.spec_from_file_location('fixture_meter',fixture)
    module = importlib.util.module_from_spec(loader)
    loader.loader.exec_module(module)
    return module


class WarmLowerTest(unittest.TestCase):
    def test_reads_enter_filesystem_and_do_not_need_backend_on_second_pass(self):
        result = assess([counters(2,32,0),counters(9,96,16),counters(9,96,32)],16)
        self.assertTrue(result['passed'])
        self.assertEqual(result['cold']['received_bytes'],64)
        self.assertEqual(result['second_pass']['logical_bytes'],16)

    def test_backend_read_and_kernel_only_cache_are_not_hot_lower(self):
        for last, reason in ((counters(10,100,32),'still reads'), (counters(9,96,16),'logical bytes')):
            with self.subTest(last=last):
                result = assess([counters(2,32,0),counters(9,96,16),last],16)
                self.assertFalse(result['passed'])
                self.assertTrue(any(reason in s for s in result['reasons']))

    def test_whole_object_download_can_warm_the_lower_without_range_gets(self):
        result = assess([counters(0,0,0),counters(0,128,16,full=1,heads=1),
                         counters(0,128,32,full=1,heads=1)],16)
        self.assertTrue(result['passed'])
        self.assertEqual(result['cold']['full_requests'],1)
        self.assertEqual(result['second_pass']['head_requests'],0)

    def test_full_get_or_head_during_second_pass_rejects_hot_lower(self):
        for full,heads in [(1,0),(0,1)]:
            with self.subTest(full=full,heads=heads):
                result = assess([counters(0,0,0),counters(1,128,16),
                                 counters(1,128,32,full=full,heads=heads)],16)
                self.assertFalse(result['passed'])
                self.assertTrue(any('still reads' in s for s in result['reasons']))

    def test_head_alone_does_not_prove_cold_payload_reads(self):
        result = assess([counters(0,0,0),counters(0,0,16,heads=1),
                         counters(0,0,32,heads=1)],16)
        self.assertFalse(result['passed'])
        self.assertTrue(any('cold pass' in s for s in result['reasons']))

    def test_parse_requires_all_native_counters_without_duplicates(self):
        raw = ''.join(f'{name} {i}\n' for i,name in enumerate(KEYS.values())).encode()
        self.assertEqual(parse(raw)['logical_bytes'],3)
        with self.assertRaisesRegex(ValueError,'Missing'):
            parse(b'# no packed mount\n')
        for key in ('full_requests', 'head_requests'):
            missing = b''.join(line for line in raw.splitlines(keepends=True)
                               if not line.startswith(KEYS[key].encode()+b' '))
            with self.subTest(missing=key), self.assertRaisesRegex(ValueError,'Missing'):
                parse(missing)
        with self.assertRaisesRegex(ValueError,'Duplicate'):
            parse(raw+raw)

    @unittest.skipUnless(hasattr(os,'O_NOFOLLOW'),'Linux fd/path semantics required')
    def test_checks_exact_bytes_and_never_follows_symlinks(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root/'sub').mkdir()
            (root/'sub/data').write_bytes(b'0123456')
            row = dict(path='sub/data',offset=2,length=3,sha256=hashlib.sha256(b'234').hexdigest())
            check_ranges([row])
            fd = os.open(root,os.O_RDONLY | os.O_DIRECTORY)
            try:
                read_range(fd,row)
                with self.assertRaisesRegex(ValueError,'SHA'):
                    read_range(fd,dict(row,offset=1))
                (root/'link').symlink_to(root/'sub',target_is_directory=True)
                with self.assertRaises(OSError):
                    read_range(fd,dict(row,path='link/data'))
            finally:
                os.close(fd)

    @unittest.skipUnless(hasattr(os,'O_NOFOLLOW'),'Linux fd/path semantics required')
    def test_candidate_fixture_requires_cache_hits_and_independent_backend_quiet(self):
        module = fixture_meter()
        def render(fuse_reads,byte_count,native,hits):
            parts = {**{KEYS[k]:v for k,v in native.items()},'brewfs_fuse_read_ops_total':fuse_reads,
                     'brewfs_fuse_read_bytes_total':byte_count,'brewfs_workspace_row_cache_hits_total':1,
                     'brewfs_packed_v3_payload_retention_hits_total':hits,
                     'brewfs_packed_v3_payload_retention_capacity_bytes':1048576}
            return ''.join(f'{k} {v}\n' for k,v in parts.items())
        with tempfile.TemporaryDirectory() as temp:
            base=Path(temp)
            (base/'data').write_bytes(b'77')
            rows=[dict(path='data',offset=0,length=2,sha256=hashlib.sha256(b'77').hexdigest())]
            for suffix,hits,extra,want in [('ok',1,0,True),('no-cache-hit',0,0,False),('s3-sees-read',1,1,False)]:
                with self.subTest(suffix=suffix):
                    raw=[render(0,0,counters(0,0,0),0),render(1,2,counters(1,4,2),0),
                         render(2,4,counters(1,4,4),hits)]
                    rustfs=iter([dict(gets=n,ranges=n,heads=0,bytes=b,errors=0) for n,b in [(0,0),(1,4),(1+extra,4+extra)]])
                    with patch.object(Path,'read_text',side_effect=raw):
                        result=module.probe(base,rows,lambda:next(rustfs),base/suffix,retained_bytes=1048576)
                    self.assertEqual(result['hot'],want)
        zero=dict(queries=0,bypasses=0)
        query=dict(queries=2,bypasses=0)
        bypass=dict(queries=0,bypasses=2)
        self.assertTrue(module.extent_pass(zero,query,'overlay'))
        self.assertTrue(module.extent_pass(zero,bypass,'lower-only'))
        self.assertFalse(module.extent_pass(zero,query,'lower-only'))
        self.assertFalse(module.extent_pass(zero,zero,'overlay'))

    @unittest.skipUnless(hasattr(os,'O_NOFOLLOW'),'Linux fd/path semantics required')
    def test_timing_only_after_hot_and_rejects_reads_that_go_cold_during_timing(self):
        module = fixture_meter()
        real_open = os.open

        def read_only(path,flags,*args,**kwargs):
            if flags & os.O_WRONLY:
                raise OSError(errno.EROFS,'test read-only mount')
            return real_open(path,flags,*args,**kwargs)

        def stats(i,remote_reads):
            native = counters(remote_reads,remote_reads*4,i*2)
            metrics = {**{KEYS[k]:v for k,v in native.items()},'brewfs_fuse_read_ops_total':i,
                'brewfs_fuse_read_bytes_total':i*2,'brewfs_workspace_row_cache_hits_total':i,
                'brewfs_packed_v3_payload_retention_hits_total':max(0,i-1),
                'brewfs_packed_v3_payload_retention_capacity_bytes':1048576,
                'brewfs_workspace_read_extent_control_info{mode="overlay"}':1,
                'brewfs_workspace_read_extent_control_queries_total':i,
                'brewfs_workspace_read_extent_control_bypasses_total':0}
            return ''.join(f'{key} {value}\n' for key,value in metrics.items())

        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            (base/'data').write_bytes(b'77')
            rows = [dict(path='data',offset=0,length=2,sha256=hashlib.sha256(b'77').hexdigest())]
            for case,status,gets,heads in [('hot','hot',[0,1,1,1],[0,0,0,0]),
                    ('get','rejected',[0,1,1,2],[0,0,0,0]),('head','rejected',[0,1,1,1],[0,0,0,1]),
                    ('cold','warmup_failed',[0,1,2],[0,0,0])]:
                with self.subTest(case=case):
                    raw = [stats(i,n) for i,n in zip((0,1,2,4),gets)]
                    sidecar = iter(dict(gets=n,ranges=n,heads=h,bytes=n*4,errors=0) for n,h in zip(gets,heads))
                    with patch('bench.runtime.common.mounts',return_value=[dict(options='ro',filesystem='fuse.brewfs')]), \
                         patch.object(os,'open',side_effect=read_only), patch.object(Path,'read_text',side_effect=raw):
                        result = module.probe(base,rows,lambda:next(sidecar),base/case,
                            retained_bytes=1048576,extent_arm='overlay',timed_passes=2)
                    timed = result['timed_extent_reads']
                    self.assertEqual(timed['status'],status)
                    self.assertEqual(timed['passed'],status=='hot')
                    self.assertEqual((base/case/'timed-end.stats').exists(),status!='warmup_failed')
                    if status!='warmup_failed':
                        self.assertEqual(len(timed['pread_ns']),2)
                        self.assertEqual(timed['final_extent'],dict(queries=4,bypasses=0))


if __name__=='__main__':
    unittest.main()
