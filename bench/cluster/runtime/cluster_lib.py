#!/usr/bin/env python3
"""Shared hash-free coordinator library for run-001 on A366.

Must be executed with `/srv/ax-idcai-20261113/venv/bin/python3` on A366 (provides boto3).
"""

from concurrent.futures import ThreadPoolExecutor
import datetime as dt
import fcntl
import json
import os
from pathlib import Path
import random
import shlex
import shutil
import socket
import subprocess
import sys
import threading
import time

import boto3
from botocore.config import Config as BotoConfig
from metric_totals import rustfs_payload_metrics

from runtime_context import RUN_ROOT, RUN_ID, RUN_PREFIX, RUN_NAMESPACE, REDIS_PORT, PD_ENDPOINT
REPO_ROOT = Path("/srv/ax-cluster/direct/repo")
BIN_DIR = Path("/srv/ax-cluster/direct/bin")
SCRIPTS_DIR = Path("/srv/ax-cluster/direct/scripts")
INPUT_DIR = Path("/srv/ax-cluster/input")
PRIVATE_DIR = Path("/srv/ax-cluster/private")
SSH_KEY = PRIVATE_DIR / "cluster_ed25519"
MEASUREMENT_LOCK_PATH = Path("/srv/ax-idcai-20261113/measurement.lock")

HOSTS = [
    {"name": "A366", "ssh": "idcai-60001", "ip": "175.168.3.66", "slots": 52, "active": 48},
    {"name": "A355", "ssh": "idcai-60525", "ip": "175.168.3.55", "slots": 40, "active": 32},
    {"name": "A672", "ssh": "idcai-60103", "ip": "175.168.6.72", "slots": 16, "active": 16},
    {"name": "A601", "ssh": "idcai-60439", "ip": "175.168.6.1", "slots": 48, "active": 40},
    {"name": "A635", "ssh": "idcai-60533", "ip": "175.168.6.35", "slots": 48, "active": 40},
    {"name": "A368", "ssh": "idcai-60555", "ip": "175.168.3.68", "slots": 20, "active": 16},
]
HOST_BY_NAME = {h["name"]: h for h in HOSTS}
HOST_BY_SSH = {h["ssh"]: h for h in HOSTS}
ALL_HOST_NAMES = [h["name"] for h in HOSTS]

RUSTFS_BACKENDS = json.loads((REPO_ROOT / 'configs/rustfs-backends.json').read_text())
RUSTFS_BACKEND = 'dynamic'
RUSTFS_ENDPOINTS = list(RUSTFS_BACKENDS[RUSTFS_BACKEND]['endpoints'])


def select_rustfs_backend(name):
    global RUSTFS_BACKEND
    RUSTFS_BACKEND = name
    RUSTFS_ENDPOINTS[:] = RUSTFS_BACKENDS[name]['endpoints']


REDIS_HOST = "175.168.3.55"


_CMD_LOCK = threading.Lock()
_STATUS_LOCK = threading.Lock()
_SSH_OPEN_LIMIT = threading.BoundedSemaphore(2)
_SSH_HOST_LOCKS = {h["name"]: threading.Lock() for h in HOSTS}
_SSH_CHANNELS = {h["name"]: threading.BoundedSemaphore(4) for h in HOSTS}


def ssh_options(host):
    control_dir = Path("/srv/ax-cluster/direct/runtime/ssh")
    control_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    control = str(control_dir / host["name"])
    flags = ["-i", str(SSH_KEY), "-o", "StrictHostKeyChecking=accept-new",
             "-o", "BatchMode=yes", "-o", "ConnectTimeout=15",
             "-o", "ServerAliveInterval=20", "-o", "ServerAliveCountMax=3",
             "-o", "ControlPath=" + control, "-o", "ControlPersist=1800"]
    target = "root@" + host["ip"]
    with _SSH_HOST_LOCKS[host["name"]]:
        checked = subprocess.run(["ssh", *flags, "-O", "check", target], capture_output=True)
        if checked.returncode:
            with _SSH_OPEN_LIMIT:
                for attempt in range(3):
                    try:
                        subprocess.run(["ssh", *flags, "-MNf", "-o", "ControlMaster=yes", target],
                                       capture_output=True, check=True, timeout=25)
                        break
                    except (subprocess.CalledProcessError,subprocess.TimeoutExpired):
                        if attempt==2:raise
                        print(f'SSH connection retry {attempt+1}/2 for {host["name"]}',flush=True)
                        time.sleep(5*(attempt+1))
    return flags + ["-o", "ControlMaster=auto"]


