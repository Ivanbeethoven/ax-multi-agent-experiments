#!/usr/bin/env python3
"""Generate reviewed per-host Compose files. Never connects, starts, formats or provisions."""
import argparse
import ipaddress
import json
from pathlib import Path, PurePosixPath
import re


def linux_path(value):
    if not isinstance(value, str) or "\\" in value or "\x00" in value:
        raise ValueError("host paths must be Linux paths")
    path = PurePosixPath(value)
    if not path.is_absolute() or path.anchor != "/" or path == PurePosixPath("/") or ".." in path.parts:
        raise ValueError("host paths must be non-root absolute paths without parent traversal")
    return path


def generate(spec, out):
    image = spec["image"]
    if 'REPLACE_WITH' in image or 'REGISTRY/' in image:
        raise ValueError('replace deployment placeholders before generating')
    if not re.fullmatch(r"[^\s]+@sha256:[0-9a-f]{64}", image):
        raise ValueError("image must be pinned by sha256 digest")
    mode, nodes = spec["mode"], spec["nodes"]
    experiment = spec.get('experiment')
    if experiment is not None:
        if not re.fullmatch(r'ax-[a-z0-9-]{3,55}', experiment.get('project_name', '')):
            raise ValueError('unique ax- campaign/node/attempt project_name required')
        if experiment.get('cpus') != 3 or experiment.get('memory_bytes') != 16 * 1024**3:
            raise ValueError('formal backend budget is 3 CPU / 16 GiB')
        if experiment.get('stage') not in ('G','A','P','D','F','M'):
            raise ValueError('explicit experiment stage required')
        profile = experiment.get('layout_profile')
        if profile not in ('disabled','block-only','enabled') or (experiment['stage'] != 'D' and profile != 'disabled'):
            raise ValueError('dynamic layout profiles are exclusive to D')
        if mode != 'single-node-four-disk':
            raise ValueError('current formal experiment requires single-node four-disk EC')
    if mode not in ("smoke", "single-node-four-disk", "distributed"):
        raise ValueError("mode must be smoke, single-node-four-disk or distributed")
    count, disks = {"smoke": (1, 1), "single-node-four-disk": (1, 4), "distributed": (4, 4)}[mode]
    if len(nodes) != count:
        raise ValueError(f"{mode} profile requires exactly {count} nodes")
    ips = [str(ipaddress.IPv4Address(n["private_ip"])) for n in nodes]
    if len(set(ips)) != count or not all(ipaddress.ip_address(ip).is_private for ip in ips):
        raise ValueError("use distinct private IPv4 addresses")
    extra_hosts = {f"rustfs{i + 1}": ip for i, ip in enumerate(ips)}
    prepared = []
    for i, node in enumerate(nodes):
        mounts = node["data_mounts"]
        paths = [linux_path(p) for p in [*mounts, node["log_dir"], node["credentials_file"]]]
        if len(mounts) != disks or len(set(paths[:disks])) != disks:
            raise ValueError(f"node {i} requires {disks} distinct data mountpoints")
        for j, left in enumerate(paths):
            for right in paths[j + 1:]:
                if left == right or left in right.parents or right in left.parents:
                    raise ValueError("data, logs and credentials paths must not overlap or contain one another")
        env = {
            "RUSTFS_ADDRESS": f"{ips[i]}:9000",
            "RUSTFS_CONSOLE_ADDRESS": "127.0.0.1:9001",
            "RUSTFS_CONSOLE_ENABLE": "false",
            "RUSTFS_OBS_LOGGER_LEVEL": "warn",
            "RUSTFS_OBS_LOG_DIRECTORY": "/var/log/rustfs/",
            "RUSTFS_VOLUMES": {"smoke": "/data/rustfs0", "single-node-four-disk": "/data/rustfs{0...3}",
                               "distributed": "http://rustfs{1...4}:9000/data/rustfs{0...3}"}[mode],
        }
        service = {
            "image": image, "network_mode": "host", "restart": "unless-stopped",
            "env_file": [node["credentials_file"]], "environment": env,
            "extra_hosts": extra_hosts,
            "volumes": [
                {"type": "bind", "source": p, "target": f"/data/rustfs{j}", "bind": {"create_host_path": False}}
                for j, p in enumerate(mounts)
            ] + [{"type": "bind", "source": node["log_dir"], "target": "/var/log/rustfs", "bind": {"create_host_path": False}}],
        }
        if experiment is not None:
            enabled = experiment['layout_profile'] != 'disabled'
            quantum = experiment['layout_profile'] == 'enabled'
            env.update({
                'RUSTFS_EC_BLOCK_SIZE_HINT_ENABLE': str(enabled).lower(),
                'RUSTFS_EC_BLOCK_SIZE_HINT_FLEET_CONFIRMED': str(enabled).lower(),
                'RUSTFS_EC_READ_QUANTUM_ENABLE': str(quantum).lower(),
                'RUSTFS_EC_READ_QUANTUM_FLEET_CONFIRMED': str(quantum).lower(),
            })
            service.update(cpus=experiment['cpus'], mem_limit=experiment['memory_bytes'],
                           memswap_limit=experiment['memory_bytes'], restart='no')
        prepared.append((node, service))
    # No filesystem output is created until every node has passed validation.
    out.mkdir(parents=True, exist_ok=False)
    for i, (node, service) in enumerate(prepared):
        folder = out / f"rustfs{i + 1}"
        folder.mkdir()
        project = experiment['project_name'] if experiment else 'agent-rustfs'
        (folder / "compose.json").write_text(json.dumps({"name": project, "services": {"rustfs": service}}, indent=2) + "\n")
        (folder / "node.json").write_text(json.dumps({**node, "mode": mode, "image": image}, indent=2) + "\n")
    (out / "deployment.json").write_text(json.dumps({"schema_version": 1, **spec, "status": "generated_not_deployed"}, indent=2) + "\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("spec", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    generate(json.loads(args.spec.read_text(encoding="utf-8-sig")), args.output)
