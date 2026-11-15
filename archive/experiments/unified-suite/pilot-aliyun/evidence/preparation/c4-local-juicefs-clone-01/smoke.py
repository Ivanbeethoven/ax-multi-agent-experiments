#!/usr/bin/env python3
"""Tiny, local-only C4 JuiceFS native clone qualification. No benchmark timing."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
import uuid

HERE = Path(__file__).resolve().parent
FIXTURE = HERE.parent / "c4-workspace-v1-packed-fork-01" / "receipt.json"
FIXTURE_SHA = "7fb96f1a2dcda51f2fc200713ba8fa0ce05f905455713267e0803956dd9966c4"
PAYLOAD = {"a.txt": b"packed smoke\n", "sub/b.txt": bytes(range(64))}
EDIT = b"child one has different contents\n"
JUICEFS = Path("/home/hxy/ax-unified-pilot-prep-next-01/bin/juicefs")
JUICEFS_SHA = "6d450d3c151b6affede364d9a198d2505111b0612a5fb4d31613444c635174cc"
JUICEFS_VERSION = "juicefs version 1.4.2+2026-10-22.46af7af"
IMAGES = {
    "redis": "sha256:63218503f6461303e502355b8b0359a1194a190341f954e7fc7e8f92cd382a43",
    "rustfs": "sha256:942893356344d292d9fcccadfceb039467c589ba10b4045cb148c0a65d2d3fec",
}
RUSTFS_REV = "f4ed202508f2153afb7956bae6f7799b60eccd00"
LABEL = "ax.c4.juicefs-clone.owner"
DOCKER = ("docker", "--host", "unix:///var/run/docker.sock")


def require(ok, reason):
    if not ok:
        raise ValueError(reason)


def digest(path):
    with path.open("rb") as f:
        return hashlib.file_digest(f, "sha256").hexdigest()


def expected(payload):
    return {name: dict(bytes=len(data), sha256=hashlib.sha256(data).hexdigest())
            for name, data in payload.items()}


def seed(point):
    (point / "parent/sub").mkdir(parents=True)
    for name, data in PAYLOAD.items():
        path = point / "parent" / name
        with path.open("xb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())


def change_child(point):
    root = point / "child-1"
    with (root / "a.txt").open("wb") as f:
        f.write(EDIT)
        f.flush()
        os.fsync(f.fileno())
    (root / "sub/b.txt").rename(root / "child-only.txt")


def inspect_trees(point, names):
    output = {}
    for name in names:
        root = point / name
        files, directories, inodes = {}, [], {"": root.stat().st_ino}
        for path in sorted(root.rglob("*")):
            key = path.relative_to(root).as_posix()
            require(not path.is_symlink(), "unexpected symlink in clone tree")
            inodes[key] = path.stat().st_ino
            if path.is_dir():
                directories.append(key)
            else:
                require(path.is_file(), "unexpected file type in clone tree")
                files.update(expected({key: path.read_bytes()}))
        output[name] = dict(files=files, directories=directories, inodes=inodes)
    return output


def tcp(port):
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=.25):
            return True
    except OSError:
        return False


class Smoke:
    def __init__(self, binary):
        self.binary = binary.resolve(strict=True)
        self.run_id = "ax-c4-jfs-clone-" + uuid.uuid4().hex[:10]
        self.work = Path(tempfile.mkdtemp(prefix=self.run_id + "-")).resolve()
        self.home = self.work / "home"
        self.home.mkdir()
        self.access, self.secret = secrets.token_hex(12), secrets.token_hex(24)
        self.env = {key: value for key, value in os.environ.items()
                    if not key.startswith(("AWS_", "JUICEFS_")) and key not in ("ACCESS_KEY", "SECRET_KEY", "META_PASSWORD")}
        self.env.update(HOME=str(self.home), XDG_CACHE_HOME=str(self.home / "cache"),
                        ACCESS_KEY=self.access, SECRET_KEY=self.secret,
                        AWS_ACCESS_KEY_ID=self.access, AWS_SECRET_ACCESS_KEY=self.secret,
                        AWS_DEFAULT_REGION="us-east-1", AWS_REGION="us-east-1", AWS_EC2_METADATA_DISABLED="true",
                        NO_PROXY="127.0.0.1,localhost", no_proxy="127.0.0.1,localhost")
        self.phase = "preflight"
        self.clients, self.owned, self.ports = {}, {}, {}
        self.mount_exits = {}
        self.receipt = dict(schema="c4-local-juicefs-native-clone-v1", passed=False,
                            scope="One WSL host; native clone correctness only, no performance timing.",
                            platform="WSL2", utc=datetime.now(timezone.utc).isoformat(), run_id=self.run_id,
                            wsl_boot_id=Path("/proc/sys/kernel/random/boot_id").read_text().strip(),
                            script_sha256=digest(Path(__file__).resolve()),
                            no_model=True, no_cloud=True, formal_sample=False, no_copy_baseline=True,
                            fixture_receipt_sha256=FIXTURE_SHA, expected_files=expected(PAYLOAD),
                            rustfs_image_revision=RUSTFS_REV, image_sha256=IMAGES, mount_exits=self.mount_exits,
                            readbacks={}, clone_commands=[])

    def clean_text(self, raw):
        for value in (self.access, self.secret, str(self.work)):
            raw = raw.replace(value, "[redacted]")
        return re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", raw)[-2500:]

    def invoke(self, argv, *, env=None, check=True, limit=15):
        proc = subprocess.run(list(map(str, argv)), env=env, check=False, capture_output=True,
                              text=True, timeout=limit)
        if check and proc.returncode:
            raise RuntimeError(f"{Path(argv[0]).name} exited {proc.returncode}: "
                               + self.clean_text(proc.stderr + "\n" + proc.stdout))
        return proc

    def docker(self, *argv, **kwargs):
        return self.invoke([*DOCKER, *argv], **kwargs)

    def client(self, *argv, **kwargs):
        return self.invoke([self.binary, *argv], env=self.env, **kwargs)

    def wait_for(self, check, desc, limit=15):
        end = time.monotonic() + limit
        while time.monotonic() < end:
            value = check()
            if value:
                return value
            time.sleep(.1)
        raise TimeoutError(desc)

    def preflight(self):
        require("microsoft" in Path("/proc/sys/kernel/osrelease").read_text().lower() and Path("/dev/fuse").is_char_device(),
                "WSL2 and /dev/fuse required")
        require(os.geteuid() == 0, "Run as root for the isolated mount and temporary RustFS ownership")
        require(digest(FIXTURE) == FIXTURE_SHA and json.loads(FIXTURE.read_text())["expected_files"] == expected(PAYLOAD),
                "BrewFS qualification fixture pin or bytes changed")
        require(digest(self.binary) == JUICEFS_SHA, "JuiceFS binary SHA differs from the prebuilt 1.4.2 pin")
        version = self.client("version").stdout.strip()
        require(version == JUICEFS_VERSION, "JuiceFS version differs")
        self.receipt.update(juicefs_version=version, juicefs_binary_sha256=JUICEFS_SHA)
        for name, image in IMAGES.items():
            [info] = json.loads(self.docker("image", "inspect", image).stdout)
            require(info["Id"] == image, "local Docker image hash differs: " + name)
            if name == "rustfs":
                require(info["Config"]["Labels"]["org.opencontainers.image.revision"] == RUSTFS_REV,
                        "RustFS image revision differs")

    def start_service(self, service, port, options, *cmd):
        name = self.run_id + "-" + service
        self.owned[service] = name
        self.docker("run", "--pull", "never", "--rm", "-d", "--label", LABEL + "=" + self.run_id,
                    "--name", name, "-p", f"127.0.0.1::{port}", *options, IMAGES[service], *cmd)
        raw = self.docker("port", name, f"{port}/tcp").stdout.strip()
        require(re.fullmatch(r"127\.0\.0\.1:[0-9]+", raw), "expected loopback-only port mapping")
        self.ports[service] = int(raw.rsplit(":", 1)[1])

    def start_services(self):
        self.start_service("redis", 6379, ["--memory", "256m", "--cpus", "0.5", "--tmpfs", "/data"],
                           "redis-server", "--appendonly", "no", "--save", "")
        rustenv = self.work / "rustfs.env"
        knobs = dict(RUSTFS_ACCESS_KEY=self.access, RUSTFS_SECRET_KEY=self.secret,
                     RUSTFS_ADDRESS="0.0.0.0:9000", RUSTFS_CONSOLE_ENABLE="false",
                     RUSTFS_VOLUMES="/data/rustfs{0...3}", RUSTFS_UNSAFE_BYPASS_DISK_CHECK="true",
                     RUSTFS_OBS_LOGGER_LEVEL="warn", RUSTFS_OBS_LOG_DIRECTORY="/var/log/rustfs",
                     RUSTFS_OBS_METRICS_EXPORT_ENABLED="false", RUSTFS_OBS_TRACES_EXPORT_ENABLED="false",
                     RUSTFS_OBS_LOGS_EXPORT_ENABLED="false", RUSTFS_OBS_PROFILING_EXPORT_ENABLED="false",
                     RUSTFS_EC_BLOCK_SIZE_HINT_ENABLE="true", RUSTFS_EC_BLOCK_SIZE_HINT_FLEET_CONFIRMED="true",
                     RUSTFS_EC_READ_QUANTUM_ENABLE="true", RUSTFS_EC_READ_QUANTUM_FLEET_CONFIRMED="true")
        with rustenv.open("x", encoding="utf-8") as file:
            rustenv.chmod(0o600)
            file.writelines(f"{key}={val}\n" for key, val in knobs.items())
        opts = ["--memory", "2g", "--cpus", "1", "--env-file", str(rustenv)]
        for leaf, dst in [(str(i), f"/data/rustfs{i}") for i in range(4)] + [("rustlogs", "/var/log/rustfs")]:
            path = self.work / leaf
            path.mkdir()
            os.chown(path, 10001, 10001)
            opts.extend(["--mount", f"type=bind,src={path},dst={dst}"])
        self.start_service("rustfs", 9000, opts)
        self.wait_for(lambda: self.docker("exec", self.owned["redis"], "redis-cli", "ping", limit=2,
                                         check=False).stdout.strip() == "PONG", "Redis readiness")
        self.wait_for(lambda: tcp(self.ports["rustfs"]), "RustFS readiness")
        self.receipt["loopback_service_ports"] = dict(self.ports)
        self.receipt["dynamic_rustfs_layout_enabled"] = all(
            val == "true" for key, val in knobs.items() if key.startswith("RUSTFS_EC_"))

    def mount_row(self, point):
        rows = [line.split() for line in Path("/proc/self/mountinfo").read_text().splitlines()]
        hits = [row for row in rows if row[4] == str(point)]
        require(len(hits) < 2, "stacked FUSE mounts")
        if not hits:
            return None
        row = hits[0]
        require(row[row.index("-") + 1] == "fuse.juicefs", "wrong filesystem at test mount")
        return dict(mount_id=int(row[0]), filesystem="fuse.juicefs")

    def mount(self, side, redis_url):
        point, cache = self.work / side, self.work / ("cache-" + side)
        point.mkdir()
        cache.mkdir()
        log_path = self.work / (side + ".log")
        with log_path.open("xb") as log:
            proc = subprocess.Popen([str(self.binary), "mount", "--no-usage-report", "--no-bgjob",
                "--backup-meta", "0", "--cache-size", "0", "--cache-dir", str(cache), "--prefetch", "0",
                "--attr-cache", "0s", "--entry-cache", "0s", "--dir-entry-cache", "0s",
                "--metrics", "127.0.0.1:0", redis_url, str(point)],
                stdout=log, stderr=subprocess.STDOUT, env=self.env)
        self.clients[side] = (point, proc)
        def ready():
            if proc.poll() is not None:
                raise RuntimeError(f"{side} exited {proc.returncode}: " + self.clean_text(log_path.read_text(errors="replace")))
            return self.mount_row(point)
        row = self.wait_for(ready, side + " FUSE readiness")
        ticks = int(Path(f"/proc/{proc.pid}/stat").read_text().rsplit(")", 1)[1].split()[19])
        self.receipt.setdefault("mounts", {})[side] = {**row, "foreground_pid": proc.pid,
            "foreground_start_ticks": ticks, "point": side, "cache_size_mib": 0, "prefetch_blocks": 0}
        return point

    def stop_mount(self, side, strict=True):
        point, proc = self.clients[side]
        unmount = self.invoke(["fusermount3", "-u", point], check=False, limit=6).returncode if self.mount_row(point) else None
        forced = False
        try:
            code = proc.wait(timeout=6)
        except subprocess.TimeoutExpired:
            forced = True
            proc.terminate()
            try:
                code = proc.wait(timeout=4)
            except subprocess.TimeoutExpired:
                proc.kill()
                code = proc.wait(timeout=3)
        clean = (unmount == 0 and code == 0 and not self.mount_row(point) and not forced)
        self.mount_exits[side] = dict(normal_unmount_exit_code=unmount, foreground_exit_code=code,
                                     mount_absent=not self.mount_row(point), cleanup_signal_needed=forced)
        if not self.mount_row(point):
            self.clients.pop(side)
        if strict:
            require(clean, side + " did not complete normal unmount")

    def readback(self, stage, point, desired):
        # FUSE I/O gets its own bounded subprocess; this driver can still clean up.
        call = self.invoke([sys.executable, Path(__file__).resolve(), "inspect", point, *desired], limit=14)
        trees = json.loads(call.stdout)
        for name, files in desired.items():
            require(trees[name]["files"] == expected(files) and trees[name]["directories"] == ["sub"],
                    f"{stage}/{name}: exact tree mismatch")
        keys = set.intersection(*(set(row["inodes"]) for row in trees.values()))
        distinct = all(len({row["inodes"][key] for row in trees.values()}) == len(trees) for key in keys)
        self.receipt["readbacks"][stage] = dict(passed=distinct, trees=trees, distinct_corresponding_inodes=distinct)
        require(distinct, stage + ": source and clone share corresponding inode IDs")
        return trees

    def run(self):
        self.preflight()
        self.phase = "start-loopback-services"
        self.start_services()
        redis = f"redis://127.0.0.1:{self.ports['redis']}/0"
        bucket = "c4-jfs-" + uuid.uuid4().hex[:12]
        endpoint = f"http://127.0.0.1:{self.ports['rustfs']}/{bucket}"
        self.phase = "format-isolated-volume"
        self.client("format", "--storage", "s3", "--bucket", endpoint, "--block-size", "4096",
                    "--compress", "none", "--trash-days", "0", redis, bucket)
        self.phase = "seed-parent"
        point = self.mount("live", redis)
        self.invoke([sys.executable, Path(__file__).resolve(), "seed", point], limit=14)
        self.readback("parent_before_clone", point, {"parent": PAYLOAD})
        self.phase = "native-clone"
        for child in ("child-1", "child-2"):
            require(not (point / child).exists(), "pre-existing clone destination")
            proc = self.client("clone", "-p", "--threads", "1", point / "parent", point / child, limit=12)
            self.receipt["clone_commands"].append(dict(argv=["juicefs", "clone", "-p", "--threads", "1",
                "<mount>/parent", "<mount>/" + child], exit_code=proc.returncode))
        self.readback("two_native_clones", point, {"parent": PAYLOAD, "child-1": PAYLOAD, "child-2": PAYLOAD})
        self.phase = "mutate-only-child-1"
        self.invoke([sys.executable, Path(__file__).resolve(), "change", point], limit=14)
        isolated = {"parent": PAYLOAD, "child-1": {"a.txt": EDIT, "child-only.txt": PAYLOAD["sub/b.txt"]},
                    "child-2": PAYLOAD}
        live = self.readback("after_child_1_edit", point, isolated)
        self.phase = "cold-client-remount"
        self.stop_mount("live")
        cold_point = self.mount("cold", redis)
        original_mount, cold_mount = (self.receipt["mounts"][side] for side in ("live", "cold"))
        require(self.mount_exits["live"]["mount_absent"] and original_mount["point"] != cold_mount["point"]
                and (original_mount["foreground_pid"], original_mount["foreground_start_ticks"])
                    != (cold_mount["foreground_pid"], cold_mount["foreground_start_ticks"]),
                "cold client must be a distinct process after the original mount is absent")
        self.receipt["kernel_mount_id_reused"] = original_mount["mount_id"] == cold_mount["mount_id"]
        cold = self.readback("cold_after_child_1_edit", cold_point, isolated)
        require(live == cold, "file digests or inode identities changed on cold client remount")
        self.stop_mount("cold")
        self.receipt["fresh_client_readback_passed"] = True
        self.receipt["cold_scope"] = "New FUSE client, empty cache directory, client caching/prefetch disabled; servers unchanged."
        self.receipt["passed"] = True

    def cleanup(self):
        errors = []
        for side in list(self.clients):
            try:
                self.stop_mount(side, strict=False)
            except Exception as exc:
                errors.append(self.clean_text(str(exc)))
        for name in self.owned.values():
            try:
                inspected = self.docker("inspect", "--type", "container", name, check=False, limit=5)
                if not inspected.returncode:
                    [value] = json.loads(inspected.stdout)
                    require(value["Config"]["Labels"].get(LABEL) == self.run_id, "container label changed")
                    self.docker("stop", "-t", "3", value["Id"], limit=8)
            except Exception as exc:
                errors.append(self.clean_text(str(exc)))
        try:
            absent = self.wait_for(lambda: not self.docker("ps", "-aq", "--filter",
                f"label={LABEL}={self.run_id}", limit=3).stdout.strip(), "container removal", limit=8)
        except Exception as exc:
            absent = False
            errors.append(self.clean_text(str(exc)))
        mounts_absent = not self.clients
        if mounts_absent and absent:
            try:
                shutil.rmtree(self.work)
            except OSError as exc:
                errors.append(self.clean_text(str(exc)))
        released = all(not tcp(port) for port in self.ports.values())
        self.receipt["cleanup"] = dict(owned_containers_absent=absent, mount_points_absent=mounts_absent,
                                       loopback_ports_released=released, scratch_absent=not self.work.exists(),
                                       errors=errors)
        self.receipt["passed"] = self.receipt["passed"] and not errors and absent and mounts_absent and released and not self.work.exists()


def main():
    if len(sys.argv) >= 3 and sys.argv[1] in ("seed", "change", "inspect"):
        op, point = sys.argv[1], Path(sys.argv[2])
        if op == "seed":
            seed(point)
        elif op == "change":
            change_child(point)
        else:
            print(json.dumps(inspect_trees(point, sys.argv[3:])))
        return 0
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--juicefs", type=Path, default=JUICEFS)
    p.add_argument("--out", required=True, type=Path)
    a = p.parse_args()
    require(a.out.is_absolute() and a.out.parent.is_dir() and not a.out.exists() and not a.out.is_symlink(),
            "new absolute receipt path required")
    smoke = Smoke(a.juicefs)
    def interrupt(*_):
        raise TimeoutError("local qualification time limit or interruption")
    signal.signal(signal.SIGTERM, interrupt)
    signal.signal(signal.SIGALRM, interrupt)
    signal.alarm(120)
    try:
        smoke.run()
    except Exception as exc:
        smoke.receipt.update(failed_phase=smoke.phase, error=type(exc).__name__ + ": " + smoke.clean_text(str(exc)))
    finally:
        signal.alarm(0)
        smoke.cleanup()
        with a.out.open("x", encoding="utf-8") as out:
            json.dump(smoke.receipt, out, indent=2)
            out.write("\n")
    return 0 if smoke.receipt["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
