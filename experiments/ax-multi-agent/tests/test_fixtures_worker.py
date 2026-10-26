"""Meaningful local smoke checks, not cloud measurements or a paper result."""

import hashlib
import json
from pathlib import Path
import shutil
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from harness.fixtures import (BLOCK_BYTES, _check_not_link, create_fixture, expected_layout,
                              load_manifest, safe_path,
                              verify_fixture)
from harness.worker import (consume_records, consumer_keys, mutate_workspace, query_metadata,
                            read_workload, verify_state)
from harness import worker


class FixturesWorkerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="brewfs-fixture-tests-")
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)

    def fixture(self, kind="F1", name="data", version=1):
        root = self.directory / name
        manifest = create_fixture(kind, root, profile="smoke", version=version)
        return root, manifest

    def test_full_sizes_are_exact_without_materializing_full_data(self):
        self.assertEqual(sum(expected_layout("F1", "full").values()), 1024 ** 3)
        self.assertEqual(sum(expected_layout("F2", "full").values()), 1024 ** 3)
        self.assertEqual(len(expected_layout("F1", "full")), 32768 + 128)

    def test_f1_exact_formula_and_manifest_are_deterministic(self):
        root, first = self.fixture()
        _, second = self.fixture(name="replica")
        self.assertFalse(first["paper_eligible"])
        self.assertEqual(first["logical_data_bytes"], 5 * 1024 ** 2)
        self.assertEqual(first["data_manifest_sha256"], second["data_manifest_sha256"])
        self.assertEqual(load_manifest(first["manifest_path"])["data_manifest_sha256"], first["data_manifest_sha256"])
        self.assertFalse(Path(first["manifest_path"]).is_relative_to(root))
        relative = "small/d000/s017.bin"
        independently_expected = hashlib.shake_256(
            b"brewfs-ax-v2|20261023|1|small/d000/s017.bin").digest(4096)
        self.assertEqual((root / relative).read_bytes(), independently_expected)
        self.assertTrue(verify_fixture(root, "F1", "smoke")["ok"])

    def test_f2_v2_changes_only_the_specified_keys(self):
        v1, first = self.fixture("F2", "v1", 1)
        v2, second = self.fixture("F2", "v2", 2)
        self.assertEqual(first["logical_data_bytes"], 4 * 1024 ** 2)
        changed = []
        for shard in range(4):
            before = (v1 / f"shards/p{shard:02d}.bin").read_bytes()
            after = (v2 / f"shards/p{shard:02d}.bin").read_bytes()
            for index in range(256):
                offset = index * BLOCK_BYTES
                key = shard * 256 + index
                a, b = before[offset:offset + BLOCK_BYTES], after[offset:offset + BLOCK_BYTES]
                if a != b:
                    changed.append(key)
                independent = hashlib.shake_256(
                    f"brewfs-ax-v2|20261023|{2 if key % 100 == 0 else 1}|key={key}".encode()).digest(BLOCK_BYTES)
                self.assertEqual(b, independent)
        self.assertEqual(changed, list(range(0, 1024, 100)))
        self.assertEqual(second["index"]["changed_records"], 11)
        self.assertTrue(verify_fixture(v2, "F2", "smoke", 2)["ok"])
        self.assertFalse(verify_fixture(v2, "F2", "smoke", 1)["ok"])

    def test_w1_read_mutate_and_cross_workspace_pollution_detection(self):
        root, _ = self.fixture()
        read = read_workload(root, "smoke")
        self.assertTrue(read["ok"])
        self.assertEqual(read["logical_read_bytes"], 256 * 4096)
        result = mutate_workspace(root, 7, profile="smoke")
        self.assertEqual(result["logical_write_bytes"], 1048576)
        self.assertFalse(result["remote_durability_confirmed"])
        self.assertFalse(result["content_verified"])
        self.assertTrue(verify_state(root, 7, profile="smoke")["ok"])
        self.assertFalse(verify_state(root, 8, profile="smoke")["ok"])
        expected = hashlib.shake_256(
            b"brewfs-ax-v2|20261023|workspace=7|round=1|write=0").digest(4096)
        with (root / "large/l000.bin").open("rb") as source:
            self.assertEqual(source.read(4096), expected)

    def test_saved_parent_is_inherited_and_rename_delete_are_checked(self):
        parent, _ = self.fixture()
        mutate_workspace(parent, -1, profile="smoke")
        child = self.directory / "child"
        sibling = self.directory / "sibling"
        shutil.copytree(parent, child)
        shutil.copytree(parent, sibling)
        mutate_workspace(child, 3, modified_bytes=4096, shape="scattered", namespace=True, profile="smoke")
        self.assertTrue(verify_state(child, 3, modified_bytes=4096, shape="scattered",
                                     namespace=True, profile="smoke", parent=True)["ok"])
        self.assertFalse(verify_state(child, 3, modified_bytes=4096, shape="scattered",
                                      namespace=True, profile="smoke", parent=False)["ok"])
        self.assertTrue(verify_state(parent, profile="smoke", parent=True)["ok"])
        self.assertTrue(verify_state(sibling, profile="smoke", parent=True)["ok"])
        self.assertFalse((child / "small/d000/s000.bin").exists())
        self.assertFalse((child / "small/d000/s032.bin").exists())
        self.assertEqual((child / "small/d000/r032.bin").read_bytes(),
                         (parent / "small/d000/s032.bin").read_bytes())
        # Reintroduced deleted names must be detected by the exact tree oracle.
        (child / "small/d000/s000.bin").write_bytes(b"unwanted resurrection")
        checked = verify_state(child, 3, modified_bytes=4096, shape="scattered",
                               namespace=True, profile="smoke", parent=True)
        self.assertFalse(checked["ok"])
        self.assertTrue(any(e["reason"] == "unexpected_entry" for e in checked["errors"]))

    def test_repeated_overwrite_final_round_and_corruption_detection(self):
        root, _ = self.fixture()
        for round_id in (1, 2, 3):
            mutate_workspace(root, 0, round_id=round_id, modified_bytes=4096, profile="smoke")
        self.assertTrue(verify_state(root, 0, round_id=3, modified_bytes=4096, profile="smoke")["ok"])
        self.assertFalse(verify_state(root, 0, round_id=2, modified_bytes=4096, profile="smoke")["ok"])
        with (root / "large/l003.bin").open("r+b") as stream:
            stream.seek(100)
            original = stream.read(1)
            stream.seek(100)
            stream.write(bytes([original[0] ^ 0xFF]))
        self.assertFalse(verify_state(root, 0, round_id=3, modified_bytes=4096, profile="smoke")["ok"])

    def test_queries_cover_positive_negative_and_order(self):
        root, _ = self.fixture()
        for order in ("direct", "list-first"):
            result = query_metadata(root, "smoke", order)
            self.assertTrue(result["ok"])
            self.assertEqual(result["positive_queries"], 256)
            self.assertEqual(result["negative_queries"], 256)
            self.assertEqual(result["listed_directories"], int(order == "list-first"))
            self.assertTrue(result["safety_checks_in_query_time"])
        (root / "small/d000/m017.bin").write_bytes(b"unexpected positive")
        self.assertFalse(query_metadata(root, "smoke")["ok"])

    def test_f2_consumer_binding_outputs_and_disjoint_bounds(self):
        root, _ = self.fixture("F2", version=2)
        output = self.directory / "consumer.jsonl"
        result = consume_records(root, 63, "disjoint", 2, "smoke", output)
        self.assertTrue(result["ok"])
        self.assertEqual((result["first_key"], result["last_key"]), (1008, 1023))
        rows = [json.loads(line) for line in output.read_text(encoding="utf-8").splitlines()]
        self.assertEqual(len(rows), 16)
        self.assertEqual(rows[0]["dataset_version"], "V2")
        self.assertEqual(rows[0]["profile"], "smoke")
        self.assertFalse(rows[0]["paper_eligible"])
        self.assertIsNotNone(result["first_verified_result_elapsed_s"])
        wrong_version = consume_records(root, version=1, profile="smoke")
        self.assertFalse(wrong_version["ok"])
        self.assertIsNone(wrong_version["first_verified_result_elapsed_s"])
        self.assertEqual(consume_records(root, mode="sequential", version=2,
                                         profile="smoke")["records_read"], 1024)
        with self.assertRaises(ValueError):
            consume_records(root, 64, "disjoint", profile="smoke")
        with self.assertRaises(ValueError):
            consume_records(root, profile="smoke", output=root / "results.jsonl")

    def test_scattered_full_keys_are_unique_and_touch_every_shard(self):
        keys = consumer_keys(mode="scattered", profile="full")
        expected = [(40503 * j + 17) % 262144 for j in range(1024)]
        self.assertEqual(keys, expected)
        self.assertEqual(len(set(keys)), 1024)
        self.assertEqual({key // 4096 for key in keys}, set(range(64)))
        self.assertNotEqual(keys, sorted(keys))
        self.assertEqual(consumer_keys(63, "scattered", "full"), keys)

    def test_scattered_consumption_keeps_read_order_but_sorts_verified_output(self):
        root, _ = self.fixture("F2", version=2)
        output = self.directory / "scattered.jsonl"
        keys = [(40503 * j + 17) % 1024 for j in range(16)]
        actual_read_keys = []
        original_open = worker._open_regular
        def tracked_open(path, mode):
            stream = original_open(path, mode)
            if mode != "rb":
                return stream
            shard = int(path.stem[1:])
            tracked = mock.Mock(wraps=stream)
            def tracked_read(length):
                actual_read_keys.append(shard * 256 + stream.tell() // BLOCK_BYTES)
                return stream.read(length)
            tracked.read.side_effect = tracked_read
            return tracked
        with mock.patch.object(worker, "_open_regular", side_effect=tracked_open):
            result = consume_records(root, mode="scattered", version=2, profile="smoke", output=output)
        self.assertTrue(result["ok"])
        self.assertEqual(result["records_read"], 16)
        self.assertEqual(result["logical_read_bytes"], 16 * BLOCK_BYTES)
        self.assertEqual(result["shards_touched"], 4)
        self.assertEqual(actual_read_keys, keys)
        self.assertEqual(result["read_order"], {
            "formula": "key=(40503*j+17)%total_records", "j_start": 0, "count": 16,
            "total_records": 1024, "first_read_key": keys[0], "last_read_key": keys[-1]})
        rows = [json.loads(line) for line in output.read_text(encoding="utf-8").splitlines()]
        self.assertEqual([row["key"] for row in rows], sorted(keys))
        self.assertEqual(result["output_sha256"], hashlib.sha256(output.read_bytes()).hexdigest())
        # Corrupt a key actually selected by the scattered formula; wrong data must fail.
        key = keys[5]
        shard, within = divmod(key, 256)
        with (root / f"shards/p{shard:02d}.bin").open("r+b") as stream:
            stream.seek(within * BLOCK_BYTES)
            stream.write(b"x" * BLOCK_BYTES)
        failed = consume_records(root, mode="scattered", version=2, profile="smoke")
        self.assertFalse(failed["ok"])
        self.assertEqual([error["key"] for error in failed["errors"]], [key])
        self.assertIsNone(failed["first_verified_result_elapsed_s"])

    def test_refuses_existing_data_manifest_escape_and_oversized_mutations(self):
        root, _ = self.fixture()
        original = (root / "large/l000.bin").read_bytes()
        with self.assertRaises(ValueError):
            create_fixture("F1", root, "smoke")
        with self.assertRaises(ValueError):
            create_fixture("F1", self.directory / "invalid", "smoke",
                           manifest_path=self.directory / "invalid/manifest.json")
        with self.assertRaises(ValueError):
            mutate_workspace(root, 0, modified_bytes=128 * 1024 ** 2, profile="smoke")
        with self.assertRaises(ValueError):
            mutate_workspace(root, 0, modified_bytes=4097, profile="smoke")
        self.assertEqual((root / "large/l000.bin").read_bytes(), original)
        for relative in ("../outside.bin", "small/../../outside.bin", "/absolute.bin",
                         "C:/escape.bin", "small\\escape.bin"):
            with self.assertRaises(ValueError):
                safe_path(root, relative)

    def test_symlink_path_escape_is_rejected(self):
        root, _ = self.fixture()
        outside = self.directory / "outside"
        outside.mkdir()
        link = root / "escape"
        try:
            link.symlink_to(outside, target_is_directory=True)
        except (OSError, NotImplementedError) as error:
            self.skipTest(f"host cannot create symlinks: {error}")
        with self.assertRaises(ValueError):
            safe_path(root, "escape/payload.bin")
        with self.assertRaises(ValueError):
            verify_fixture(root, "F1", "smoke")
        self.assertFalse((outside / "payload.bin").exists())

    def test_windows_reparse_attribute_is_rejected_without_symlink_privilege(self):
        fake_junction_info = SimpleNamespace(st_mode=0o040755, st_file_attributes=0x400)
        with mock.patch.object(Path, "lstat", return_value=fake_junction_info):
            with self.assertRaisesRegex(ValueError, "reparse"):
                _check_not_link(self.directory / "junction")

    def test_modified_manifest_hash_cannot_fool_independent_oracle(self):
        root, manifest = self.fixture()
        target = root / "small/d000/s000.bin"
        corrupted = b"x" * 4096
        target.write_bytes(corrupted)
        for entry in manifest["entries"]:
            if entry["path"] == "small/d000/s000.bin":
                entry["sha256"] = hashlib.sha256(corrupted).hexdigest()
        # Even a self-consistent altered manifest cannot redefine expected bytes.
        manifest["data_manifest_sha256"] = hashlib.sha256(json.dumps(
            manifest["entries"], sort_keys=True, separators=(",", ":"),
            ensure_ascii=False).encode()).hexdigest()
        Path(manifest["manifest_path"]).write_text(json.dumps(manifest), encoding="utf-8")
        self.assertEqual(load_manifest(manifest["manifest_path"])["data_manifest_sha256"],
                         manifest["data_manifest_sha256"])
        self.assertFalse(verify_fixture(root, "F1", "smoke")["ok"])


if __name__ == "__main__":
    unittest.main()
