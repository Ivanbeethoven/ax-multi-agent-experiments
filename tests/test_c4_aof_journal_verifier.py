import base64
import importlib.util
import struct
import unittest
from pathlib import Path


SCRIPT = (Path(__file__).resolve().parents[1]
          / "experiments/unified-suite/pilot-aliyun/scripts/verify-c4-aof-journal.py")
SPEC = importlib.util.spec_from_file_location("verify_c4_aof_journal", SCRIPT)
VERIFIER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(VERIFIER)


def pin(slot, data):
    return {"journal_block": slot, "raw_base64": base64.b64encode(data).decode(), "sha256": VERIFIER.sha(data)}


def transaction():
    sequence, seed = 3737797, 0xDEADBEEF
    desc = bytearray(VERIFIER.BLOCK_SIZE)
    data = bytes(VERIFIER.BLOCK_SIZE)
    commit = bytearray(VERIFIER.BLOCK_SIZE)
    struct.pack_into(">3I", desc, 0, VERIFIER.MAGIC, 1, sequence)
    check = VERIFIER.crc32c(VERIFIER.crc32c(seed, struct.pack(">I", sequence)), data)
    struct.pack_into(">4I", desc, 12, VERIFIER.INODE_BLOCK, 8, 0, check)
    struct.pack_into(">I", desc, len(desc) - 4, VERIFIER.crc32c(seed, desc))
    struct.pack_into(">3I", commit, 0, VERIFIER.MAGIC, 2, sequence)
    struct.pack_into(">QI", commit, 48, VERIFIER.START, 500)
    struct.pack_into(">I", commit, 16, VERIFIER.crc32c(seed, commit))
    return sequence, seed, [pin(9, desc), pin(1, data), pin(2, commit)]


def inode(generation, links, sectors, size):
    data = bytearray(VERIFIER.BLOCK_SIZE)
    off = (524296 - VERIFIER.FIRST_INODE) * VERIFIER.INODE_SIZE
    struct.pack_into("<H", data, off, 0x81A4)
    struct.pack_into("<I", data, off + 4, size)
    struct.pack_into("<HI", data, off + 26, links, sectors)
    struct.pack_into("<I", data, off + 100, generation)
    return data


class JournalVerifierTests(unittest.TestCase):
    def test_check_crc32c_and_ring_wrap(self):
        self.assertEqual(VERIFIER.crc32c(0xFFFFFFFF, b"123456789"), 0x1CF96D7C)
        sequence, seed, members = transaction()
        tx = VERIFIER.check_transaction(sequence, members, 1, 10, seed)
        self.assertEqual((tx["start"], tx["commit"]), (9, 2))
        self.assertEqual(tx["targets"][VERIFIER.INODE_BLOCK][1]["journal_slot"], 1)

    def test_payload_rehashed_after_tamper_still_fails_tag_crc(self):
        sequence, seed, members = transaction()
        members[1] = pin(1, b"\x01" + bytes(VERIFIER.BLOCK_SIZE - 1))
        with self.assertRaisesRegex(ValueError, "payload tag CRC32C mismatch"):
            VERIFIER.check_transaction(sequence, members, 1, 10, seed)

    def test_rehashed_commit_tamper_still_fails_commit_crc(self):
        sequence, seed, members = transaction()
        data = bytearray(base64.b64decode(members[2]["raw_base64"]))
        data[59] ^= 1
        members[2] = pin(2, data)
        with self.assertRaisesRegex(ValueError, "commit CRC32C/header mismatch"):
            VERIFIER.check_transaction(sequence, members, 1, 10, seed)

    def test_reassigned_payload_slot_is_rejected(self):
        sequence, seed, members = transaction()
        members[1]["journal_block"] = 8
        with self.assertRaisesRegex(ValueError, "missing or duplicate target"):
            VERIFIER.check_transaction(sequence, members, 1, 10, seed)

    def test_unlink_and_zero_allocation_are_separate_and_generation_bound(self):
        images = {10: inode(5, 1, 8, 184), 11: inode(5, 0, 8, 184), 12: inode(5, 0, 0, 0)}
        txs = {seq: {"targets": {key: (None, {"slot": seq}) for key in VERIFIER.TARGETS}} for seq in images}
        row = VERIFIER.prove_retirement(VERIFIER.MANIFEST, 524296, 10, 11, images, txs)
        self.assertEqual((row["generation"], row["allocated_bytes"], row["zero_sequence"]), (5, 4096, 12))
        images[12] = inode(6, 0, 0, 0)
        with self.assertRaisesRegex(ValueError, "original generation not unlinked before reuse"):
            VERIFIER.prove_retirement(VERIFIER.MANIFEST, 524296, 10, 11, images, txs)


if __name__ == "__main__":
    unittest.main()
