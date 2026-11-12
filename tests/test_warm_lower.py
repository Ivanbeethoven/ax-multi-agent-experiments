"""Check the lower hot gate, including failure modes that fake a speedup."""
import hashlib
import os
from pathlib import Path
import tempfile
import unittest

from bench.warm_lower import KEYS, assess, check_ranges, parse, read_range


def counters(gets, byte_count, logical, failures=0):
    return dict(range_requests=gets,received_bytes=byte_count,logical_bytes=logical,failures=failures)


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

    def test_parse_requires_all_native_counters_without_duplicates(self):
        raw = ''.join(f'{name} {i}\n' for i,name in enumerate(KEYS.values())).encode()
        self.assertEqual(parse(raw)['logical_bytes'],3)
        with self.assertRaisesRegex(ValueError,'Missing'):
            parse(b'# no packed mount\n')
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


if __name__=='__main__':
    unittest.main()
