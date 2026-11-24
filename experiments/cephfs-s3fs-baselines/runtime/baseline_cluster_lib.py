#!/usr/bin/env python3
"""Shared hash-free coordinator library on node-coord for CephFS and s3fs baselines."""

from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
import datetime as dt
import json
import os
from pathlib import Path
import random
import shutil
import socket
import subprocess
import sys
import threading
import time

sys.path.insert(0, str(Path(__file__).resolve().parent))
import baseline_context as ctx
import baseline_mounts as mounts_mod

RUNTIME_CFG = ctx.load_baseline_runtime()
PROFILES_CFG = mounts_mod.load_profiles()
RUSTFS_BACKENDS = ctx.load_rustfs_backends()

RUN_ID = RUNTIME_CFG["run_id"]
BUCKET_PREFIX = RUNTIME_CFG["bucket_prefix"]
CEPHFS_ROOT_PREFIX = RUNTIME_CFG["cephfs_root_prefix"].rstrip("/")
BASELINE_ROOT = Path(RUNTIME_CFG["remote_baseline_root"])
SCRIPTS_DIR = BASELINE_ROOT / "scripts"
RUN_ROOT = Path(RUNTIME_CFG["remote_runs_root"]) / RUN_ID
INPUT_DIR = Path(RUNTIME_CFG["shared_inputs_dir"])
BIN_DIR = Path(RUNTIME_CFG["shared_bin_dir"])
PRIVATE_DIR = Path(RUNTIME_CFG["private_dir"])
SSH_KEY = PRIVATE_DIR / "cluster_ed25519"
MEASUREMENT_LOCK_PATH = Path(RUNTIME_CFG["measurement_lock_path"])
COLLECTOR_SOCK = Path(RUNTIME_CFG["collector_socket_path"])

HOSTS = [
    {"name": "node-coord", "ssh": "cluster-node-1", "ip": "175.168.3.66", "slots": 52, "active": 48},
    {"name": "node-meta", "ssh": "cluster-node-2", "ip": "175.168.3.55", "slots": 40, "active": 32},
    {"name": "node-store-1", "ssh": "cluster-node-3", "ip": "175.168.6.72", "slots": 16, "active": 16},
    {"name": "node-store-2", "ssh": "cluster-node-4", "ip": "175.168.6.1", "slots": 48, "active": 40},
    {"name": "node-store-3", "ssh": "cluster-node-5", "ip": "175.168.6.35", "slots": 48, "active": 40},
    {"name": "node-store-4", "ssh": "cluster-node-6", "ip": "175.168.3.68", "slots": 20, "active": 16},
]
HOST_BY_NAME = {h["name"]: h for h in HOSTS}
HOST_BY_SSH = {h["ssh"]: h for h in HOSTS}
ALL_HOST_NAMES = [h["name"] for h in HOSTS]
STORAGE_HOST_NAMES = ("node-store-1", "node-store-2", "node-store-3", "node-store-4")

RUSTFS_BACKEND = RUNTIME_CFG["default_s3fs_backend"]
RUSTFS_ENDPOINTS = list(RUSTFS_BACKENDS[RUSTFS_BACKEND]["endpoints"])
COEXIST_SAFE = False


def set_coexist_safe(enabled: bool) -> None:
    global COEXIST_SAFE
    COEXIST_SAFE = bool(enabled)

_CMD_LOCK = threading.Lock()
_SSH_OPEN_LIMIT = threading.BoundedSemaphore(2)
_SSH_HOST_LOCKS = {h["name"]: threading.Lock() for h in HOSTS}
_SSH_CHANNELS = {h["name"]: threading.BoundedSemaphore(4) for h in HOSTS}


def utc_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def select_rustfs_backend(name: str) -> None:
    global RUSTFS_BACKEND
    RUSTFS_BACKEND = name
    RUSTFS_ENDPOINTS[:] = RUSTFS_BACKENDS[name]["endpoints"]


def endpoint_for_slot(slot: int, host_idx: int = 0) -> str:
    return RUSTFS_ENDPOINTS[(slot + host_idx) % len(RUSTFS_ENDPOINTS)]


def load_s3_secrets() -> dict[str, str]:
    env_p = Path(RUNTIME_CFG["rustfs_env_path"])
    if not env_p.is_file():
        return {"s3_access_key": "", "s3_secret_key": ""}
    kv: dict[str, str] = {}
    for ln in env_p.read_text(encoding="utf-8").splitlines():
        if "=" in ln and not ln.startswith("#"):
            k, v = ln.split("=", 1)
            kv[k.strip()] = v.strip()
    return {
        "s3_access_key": kv.get("RUSTFS_ACCESS_KEY", ""),
        "s3_secret_key": kv.get("RUSTFS_SECRET_KEY", ""),
    }


def redact_argv(argv: list[str]) -> list[str]:
    sec = load_s3_secrets()
    out: list[str] = []
    for tok in argv:
        s = str(tok)
        for v in sec.values():
            if v and len(v) >= 6:
                s = s.replace(v, "<redacted>")
        out.append(s)
    return out


def assert_cluster_idle_for_baselines() -> None:
    for status_file in RUNTIME_CFG.get("guard_active_main_runs", []):
        p = Path(status_file)
        if p.is_file():
            doc = json.loads(p.read_text(encoding="utf-8"))
            if doc.get("current"):
                raise RuntimeError(
                    f"Active main suite job {doc['current']!r} detected in {status_file}; refusing to interfere."
                )
    for unit in RUNTIME_CFG.get("guard_main_units", ["ax-run-002"]):
        act_p = subprocess.run(
            ["systemctl", "is-active", unit],
            capture_output=True,
            text=True,
            check=False,
        )
        if act_p.stdout.strip() == "active":
            raise RuntimeError(f"Active main suite service {unit!r} is still running on node-coord; wait until it finishes.")


