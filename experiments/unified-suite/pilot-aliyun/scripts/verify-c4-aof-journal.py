#!/usr/bin/env python3
"""Verify the selected JBD2 evidence for the frozen C4 AOF rewrite, offline."""

import argparse
import base64
import binascii
import hashlib
import json
import struct
from pathlib import Path


BLOCK_SIZE = 4096
MAGIC = 0xC03B3998
DIR_BLOCK = 557089
INODE_BLOCK = 524320
TARGETS = {DIR_BLOCK, INODE_BLOCK}
FIRST_INODE = 524289
INODE_SIZE = 256
MANIFEST = "appendonly.aof.manifest"
TEMP = "temp-appendonly.aof.manifest"
AOF_OLD = {"appendonly.aof.16.incr.aof", "appendonly.aof.16.base.rdb"}
AOF_NEW = {"appendonly.aof.17.incr.aof", "appendonly.aof.17.base.rdb"}
DIR_SEQUENCES = (3736295, 3736296, 3737794, 3737795, 3737796, 3737797)
INODE_SEQUENCES = {
    3731795, *range(3736293, 3736299), *range(3737792, 3737800), 3737819, 3737841,
}
INPUT_SHA = "b494ccf96ae833e908087ec4dd67b47ffc54222f40f9f2780778fbf05c6030d4"
PRODUCER_SHA = "60f4e7675ec1bc0b51e2b08ad44a2cee25deea56dad395742faf982e4ef82d6a"
BOOT_ID = "9a6ebc7c-3b6a-4819-8720-d8e61ff5941e"
DISK_SERIAL = "bp1gy3cfuj9jik0upt3k"
START, END = 1794391420, 1794391450  # 2026-11-11 18:03:40–18:04:10 CST


def require(condition, message):
    if not condition:
        raise ValueError(message)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def table_entry(value):
    for _ in range(8):
        value = (value >> 1) ^ (0x82F63B78 if value & 1 else 0)
    return value


CRC_TABLE = tuple(map(table_entry, range(256)))


def crc32c(seed, data):
    for byte in data:
        seed = CRC_TABLE[(seed ^ byte) & 0xFF] ^ (seed >> 8)
    return seed


def crc_at(raw, seed, offset):
    return crc32c(seed, raw[:offset] + b"\0" * 4 + raw[offset + 4:])


def read_block(encoded):
    raw = base64.b64decode(encoded, validate=True)
    require(len(raw) == BLOCK_SIZE, "wrong raw block length")
    return raw


def journal_superblock(raw):
    magic, kind, zero, blocksize, length, first = struct.unpack_from(">6I", raw)
    require((magic, kind, zero, blocksize, length, first) == (MAGIC, 4, 0, 4096, 65536, 1),
            "unexpected JBD2 superblock geometry")
    require(struct.unpack_from(">3I", raw, 36) == (0, 0x13, 0) and raw[80] == 4,
            "expected JBD2 CRC32C v3, revoke and 64-bit features only")
    require(crc_at(raw[:1024], 0xFFFFFFFF, 252) == struct.unpack_from(">I", raw, 252)[0],
            "JBD2 superblock CRC32C mismatch")
    return first, length, crc32c(0xFFFFFFFF, raw[48:64])


def descriptor_tags(raw):
    """Read v3 tags: destination, flags, checksum. Explicit UUID is skipped by JBD2."""
    cursor = 12
    while cursor + 16 <= BLOCK_SIZE - 4:
        low, flags, high, checksum = struct.unpack_from(">4I", raw, cursor)
        cursor += 16
        require(not flags & ~0xF and not flags & 4, "unsupported descriptor flag")
        if not flags & 2:
            cursor += 16
            require(cursor <= BLOCK_SIZE - 4, "short explicit descriptor UUID")
        yield low | high << 32, flags, checksum
        if flags & 8:
            return
    raise ValueError("descriptor has no LAST_TAG")


