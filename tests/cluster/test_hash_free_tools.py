import ast
import importlib.util
from io import BytesIO
from pathlib import Path
import tempfile
import unittest


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod

REPO = Path(__file__).resolve().parents[2]


class HashFreeToolsTests(unittest.TestCase):
    def test_no_hashlib_imports_in_patched_python_tools(self):
        for rel in (
            "infra/rustfs/s3_probe.py",
            "infra/rustfs/rustfs_node.py",
            "datasets/trace-expansion/download_from_drive.py",
        ):
            tree = ast.parse((REPO / rel).read_text(encoding="utf-8"), filename=rel)
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        self.assertNotIn(alias.name, ("hashlib", "zlib", "binascii"))
                elif isinstance(node, ast.ImportFrom):
                    self.assertNotIn(node.module, ("hashlib", "zlib", "binascii"))

    def test_no_hash_crates_in_rust_tools(self):
        for rel in (
            "tools/full-fuse-replay/Cargo.toml",
            "tools/ml-corpus-scan/Cargo.toml",
            "tools/fs-probe/Cargo.toml",
        ):
            txt = (REPO / rel).read_text(encoding="utf-8").lower()
            for banned in ("sha2", "sha1", "md5", "blake", "crc32", "xxhash"):
                self.assertNotIn(banned, txt, f"{banned} found in {rel}")
        for p in (REPO / "tools").rglob("*.rs"):
            txt = p.read_text(encoding="utf-8")
            self.assertNotIn("Sha256", txt, f"Sha256 found in {p}")
            self.assertNotIn("sha2::", txt, f"sha2:: found in {p}")

    def test_s3_probe_checks_direct_byte_equality(self):
        probe_mod = load_module("s3_probe_test", REPO / "infra/rustfs/s3_probe.py")

        class FakeClient:
            def __init__(self, corrupt_range=False):
                self.store = {}
                self.corrupt_range = corrupt_range

            def create_bucket(self, Bucket):
                self.store[Bucket] = {}

            def put_object(self, Bucket, Key, Body):
                self.store[Bucket][Key] = bytes(Body)

            def head_object(self, Bucket, Key):
                return {"ContentLength": len(self.store[Bucket][Key])}

            def get_object(self, Bucket, Key, Range=None):
                data = self.store[Bucket][Key]
                if Range:
                    s, e = [int(x) for x in Range.split("=")[1].split("-")]
                    chunk = bytearray(data[s : e + 1])
                    if self.corrupt_range:
                        chunk[0] ^= 0xFF
                    return {"Body": BytesIO(bytes(chunk))}
                return {"Body": BytesIO(data)}

            def list_objects_v2(self, Bucket):
                return {"IsTruncated": False, "Contents": [{"Key": k} for k in self.store[Bucket]]}

            def delete_object(self, Bucket, Key):
                del self.store[Bucket][Key]

            def delete_bucket(self, Bucket):
                del self.store[Bucket]

        ok = probe_mod.run_probe(FakeClient())
        self.assertEqual(ok["probe"], "passed")
        self.assertTrue(ok["direct_byte_equal"])
        self.assertNotIn("sha256", ok)

        with self.assertRaises(RuntimeError):
            probe_mod.run_probe(FakeClient(corrupt_range=True))

    def test_download_verify_checks_size_and_gzip_magic(self):
        dl_mod = load_module("dl_test", REPO / "datasets/trace-expansion/download_from_drive.py")
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "sample.tar.gz"
            p.write_bytes(b"\x1f\x8b\x08\x00hello")
            res = dl_mod.verify(p, {"case": "sample", "bytes": 9})
            self.assertTrue(res["verified"])
            self.assertNotIn("sha256", res)
            with self.assertRaises(ValueError):
                dl_mod.verify(p, {"case": "sample", "bytes": 10})
            p.write_bytes(b"PK\x03\x04hello")
            with self.assertRaises(ValueError):
                dl_mod.verify(p, {"case": "sample", "bytes": 9})


if __name__ == "__main__":
    unittest.main()
