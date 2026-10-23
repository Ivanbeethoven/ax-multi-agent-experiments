#!/usr/bin/env python3
"""Linux node preflight; --start explicitly starts the already generated service."""
import argparse
import json
import os
from pathlib import Path
import stat
import subprocess


def check(node):
    addresses = subprocess.check_output(["ip", "-json", "addr", "show"], text=True)
    local_ips = {a["local"] for interface in json.loads(addresses) for a in interface.get("addr_info", [])}
    if node["private_ip"] not in local_ips:
        raise ValueError("node spec belongs to a different host")
    devices = set()
    for name in node["data_mounts"]:
        path = Path(name)
        if path.is_symlink() or not os.path.ismount(path):
            raise ValueError(f"not a distinct mounted data filesystem: {name}")
        result = json.loads(subprocess.check_output(["findmnt", "--json", "--mountpoint", name, "--output", "SOURCE,FSTYPE,OPTIONS,MAJ:MIN"], text=True))
        fs = result["filesystems"][0]
        if fs["fstype"] not in ("xfs", "ext4") or "rw" not in fs["options"].split(","):
            raise ValueError(f"requires mounted read/write XFS or ext4: {name}")
        device = fs["maj:min"]
        if device in devices:
            raise ValueError("each formal RustFS volume must use a different underlying filesystem/device")
        devices.add(device)
        if path.stat().st_uid != 10001:
            raise ValueError(f"data mount owner must be container UID 10001: {name}")
    log = Path(node["log_dir"])
    if not log.is_dir() or log.stat().st_uid != 10001:
        raise ValueError("log directory must exist and be owned by UID 10001")
    secret = Path(node["credentials_file"])
    if secret.is_symlink() or not secret.is_file() or stat.S_IMODE(secret.stat().st_mode) != 0o600:
        raise ValueError("credentials file must be a regular non-symlink file with mode 0600")
    data = dict(line.split("=", 1) for line in secret.read_text().splitlines() if line and not line.startswith("#"))
    for key in ("RUSTFS_ACCESS_KEY", "RUSTFS_SECRET_KEY"):
        value = data.get(key, "")
        if len(value) < 12 or value in ("rustfsadmin", "REPLACE_ME") or "$" in value:
            raise ValueError(f"set a non-placeholder {key} (at least 12 chars, no Compose interpolation)")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    parser.add_argument("--start", action="store_true")
    args = parser.parse_args()
    directory = args.directory.resolve()
    node = json.loads((directory / "node.json").read_text())
    check(node)
    cmd = ["docker", "compose", "-f", str(directory / "compose.json")]
    subprocess.run([*cmd, "config", "--quiet"], check=True)
    if args.start:
        subprocess.run([*cmd, "up", "-d", "rustfs"], check=True)
    print(json.dumps({"preflight": "passed", "start_requested": args.start, "ready": "requires_s3_probe"}))
