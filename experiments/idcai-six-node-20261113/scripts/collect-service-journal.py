#!/usr/bin/env python3
"""Archive sanitized journals for one closed, owned IDC AI experiment.

Run after stopping the three services, before removing the run's private env.
Raw journal bytes remain in journald; only hashes and sanitized records persist.
"""

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import stat
import subprocess
import sys


BASE = Path("/srv/ax-idcai-20261113/four-node")
SCHEMA = "ax-idcai-service-journal-v1"
JOURNAL_NAME = "service-journal.jsonl"
PROOF_NAME = "service-journal-proof.json"
SECRET_NAMES = ("RUSTFS_ACCESS_KEY", "RUSTFS_SECRET_KEY", "RUSTFS_RPC_SECRET")
RUN_RE = re.compile(r"ax-four-[0-9a-f]{12}", re.ASCII)
ANSI_RE = re.compile(r"\x1b\][^\x07]*?(?:\x07|\x1b\\)|(?:\x1b\[|\x9b)[0-?]*[ -/]*[@-~]|\x1b[@-_]")
SENSITIVE_RE = re.compile(
    r"credential|password|passwd|authorization|authentication|"
    r"secret|signature|signing[ _-]?key|access[ _-]?key|api[ _-]?key|"
    r"private[ _-]?key|session[ _-]?token|security[ _-]?token|"
    r"(?:\bbearer\b)|(?:\btoken\b)|(?:\brequirepass\b)|"
    r"x-amz-(?:credential|signature|security-token)|"
    r"(?:https?://[^/\s:@]+:[^/\s@]+@)|"
    r"(?:\bbasic\s+[A-Za-z0-9+/=]{8,})|签名|密钥|密码|口令|令牌|凭据",
    re.IGNORECASE,
)


class CollectionError(Exception):
    """A fixed, secret-free diagnostic suitable for stderr."""


def digest(data):
    return hashlib.sha256(data).hexdigest()


def open_directory(path):
    """Resolve every absolute path component with O_NOFOLLOW, including BASE."""
    if not path.is_absolute() or any(part in (".", "..") for part in path.parts):
        raise CollectionError("invalid archive directory")
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    directory = os.open("/", flags)
    try:
        for component in path.parts[1:]:
            next_directory = os.open(component, flags, dir_fd=directory)
            os.close(directory)
            directory = next_directory
        return directory
    except BaseException:
        os.close(directory)
        raise


def file_exists(directory, name):
    try:
        info = os.stat(name, dir_fd=directory, follow_symlinks=False)
    except FileNotFoundError:
        return False
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise CollectionError("archive member must be a regular, unlinked file")
    return True


def read_regular(directory, name, limit=None):
    descriptor = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise CollectionError("archive member must be a regular, unlinked file")
        with os.fdopen(descriptor, "rb", closefd=False) as source:
            data = source.read() if limit is None else source.read(limit + 1)
        if limit is not None and len(data) > limit:
            raise CollectionError("archive metadata exceeds size limit")
        return data
    finally:
        os.close(descriptor)


def load_secret_values(directory):
    values = {}
    for line in read_regular(directory, "env", 65536).decode("utf-8").splitlines():
        name, separator, value = line.partition("=")
        if separator and name in SECRET_NAMES:
            if name in values or not value:
                raise CollectionError("private env has missing or duplicate credential fields")
            values[name] = value
    if set(values) != set(SECRET_NAMES):
        raise CollectionError("private env has missing or duplicate credential fields")
    return sorted(set(values.values()), key=len, reverse=True)


def sanitize_message(message, secret_values):
    if isinstance(message, list) and all(type(value) is int and 0 <= value <= 255 for value in message):
        message = bytes(message).decode("utf-8", errors="replace")
    if not isinstance(message, str):
        raise CollectionError("journal MESSAGE has an unsupported representation")
    message = ANSI_RE.sub("", message)
    replacements = 0
    for value in secret_values:
        replacements += message.count(value)
        message = message.replace(value, "[REDACTED]")
    sensitive = bool(SENSITIVE_RE.search(message))
    if sensitive:
        message = "[REDACTED: sensitive credential or authentication field]"
    return message, replacements, sensitive


