#!/usr/bin/env python3
"""Generate exact-host/device Ceph service specs and preview commands, never run them."""
import argparse
import ipaddress
import json
from pathlib import Path, PurePosixPath
import re
import shlex

def generate(spec, output):
    nodes = spec["nodes"]
    if len(nodes) < 3 or len({n["name"] for n in nodes}) != len(nodes):
        raise ValueError("provide at least three distinct Ceph hosts")
    hosts, osds, commands = [], [], []
    seen_ips, seen_devices = set(), set()
    for node in nodes:
        name = node["name"]
        ip = str(ipaddress.IPv4Address(node["private_ip"]))
        if not re.fullmatch(r"[a-z][a-z0-9-]{1,40}", name) or not ipaddress.ip_address(ip).is_private:
            raise ValueError("invalid hostname/private IP")
        if ip in seen_ips:
            raise ValueError("Ceph hosts must have distinct private IPs")
        seen_ips.add(ip)
        devices = node["devices"]
        if not devices:
            raise ValueError("provide an explicit OSD device")
        normalized = []
        for value in devices:
            if not isinstance(value, str) or "\\" in value or "\x00" in value:
                raise ValueError("OSD device must be a Linux path")
            path = PurePosixPath(value)
            if path.parent != PurePosixPath("/dev/disk/by-id") or ".." in path.parts:
                raise ValueError("OSD devices must be explicit /dev/disk/by-id entries, not root or directories")
            if path in seen_devices:
                raise ValueError("an OSD device identity must not be listed more than once")
            seen_devices.add(path)
            normalized.append(str(path))
        hosts.append({"service_type": "host", "hostname": name, "addr": ip, "labels": ["agent-ceph"]})
        osds.append({"service_type": "osd", "service_id": name, "placement": {"hosts": [name]}, "spec": {"data_devices": {"paths": normalized}}})
        commands.append(f"ceph orch device ls --hostname={shlex.quote(name)} --wide --refresh")
    # Validate every host/device before creating any output.
    output.mkdir(parents=True, exist_ok=False)
    # JSON is a YAML subset; separate documents with the standard YAML delimiter.
    (output / "hosts.yaml").write_text("\n---\n".join(json.dumps(x, indent=2) for x in hosts) + "\n")
    (output / "osds.yaml").write_text("\n---\n".join(json.dumps(x, indent=2) for x in osds) + "\n")
    commands += ["ceph orch apply -i osds.yaml --dry-run", "# OSD creation is intentionally not executed by this generator."]
    (output / "preview.sh").write_text("#!/usr/bin/env bash\nset -euo pipefail\n" + "\n".join(commands) + "\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("spec", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    generate(json.loads(args.spec.read_text(encoding="utf-8-sig")), args.output)