class MeasurementLock:
    def __init__(self, path: Path = MEASUREMENT_LOCK_PATH) -> None:
        self.path = path
        self._fp = None

    def __enter__(self):
        if COEXIST_SAFE:
            return self
        import fcntl

        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._fp = self.path.open("a+", encoding="utf-8")
        try:
            fcntl.flock(self._fp.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            self._fp.close()
            self._fp = None
            raise RuntimeError(f"Measurement lock {self.path} is currently held by another benchmark") from exc
        self._fp.seek(0)
        self._fp.truncate()
        self._fp.write(json.dumps({"holder": "cephfs-s3fs-baselines", "run_id": RUN_ID, "acquired_at": utc_iso()}) + "\n")
        self._fp.flush()
        return self

    def __exit__(self, exc_type, exc, tb):
        import fcntl

        if self._fp is not None:
            try:
                fcntl.flock(self._fp.fileno(), fcntl.LOCK_UN)
            finally:
                self._fp.close()
                self._fp = None


def _ssh_options(host: dict) -> list[str]:
    control_dir = BASELINE_ROOT / "runtime" / "ssh"
    control_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    control = str(control_dir / host["name"])
    flags = [
        "-i",
        str(SSH_KEY),
        "-o",
        "StrictHostKeyChecking=accept-new",
        "-o",
        "BatchMode=yes",
        "-o",
        "ConnectTimeout=15",
        "-o",
        "ServerAliveInterval=20",
        "-o",
        "ServerAliveCountMax=3",
        "-o",
        f"ControlPath={control}",
        "-o",
        "ControlPersist=1800",
    ]
    target = f"root@{host['ip']}"
    with _SSH_HOST_LOCKS[host["name"]]:
        checked = subprocess.run(["ssh", *flags, "-O", "check", target], capture_output=True, check=False)
        if checked.returncode != 0:
            with _SSH_OPEN_LIMIT:
                for attempt in range(3):
                    try:
                        subprocess.run(
                            ["ssh", *flags, "-MNf", "-o", "ControlMaster=yes", target],
                            capture_output=True,
                            check=True,
                            timeout=25,
                        )
                        break
                    except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
                        if attempt == 2:
                            raise
                        time.sleep(3 * (attempt + 1))
    return [*flags, "-o", "ControlMaster=auto"]


def log_command(host: str, argv: list[str], rc: int, started_at: str, ended_at: str, elapsed_s: float) -> None:
    log_file = RUN_ROOT / "commands.jsonl"
    log_file.parent.mkdir(parents=True, exist_ok=True)
    rec = {
        "host": host,
        "argv": redact_argv(argv),
        "returncode": rc,
        "started_at": started_at,
        "ended_at": ended_at,
        "elapsed_seconds": round(elapsed_s, 6),
    }
    with _CMD_LOCK:
        with log_file.open("a", encoding="utf-8") as fp:
            fp.write(json.dumps(rec) + "\n")


def run_on_host(
    host_name: str,
    argv: list[str],
    *,
    timeout: float = 1800.0,
    check: bool = True,
    log_cmd: bool = True,
    extra_env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess:
    import shlex

    started_at = utc_iso()
    t0 = time.monotonic()
    str_argv = [str(x) for x in argv]
    if host_name == "node-coord":
        env = os.environ.copy()
        env["PYTHONPATH"] = f"{BASELINE_ROOT}:{BASELINE_ROOT / 'scripts'}"
        if extra_env:
            env.update(extra_env)
        p = subprocess.run(str_argv, capture_output=True, timeout=timeout, check=False, env=env)
    else:
        host = HOST_BY_NAME[host_name]
        opts = _ssh_options(host)
        env_prefix = f"PYTHONPATH={shlex.quote(str(BASELINE_ROOT))}:{shlex.quote(str(BASELINE_ROOT / 'scripts'))} "
        if extra_env:
            for k, v in extra_env.items():
                env_prefix += f"{k}={shlex.quote(v)} "
        remote_cmd = env_prefix + " ".join(shlex.quote(x) for x in str_argv)
        with _SSH_CHANNELS[host_name]:
            p = subprocess.run(
                ["ssh", *opts, f"root@{host['ip']}", remote_cmd],
                capture_output=True,
                timeout=timeout,
                check=False,
            )
    elapsed = time.monotonic() - t0
    if log_cmd:
        log_command(host_name, str_argv, p.returncode, started_at, utc_iso(), elapsed)
    if check and p.returncode != 0:
        stderr_txt = p.stderr.decode("utf-8", errors="replace")[-1500:]
        stdout_txt = p.stdout.decode("utf-8", errors="replace")[-1500:]
        raise RuntimeError(
            f"Command failed on {host_name} (rc={p.returncode}): {redact_argv(str_argv)}\n"
            f"STDOUT:\n{stdout_txt}\nSTDERR:\n{stderr_txt}"
        )
    return p


def sync_file_to_host(host_name: str, local_path: Path, remote_path: str) -> None:
    import shlex

    if host_name == "node-coord":
        dst = Path(remote_path)
        if dst.resolve() != local_path.resolve():
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(local_path, dst)
        return
    host = HOST_BY_NAME[host_name]
    opts = _ssh_options(host)
    parent = str(Path(remote_path).parent)
    remote_cmd = f"mkdir -p {shlex.quote(parent)} && cat > {shlex.quote(remote_path)}"
    with _SSH_CHANNELS[host_name]:
        subprocess.run(
            ["ssh", *opts, f"root@{host['ip']}", remote_cmd],
            input=local_path.read_bytes(),
            capture_output=True,
            check=True,
            timeout=120,
        )


# ==============================================================================
# Collector RPC & Phase Delta Summarization
# ==============================================================================

def collector_rpc(host_name: str, payload: dict) -> dict:
    if host_name == "node-coord":
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.settimeout(10.0)
        s.connect(str(COLLECTOR_SOCK))
        with s:
            s.sendall((json.dumps(payload) + "\n").encode("utf-8"))
            buf = b""
            while chunk := s.recv(65536):
                buf += chunk
                if b"\n" in buf:
                    break
        resp = json.loads(buf.decode("utf-8").strip())
    else:
        py = (
            "import json, socket\n"
            f"s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)\n"
            "s.settimeout(10.0)\n"
            f"s.connect({str(COLLECTOR_SOCK)!r})\n"
            f"s.sendall((json.dumps({payload!r}) + '\\n').encode())\n"
            "buf = b''\n"
            "while True:\n"
            "  c = s.recv(65536)\n"
            "  if not c: break\n"
            "  buf += c\n"
            "  if b'\\n' in buf: break\n"
            "s.close()\n"
            "print(buf.decode().strip())\n"
        )
        p = run_on_host(host_name, ["/usr/bin/python3", "-c", py], log_cmd=False)
        resp = json.loads(p.stdout.decode("utf-8").strip().splitlines()[-1])
    if not resp.get("ok"):
        raise RuntimeError(f"Baseline collector RPC failed on {host_name}: {resp}")
    return resp


def collector_mark_all(tag: str, label: str, hosts: list[str] | None = None) -> dict[str, dict]:
    target_hosts = hosts or ALL_HOST_NAMES
    settle_started = time.monotonic()
    time.sleep(2.2)
    settled_seconds = time.monotonic() - settle_started
    with ThreadPoolExecutor(max_workers=len(target_hosts)) as pool:
        futs = {
            h: pool.submit(collector_rpc, h, {"action": "mark", "tag": tag, "label": label})
            for h in target_hosts
        }
        samples = {h: futs[h].result()["snapshot"] for h in target_hosts}
    for sample in samples.values():
        sample["metric_export_settle_seconds"] = settled_seconds
    return samples


def collector_set_mounts(mounts_by_host: dict[str, list[dict]], hosts: list[str] | None = None) -> None:
    target_hosts = hosts or ALL_HOST_NAMES
    with ThreadPoolExecutor(max_workers=len(target_hosts)) as pool:
        futs = [
            pool.submit(collector_rpc, h, {"action": "set_mounts", "mounts": mounts_by_host.get(h, [])})
            for h in target_hosts
        ]
        for f in futs:
            f.result()


def _rustfs_payload_metrics(metrics: dict[str, float]) -> dict:
    import re

    totals: dict[str, float] = {}
    methods: dict[str, float] = {}
    for key, value in metrics.items():
        name = key.split("{", 1)[0]
        totals[name] = totals.get(name, 0.0) + value
        if name == "rustfs_s3_http_requests_total":
            match = re.search(r'(?:\{|,)method="([^"\n]+)"', key)
            method = match.group(1) if match else "ALL"
            methods[method] = methods.get(method, 0.0) + value

    def _cnt(name: str) -> int:
        return int(round(totals.get(name, 0.0)))

    return {
        "rustfs_s3_get_bytes": _cnt("rustfs_s3_get_object_size_bytes_sum"),
        "rustfs_s3_put_bytes": _cnt("rustfs_s3_put_object_size_bytes_sum"),
        "rustfs_s3_get_requests": _cnt("rustfs_s3_get_object_total"),
        "rustfs_s3_put_requests": _cnt("rustfs_s3_put_object_total"),
        "rustfs_s3_requests_by_method": {m: int(round(v)) for m, v in methods.items()},
        "rustfs_http_response_bytes": _cnt("rustfs_http_server_response_body_bytes_total"),
        "rustfs_http_request_bytes": _cnt("rustfs_http_server_request_body_bytes_total"),
        "rustfs_shard_read_bytes": _cnt("rustfs_io_get_object_shard_read_observed_bytes_total"),
        "rustfs_internode_sent_bytes": _cnt("rustfs_system_network_internode_sent_bytes_total"),
        "rustfs_internode_received_bytes": _cnt("rustfs_system_network_internode_recv_bytes_total"),
    }


def summarize_phase_delta(snap_start: dict[str, dict], snap_end: dict[str, dict]) -> dict:
    phys_read_bytes = 0
    phys_write_bytes = 0
    net_rx_bytes = 0
    net_tx_bytes = 0
    rustfs_prom_delta: dict[str, float] = {}
    ceph_daemon_delta: dict[str, float] = {}

    for h in STORAGE_HOST_NAMES:
        s0 = snap_start.get(h) or {}
        s1 = snap_end.get(h) or {}
        d0 = s0.get("disk") or {}
        d1 = s1.get("disk") or {}
        phys_read_bytes += max(0, int(d1.get("nvme_read_bytes", 0)) - int(d0.get("nvme_read_bytes", 0)))
        phys_write_bytes += max(0, int(d1.get("nvme_write_bytes", 0)) - int(d0.get("nvme_write_bytes", 0)))

        m0 = s0.get("rustfs_prom") or {}
        m1 = s1.get("rustfs_prom") or {}
        for k, v1 in m1.items():
            v0 = m0.get(k, 0.0)
            if v1 >= v0 and v1 != v0:
                rustfs_prom_delta[k] = rustfs_prom_delta.get(k, 0.0) + (v1 - v0)

    for h in ALL_HOST_NAMES:
        s0 = snap_start.get(h) or {}
        s1 = snap_end.get(h) or {}
        n0 = s0.get("net_eth0") or {}
        n1 = s1.get("net_eth0") or {}
        net_rx_bytes += max(0, int(n1.get("rx_bytes", 0)) - int(n0.get("rx_bytes", 0)))
        net_tx_bytes += max(0, int(n1.get("tx_bytes", 0)) - int(n0.get("tx_bytes", 0)))

        c0 = s0.get("ceph_daemons") or {}
        c1 = s1.get("ceph_daemons") or {}
        for k, v1 in c1.items():
            v0 = c0.get(k, 0.0)
            if v1 >= v0 and v1 != v0:
                ceph_daemon_delta[k] = ceph_daemon_delta.get(k, 0.0) + (v1 - v0)

    return {
        "backend_physical_read_bytes": phys_read_bytes,
        "backend_physical_write_bytes": phys_write_bytes,
        "cluster_eth0_rx_bytes_delta": net_rx_bytes,
        "cluster_eth0_tx_bytes_delta": net_tx_bytes,
        "ceph_osd_read_ops_delta": int(round(ceph_daemon_delta.get("ceph_osd_op_r", 0.0))),
        "ceph_osd_write_ops_delta": int(round(ceph_daemon_delta.get("ceph_osd_op_w", 0.0))),
        "ceph_osd_read_bytes_delta": int(round(ceph_daemon_delta.get("ceph_osd_op_r_out_bytes", 0.0))),
        "ceph_osd_write_bytes_delta": int(round(ceph_daemon_delta.get("ceph_osd_op_w_in_bytes", 0.0))),
        "ceph_mds_requests_delta": int(round(ceph_daemon_delta.get("ceph_mds_request", 0.0))),
        "ceph_daemon_delta": ceph_daemon_delta,
        **_rustfs_payload_metrics(rustfs_prom_delta),
        "rustfs_metrics_delta": rustfs_prom_delta,
    }


def evict_storage_page_cache() -> None:
    """Drop OS page cache on the 4 storage nodes outside timed windows before cold read passes."""
    if COEXIST_SAFE:
        return
    def _drop(h: str) -> None:
        run_on_host(h, ["sh", "-c", "sync && echo 3 > /proc/sys/vm/drop_caches"], timeout=60, log_cmd=False)

    with ThreadPoolExecutor(max_workers=len(STORAGE_HOST_NAMES)) as pool:
        list(pool.map(_drop, STORAGE_HOST_NAMES))


# ==============================================================================
# Mount Lifecycle Dispatch (1..64 mounts via host-local batch runner)
# ==============================================================================

def generate_mounts_json(requests: list[dict], out_path: Path) -> dict:
    doc = mounts_mod.build_mounts_document(requests, PROFILES_CFG)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")
    return doc


def batch_start_mounts(
    mounts_path: Path,
    specs_by_id: dict[str, dict],
    run_tag: str,
    *,
    register_collector: bool = True,
) -> list[dict]:
    mounts_doc = json.loads(mounts_path.read_text(encoding="utf-8"))
    rec_by_id = {r["id"]: r for r in mounts_doc["mounts"] if r["id"] in specs_by_id}
    by_host: dict[str, dict[str, dict]] = defaultdict(dict)
    for mid, rec in rec_by_id.items():
        hname = HOST_BY_SSH[rec["host"]]["name"]
        sp = dict(specs_by_id[mid])
        sp["host_name"] = hname
        sp["host_ssh"] = rec["host"]
        sp["slot"] = rec["slot"]
        by_host[hname][mid] = sp

    hosts = sorted(by_host.keys())

    def _start_host(hname: str) -> list[dict]:
        remote_mounts = f"{RUN_ROOT}/hosts/{hname}/batch/{run_tag}-mounts.json"
        remote_specs = f"{RUN_ROOT}/hosts/{hname}/batch/{run_tag}-specs.json"
        remote_audits = f"{RUN_ROOT}/hosts/{hname}/audits/{run_tag}"
        local_tmp = RUN_ROOT / "tmp" / f"{run_tag}-{hname}-specs.json"
        local_tmp.parent.mkdir(parents=True, exist_ok=True)
        local_tmp.write_text(json.dumps(by_host[hname], indent=2) + "\n", encoding="utf-8")

        sync_file_to_host(hname, mounts_path, remote_mounts)
        sync_file_to_host(hname, local_tmp, remote_specs)
        local_tmp.unlink(missing_ok=True)

        p = run_on_host(
            hname,
            [
                "/usr/bin/python3",
                str(SCRIPTS_DIR / "baseline_host_batch.py"),
                "start-batch",
                "--mounts",
                remote_mounts,
                "--specs",
                remote_specs,
                "--audit-dir",
                remote_audits,
            ],
            timeout=600.0,
        )
        run_on_host(hname, ["rm", "-f", remote_mounts, remote_specs], check=False, log_cmd=False)
        return json.loads(p.stdout.decode("utf-8").strip().splitlines()[-1])["mounts"]

    with ThreadPoolExecutor(max_workers=max(1, len(hosts))) as pool:
        per_host = list(pool.map(_start_host, hosts))
    started = [m for sub in per_host for m in sub]

    if register_collector:
        col_map: dict[str, list[dict]] = defaultdict(list)
        for m in started:
            col_map[m["host_name"]].append(
                {
                    "id": m["id"],
                    "slot": m["slot"],
                    "engine": m["engine"],
                    "pid": m["pid"],
                    "cgroup": m["cgroup"],
                    "mount_directory": m["mount_directory"],
                }
            )
        collector_set_mounts(col_map)
    return started


def batch_stop_mounts(
    started_mounts: list[dict],
    run_tag: str,
    *,
    clear_collector: bool = True,
    remove_cache: bool = True,
) -> list[dict]:
    if not started_mounts:
        return []
    if clear_collector:
        collector_set_mounts({})

    by_host: dict[str, list[dict]] = defaultdict(list)
    for m in started_mounts:
        by_host[m["host_name"]].append(m)
    hosts = sorted(by_host.keys())

    def _stop_host(hname: str) -> list[dict]:
        remote_info = f"{RUN_ROOT}/hosts/{hname}/batch/{run_tag}-stop-info.json"
        remote_stops = f"{RUN_ROOT}/hosts/{hname}/stops/{run_tag}"
        local_tmp = RUN_ROOT / "tmp" / f"{run_tag}-{hname}-stop.json"
        local_tmp.parent.mkdir(parents=True, exist_ok=True)
        local_tmp.write_text(json.dumps(by_host[hname], indent=2) + "\n", encoding="utf-8")
        sync_file_to_host(hname, local_tmp, remote_info)
        local_tmp.unlink(missing_ok=True)

        argv = [
            "/usr/bin/python3",
            str(SCRIPTS_DIR / "baseline_host_batch.py"),
            "stop-batch",
            "--mounts-info",
            remote_info,
            "--stop-dir",
            remote_stops,
        ]
        if remove_cache:
            argv.append("--remove-cache")
        p = run_on_host(hname, argv, timeout=600.0)
        run_on_host(hname, ["rm", "-f", remote_info], check=False, log_cmd=False)
        return json.loads(p.stdout.decode("utf-8").strip().splitlines()[-1])["stopped"]

    with ThreadPoolExecutor(max_workers=max(1, len(hosts))) as pool:
        per_host = list(pool.map(_stop_host, hosts))
    return [r for sub in per_host for r in sub]


def wait_uploads(started_mounts: list[dict], timeout: float = 3600.0) -> list[dict]:
    def _one(m: dict) -> dict:
        p = run_on_host(
            m["host_name"],
            [
                "/usr/bin/python3",
                str(SCRIPTS_DIR / "baseline_mount_driver.py"),
                "wait-upload-zero",
                "--engine",
                m["engine"],
                "--mount-dir",
                m["mount_directory"],
                "--admin-socket",
                m.get("admin_socket") or "",
                "--cache-root",
                m.get("cache_root") or "",
                "--timeout",
                str(timeout),
            ],
            timeout=timeout + 60.0,
        )
        d = json.loads(p.stdout.decode("utf-8").strip().splitlines()[-1])
        d["id"] = m["id"]
        d["host_name"] = m["host_name"]
        return d

    with ThreadPoolExecutor(max_workers=min(32, max(1, len(started_mounts)))) as pool:
        return list(pool.map(_one, started_mounts))


def read_mount_metrics(m: dict) -> dict:
    p = run_on_host(
        m["host_name"],
        [
            "/usr/bin/python3",
            str(SCRIPTS_DIR / "baseline_mount_driver.py"),
            "metrics",
            "--engine",
            m["engine"],
            "--admin-socket",
            m.get("admin_socket") or "",
            "--cache-root",
            m.get("cache_root") or "",
            "--cgroup",
            m.get("cgroup") or "",
        ],
        log_cmd=False,
    )
    return json.loads(p.stdout.decode("utf-8").strip().splitlines()[-1])


# ==============================================================================
# S3 Helpers for s3fs (Bucket management, Subpath markers, Usage & Cleanup)
# ==============================================================================

def make_s3_client(endpoint: str | None = None):
    import boto3
    from botocore.config import Config as BotoConfig

    sec = load_s3_secrets()
    cfg = BotoConfig(
        signature_version="s3v4",
        s3={"addressing_style": "path", "payload_signing_enabled": False},
        max_pool_connections=64,
        retries={"max_attempts": 8, "mode": "adaptive"},
    )
    return boto3.client(
        "s3",
        endpoint_url=endpoint or RUSTFS_ENDPOINTS[0],
        aws_access_key_id=sec["s3_access_key"],
        aws_secret_access_key=sec["s3_secret_key"],
        region_name="us-east-1",
        config=cfg,
    )


def ensure_bucket(bucket: str, endpoint: str | None = None) -> None:
    if not bucket.startswith(BUCKET_PREFIX + "-"):
        raise ValueError(f"Baseline bucket must start with {BUCKET_PREFIX}-: {bucket}")
    s3 = make_s3_client(endpoint)
    try:
        s3.head_bucket(Bucket=bucket)
    except Exception:
        s3.create_bucket(Bucket=bucket)


def ensure_s3fs_dir_marker(bucket: str, prefix: str, endpoint: str | None = None) -> None:
    """Create a 0-byte directory marker object (<prefix>/) with POSIX 0755 mode for s3fs subpath mounts."""
    norm = prefix.strip("/")
    if not norm:
        return
    s3 = make_s3_client(endpoint)
    now_sec = str(int(time.time()))
    s3.put_object(
        Bucket=bucket,
        Key=f"{norm}/",
        Body=b"",
        ContentType="application/x-directory",
        Metadata={"mode": str(0o040755), "mtime": now_sec, "ctime": now_sec, "uid": "0", "gid": "0"},
    )


def list_all_keys(bucket: str, prefix: str = "") -> list[str]:
    s3 = make_s3_client()
    paginator = s3.get_paginator("list_objects_v2")
    kwargs: dict[str, object] = {"Bucket": bucket}
    if prefix:
        kwargs["Prefix"] = prefix
    keys: list[str] = []
    for page in paginator.paginate(**kwargs):
        for obj in page.get("Contents", []):
            keys.append(obj["Key"])
    return keys


def bucket_usage_summary(bucket: str, prefix: str = "") -> dict:
    s3 = make_s3_client()
    paginator = s3.get_paginator("list_objects_v2")
    kwargs: dict[str, object] = {"Bucket": bucket}
    if prefix:
        kwargs["Prefix"] = prefix
    objects = 0
    total_bytes = 0
    for page in paginator.paginate(**kwargs):
        for obj in page.get("Contents", []):
            objects += 1
            total_bytes += int(obj.get("Size", 0))
    return {"bucket": bucket, "prefix": prefix, "objects": objects, "logical_bytes": total_bytes}


def delete_keys_parallel(bucket: str, keys: list[str], workers: int = 24) -> None:
    if not keys:
        return
    clients = [make_s3_client(ep) for ep in RUSTFS_ENDPOINTS]

    def _del(item: tuple[int, str]) -> None:
        idx, key = item
        for attempt in range(6):
            try:
                clients[(idx + attempt) % len(clients)].delete_object(Bucket=bucket, Key=key)
                return
            except Exception:
                if attempt == 5:
                    raise
                time.sleep(0.05 * (2**attempt))

    with ThreadPoolExecutor(max_workers=min(workers, max(1, len(keys)))) as pool:
        list(pool.map(_del, enumerate(keys)))


def delete_bucket_and_objects(bucket: str) -> None:
    if not bucket.startswith(BUCKET_PREFIX + "-"):
        raise ValueError(f"Refusing to delete non-baseline bucket: {bucket}")
    s3 = make_s3_client()
    try:
        s3.head_bucket(Bucket=bucket)
    except Exception:
        return
    keys = list_all_keys(bucket)
    if keys:
        delete_keys_parallel(bucket, keys)
    try:
        s3.delete_bucket(Bucket=bucket)
    except Exception:
        pass


# ==============================================================================
# CephFS Admin Helpers (Short-lived root mount, subdir creation, df stats)
# ==============================================================================

def ceph_df_summary() -> dict:
    p = run_on_host("node-coord", ["/usr/bin/ceph", "df", "-f", "json"], log_cmd=False)
    doc = json.loads(p.stdout.decode("utf-8"))
    pools = {pl["name"]: pl.get("stats", {}) for pl in doc.get("pools", [])}
    return {
        "total_used_bytes": int((doc.get("stats") or {}).get("total_used_raw_bytes", 0)),
        "cephfs_data_stored_bytes": int((pools.get("cephfs_data") or {}).get("stored", 0)),
        "cephfs_meta_stored_bytes": int((pools.get("cephfs_meta") or {}).get("stored", 0)),
        "cephfs_data_objects": int((pools.get("cephfs_data") or {}).get("objects", 0)),
    }


class CephAdminRootMount:
    """Short-lived CephFS root mount on node-coord slot 52, always unmounted before timed windows."""

    def __init__(self, tag: str, out_dir: Path, slot: int = 52) -> None:
        self.tag = tag
        self.out_dir = out_dir
        self.slot = slot
        self._mounted: list[dict] = []

    def __enter__(self) -> Path:
        mid = f"ceph-adm-{self.tag}"
        req = {
            "id": mid,
            "phase": "admin",
            "engine": "cephfs",
            "host": "cluster-node-1",
            "slot": self.slot,
            "workspace": "/",
            "parent_workspace": "none",
            "purpose": "cephfs-admin",
            "cache_state": "cold",
            "windows": ["admin"],
            "root": str(RUN_ROOT),
        }
        mp = self.out_dir / f"{mid}-mounts.json"
        generate_mounts_json([req], mp)
        self._mounted = batch_start_mounts(mp, {mid: {"subdir": "/"}}, f"{mid}-start", register_collector=False)
        mp.unlink(missing_ok=True)
        return Path(self._mounted[0]["mount_directory"])

    def __exit__(self, exc_type, exc, tb) -> None:
        if self._mounted:
            batch_stop_mounts(self._mounted, f"ceph-adm-{self.tag}-stop", clear_collector=False, remove_cache=True)
            self._mounted = []


def ensure_cephfs_subdirs(subdirs: list[str], out_dir: Path, tag: str = "mkdir") -> None:
    with CephAdminRootMount(tag, out_dir) as root_mp:
        for sd in subdirs:
            rel = sd.lstrip("/")
            if not rel.startswith(CEPHFS_ROOT_PREFIX.lstrip("/")):
                raise ValueError(f"Refusing CephFS path outside {CEPHFS_ROOT_PREFIX}: {sd}")
            (root_mp / rel).mkdir(parents=True, exist_ok=True)


def remove_cephfs_subdirs(subdirs: list[str], out_dir: Path, tag: str = "rmdir") -> None:
    with CephAdminRootMount(tag, out_dir) as root_mp:
        for sd in subdirs:
            rel = sd.lstrip("/")
            if not rel.startswith(CEPHFS_ROOT_PREFIX.lstrip("/")):
                raise ValueError(f"Refusing to remove CephFS path outside {CEPHFS_ROOT_PREFIX}: {sd}")
            target = root_mp / rel
            if target.exists():
                shutil.rmtree(target, ignore_errors=True)


def fuse_copy_workspaces(
    system: str,
    copy_entries: list[dict],
    out_dir: Path,
    *,
    tag: str = "fork-copy",
    outer_workers: int = 4,
    inner_workers: int = 12,
    src_slot: int = 50,
    dst_slot: int = 51,
) -> dict:
    """Create isolated child workspaces via full FUSE data copy for CephFS or s3fs and record all copy costs.

    Each entry in `copy_entries`:
      - for cephfs: {"src": "/ax-baselines/.../base", "dst": "/ax-baselines/.../c000"}
      - for s3fs:   {"bucket": "axbase1-...", "src": "base", "dst": "children/.../c000"}
    """
    if not copy_entries:
        return {
            "mechanism": "none",
            "native_fork_supported": False,
            "system": system,
            "children_count": 0,
            "copied_files": 0,
            "copied_symlinks": 0,
            "copied_logical_bytes": 0,
            "timing": {
                "mount_ready_seconds": 0.0,
                "copy_wall_seconds": 0.0,
                "upload_drain_seconds": 0.0,
                "unmount_seconds": 0.0,
                "total_copy_seconds": 0.0,
                "per_child_avg_seconds": 0.0,
            },
            "client_resources": {"cpu_seconds": 0.0, "peak_memory_bytes": 0},
            "storage_cost": {"added_storage_bytes": 0, "added_objects": 0},
            "backend_io_cost": {},
        }

    buckets = sorted({e["bucket"] for e in copy_entries if e.get("bucket")})
    if system == "cephfs":
        storage_before = ceph_df_summary()
    else:
        b_before = [bucket_usage_summary(b) for b in buckets]
        storage_before = {
            "buckets": b_before,
            "logical_bytes": sum(x["logical_bytes"] for x in b_before),
            "objects": sum(x["objects"] for x in b_before),
        }

    snap_before = collector_mark_all(tag, "fork-copy-start")
    t_total0 = time.monotonic()

    mount_ready_sum = 0.0
    copy_wall_sum = 0.0
    drain_sum = 0.0
    unmount_sum = 0.0
    total_files = 0
    total_symlinks = 0
    total_bytes = 0
    total_cpu_usec = 0
    max_peak_mem = 0

    if system == "cephfs":
        groups = [("cephfs", copy_entries)]
    else:
        by_bkt: dict[str, list[dict]] = defaultdict(list)
        for e in copy_entries:
            by_bkt[e["bucket"]].append(e)
        groups = sorted(by_bkt.items())

    for g_idx, (g_key, entries) in enumerate(groups):
        src_id = f"{tag}-g{g_idx}-src"
        dst_id = f"{tag}-g{g_idx}-dst"
        mp_cfg = out_dir / f"{tag}-g{g_idx}-copy-mounts.json"
        dst_overrides = {"disk_cache": "off"} if system == "s3fs" else {}
        reqs = [
            {
                "id": src_id,
                "phase": "fork-copy",
                "engine": system,
                "host": "cluster-node-1",
                "slot": src_slot,
                "workspace": "/" if system == "cephfs" else f"{g_key}:/",
                "parent_workspace": "none",
                "purpose": "fork-copy-reader",
                "cache_state": "cold",
                "windows": ["fork-copy"],
                "root": str(RUN_ROOT),
            },
            {
                "id": dst_id,
                "phase": "fork-copy",
                "engine": system,
                "host": "cluster-node-1",
                "slot": dst_slot,
                "workspace": "/" if system == "cephfs" else f"{g_key}:/",
                "parent_workspace": "none",
                "purpose": "fork-copy-writer",
                "cache_state": "cold",
                "windows": ["fork-copy"],
                "root": str(RUN_ROOT),
                "overrides": dst_overrides,
            },
        ]
        generate_mounts_json(reqs, mp_cfg)
        if system == "cephfs":
            specs = {src_id: {"subdir": "/"}, dst_id: {"subdir": "/"}}
        else:
            specs = {
                src_id: {"bucket": g_key, "prefix": "", "s3_endpoint": RUSTFS_ENDPOINTS[0]},
                dst_id: {"bucket": g_key, "prefix": "", "s3_endpoint": RUSTFS_ENDPOINTS[1 % len(RUSTFS_ENDPOINTS)]},
            }

        t_m0 = time.monotonic()
        started = batch_start_mounts(mp_cfg, specs, f"{tag}-g{g_idx}-s", register_collector=True)
        mount_ready_sum += time.monotonic() - t_m0
        m_map = {m["id"]: m for m in started}
        src_mp = Path(m_map[src_id]["mount_directory"])
        dst_mp = Path(m_map[dst_id]["mount_directory"])

        # Measure initial cgroup CPU on both mounts before copy
        c0_map = {
            src_id: int((read_mount_metrics(m_map[src_id]).get("cgroup") or {}).get("cpu_usage_usec", 0)),
            dst_id: int((read_mount_metrics(m_map[dst_id]).get("cgroup") or {}).get("cpu_usage_usec", 0)),
        }

        try:
            spec_pairs = []
            for e in entries:
                s_rel = e["src"].lstrip("/")
                d_rel = e["dst"].lstrip("/")
                if system == "cephfs":
                    for sd in (s_rel, d_rel):
                        if not sd.startswith(CEPHFS_ROOT_PREFIX.lstrip("/")):
                            raise ValueError(f"CephFS copy path outside {CEPHFS_ROOT_PREFIX}: /{sd}")
                spec_pairs.append({"src": str(src_mp / s_rel), "dst": str(dst_mp / d_rel)})

            spec_file = out_dir / f"{tag}-g{g_idx}-pairs.json"
            spec_file.parent.mkdir(parents=True, exist_ok=True)
            spec_file.write_text(
                json.dumps(
                    {
                        "outer_workers": min(outer_workers, max(1, len(spec_pairs))),
                        "inner_workers": inner_workers,
                        "pairs": spec_pairs,
                    },
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )
            t_c0 = time.monotonic()
            cp_proc = run_on_host(
                "node-coord",
                [
                    "/usr/bin/python3",
                    str(SCRIPTS_DIR / "baseline_mount_driver.py"),
                    "exec-in-cgroup",
                    "--cgroup",
                    m_map[dst_id]["cgroup"],
                    "--",
                    "/usr/bin/python3",
                    str(SCRIPTS_DIR / "baseline_host_batch.py"),
                    "copy-tree-batch",
                    "--spec",
                    str(spec_file),
                ],
                timeout=14400.0,
            )
            copy_wall_sum += time.monotonic() - t_c0
            spec_file.unlink(missing_ok=True)
            cp_doc = json.loads(cp_proc.stdout.decode("utf-8").strip().splitlines()[-1])
            for item in cp_doc.get("items", []):
                total_files += int(item.get("files", 0))
                total_symlinks += int(item.get("symlinks", 0))
                total_bytes += int(item.get("bytes", 0))

            up = wait_uploads([m_map[dst_id]], timeout=7200.0)[0]
            drain_sum += float(up["upload_drain_seconds"])
        finally:
            t_u0 = time.monotonic()
            stops = batch_stop_mounts(started, f"{tag}-g{g_idx}-x", clear_collector=True, remove_cache=True)
            unmount_sum += time.monotonic() - t_u0
            mp_cfg.unlink(missing_ok=True)
            for st in stops:
                c0 = c0_map.get(st.get("id", ""), 0)
                fcg = st.get("final_cgroup") or {}
                c1 = int(fcg.get("cpu_usage_usec", c0))
                total_cpu_usec += max(0, c1 - c0)
                pk = fcg.get("memory_peak")
                if isinstance(pk, int):
                    max_peak_mem = max(max_peak_mem, pk)

    total_wall_s = time.monotonic() - t_total0
    snap_after = collector_mark_all(tag, "fork-copy-end")

    if system == "cephfs":
        storage_after = ceph_df_summary()
        added_bytes = max(0, storage_after["cephfs_data_stored_bytes"] - storage_before["cephfs_data_stored_bytes"])
        added_objs = max(0, storage_after["cephfs_data_objects"] - storage_before["cephfs_data_objects"])
    else:
        b_after = [bucket_usage_summary(b) for b in buckets]
        storage_after = {
            "buckets": b_after,
            "logical_bytes": sum(x["logical_bytes"] for x in b_after),
            "objects": sum(x["objects"] for x in b_after),
        }
        added_bytes = max(0, storage_after["logical_bytes"] - storage_before["logical_bytes"])
        added_objs = max(0, storage_after["objects"] - storage_before["objects"])

    n_children = len(copy_entries)
    return {
        "mechanism": "full-fuse-data-copy",
        "native_fork_supported": False,
        "system": system,
        "children_count": n_children,
        "copied_files": total_files,
        "copied_symlinks": total_symlinks,
        "copied_logical_bytes": total_bytes,
        "timing": {
            "mount_ready_seconds": round(mount_ready_sum, 6),
            "copy_wall_seconds": round(copy_wall_sum, 6),
            "upload_drain_seconds": round(drain_sum, 6),
            "unmount_seconds": round(unmount_sum, 6),
            "total_copy_seconds": round(total_wall_s, 6),
            "per_child_avg_seconds": round(total_wall_s / max(1, n_children), 6),
        },
        "client_resources": {
            "cpu_seconds": round(total_cpu_usec / 1e6, 6),
            "peak_memory_bytes": max_peak_mem,
        },
        "storage_cost": {
            "added_storage_bytes": added_bytes,
            "added_objects": added_objs,
            "before": storage_before,
            "after": storage_after,
        },
        "backend_io_cost": summarize_phase_delta(snap_before, snap_after),
    }


# ==============================================================================
# Histogram & Placement Helpers (Aligned with main suite)
# ==============================================================================

def placements(count: int, same_host: bool = False) -> list[tuple[str, int]]:
    if same_host:
        return [("node-coord", idx + 3) for idx in range(count)]
    weights = [16, 11, 6, 13, 13, 5]
    counts = [count * w // 64 for w in weights]
    order = sorted(range(6), key=lambda idx: count * weights[idx] % 64, reverse=True)
    for idx in order[: count - sum(counts)]:
        counts[idx] += 1
    return [
        (host, slot + (3 if host == "node-coord" else 1))
        for host, amount in zip(ALL_HOST_NAMES, counts)
        for slot in range(amount)
    ]


def merge_log2_histograms(hist_objs: list[dict]) -> dict:
    merged = [0] * 32
    max_us = 0.0
    min_us = None
    for h in hist_objs:
        if not h:
            continue
        if "buckets" in h and len(h["buckets"]) == 32:
            b = [int(x) for x in h["buckets"]]
        else:
            b = [0] * 32
            for le_us, cnt in h.get("bins", []):
                le_us, cnt = int(le_us), int(cnt)
                idx = 0 if le_us == 0 else (31 if le_us >= (1 << 31) else max(0, min(31, (le_us + 1).bit_length() - 1)))
                b[idx] += cnt
        for i in range(32):
            merged[i] += b[i]
        if h.get("max_us") is not None:
            max_us = max(max_us, float(h["max_us"]))
        if h.get("min_us") is not None and h.get("count", 0) > 0:
            v = float(h["min_us"])
            min_us = v if min_us is None else min(min_us, v)

    tot = sum(merged)

    def _quantile(q: float) -> float:
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
        "p50_us": _quantile(0.50),
        "p95_us": _quantile(0.95),
        "p99_us": _quantile(0.99),
        "buckets": merged,
    }


HARDLINK_COPY_SHIM_C = SCRIPTS_DIR / "s3fs_hardlink_copy_shim.c"
HARDLINK_COPY_SHIM_SO = BASELINE_ROOT / "bin" / "libs3fs_hardlink_copy.so"


def ensure_hardlink_copy_shim(hosts: list[str] | None = None) -> str:
    """Compile the s3fs hardlink->copy LD_PRELOAD shim on node-coord and sync it to target hosts."""
    HARDLINK_COPY_SHIM_SO.parent.mkdir(parents=True, exist_ok=True)
    need_build = (
        not HARDLINK_COPY_SHIM_SO.is_file()
        or HARDLINK_COPY_SHIM_C.stat().st_mtime_ns > HARDLINK_COPY_SHIM_SO.stat().st_mtime_ns
    )
    if need_build:
        subprocess.run(
            [
                "gcc",
                "-shared",
                "-fPIC",
                "-O2",
                "-Wall",
                "-Wextra",
                str(HARDLINK_COPY_SHIM_C),
                "-o",
                str(HARDLINK_COPY_SHIM_SO),
                "-ldl",
            ],
            check=True,
            timeout=60,
        )
        HARDLINK_COPY_SHIM_SO.chmod(0o755)

    target_hosts = ALL_HOST_NAMES if hosts is None else hosts
    for h in target_hosts:
        if h == "node-coord":
            continue
        run_on_host(h, ["mkdir", "-p", str(HARDLINK_COPY_SHIM_SO.parent)], log_cmd=False)
        sync_file_to_host(h, HARDLINK_COPY_SHIM_SO, str(HARDLINK_COPY_SHIM_SO))
        run_on_host(h, ["chmod", "755", str(HARDLINK_COPY_SHIM_SO)], log_cmd=False)
    return str(HARDLINK_COPY_SHIM_SO)