def check_transaction(sequence, members, first, length, seed):
    require(isinstance(members, list) and 2 <= len(members) <= 64, "bad selected chain length")
    slots = {}
    for member in members:
        index = member["journal_block"]
        require(type(index) is int and first <= index < length and index not in slots,
                "invalid or repeated selected ring slot")
        raw = read_block(member["raw_base64"])
        require(sha(raw) == member["sha256"], f"raw block SHA-256 mismatch at slot {index}")
        slots[index] = raw

    def advance(slot, count=1):
        return first + (slot - first + count) % (length - first)

    seed_for_data = crc32c(seed, struct.pack(">I", sequence))
    cursor = initial = next(iter(slots))
    expected = []
    targets = {}
    skipped = 0
    for _ in range(len(slots)):
        require(cursor in slots, f"missing control block at ring slot {cursor}")
        expected.append(cursor)
        raw = slots[cursor]
        magic, kind, seq = struct.unpack_from(">3I", raw)
        require(magic == MAGIC and seq == sequence, f"wrong control header at ring slot {cursor}")
        if kind == 2:
            require(raw[12:16] == b"\0" * 4
                    and crc_at(raw, seed, 16) == struct.unpack_from(">I", raw, 16)[0],
                    f"JBD2 commit CRC32C/header mismatch at slot {cursor}")
            sec, ns = struct.unpack_from(">QI", raw, 48)
            require(START <= sec <= END and ns < 10**9, "commit outside frozen capture window")
            require(expected == list(slots), "unexpected or reordered raw members")
            return {
                "start": initial, "commit": cursor, "commit_time": (sec, ns),
                "commit_sha256": sha(raw), "targets": targets, "omitted_payloads": skipped,
            }

        require(kind in (1, 5) and
                crc_at(raw, seed, BLOCK_SIZE - 4) == struct.unpack_from(">I", raw, BLOCK_SIZE - 4)[0],
                f"JBD2 descriptor/revoke CRC32C/header mismatch at slot {cursor}")
        if kind == 5:
            used = struct.unpack_from(">I", raw, 12)[0]
            require(16 <= used <= BLOCK_SIZE - 4 and (used - 16) % 8 == 0, "malformed revoke")
            require(not TARGETS.intersection(row[0] for row in struct.iter_unpack(">Q", raw[16:used])),
                    "scoped block revoked")
            cursor = advance(cursor)
            continue

        descriptor_slot = cursor
        cursor = advance(cursor)
        for dest, flags, checksum in descriptor_tags(raw):
            if dest in TARGETS:
                require(dest not in targets and cursor in slots, f"missing or duplicate target {dest}")
                data = slots[cursor]
                expected.append(cursor)
                require(crc32c(seed_for_data, data) == checksum,
                        f"JBD2 payload tag CRC32C mismatch at slot {cursor}")
                if flags & 1:
                    require(data[:4] == b"\0" * 4, "invalid escaped payload")
                    data = struct.pack(">I", MAGIC) + data[4:]
                targets[dest] = (data, {
                    "descriptor_slot": descriptor_slot, "journal_slot": cursor,
                    "raw_sha256": sha(slots[cursor]), "tag_crc32c": f"{checksum:08x}",
                })
            else:
                require(cursor not in slots, f"unexpected captured non-target payload at slot {cursor}")
                skipped += 1
            cursor = advance(cursor)
    raise ValueError("selected chain has no commit")


def directory(raw):
    entries = {}
    cursor = 0
    tail = False
    while cursor < BLOCK_SIZE:
        require(cursor + 8 <= BLOCK_SIZE, "short ext4 directory header")
        inode, length, count, kind = struct.unpack_from("<IHBB", raw, cursor)
        require(length >= 8 and length % 4 == 0 and cursor + length <= BLOCK_SIZE and count <= length - 8,
                "malformed ext4 directory record")
        if kind == 0xDE:
            require((inode, length, count, cursor + length) == (0, 12, 0, BLOCK_SIZE),
                    "malformed ext4 directory checksum tail")
            tail = True
        elif inode:
            name = raw[cursor + 8:cursor + 8 + count].decode("ascii")
            require(name and "\0" not in name and "/" not in name and name not in entries,
                    "invalid or duplicate ext4 directory name")
            require(kind == (2 if name in (".", "..") else 1), "unexpected ext4 directory file type")
            entries[name] = inode
        cursor += length
    require(tail and entries.get(".") == 524290 and entries.get("..") == 524289,
            "not the pinned AOF directory geometry")
    return entries


