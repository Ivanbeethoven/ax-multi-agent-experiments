"""Redis 7/8 capability responses must retain the verifier's write restriction."""
import io
from pathlib import Path
import runpy
import unittest
from unittest.mock import patch

NODE=Path(__file__).resolve().parents[1]/'experiments/unified-suite/pilot-aliyun/scripts/packed-c3-node.py'
M=runpy.run_path(str(NODE));response=M['redis_response'];permissions=M['redis_reader_permissions']


class RedisReaderPermissionsTests(unittest.TestCase):
    def allowed(self,raw):
        reply=response(io.BytesIO(raw))
        with patch.dict(permissions.__globals__,redis_command=lambda url,argv:reply):return permissions('redis://owned:6379/70')

    def test_redis7_missing_setinfo_keeps_read_only_commands(self):
        grants,proof=self.allowed(b'*1\r\n$-1\r\n')
        self.assertNotIn('+client|setinfo',grants)
        self.assertFalse(proof['client_setinfo_available'])
        self.assertIn('+evalsha',grants)
        self.assertNotIn('+set',grants)
        self.assertNotIn('+client',grants)

    def test_redis8_advertised_setinfo_grants_only_that_subcommand(self):
        raw=b'*1\r\n*4\r\n$14\r\nclient|setinfo\r\n:-4\r\n*2\r\n+loading\r\n+stale\r\n*0\r\n'
        # The name is 14 bytes; nested flags and empty arrays match COMMAND INFO.
        grants,proof=self.allowed(raw)
        self.assertTrue(proof['client_setinfo_available'])
        self.assertIn('+client|setinfo',grants)
        self.assertNotIn('+client',grants)
        self.assertFalse(proof['write_permissions_added'])

    def test_unexpected_command_fails_closed(self):
        with patch.dict(permissions.__globals__,redis_command=lambda *args:[[b'client|kill']]):
            with self.assertRaisesRegex(ValueError,'another command'):permissions('redis://owned:6379/70')

    def test_noperm_remains_an_error(self):
        with self.assertRaisesRegex(RuntimeError,'^NOPERM'):response(io.BytesIO(b'-NOPERM writes denied\r\n'))

    def test_truncated_capability_fails_closed(self):
        with self.assertRaises(ValueError):response(io.BytesIO(b'*1\r\n$14\r\nclient|setinfo\r'))


if __name__=='__main__':unittest.main()
