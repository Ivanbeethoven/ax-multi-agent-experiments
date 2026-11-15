#!/usr/bin/env python3
"""Per-host telemetry collector for CephFS and s3fs baseline experiments (hash-free)."""

import argparse
import glob
import json
import os
from pathlib import Path
import socket
import subprocess
import threading
import time
from urllib.request import ProxyHandler, Request, build_opener

OPENER = build_opener(ProxyHandler({}))


def _read_proc_cpu() -> dict[str, int]:
    for line in Path("/proc/stat").read_text(encoding="utf-8").splitlines():
        if line.startswith("cpu "):
            vals = [int(x) for x in line.split()[1:9]]
            keys = ("user", "nice", "system", "idle", "iowait", "irq", "softirq", "steal")
            return dict(zip(keys, vals))
    return {}


def _read_meminfo() -> dict[str, int]:
    out: dict[str, int] = {}
    wanted = {"MemTotal:", "MemAvailable:", "Cached:", "Dirty:", "Writeback:"}
    for line in Path("/proc/meminfo").read_text(encoding="utf-8").splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[0] in wanted:
            out[parts[0].rstrip(":")] = int(parts[1]) * 1024
    return out


def _read_diskstats() -> dict[str, int]:
    sectors_read = 0
    sectors_written = 0
    read_ios = 0
    write_ios = 0
    for line in Path("/proc/diskstats").read_text(encoding="utf-8").splitlines():
        parts = line.split()
        if len(parts) < 14:
            continue
        dev = parts[2]
        if dev.startswith("nvme") and "p" not in dev:
            read_ios += int(parts[3])
            sectors_read += int(parts[5])
            write_ios += int(parts[7])
            sectors_written += int(parts[9])
    return {
        "nvme_read_ios": read_ios,
        "nvme_read_bytes": sectors_read * 512,
        "nvme_write_ios": write_ios,
        "nvme_write_bytes": sectors_written * 512,
    }


def _read_net_dev(iface: str = "eth0") -> dict[str, int]:
    for line in Path("/proc/net/dev").read_text(encoding="utf-8").splitlines():
        if ":" not in line:
            continue
        name, rest = line.split(":", 1)
        if name.strip() == iface:
            vals = [int(x) for x in rest.split()]
            return {
                "rx_bytes": vals[0],
                "rx_packets": vals[1],
                "tx_bytes": vals[8],
                "tx_packets": vals[9],
            }
    return {}


def _scrape_rustfs_prom(port: int) -> dict[str, float]:
    out: dict[str, float] = {}
    try:
        with OPENER.open(Request(f"http://127.0.0.1:{port}/metrics"), timeout=1.5) as resp:
            raw = resp.read(4 * 1024 * 1024).decode("utf-8", errors="replace")
    except Exception:
        return out
    for ln in raw.splitlines():
        if not ln or ln.startswith("#"):
            continue
        toks = ln.split()
        if len(toks) >= 2 and ("s3" in toks[0] or "rustfs" in toks[0] or "http" in toks[0]):
            try:
                out[toks[0]] = float(toks[1])
            except ValueError:
                pass
    return out


def _read_local_ceph_daemons() -> dict[str, float]:
    totals: dict[str, float] = {}
    for sock_path in sorted(glob.glob("/var/run/ceph/ceph-osd.*.asok")):
        p = subprocess.run(
            ["/usr/bin/ceph", "--admin-daemon", sock_path, "perf", "dump", "osd"],
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
        )
        if p.returncode == 0 and p.stdout.strip():
            try:
                osd = json.loads(p.stdout).get("osd") or {}
                for k in ("op_r", "op_w", "op_rw", "op_r_out_bytes", "op_w_in_bytes", "subop_w_in_bytes"):
                    if isinstance(osd.get(k), (int, float)):
                        totals[f"ceph_osd_{k}"] = totals.get(f"ceph_osd_{k}", 0.0) + float(osd[k])
            except Exception:
                pass

    for sock_path in sorted(glob.glob("/var/run/ceph/ceph-mds.*.asok")):
        p = subprocess.run(
            ["/usr/bin/ceph", "--admin-daemon", sock_path, "perf", "dump", "mds"],
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
        )
        if p.returncode == 0 and p.stdout.strip():
            try:
                mds = json.loads(p.stdout).get("mds") or {}
                for k in ("request", "reply", "forward", "dir_fetch", "dir_commit"):
                    if isinstance(mds.get(k), (int, float)):
                        totals[f"ceph_mds_{k}"] = totals.get(f"ceph_mds_{k}", 0.0) + float(mds[k])
            except Exception:
                pass
    return totals