def inode_row(raw, inode):
    index = inode - FIRST_INODE
    require(0 <= index < BLOCK_SIZE // INODE_SIZE, "inode outside pinned table block")
    off = index * INODE_SIZE
    mode = struct.unpack_from("<H", raw, off)[0]
    size = struct.unpack_from("<I", raw, off + 4)[0] | struct.unpack_from("<I", raw, off + 108)[0] << 32
    low, flags = struct.unpack_from("<2I", raw, off + 28)
    high = struct.unpack_from("<H", raw, off + 116)[0]
    # The actual rows use only i_blocks_lo with the standard 512-byte units.
    # Fail closed on units requiring an ext4 superblock not in this export.
    require(not high and not flags & 0x40000, "unsupported ext4 i_blocks high word or HUGE_FILE flag")
    return {
        "mode": mode, "logical_bytes": size, "allocated_bytes": low * 512,
        "links": struct.unpack_from("<H", raw, off + 26)[0],
        "generation": struct.unpack_from("<I", raw, off + 100)[0],
    }


def prove_retirement(name, inode, previous, unlink, inode_images, transactions):
    old = inode_row(inode_images[previous], inode)
    require(old["mode"] & 0xF000 == 0x8000 and old["links"] == 1
            and old["allocated_bytes"] > 0 and old["allocated_bytes"] % BLOCK_SIZE == 0,
            f"{name}: unproven live pre-unlink allocation")
    generation = old["generation"]
    for seq in sorted(s for s in inode_images if s >= unlink):
        row = inode_row(inode_images[seq], inode)
        require(row["generation"] == generation and row["links"] == 0,
                f"{name}: original generation not unlinked before reuse at {seq}")
        require(row["allocated_bytes"] <= old["allocated_bytes"], f"{name}: orphan allocation grew")
        if row["allocated_bytes"] == 0 and row["logical_bytes"] == 0:
            return {
                "name": name, "inode": inode, **old,
                "before_sequence": previous, "unlink_sequence": unlink, "zero_sequence": seq,
                "before_directory": transactions[previous]["targets"][DIR_BLOCK][1],
                "unlink_directory": transactions[unlink]["targets"][DIR_BLOCK][1],
                "before_inode": transactions[previous]["targets"][INODE_BLOCK][1],
                "unlink_inode": transactions[unlink]["targets"][INODE_BLOCK][1],
                "zero_inode": transactions[seq]["targets"][INODE_BLOCK][1],
            }
    raise ValueError(f"{name}: no same-generation zero allocation in selected data")


def retirements(dirs, inodes, transactions):
    permitted = {".", "..", MANIFEST, TEMP, *AOF_OLD, *AOF_NEW}
    for image in dirs.values():
        require(set(image) <= permitted and MANIFEST in image and len(set(image.values())) == len(image),
                "unrecognized AOF directory state or aliased inode")
    first, last = dirs[DIR_SEQUENCES[0]], dirs[DIR_SEQUENCES[-1]]
    require(AOF_OLD <= first.keys() and AOF_NEW <= last.keys() and not AOF_OLD.intersection(last)
            and TEMP not in last, "unexpected starting or ending AOF generation")
    rows = []
    for prev, curr in zip(DIR_SEQUENCES, DIR_SEQUENCES[1:]):
        before, after = dirs[prev], dirs[curr]
        if before[MANIFEST] != after[MANIFEST]:
            require(curr == prev + 1 and before.get(TEMP) == after[MANIFEST] and TEMP not in after,
                    "manifest replacement is not a directly observed temp rename")
            require(before[MANIFEST] not in after.values(), "old manifest inode remains named")
            src = inode_row(inodes[prev], before[TEMP])
            dest = inode_row(inodes[curr], after[MANIFEST])
            require(src == dest and src["links"] == 1, "renamed temp inode generation or allocation changed")
            rows.append(prove_retirement(MANIFEST, before[MANIFEST], prev, curr, inodes, transactions))
        for name in sorted((before.keys() - after.keys()) - {TEMP}):
            require(name in AOF_OLD and curr == prev + 1 and before[name] not in after.values(),
                    "unexpected removed or renamed AOF file")
            rows.append(prove_retirement(name, before[name], prev, curr, inodes, transactions))
        for name in (before.keys() & after.keys()) - {MANIFEST, TEMP}:
            require(before[name] == after[name], "unexplained AOF inode replacement")
    require([row["name"] for row in rows].count(MANIFEST) == 3
            and {row["name"] for row in rows} == {MANIFEST, *AOF_OLD}
            and len(rows) == len({(r["inode"], r["generation"]) for r in rows}) == 5,
            "wrong count of distinct retired file generations")
    return rows


def verify(document):
    require(document["schema"] == "c4-aof-journal-targeted-read-v2"
            and (document["device"], document["boot_id"], document["disk_serial"]) ==
            ("/dev/vdb", BOOT_ID, DISK_SERIAL)
            and (document["block_bytes"], document["dir_block"], document["inode_table_block"]) ==
            (BLOCK_SIZE, DIR_BLOCK, INODE_BLOCK), "input does not describe the frozen device and targets")
    sb = read_block(document["journal_superblock_base64"])
    first, length, seed = journal_superblock(sb)
    chains = document["selected_transaction_chains"]
    require(set(chains) == {str(s) for s in INODE_SEQUENCES}, "unexpected selected sequence coverage")
    transactions = {}
    dirs, inodes = {}, {}
    prior_seq = prior = None
    used_slots = set()
    contiguous = []
    for seq in sorted(INODE_SEQUENCES):
        members = chains[str(seq)]
        tx = check_transaction(seq, members, first, length, seed)
        slots = {m["journal_block"] for m in members}
        require(not used_slots.intersection(slots), "raw ring slot repeated in selected transactions")
        used_slots.update(slots)
        if prior is not None:
            require(prior["commit_time"] <= tx["commit_time"], "commit timestamps reverse sequence order")
            if seq == prior_seq + 1:
                require(tx["start"] == first + (prior["commit"] - first + 1) % (length - first),
                        "adjacent transactions do not join at the expected physical ring slot")
                contiguous.append([prior_seq, seq])
        expected_targets = TARGETS if seq in DIR_SEQUENCES else {INODE_BLOCK}
        require(set(tx["targets"]) == expected_targets, "wrong scoped block coverage at selected sequence")
        inodes[seq] = tx["targets"][INODE_BLOCK][0]
        if seq in DIR_SEQUENCES:
            dirs[seq] = directory(tx["targets"][DIR_BLOCK][0])
        transactions[seq] = tx
        prior_seq, prior = seq, tx

    rows = retirements(dirs, inodes, transactions)
    return {
        "schema": "c4-aof-journal-verification-v1", "passed": True,
        "retirement_bytes": sum(r["allocated_bytes"] for r in rows),
        "retirement_inodes": len(rows),
        "retired_aof_logical_bytes": sum(r["logical_bytes"] for r in rows if r["name"] in AOF_OLD),
        "retired_files": rows,
        "coverage": {
            "device": "/dev/vdb", "declared_boot_id": BOOT_ID, "declared_disk_serial": DISK_SERIAL,
            "journal_superblock_sha256": sha(sb), "directory_block": DIR_BLOCK,
            "inode_table_block": INODE_BLOCK, "first_inode_in_table_block": FIRST_INODE,
            "inode_size": INODE_SIZE, "checked_ring_blocks_excluding_superblock": len(used_slots),
            "checked_directory_images": len(dirs), "checked_inode_images": len(inodes),
            "omitted_non_target_payloads_in_selected_chain_spans": sum(t["omitted_payloads"] for t in transactions.values()),
            "joined_sequence_pairs": contiguous,
            "selected_commits": [
                {"sequence": seq, "start_slot": tx["start"], "commit_slot": tx["commit"],
                 "epoch_seconds": tx["commit_time"][0], "nanoseconds": tx["commit_time"][1],
                 "commit_sha256": tx["commit_sha256"]} for seq, tx in transactions.items()
            ],
        },
        "limitations": [
            "The export is a pinned selection, not a complete journal. It omits other payload bytes, intervening sequences, and any transaction prefixes not present before the first selected descriptor. This verifier does not independently count the producer's 10703 scanned commits.",
            "JBD2 CRCs validate the included target images. The pinned capture supplies their ext4 geometry and device identity; no independent ext4 inode or directory checksum, allocation bitmap, or cross-directory proof is available here.",
            "Retirement counts are historical for these five inode generations. They do not quantify unrelated frees, current free capacity, or future rewrite peaks.",
        ],
    }


def unique_object(pairs):
    values = {}
    for key, value in pairs:
        require(key not in values, f"duplicate JSON key: {key}")
        values[key] = value
    return values


def source_pin(path, data):
    return {"path": str(path.resolve(strict=True)), "sha256": sha(data)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True, help="new verification receipt; never overwrite")
    parser.add_argument("--producer", type=Path, default=Path(__file__).with_name("audit-c4-aof-journal.py"))
    args = parser.parse_args()
    try:
        raw = args.input.read_bytes()
        journal_pin = source_pin(args.input, raw)
        require(journal_pin["sha256"] == INPUT_SHA, "input does not match the original frozen SHA-256")
        producer_pin = source_pin(args.producer, args.producer.read_bytes())
        require(producer_pin["sha256"] == PRODUCER_SHA, "producer source SHA-256 mismatch")
        receipt = verify(json.loads(raw, object_pairs_hook=unique_object))
        verifier = Path(__file__)
        receipt["source_pins"] = [journal_pin, source_pin(verifier, verifier.read_bytes()), producer_pin]
        receipt["producer_pin"] = producer_pin
        rendered = json.dumps(receipt, indent=2) + "\n"
        with args.out.open("x", encoding="utf-8", newline="\n") as stream:
            stream.write(rendered)
    except (OSError, KeyError, TypeError, ValueError, struct.error, binascii.Error) as error:
        parser.exit(1, f"verification failed: {error}\n")


if __name__ == "__main__":
    main()
