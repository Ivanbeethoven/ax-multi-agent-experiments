"""Install Linux experiment dependencies and record the actual host environment."""

import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess


PACKAGES = {
    "client": {"python3":"python3", "fio":"fio", "fusermount3":"fuse3", "zstd":"zstd", "ip":"iproute2", "iostat":"sysstat"},
    "objects": {"docker":"docker.io", "curl":"curl"},
    "metadata": {"redis-server":"redis-server", "redis-cli":"redis-tools"},
    "build": {"git":"git", "cc":"build-essential", "cmake":"cmake", "clang":"clang", "pkg-config":"pkg-config", "protoc":"protobuf-compiler", "objcopy":"binutils"},
}


def inspect(roles, root):
    commands = {name: shutil.which(name) for role in roles for name in PACKAGES[role]}
    memory = {line.split(":")[0]: int(line.split()[1]) * 1024
              for line in Path("/proc/meminfo").read_text().splitlines()
              if line.startswith(("MemTotal:", "MemAvailable:"))}
    target = root
    while not target.exists():
        target = target.parent
    disk = shutil.disk_usage(target)
    result = {"host":os.uname().nodename, "roles":roles, "kernel":os.uname().release,
              "logical_cpus":os.cpu_count(), "memory":memory,
              "disk":{"total":disk.total,"free":disk.free}, "commands":commands,
              "cgroup_v2":Path("/sys/fs/cgroup/cgroup.controllers").exists(),
              "fuse_device":Path("/dev/fuse").exists(), "root":str(root)}
    if commands.get("ip"):
        result["ipv4_interfaces"] = json.loads(subprocess.check_output(["ip","-j","-4","address"],text=True))
        result["routes"] = json.loads(subprocess.check_output(["ip","-j","route"],text=True))
    result["ready"] = all(commands.values()) and result["cgroup_v2"] and ("client" not in roles or result["fuse_device"])
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--role", action="append", choices=PACKAGES, required=True)
    parser.add_argument("--root", type=Path, default=Path("/srv/ax-cluster"))
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()
    if os.name != "posix" or not Path("/proc/meminfo").exists():
        parser.error("Run this command on the Linux experiment host")
    if not args.root.is_absolute() or ".." in args.root.parts:
        parser.error("An absolute experiment root is required")
    if args.apply:
        if os.geteuid() != 0:
            parser.error("Package installation requires root")
        packages = sorted({package for role in args.role for name, package in PACKAGES[role].items() if not shutil.which(name)})
        if packages:
            environment = os.environ.copy()
            environment["DEBIAN_FRONTEND"] = "noninteractive"
            subprocess.run(["apt-get","update"],check=True,env=environment)
            subprocess.run(["apt-get","install","-y","--no-install-recommends",*packages],check=True,env=environment)
        for name in ("sources","releases","input","runs","cache","mounts","private"):
            (args.root / name).mkdir(parents=True,exist_ok=True)
        (args.root / "private").chmod(0o700)
        if "client" in args.role and not Path("/dev/fuse").exists():
            subprocess.run(["modprobe","fuse"],check=True)
    result = inspect(args.role,args.root)
    if args.out:
        with args.out.open("x",encoding="utf-8") as stream:
            json.dump(result,stream,indent=2)
            stream.write("\n")
    print(json.dumps(result))
    raise SystemExit(0 if result["ready"] else 2)


if __name__ == "__main__":
    main()
