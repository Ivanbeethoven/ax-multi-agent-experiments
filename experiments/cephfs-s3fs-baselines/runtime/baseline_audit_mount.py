"""Audit running CephFS and s3fs FUSE mounts, cgroups, and secret hygiene."""

import json
from pathlib import Path

SENSITIVE_TOKENS = ("AWS_SECRET_ACCESS_KEY", "SECRET_KEY")


def redact_text(text: str, secret_values: list[str] | None = None) -> str:
    out = text
    for val in secret_values or []:
        if val:
            out = out.replace(val, "<redacted>")
    return out


def parse_mountinfo_for_target(mountinfo_text: str, mount_dir: str) -> dict | None:
    target_norm = mount_dir.rstrip("/") or "/"
    for line in mountinfo_text.splitlines():
        parts = line.split(" - ", 1)
        if len(parts) != 2:
            continue
        left = parts[0].split()
        right = parts[1].split()
        if len(left) >= 5 and len(right) >= 2:
            mp = left[4].replace("\\040", " ").rstrip("/") or "/"
            if mp == target_norm:
                return {
                    "mount_point": mp,
                    "fstype": right[0],
                    "source": right[1],
                    "options": right[2] if len(right) >= 3 else "",
                }
    return None


def verify_mount_state(
    *,
    record: dict,
    pid: int,
    cmdline: list[str],
    environ_keys: list[str],
    mountinfo_text: str,
    memory_max_raw: str,
    memory_swap_max_raw: str,
    secret_values: list[str] | None = None,
) -> dict:
    errors: list[str] = []
    joined_cmd = " ".join(cmdline)

    for secret in secret_values or []:
        if secret and secret in joined_cmd:
            errors.append("Raw secret leaked on process command line")
    for env_key in environ_keys:
        if any(tok in env_key.upper() for tok in SENSITIVE_TOKENS):
            errors.append(f"Disallowed secret environment variable on mount process: {env_key}")

    expected_mem = str(record["effective"]["memory_max_bytes"])
    expected_swap = str(record["effective"]["memory_swap_max_bytes"])
    if memory_max_raw.strip() != expected_mem:
        errors.append(f"cgroup memory.max={memory_max_raw.strip()!r} != {expected_mem!r}")
    if memory_swap_max_raw.strip() != expected_swap:
        errors.append(f"cgroup memory.swap.max={memory_swap_max_raw.strip()!r} != {expected_swap!r}")

    minfo = parse_mountinfo_for_target(mountinfo_text, record["mount_directory"])
    if minfo is None:
        errors.append(f"Mount point {record['mount_directory']} not present in /proc/self/mountinfo")
    else:
        if record["engine"] == "cephfs" and not minfo["fstype"].startswith("fuse"):
            errors.append(f"Unexpected fstype for ceph-fuse: {minfo['fstype']}")
        if record["engine"] == "s3fs" and not minfo["fstype"].startswith("fuse"):
            errors.append(f"Unexpected fstype for s3fs: {minfo['fstype']}")

    if record["engine"] == "s3fs":
        if "enable_unsigned_payload" not in joined_cmd:
            errors.append("s3fs must enable enable_unsigned_payload")
        if "tmpdir=/tmp" in joined_cmd:
            errors.append("s3fs tmpdir must not point to /tmp")
    elif record["engine"] == "cephfs":
        expected_oc = f"--client_oc_size={record['effective']['client_oc_size_bytes']}"
        if expected_oc not in cmdline:
            errors.append(f"ceph-fuse missing {expected_oc}")

    return {
        "ok": len(errors) == 0,
        "id": record["id"],
        "engine": record["engine"],
        "pid": pid,
        "cgroup": record["cgroup"],
        "memory_max": memory_max_raw.strip(),
        "memory_swap_max": memory_swap_max_raw.strip(),
        "mountinfo": minfo,
        "redacted_cmdline": [redact_text(arg, secret_values) for arg in cmdline],
        "errors": errors,
    }


def audit_live_process(record: dict, pid: int, secret_values: list[str] | None = None) -> dict:
    proc = Path(f"/proc/{pid}")
    raw_cmd = (proc / "cmdline").read_bytes().split(b"\x00")
    cmdline = [x.decode("utf-8", errors="replace") for x in raw_cmd if x]
    raw_env = (proc / "environ").read_bytes().split(b"\x00")
    env_keys = [x.split(b"=", 1)[0].decode("utf-8", errors="replace") for x in raw_env if b"=" in x]
    mountinfo = Path("/proc/self/mountinfo").read_text(encoding="utf-8", errors="replace")
    cg = Path(record["cgroup"])
    mem_max = (cg / "memory.max").read_text(encoding="utf-8") if (cg / "memory.max").is_file() else "missing"
    swap_max = (cg / "memory.swap.max").read_text(encoding="utf-8") if (cg / "memory.swap.max").is_file() else "missing"
    return verify_mount_state(
        record=record,
        pid=pid,
        cmdline=cmdline,
        environ_keys=env_keys,
        mountinfo_text=mountinfo,
        memory_max_raw=mem_max,
        memory_swap_max_raw=swap_max,
        secret_values=secret_values,
    )