class BaselineCollector:
    def __init__(self, host_name: str, sock_path: Path, out_dir: Path, backend_file: Path) -> None:
        self.host_name = host_name
        self.sock_path = sock_path
        self.out_dir = out_dir
        self.backend_file = backend_file
        self._lock = threading.Lock()
        self._mounts: list[dict] = []
        self._stop = threading.Event()
        self._chunk_idx = 0
        self.out_dir.mkdir(parents=True, exist_ok=True)
        self._log_path = self.out_dir / f"samples-{self._chunk_idx:04d}.jsonl"
        self._log_fp = self._log_path.open("a", encoding="utf-8")
        self._lines_in_chunk = 0

    def _rustfs_metrics_port(self) -> int:
        if self.backend_file.is_file():
            try:
                doc = json.loads(self.backend_file.read_text(encoding="utf-8"))
                return int(doc.get("metrics_port", 9464))
            except Exception:
                return 9464
        return 9464

    def snapshot(self, tag: str = "", label: str = "") -> dict:
        with self._lock:
            mounts = list(self._mounts)
        mount_samples = []
        for m in mounts:
            cg = Path(m["cgroup"])
            mem_cur = int((cg / "memory.current").read_text().strip()) if (cg / "memory.current").is_file() else 0
            mem_peak = int((cg / "memory.peak").read_text().strip()) if (cg / "memory.peak").is_file() else 0
            mount_samples.append(
                {
                    "id": m["id"],
                    "engine": m["engine"],
                    "slot": m["slot"],
                    "memory_current": mem_cur,
                    "memory_peak": mem_peak,
                }
            )
        port = self._rustfs_metrics_port()
        return {
            "host": self.host_name,
            "ts_utc_ns": time.time_ns(),
            "tag": tag,
            "label": label,
            "cpu": _read_proc_cpu(),
            "mem": _read_meminfo(),
            "disk": _read_diskstats(),
            "net_eth0": _read_net_dev("eth0"),
            "rustfs_metrics_port": port,
            "rustfs_prom": _scrape_rustfs_prom(port),
            "ceph_daemons": _read_local_ceph_daemons(),
            "mounts": mount_samples,
        }

    def _write_sample(self, sample: dict) -> None:
        with self._lock:
            self._log_fp.write(json.dumps(sample) + "\n")
            self._lines_in_chunk += 1
            if self._lines_in_chunk >= 3600:
                self._rotate_locked()

    def _rotate_locked(self) -> None:
        self._log_fp.flush()
        self._log_fp.close()
        old = self._log_path
        if old.is_file() and old.stat().st_size > 0:
            subprocess.run(
                ["zstd", "-q", "-f", "--no-check", "--rm", str(old)],
                capture_output=True,
                check=False,
            )
        self._chunk_idx += 1
        self._log_path = self.out_dir / f"samples-{self._chunk_idx:04d}.jsonl"
        self._log_fp = self._log_path.open("a", encoding="utf-8")
        self._lines_in_chunk = 0

    def sample_loop(self) -> None:
        while not self._stop.is_set():
            try:
                self._write_sample(self.snapshot())
            except Exception:
                pass
            self._stop.wait(1.0)

    def handle_rpc(self, req: dict) -> dict:
        action = req.get("action")
        if action == "ping":
            return {"ok": True, "host": self.host_name}
        if action == "set_mounts":
            with self._lock:
                self._mounts = list(req.get("mounts") or [])
            return {"ok": True, "count": len(self._mounts)}
        if action == "mark":
            snap = self.snapshot(tag=req.get("tag", ""), label=req.get("label", ""))
            self._write_sample(snap)
            return {"ok": True, "snapshot": snap}
        if action == "flush":
            with self._lock:
                self._log_fp.flush()
            return {"ok": True}
        return {"ok": False, "error": f"Unknown action {action!r}"}

    def serve(self) -> None:
        self.sock_path.parent.mkdir(parents=True, exist_ok=True)
        self.sock_path.unlink(missing_ok=True)
        srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        srv.bind(str(self.sock_path))
        os.chmod(self.sock_path, 0o600)
        srv.listen(32)
        t = threading.Thread(target=self.sample_loop, daemon=True)
        t.start()
        try:
            while not self._stop.is_set():
                conn, _ = srv.accept()
                with conn:
                    raw = b""
                    while chunk := conn.recv(65536):
                        raw += chunk
                        if b"\n" in raw:
                            break
                    req = json.loads(raw.decode("utf-8").strip() or "{}")
                    resp = self.handle_rpc(req)
                    conn.sendall((json.dumps(resp) + "\n").encode("utf-8"))
        finally:
            self._stop.set()
            srv.close()
            self.sock_path.unlink(missing_ok=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", required=True)
    parser.add_argument("--socket", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument(
        "--backend-file",
        type=Path,
        default=Path("/srv/ax-cluster/baselines/runtime/rustfs-backend.json"),
    )
    args = parser.parse_args()
    collector = BaselineCollector(args.host, args.socket, args.out_dir, args.backend_file)
    collector.serve()


if __name__ == "__main__":
    main()
