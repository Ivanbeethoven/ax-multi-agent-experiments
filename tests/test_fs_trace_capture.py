import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from fs_trace_capture import Converter, insert, invalidate, overlap


def hx(value):
    return "".join(f"\\x{b:02x}" for b in value.encode())


class CaptureTests(unittest.TestCase):
    def setUp(self):
        self.c = Converter("/tmp/trace-unit-workspace")
        self.root = hx(self.c.root)
        self.path = hx(self.c.root + "/a")

    def call(self, s):
        self.c.consume("100 1234.000001 " + s + " <0.000001>", 1)

    def opened(self):
        self.call(f'openat(AT_FDCWD<{self.root}>, "{self.path}", O_RDWR) = 3<{self.path}>')

    def test_short_read_eof_and_overlap(self):
        self.opened()
        self.call(f'read(3<{self.path}>, "\\x61\\x62", 8) = 2')
        self.call(f'pread64(3<{self.path}>, "\\x62", 1, 1) = 1')
        self.call(f'pread64(3<{self.path}>, "", 8, 9) = 0')
        self.assertEqual(self.c.read_bytes, 3)
        self.assertEqual(self.c.repeat_bytes, 1)
        self.assertEqual(self.c.requested_read, 17)

    def test_short_write_counts_returned_bytes(self):
        self.opened()
        self.call(f'write(3<{self.path}>, "\\x61\\x62\\x63\\x64", 4) = 2')
        self.assertEqual(self.c.write_bytes, 2)
        self.assertEqual(self.c.requested_write, 4)
        self.assertEqual(self.c.records[-1]["data_hex"], "61626364")
        self.assertEqual(self.c.records[-1]["expected_bytes"], 2)

    def test_recreated_path_has_new_identity(self):
        self.opened()
        self.call(f'read(3<{self.path}>, "\\x61", 1) = 1')
        self.call(f'close(3<{self.path}>) = 0')
        self.call(f'unlink("{self.path}") = 0')
        self.opened()
        self.call(f'read(3<{self.path}>, "\\x61", 1) = 1')
        self.assertEqual(self.c.repeat_bytes, 0)

    def test_read_hash_matches_captured_content(self):
        self.opened()
        self.call(f'read(3<{self.path}>, "\\x61", 1) = 1')
        self.assertEqual(self.c.records[-1]["expected_sha256"],
                         "ca978112ca1bbdcafac231b39a23dc4da786eff8147c4e72b9807785afee48bb")

    def test_overwrite_invalidates_only_changed_range(self):
        r = insert([], 0, 10)
        r = invalidate(r, 3, 6)
        self.assertEqual(overlap(r, 2, 8), 3)
        self.assertEqual(insert(r, 3, 6), [(0, 10)])

    def test_truncated_buffer_rejected(self):
        self.opened()
        with self.assertRaises(ValueError): self.call(f'read(3<{self.path}>, "\\x61"..., 5) = 5')

    def test_unrecorded_fd_rejected(self):
        with self.assertRaises(ValueError): self.call(f'read(3<{self.path}>, "\\x61", 1) = 1')

    def test_mmap_workspace_rejected(self):
        self.opened()
        with self.assertRaisesRegex(ValueError, "Unsupported workspace syscall"):
            self.call(f'mmap(NULL, 4096, PROT_READ, MAP_PRIVATE, 3<{self.path}>, 0) = 12345')

    def test_unimplemented_path_syscall_rejected(self):
        with self.assertRaisesRegex(ValueError, "Unsupported workspace syscall"):
            self.call(f'creat("{self.path}", 0600) = 3<{self.path}>')

    def test_fork_rejected(self):
        with self.assertRaises(ValueError): self.call('clone(child_stack=NULL, flags=SIGCHLD) = 101')

    def test_unknown_line_rejected(self):
        with self.assertRaises(ValueError): self.c.consume('100 read(3, <unfinished ...>', 1)

    def test_failed_open_preserved(self):
        self.call(f'openat(AT_FDCWD<{self.root}>, "{self.path}", O_RDONLY) = -1 ENOENT (No such file or directory)')
        self.assertEqual(self.c.records[0]["expected_errno"], 2)
        self.assertEqual(self.c.fds, {})

    def test_empty_capture_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d); (p / "empty").write_text("")
            with self.assertRaisesRegex(ValueError, "No workspace"):
                self.c.convert(p / "empty", "0"*64, p)

    def test_open_fd_at_end_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d); (p / "empty").write_text("")
            self.opened()
            with self.assertRaisesRegex(ValueError, "Unclosed"):
                self.c.convert(p / "empty", "0"*64, p)

    def test_absolute_external_path_excluded(self):
        other = hx("/usr/lib/library.so")
        self.call(f'openat(AT_FDCWD<{self.root}>, "{other}", O_RDONLY) = 3<{other}>')
        self.assertEqual(self.c.records, [])
        self.assertEqual(self.c.excluded["openat"], 1)

    def test_random_buffer_is_not_a_path(self):
        self.call('getrandom("\\x93\\xff", 2, GRND_NONBLOCK) = 2')
        self.assertEqual(self.c.records, [])
        self.assertEqual(self.c.excluded["getrandom"], 1)


if __name__ == "__main__": unittest.main()