def utc_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def load_secrets() -> dict[str, str]:
    out = {}
    for p in (PRIVATE_DIR / "redis.env", PRIVATE_DIR / "rustfs.env"):
        for ln in p.read_text(encoding="utf-8").splitlines():
            if "=" in ln and not ln.startswith("#"):
                k, v = ln.split("=", 1)
                out[k.strip()] = v.strip()
    return {
        "redis_password": out["REDIS_PASSWORD"],
        "s3_access_key": out["RUSTFS_ACCESS_KEY"],
        "s3_secret_key": out["RUSTFS_SECRET_KEY"],
    }


SECRETS = load_secrets()


def base_secret_env() -> dict[str, str]:
    env = os.environ.copy()
    env.update(
        {
            "AWS_ACCESS_KEY_ID": SECRETS["s3_access_key"],
            "AWS_SECRET_ACCESS_KEY": SECRETS["s3_secret_key"],
            "ACCESS_KEY": SECRETS["s3_access_key"],
            "SECRET_KEY": SECRETS["s3_secret_key"],
            "META_PASSWORD": SECRETS["redis_password"],
            "REDIS_PASSWORD": SECRETS["redis_password"],
            "PYTHONPATH": str(REPO_ROOT),
        }
    )
    return env


def redact_argv(argv: list[str]) -> list[str]:
    pw = SECRETS["redis_password"]
    ak = SECRETS["s3_access_key"]
    sk = SECRETS["s3_secret_key"]
    out = []
    for tok in argv:
        s = str(tok)
        if pw:
            s = s.replace(pw, "***")
        if sk:
            s = s.replace(sk, "***")
        if ak and len(ak) > 6:
            s = s.replace(ak, "<AK>")
        out.append(s)
    return out


def log_command(
    host: str,
    argv: list[str],
    rc: int,
    started_utc: str,
    ended_utc: str,
    elapsed_seconds: float,
    log_path: str = "",
):
    rec = {
        "host": host,
        "argv": redact_argv(argv),
        "returncode": rc,
        "started_at": started_utc,
        "ended_at": ended_utc,
        "elapsed_seconds": round(elapsed_seconds, 6),
        "log_path": log_path,
    }
    line = json.dumps(rec, ensure_ascii=False, separators=(",", ":")) + "\n"
    with _CMD_LOCK:
        with (RUN_ROOT / "commands.jsonl").open("a", encoding="utf-8") as fh:
            fh.write(line)