def journal_records(unit, secret_values):
    result = subprocess.run(
        ["journalctl", "--unit", unit, "--output=json", "--all", "--no-pager", "--quiet"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False, timeout=120,
    )
    if result.returncode != 0:
        raise CollectionError("journalctl failed; private env must be retained")
    # Journald can return success with a permissions warning and partial output.
    # Do not certify an incomplete collection; never echo its raw stderr.
    if result.stderr.strip():
        raise CollectionError("journalctl emitted a diagnostic; private env must be retained")
    records = []
    redacted_records = redacted_values = sensitive_records = 0
    for line in result.stdout.splitlines():
        if not line.strip():
            continue
        entry = json.loads(line)
        if not isinstance(entry, dict):
            raise CollectionError("journal entry is not an object")
        timestamp = entry.get("__REALTIME_TIMESTAMP")
        priority = entry.get("PRIORITY")
        if not isinstance(timestamp, str) or not re.fullmatch(r"[0-9]{1,24}", timestamp):
            raise CollectionError("journal entry has an invalid timestamp")
        if priority is not None and (not isinstance(priority, str) or not re.fullmatch(r"[0-7]", priority)):
            raise CollectionError("journal entry has an invalid priority")
        message, replacements, sensitive = sanitize_message(entry.get("MESSAGE", ""), secret_values)
        redacted_values += replacements
        sensitive_records += int(sensitive)
        redacted_records += int(bool(replacements or sensitive))
        # Unit is the fixed query selector, never an untrusted journal field.
        records.append({"timestamp_us": timestamp, "unit": unit, "priority": priority, "message": message})
    return records, {
        "records": len(records), "raw_stdout_sha256": digest(result.stdout),
    }, (redacted_records, redacted_values, sensitive_records)


def install_exclusive(directory, name, payload):
    """Publish complete bytes atomically; link() refuses an existing target."""
    temporary = ".service-journal-" + secrets.token_hex(16) + ".tmp"
    descriptor = os.open(
        temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
        0o600, dir_fd=directory,
    )
    try:
        with os.fdopen(descriptor, "wb", closefd=False) as target:
            target.write(payload)
            target.flush()
            os.fsync(descriptor)
        os.link(temporary, name, src_dir_fd=directory, dst_dir_fd=directory, follow_symlinks=False)
        os.unlink(temporary, dir_fd=directory)
        os.fsync(directory)
    finally:
        os.close(descriptor)
        try:
            os.unlink(temporary, dir_fd=directory)
        except FileNotFoundError:
            pass


def validate_existing(directory, run, units):
    has_journal = file_exists(directory, JOURNAL_NAME)
    has_proof = file_exists(directory, PROOF_NAME)
    if not has_journal and not has_proof:
        return None
    if not has_journal or not has_proof:
        raise CollectionError("incomplete prior archive; refusing to overwrite")
    proof = json.loads(read_regular(directory, PROOF_NAME, 65536))
    data = read_regular(directory, JOURNAL_NAME)
    if not isinstance(proof, dict) or proof.get("schema") != SCHEMA or proof.get("run") != run:
        raise CollectionError("existing proof has an invalid identity")
    unit_proofs = proof.get("units")
    if not isinstance(unit_proofs, dict) or set(unit_proofs) != set(units):
        raise CollectionError("existing proof has invalid unit records")
    for unit_proof in unit_proofs.values():
        if (
            not isinstance(unit_proof, dict)
            or type(unit_proof.get("records")) is not int or unit_proof["records"] < 0
            or not isinstance(unit_proof.get("raw_stdout_sha256"), str)
            or not re.fullmatch(r"[0-9a-f]{64}", unit_proof["raw_stdout_sha256"])
        ):
            raise CollectionError("existing proof has invalid unit records")
    if proof.get("sanitized") != {"path": JOURNAL_NAME, "sha256": digest(data), "bytes": len(data)}:
        raise CollectionError("existing sanitized journal does not match its proof")
    for field in ("redacted_records", "redacted_values", "sensitive_records"):
        if type(proof.get(field)) is not int or proof[field] < 0:
            raise CollectionError("existing proof has invalid redaction counters")
    return proof


def collect(run):
    if not RUN_RE.fullmatch(run):
        raise CollectionError("run must match ax-four-[0-9a-f]{12}")
    units = [run + suffix + ".service" for suffix in ("", "-otel", "-redis")]
    directory = open_directory(BASE / run)
    try:
        existing = validate_existing(directory, run, units)
        if existing is not None:
            return existing
        secret_values = load_secret_values(directory)
        entries = []
        unit_proofs = {}
        totals = [0, 0, 0]
        for unit in units:
            records, unit_proof, counts = journal_records(unit, secret_values)
            entries.extend(records)
            unit_proofs[unit] = unit_proof
            totals = [total + count for total, count in zip(totals, counts)]
        payload = b"".join(
            (json.dumps(entry, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")
            for entry in entries
        )
        proof = {
            "schema": SCHEMA, "run": run, "units": unit_proofs,
            "sanitized": {"path": JOURNAL_NAME, "sha256": digest(payload), "bytes": len(payload)},
            "redacted_records": totals[0], "redacted_values": totals[1], "sensitive_records": totals[2],
        }
        # The proof is the completion marker. If publication is interrupted,
        # a subsequent call refuses the partial pair instead of overwriting it.
        install_exclusive(directory, JOURNAL_NAME, payload)
        install_exclusive(directory, PROOF_NAME, (json.dumps(proof, indent=2) + "\n").encode("utf-8"))
        return proof
    finally:
        os.close(directory)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run", help="owned ax-four-12hex run identifier")
    arguments = parser.parse_args()
    try:
        proof = collect(arguments.run)
        # Reconstruct stdout from validated fields, not arbitrary existing JSON.
        print(json.dumps({
            "schema": SCHEMA, "run": arguments.run,
            "path": str(BASE / arguments.run / JOURNAL_NAME),
            "proof_path": str(BASE / arguments.run / PROOF_NAME),
            "sha256": proof["sanitized"]["sha256"], "bytes": proof["sanitized"]["bytes"],
            "unit_records": {unit: item["records"] for unit, item in proof["units"].items()},
            "redacted_records": proof["redacted_records"],
        }, separators=(",", ":")))
        return 0
    except (CollectionError, OSError, ValueError, TypeError, subprocess.SubprocessError):
        # Exception text may originate in raw journal/env data. Never print it.
        print("service journal collection failed; preserve private env and inspect locally", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