class MeasurementLock:
    def __init__(self):
        self.fh = None

    def __enter__(self):
        MEASUREMENT_LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
        self.fh = MEASUREMENT_LOCK_PATH.open("a+", encoding="utf-8")
        t0 = time.monotonic()
        while True:
            try:
                fcntl.flock(self.fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() - t0 > 30:
                    print("Waiting for /srv/ax-idcai-20261113/measurement.lock...", flush=True)
                    t0 = time.monotonic()
                time.sleep(1.0)
        self.fh.seek(0)
        self.fh.truncate()
        self.fh.write(f"{RUN_ID} pid={os.getpid()} acquired={utc_iso()}\n")
        self.fh.flush()
        return self

    def __exit__(self, exc_type, exc, tb):
        if self.fh is not None:
            try:
                fcntl.flock(self.fh.fileno(), fcntl.LOCK_UN)
            finally:
                self.fh.close()
                self.fh = None


def run_on_host(
    host_name_or_ssh: str,
    argv: list[str],
    *,
    input_bytes: bytes | None = None,
    timeout: float = 3600.0,
    check: bool = True,
    with_secrets: bool = False,
    log_cmd: bool = True,
    log_path: str = "",
) -> subprocess.CompletedProcess:
    h = HOST_BY_NAME.get(host_name_or_ssh) or HOST_BY_SSH[host_name_or_ssh]
    hname = h["name"]
    started_utc = utc_iso()
    t0 = time.monotonic()
    if hname == "A366":
        env = base_secret_env() if with_secrets else {**os.environ, "PYTHONPATH": str(REPO_ROOT)}
        proc = subprocess.run(
            [str(x) for x in argv],
            input=input_bytes,
            env=env,
            capture_output=True,
            timeout=timeout,
        )
    else:
        quoted = " ".join(shlex.quote(str(x)) for x in argv)
        if with_secrets:
            remote_cmd = f"set -a; . /srv/ax-cluster/private/aws.env; set +a; export PYTHONPATH=/srv/ax-cluster/direct/repo; exec {quoted}"
        else:
            remote_cmd = f"export PYTHONPATH=/srv/ax-cluster/direct/repo; exec {quoted}"
        ssh_flags = ssh_options(h)
        if input_bytes is None:
            ssh_flags.insert(0, "-n")
        with _SSH_CHANNELS[hname]:
            proc = subprocess.run(
                ["ssh", *ssh_flags, f"root@{h['ip']}", remote_cmd],
                input=input_bytes,
                capture_output=True,
                timeout=timeout,
            )
    elapsed = time.monotonic() - t0
    ended_utc = utc_iso()
    if log_cmd:
        log_command(hname, [str(x) for x in argv], proc.returncode, started_utc, ended_utc, elapsed, log_path)
    if check and proc.returncode != 0:
        out_s = proc.stdout.decode("utf-8", errors="replace")[-2000:]
        err_s = proc.stderr.decode("utf-8", errors="replace")[-2000:]
        raise RuntimeError(f"Command failed on {hname} (rc={proc.returncode}): {redact_argv(argv)}\nSTDOUT:\n{out_s}\nSTDERR:\n{err_s}")
    return proc


def sync_file_to_host(host_name_or_ssh: str, local_path: Path, remote_path: str):
    h = HOST_BY_NAME.get(host_name_or_ssh) or HOST_BY_SSH[host_name_or_ssh]
    if h["name"] == "A366":
        dst = Path(remote_path)
        if dst.resolve() != local_path.resolve():
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(local_path, dst)
        return
    data = local_path.read_bytes()
    parent = str(Path(remote_path).parent)
    run_on_host(
        h["name"],
        ["bash", "-c", f"mkdir -p {shlex.quote(parent)} && cat > {shlex.quote(remote_path)}"],
        input_bytes=data,
        log_cmd=False,
    )


def collector_rpc(host_name: str, msg: dict, timeout: float = 15.0) -> dict:
    raw_line = (json.dumps(msg, separators=(",", ":")) + "\n").encode("utf-8")
    if host_name == "A366":
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
            s.settimeout(timeout)
            s.connect("/srv/ax-cluster/direct/runtime/collector.sock")
            s.sendall(raw_line)
            resp = s.makefile("rb").readline(8 * 1024 * 1024)
        out = json.loads(resp)
    else:
        py_one = (
            "import socket,sys; s=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM); "
            "s.settimeout(15.0); s.connect('/srv/ax-cluster/direct/runtime/collector.sock'); "
            "s.sendall(sys.stdin.buffer.read()); sys.stdout.buffer.write(s.makefile('rb').readline())"
        )
        p = run_on_host(host_name, ["python3", "-c", py_one], input_bytes=raw_line, timeout=timeout + 5.0, log_cmd=False)
        out = json.loads(p.stdout.decode("utf-8"))
    if not out.get("ok"):
        raise RuntimeError(f"Collector RPC failed on {host_name}: {out}")
    return out


def collector_mark_all(run_id: str, label: str, hosts: list[str] = ALL_HOST_NAMES) -> dict[str, dict]:
    # RustFS exports every second; the collector batches exports for up to one second.
    settle_started = time.monotonic()
    time.sleep(2.2)
    settled_seconds = time.monotonic() - settle_started
    with ThreadPoolExecutor(max_workers=len(hosts)) as pool:
        futs = {h: pool.submit(collector_rpc, h, {"action": "mark", "run_id": run_id, "label": label}) for h in hosts}
        samples = {h: futs[h].result()["sample"] for h in hosts}
    for sample in samples.values():
        sample["metric_export_settle_seconds"] = settled_seconds
    return samples


def collector_set_mounts(mounts_by_host: dict[str, list[dict]], hosts: list[str] = ALL_HOST_NAMES):
    with ThreadPoolExecutor(max_workers=len(hosts)) as pool:
        futs = [
            pool.submit(collector_rpc, h, {"action": "set_mounts", "mounts": mounts_by_host.get(h, [])})
            for h in hosts
        ]
        for f in futs:
            f.result()


def collector_pause_all(paused: bool, hosts: list[str] = ALL_HOST_NAMES):
    act = "pause" if paused else "resume"
    with ThreadPoolExecutor(max_workers=len(hosts)) as pool:
        futs = [pool.submit(collector_rpc, h, {"action": act}) for h in hosts]
        for f in futs:
            f.result()


def make_s3_client(endpoint: str | None = None):
    cfg_kwargs = dict(
        signature_version="s3v4",
        s3={"addressing_style": "path", "payload_signing_enabled": False},
        max_pool_connections=64,
        retries={"max_attempts": 8, "mode": "adaptive"},
    )
    try:
        cfg = BotoConfig(**cfg_kwargs, request_checksum_calculation="when_required", response_checksum_validation="when_required")
    except TypeError:
        cfg = BotoConfig(**cfg_kwargs)
    return boto3.client(
        "s3",
        endpoint_url=endpoint or RUSTFS_ENDPOINTS[0],
        aws_access_key_id=SECRETS["s3_access_key"],
        aws_secret_access_key=SECRETS["s3_secret_key"],
        region_name="us-east-1",
        config=cfg,
    )


def ensure_bucket(bucket: str, endpoint: str | None = None):
    if not bucket.startswith(RUN_PREFIX + "-"):
        raise ValueError(f"Bucket must start with axd1-: {bucket}")
    s3 = make_s3_client(endpoint)
    try:
        s3.head_bucket(Bucket=bucket)
    except Exception:
        s3.create_bucket(Bucket=bucket)


def delete_keys_parallel(bucket: str, keys: list[str], workers: int = 64):
    if not keys:
        return
    clients = [make_s3_client(ep) for ep in RUSTFS_ENDPOINTS]

    def _del_one(item: tuple[int, str]):
        idx, key = item
        last_exc = None
        for attempt in range(8):
            try:
                clients[(idx + attempt) % len(clients)].delete_object(Bucket=bucket, Key=key)
                return
            except Exception as exc:
                last_exc = exc
                time.sleep(min(4.0, 0.05 * (2 ** attempt) + random.uniform(0.0, 0.05)))
        if last_exc is not None:
            raise last_exc

    with ThreadPoolExecutor(max_workers=min(workers, max(1, len(keys)))) as pool:
        list(pool.map(_del_one, enumerate(keys)))


def delete_bucket_and_objects(bucket: str, endpoint: str | None = None):
    if not bucket.startswith(RUN_PREFIX + "-"):
        raise ValueError(f"Refusing to delete bucket without axd1- prefix: {bucket}")
    s3 = make_s3_client(endpoint)
    try:
        s3.head_bucket(Bucket=bucket)
    except Exception:
        return
    paginator = s3.get_paginator("list_objects_v2")
    all_keys: list[str] = []
    for page in paginator.paginate(Bucket=bucket):
        for obj in page.get("Contents", []):
            all_keys.append(obj["Key"])
    if all_keys:
        delete_keys_parallel(bucket, all_keys)
    try:
        s3.delete_bucket(Bucket=bucket)
    except Exception:
        pass


def delete_buckets_with_prefix(prefix: str):
    if not prefix.startswith(RUN_PREFIX + "-"):
        raise ValueError(f"Refusing prefix {prefix}")
    s3 = make_s3_client()
    resp = s3.list_buckets()
    for b in resp.get("Buckets", []):
        name = b["Name"]
        if name.startswith(prefix):
            delete_bucket_and_objects(name)


def redis_cli(*args: str, db: int = 0) -> str:
    env = os.environ.copy()
    env["REDISCLI_AUTH"] = SECRETS["redis_password"]
    p = subprocess.run(
        ["redis-cli", "-h", REDIS_HOST, "-p", str(REDIS_PORT), "-n", str(db), *args],
        env=env,
        check=True,
        capture_output=True,
        text=True,
        timeout=60,
    )
    return p.stdout


def redis_flush_db(db: int):
    if not (1 <= db <= 15):
        raise ValueError(f"Refusing FLUSHDB on db={db}; db 0 is shared namespace/forkredis!")
    redis_cli("FLUSHDB", "SYNC", db=db)


def redis_delete_pattern_db0(pattern: str) -> int:
    if RUN_NAMESPACE + "_" not in pattern and RUN_PREFIX + "-" not in pattern:
        raise ValueError(f"Refusing db0 pattern delete outside axd1 namespace: {pattern}")
    env = os.environ.copy()
    env["REDISCLI_AUTH"] = SECRETS["redis_password"]
    cursor = "0"
    deleted = 0
    while True:
        out = subprocess.run(
            ["redis-cli", "--raw", "-h", REDIS_HOST, "-p", str(REDIS_PORT), "-n", "0", "SCAN", cursor, "MATCH", pattern, "COUNT", "2000"],
            env=env,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.splitlines()
        if not out:
            break
        cursor = out[0].strip()
        keys = [k for k in out[1:] if k]
        if keys:
            for i in range(0, len(keys), 500):
                chunk = keys[i : i + 500]
                subprocess.run(
                    ["redis-cli", "-h", REDIS_HOST, "-p", str(REDIS_PORT), "-n", "0", "UNLINK", *chunk],
                    env=env,
                    check=True,
                    capture_output=True,
                )
                deleted += len(chunk)
        if cursor == "0":
            break
    return deleted


def endpoint_for_slot(slot: int, host_idx: int = 0) -> str:
    return RUSTFS_ENDPOINTS[(host_idx + slot - 1) % len(RUSTFS_ENDPOINTS)]


def generate_mounts_json(requests: list[dict], out_path: Path) -> dict:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    req_path = out_path.with_name(out_path.stem + "-requests.json")
    req_path.write_text(json.dumps(requests, indent=2) + "\n", encoding="utf-8")
    if out_path.exists():
        out_path.unlink()
    run_on_host(
        "A366",
        ["/usr/bin/python3", "-m", "bench.cluster.mounts", "--requests", str(req_path), "--out", str(out_path)],
    )
    return json.loads(out_path.read_text(encoding="utf-8"))


def start_mounts(mounts_path: Path, specs_by_id: dict[str, dict], audit_dir: Path, register_collector: bool = True) -> list[dict]:
    mounts_doc = json.loads(mounts_path.read_text(encoding="utf-8"))
    records = [r for r in mounts_doc["mounts"] if r["id"] in specs_by_id]
    hosts_involved = sorted({HOST_BY_SSH[r["host"]]["name"] for r in records})

    # Sync mounts_path to each involved host at the same path
    for hname in hosts_involved:
        sync_file_to_host(hname, mounts_path, str(mounts_path))

    def _start_one(rec: dict) -> dict:
        mid = rec["id"]
        hname = HOST_BY_SSH[rec["host"]]["name"]
        spec = dict(specs_by_id[mid])
        spec["host_name"] = hname
        local_spec = audit_dir / f"{mid}-spec.json"
        local_spec.parent.mkdir(parents=True, exist_ok=True)
        local_spec.write_text(json.dumps(spec, indent=2) + "\n", encoding="utf-8")
        remote_spec = f"/srv/ax-cluster/private/direct-mounts/{mid}-spec.json"
        sync_file_to_host(hname, local_spec, remote_spec)
        remote_audit = f"{RUN_ROOT}/hosts/{hname}/audits/{mid}-audit.json"
        p = run_on_host(
            hname,
            [
                "/usr/bin/python3",
                "/srv/ax-cluster/direct/scripts/mount_driver.py",
                "start",
                "--mounts",
                str(mounts_path),
                "--id",
                mid,
                "--spec",
                remote_spec,
                "--audit-out",
                remote_audit,
            ],
        )
        info = json.loads(p.stdout.decode("utf-8").strip().splitlines()[-1])
        info["host_name"] = hname
        info["host_ssh"] = rec["host"]
        info["slot"] = rec["slot"]
        # Retrieve audit JSON to coordinator audit_dir
        if hname == "A366":
            shutil.copy2(remote_audit, audit_dir / f"{mid}-audit.json")
        else:
            ap = run_on_host(hname, ["cat", remote_audit], log_cmd=False)
            (audit_dir / f"{mid}-audit.json").write_bytes(ap.stdout)
        return info

    with ThreadPoolExecutor(max_workers=min(32, max(1, len(records)))) as pool:
        started = list(pool.map(_start_one, records))

    if register_collector:
        by_host: dict[str, list[dict]] = {}
        for m in started:
            by_host.setdefault(m["host_name"], []).append(
                {
                    "id": m["id"],
                    "slot": m["slot"],
                    "engine": m["engine"],
                    "pid": m["pid"],
                    "cgroup": m["cgroup"],
                    "metrics_port": m["metrics_port"],
                    "mount_directory": m["mount_directory"],
                }
            )
        collector_set_mounts(by_host)
    return started


def wait_uploads(started_mounts: list[dict], timeout: float = 3600.0) -> list[dict]:
    def _wait_one(m: dict) -> dict:
        p = run_on_host(
            m["host_name"],
            [
                "/usr/bin/python3",
                "/srv/ax-cluster/direct/scripts/mount_driver.py",
                "wait-upload-zero",
                "--engine",
                m["engine"],
                "--port",
                str(m["metrics_port"]),
                "--timeout",
                str(timeout),
            ],
            timeout=timeout + 30.0,
        )
        d = json.loads(p.stdout.decode("utf-8").strip().splitlines()[-1])
        d["id"] = m["id"]
        d["host_name"] = m["host_name"]
        return d

    with ThreadPoolExecutor(max_workers=min(32, max(1, len(started_mounts)))) as pool:
        return list(pool.map(_wait_one, started_mounts))


def stop_mounts(started_mounts: list[dict], stop_dir: Path, clear_collector: bool = True, remove_cache: bool = False) -> list[dict]:
    if clear_collector:
        collector_set_mounts({})
    stop_dir.mkdir(parents=True, exist_ok=True)

    def _stop_one(m: dict) -> dict:
        mid = m["id"]
        hname = m["host_name"]
        remote_out = f"{RUN_ROOT}/hosts/{hname}/audits/{mid}-stop.json"
        cmd = [
            "/usr/bin/python3",
            "/srv/ax-cluster/direct/scripts/mount_driver.py",
            "stop",
            "--id",
            mid,
            "--engine",
            m["engine"],
            "--mount-dir",
            m["mount_directory"],
            "--unit",
            m["unit"],
            "--port",
            str(m["metrics_port"]),
            "--cgroup",
            m["cgroup"],
            "--out",
            remote_out,
        ]
        run_on_host(hname, cmd, check=False)
        if hname == "A366" and Path(remote_out).exists():
            shutil.copy2(remote_out, stop_dir / f"{mid}-stop.json")
            res = json.loads(Path(remote_out).read_text(encoding="utf-8"))
        else:
            p = run_on_host(hname, ["cat", remote_out], check=False, log_cmd=False)
            if p.returncode == 0 and p.stdout:
                (stop_dir / f"{mid}-stop.json").write_bytes(p.stdout)
                res = json.loads(p.stdout.decode("utf-8"))
            else:
                res = {"id": mid, "unmounted_cleanly": False}
        if remove_cache and m.get("cache_root", "").startswith(str(RUN_ROOT / "cache") + "/"):
            run_on_host(hname, ["rm", "-rf", m["cache_root"]], check=False, log_cmd=False)
        return res

    with ThreadPoolExecutor(max_workers=min(32, max(1, len(started_mounts)))) as pool:
        return list(pool.map(_stop_one, started_mounts))


def extract_buckets_32(hist_obj: dict) -> list[int]:
    """Supports both `buckets: [c0..c31]` and `bins: [[le_us, count], ...]` log2-us-v1 formats."""
    if "buckets" in hist_obj and len(hist_obj["buckets"]) == 32:
        return [int(x) for x in hist_obj["buckets"]]
    out = [0] * 32
    for le_us, cnt in hist_obj.get("bins", []):
        le_us = int(le_us)
        cnt = int(cnt)
        if le_us == 0:
            idx = 0
        elif le_us >= (1 << 31):
            idx = 31
        else:
            # le_us = (1 << idx) - 1
            idx = (le_us + 1).bit_length() - 1
            idx = max(0, min(31, idx))
        out[idx] += cnt
    return out


def merge_log2_histograms(hist_objs: list[dict]) -> dict:
    merged = [0] * 32
    max_us = 0.0
    min_us = None
    for h in hist_objs:
        if not h:
            continue
        b = extract_buckets_32(h)
        for i in range(32):
            merged[i] += b[i]
        if h.get("max_us") is not None:
            max_us = max(max_us, float(h["max_us"]))
        if h.get("min_us") is not None and h.get("count", 0) > 0:
            v = float(h["min_us"])
            min_us = v if min_us is None else min(min_us, v)
    tot = sum(merged)

    def quantile_upper_us(q: float) -> float:
        if tot == 0:
            return 0.0
        target = max(1, int((tot * q) + 0.999999999))
        acc = 0
        for i, c in enumerate(merged):
            acc += c
            if acc >= target:
                if i == 0:
                    return 1.0
                if i == 31:
                    return max_us if max_us > 0 else float(1 << 31)
                return float(1 << i)
        return max_us

    return {
        "schema": "log2-us-v1",
        "count": tot,
        "min_us": min_us or 0.0,
        "max_us": max_us,
        "p50_us": quantile_upper_us(0.50),
        "p95_us": quantile_upper_us(0.95),
        "p99_us": quantile_upper_us(0.99),
        "buckets": merged,
    }


def summarize_phase_delta(snap_start: dict[str, dict], snap_end: dict[str, dict]) -> dict:
    """Computes cluster-wide RustFS S3 request/byte deltas, physical disk deltas, and Redis command deltas."""
    rustfs_hosts = ("A672", "A601", "A635", "A368")
    phys_read_bytes = 0
    phys_write_bytes = 0
    rustfs_cpu_seconds = 0.0
    rustfs_metric_deltas: dict[str, float] = {}

    for h in rustfs_hosts:
        s0 = snap_start.get(h, {})
        s1 = snap_end.get(h, {})
        d0 = s0.get("disks", {})
        d1 = s1.get("disks", {})
        phys_read_bytes += max(0, int(d1.get("physical_read_bytes", 0)) - int(d0.get("physical_read_bytes", 0)))
        phys_write_bytes += max(0, int(d1.get("physical_write_bytes", 0)) - int(d0.get("physical_write_bytes", 0)))
        p0 = (s0.get("rustfs") or {}).get("process") or {}
        p1 = (s1.get("rustfs") or {}).get("process") or {}
        if "cpu_total_seconds" in p0 and "cpu_total_seconds" in p1:
            rustfs_cpu_seconds += max(0.0, float(p1["cpu_total_seconds"]) - float(p0["cpu_total_seconds"]))
        m0 = (s0.get("rustfs") or {}).get("metrics") or {}
        m1 = (s1.get("rustfs") or {}).get("metrics") or {}
        for k, v1 in m1.items():
            v0 = m0.get(k, 0.0)
            if v1 >= v0:
                rustfs_metric_deltas[k] = rustfs_metric_deltas.get(k, 0.0) + (v1 - v0)

    r0 = (snap_start.get("A355", {}).get("redis")) or {}
    r1 = (snap_end.get("A355", {}).get("redis")) or {}
    redis_cmds = max(0, int(r1.get("total_commands_processed", 0)) - int(r0.get("total_commands_processed", 0)))
    redis_net_in = max(0, int(r1.get("total_net_input_bytes", 0)) - int(r0.get("total_net_input_bytes", 0)))
    redis_net_out = max(0, int(r1.get("total_net_output_bytes", 0)) - int(r0.get("total_net_output_bytes", 0)))
    lua_calls = 0
    c0 = r0.get("cmdstat") or {}
    c1 = r1.get("cmdstat") or {}
    for cmd_name in ("eval", "evalsha", "eval_ro", "evalsha_ro"):
        lua_calls += max(0, int((c1.get(cmd_name) or {}).get("calls", 0)) - int((c0.get(cmd_name) or {}).get("calls", 0)))

    t0 = snap_start.get("A355", {}).get("tikv") or {}
    t1 = snap_end.get("A355", {}).get("tikv") or {}
    tikv_delta = {key: value - t0.get(key, 0) for key, value in t1.items()
                  if isinstance(value, (int, float)) and isinstance(t0.get(key, 0), (int, float))}
    return {
        "tikv_metrics_delta": tikv_delta,
        "backend_physical_read_bytes": phys_read_bytes,
        "backend_physical_write_bytes": phys_write_bytes,
        **rustfs_payload_metrics(rustfs_metric_deltas),
        "rustfs_cpu_seconds": round(rustfs_cpu_seconds, 6),
        "rustfs_metrics_delta": rustfs_metric_deltas,
        "redis_commands_delta": redis_cmds,
        "redis_lua_calls_delta": lua_calls,
        "redis_net_input_bytes_delta": redis_net_in,
        "redis_net_output_bytes_delta": redis_net_out,
        "redis_used_memory_end": r1.get("used_memory", 0),
        "redis_evicted_keys_end": r1.get("evicted_keys", 0),
    }


def extract_json_from_cli(stdout_bytes: bytes):
    txt = stdout_bytes.decode("utf-8", errors="replace")
    # Scan from last '{' or '[' backwards/forwards to parse embedded JSON cleanly
    for start_idx, ch in enumerate(txt):
        if ch not in "{[":
            continue
        for end_idx in range(len(txt), start_idx, -1):
            if txt[end_idx - 1] not in "}]":
                continue
            try:
                return json.loads(txt[start_idx:end_idx])
            except Exception:
                continue
    raise ValueError(f"Could not parse JSON from CLI output:\n{txt[-1000:]}")


def update_status(group: str, condition_id: str, system: str, repeat_num: int, state: str, details: dict | None = None):
    status_path = RUN_ROOT / "status.json"
    with _STATUS_LOCK:
        doc = json.loads(status_path.read_text(encoding="utf-8"))
        cond_entry = doc["groups"][group]["conditions"][condition_id]
        run_key = f"{system}/r{repeat_num}"
        cond_entry.setdefault("runs", {})[run_key] = {
            "state": state,
            "updated_at": utc_iso(),
            **(details or {}),
        }
        # Recompute condition state
        expected_systems = cond_entry["systems"]
        expected_reps = cond_entry.get("repetitions", 3)
        states = [
            cond_entry.get("runs", {}).get(f"{s}/r{r}", {}).get("state", "planned")
            for s in expected_systems
            for r in range(1, expected_reps + 1)
        ]
        if all(st == "passed" for st in states):
            cond_entry["state"] = "passed"
        elif all(st == "pending-feature" for st in states):
            cond_entry["state"] = "pending-feature"
        elif any(st == "failed" for st in states):
            cond_entry["state"] = "failed"
        elif any(st in {"running", "passed"} for st in states):
            cond_entry["state"] = "running"
        else:
            cond_entry["state"] = "planned"

        # Recompute group summary counts
        g_conds = doc["groups"][group]["conditions"].values()
        doc["groups"][group]["completed_conditions"] = sum(1 for c in g_conds if c["state"] == "passed")
        doc["groups"][group]["pending_feature_conditions"] = sum(1 for c in g_conds if c["state"] == "pending-feature")
        doc["groups"][group]["completed_runs"] = sum(
            1 for c in g_conds for r in c.get("runs", {}).values() if r.get("state") == "passed"
        )
        doc["groups"][group]["completed_system_runs"] = doc["groups"][group]["completed_runs"]
        doc["phase"] = "running"
        if "totals" in doc:
            doc["totals"]["completed_conditions"] = sum(
                g.get("completed_conditions", 0) for g in doc["groups"].values()
            )
            doc["totals"]["completed_runs"] = sum(
                g.get("completed_runs", 0) for g in doc["groups"].values()
            )
        doc["updated_at"] = utc_iso()
        tmp = status_path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        tmp.replace(status_path)
