#!/usr/bin/env python3
"""Hash-free per-host telemetry collector daemon for run-001.

Runs outside Agent cgroups. Samples every 5s (when not paused) and synchronously on
phase markers (`mark`), writing segmented `.jsonl.zst` and `.txt.zst` archives under:
  /srv/ax-cluster/runs/direct/run-001/hosts/<HOST>/
"""

from runtime_context import REDIS_PORT

import argparse
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import select
import socket
import subprocess
import sys
import time
from urllib.request import ProxyHandler, Request, build_opener

OPENER = build_opener(ProxyHandler({}))


def read_text_fast(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def parse_proc_stat(txt: str) -> dict:
    for line in txt.splitlines():
        if line.startswith("cpu "):
            parts = [int(x) for x in line.split()[1:9]]
            keys = ("user", "nice", "system", "idle", "iowait", "irq", "softirq", "steal")
            return dict(zip(keys, parts))
    return {}


def parse_meminfo(txt: str) -> dict:
    want = {"MemTotal", "MemFree", "MemAvailable", "Buffers", "Cached", "Dirty", "Writeback", "AnonPages", "Mapped"}
    out = {}
    for line in txt.splitlines():
        if ":" not in line:
            continue
        k, rest = line.split(":", 1)
        if k in want:
            tok = rest.strip().split()
            if tok:
                out[k + "_kib"] = int(tok[0])
    return out


def discover_physical_block_devs() -> set[str]:
    devs = set()
    sys_block = Path("/sys/block")
    if sys_block.is_dir():
        for d in sys_block.iterdir():
            name = d.name
            if name.startswith(("loop", "ram", "dm-", "zram", "sr")):
                continue
            devs.add(name)
    return devs


def parse_diskstats(txt: str, phys_devs: set[str]) -> dict:
    # Sum each physical block device once (never double-counting partitions!)
    per_dev = {}
    tot_read_bytes = 0
    tot_write_bytes = 0
    tot_read_ios = 0
    tot_write_ios = 0
    tot_io_ms = 0
    for line in txt.splitlines():
        f = line.split()
        if len(f) < 14:
            continue
        dev = f[2]
        if dev not in phys_devs:
            continue
        rios = int(f[3])
        rsec = int(f[5])
        wios = int(f[7])
        wsec = int(f[9])
        ioms = int(f[12])
        rb = rsec * 512
        wb = wsec * 512
        per_dev[dev] = {
            "read_ios": rios,
            "write_ios": wios,
            "read_bytes": rb,
            "write_bytes": wb,
            "io_ms": ioms,
        }
        tot_read_bytes += rb
        tot_write_bytes += wb
        tot_read_ios += rios
        tot_write_ios += wios
        tot_io_ms += ioms
    return {
        "devices": per_dev,
        "physical_read_bytes": tot_read_bytes,
        "physical_write_bytes": tot_write_bytes,
        "physical_read_ios": tot_read_ios,
        "physical_write_ios": tot_write_ios,
        "physical_io_ms": tot_io_ms,
    }


def parse_netdev(txt: str) -> dict:
    out = {}
    for line in txt.splitlines()[2:]:
        if ":" not in line:
            continue
        iface, rest = line.split(":", 1)
        iface = iface.strip()
        if iface == "lo":
            continue
        f = rest.split()
        if len(f) >= 10:
            out[iface] = {
                "rx_bytes": int(f[0]),
                "rx_packets": int(f[1]),
                "tx_bytes": int(f[8]),
                "tx_packets": int(f[9]),
            }
    return out


def parse_kv_lines(txt: str) -> dict:
    out = {}
    for line in txt.splitlines():
        parts = line.split()
        if len(parts) == 2:
            try:
                out[parts[0]] = int(parts[1])
            except ValueError:
                out[parts[0]] = parts[1]
    return out


def scrape_http(url: str, timeout: float = 1.5) -> str:
    with OPENER.open(Request(url), timeout=timeout) as resp:
        if resp.status != 200:
            raise RuntimeError(f"HTTP {resp.status} from {url}")
        return resp.read(8 * 1024 * 1024).decode("utf-8", errors="replace")


def parse_prom_summary(raw: str, prefix_filters: tuple[str, ...]) -> dict:
    out = {}
    for line in raw.splitlines():
        if not line or line.startswith("#"):
            continue
        if not line.startswith(prefix_filters):
            continue
        parts = line.rsplit(None, 2)
        if len(parts) < 2:
            continue
        # Handle optional timestamp at end: metric{labels} val [ts]
        tokens = line.split()
        if len(tokens) < 2:
            continue
        # Metric key might contain spaces only if quoted, standard prom keys have no spaces
        key = tokens[0]
        try:
            val = float(tokens[1])
        except ValueError:
            continue
        out[key] = val
    return out


def collect_mount(m: dict, peak_tracker: dict) -> tuple[dict, str]:
    mid = m["id"]
    cg = Path(m["cgroup"])
    pid = int(m.get("pid", 0))
    port = int(m["metrics_port"])

    cg_data = {}
    if cg.is_dir():
        cpu_stat = parse_kv_lines(read_text_fast(cg / "cpu.stat"))
        mem_cur_s = read_text_fast(cg / "memory.current").strip()
        mem_cur = int(mem_cur_s) if mem_cur_s.isdigit() else 0
        prev_peak = peak_tracker.get(mid, 0)
        if mem_cur > prev_peak:
            prev_peak = mem_cur
            peak_tracker[mid] = prev_peak
        mem_peak_s = read_text_fast(cg / "memory.peak").strip()
        mem_peak = int(mem_peak_s) if mem_peak_s.isdigit() else prev_peak
        if mem_peak < prev_peak:
            mem_peak = prev_peak
        swap_cur_s = read_text_fast(cg / "memory.swap.current").strip()
        pids_cur_s = read_text_fast(cg / "pids.current").strip()
        mem_events = parse_kv_lines(read_text_fast(cg / "memory.events"))
        mem_stat_all = parse_kv_lines(read_text_fast(cg / "memory.stat"))
        mem_stat = {k: mem_stat_all[k] for k in ("anon", "file", "kernel", "slab") if k in mem_stat_all}
        cg_data = {
            "cpu_stat": cpu_stat,
            "memory_current": mem_cur,
            "memory_peak": mem_peak,
            "memory_swap_current": int(swap_cur_s) if swap_cur_s.isdigit() else 0,
            "pids_current": int(pids_cur_s) if pids_cur_s.isdigit() else 0,
            "memory_events": mem_events,
            "memory_stat": mem_stat,
            "io_stat": read_text_fast(cg / "io.stat").strip(),
            "cpu_pressure": read_text_fast(cg / "cpu.pressure").strip(),
            "memory_pressure": read_text_fast(cg / "memory.pressure").strip(),
            "io_pressure": read_text_fast(cg / "io.pressure").strip(),
        }

    proc_data = {}
    if pid > 0 and Path(f"/proc/{pid}").is_dir():
        st_txt = read_text_fast(Path(f"/proc/{pid}/status"))
        rss_kib = 0
        threads = 0
        for ln in st_txt.splitlines():
            if ln.startswith("VmRSS:"):
                rss_kib = int(ln.split()[1])
            elif ln.startswith("Threads:"):
                threads = int(ln.split()[1])
        try:
            fd_count = len(os.listdir(f"/proc/{pid}/fd"))
        except OSError:
            fd_count = 0
        proc_data = {"pid": pid, "rss_kib": rss_kib, "threads": threads, "fd_count": fd_count}

    prom_raw = ""
    prom_parsed = {}
    prom_err = None
    try:
        prom_raw = scrape_http(f"http://127.0.0.1:{port}/metrics", timeout=1.5)
        mdir = m.get("mount_directory")
        if mdir and str(m.get("engine", "")).startswith("brewfs"):
            vstats = read_text_fast(Path(mdir) / ".stats")
            if vstats:
                prom_raw = prom_raw + "\n# --- .stats ---\n" + vstats
        prom_parsed = parse_prom_summary(prom_raw, ("brewfs_", "juicefs_", "forkredis_"))
    except Exception as e:
        prom_err = type(e).__name__

    return (
        {
            "id": mid,
            "slot": m["slot"],
            "engine": m.get("engine"),
            "metrics_port": port,
            "cgroup": cg_data,
            "process": proc_data,
            "metrics": prom_parsed,
            "scrape_error": prom_err,
        },
        f"### mount={mid} port={port}\n{prom_raw}\n" if prom_raw else "",
    )


def collect_redis_info(redis_pw: str) -> dict:
    env = os.environ.copy()
    env["REDISCLI_AUTH"] = redis_pw
    try:
        r = subprocess.run(
            ["redis-cli", "-h", "127.0.0.1", "-p", str(REDIS_PORT), "INFO", "ALL"],
            env=env,
            capture_output=True,
            text=True,
            timeout=1.5,
            check=True,
        )
    except Exception as e:
        return {"error": type(e).__name__}
    want = {
        "uptime_in_seconds",
        "connected_clients",
        "used_memory",
        "used_memory_rss",
        "used_memory_peak",
        "maxmemory",
        "mem_fragmentation_ratio",
        "total_connections_received",
        "total_commands_processed",
        "instantaneous_ops_per_sec",
        "total_net_input_bytes",
        "total_net_output_bytes",
        "evicted_keys",
        "keyspace_hits",
        "keyspace_misses",
        "aof_enabled",
        "aof_last_bgrewrite_status",
        "aof_last_write_status",
        "aof_current_size",
    }
    out = {"cmdstat": {}}
    for ln in r.stdout.splitlines():
        if not ln or ln.startswith("#") or ":" not in ln:
            continue
        k, v = ln.split(":", 1)
        v = v.strip()
        if k in want:
            try:
                out[k] = int(v)
            except ValueError:
                try:
                    out[k] = float(v)
                except ValueError:
                    out[k] = v
        elif k.startswith("cmdstat_"):
            # e.g. calls=123,usec=456,usec_per_call=3.71
            d = {}
            for item in v.split(","):
                if "=" in item:
                    ik, iv = item.split("=", 1)
                    try:
                        d[ik] = int(iv)
                    except ValueError:
                        try:
                            d[ik] = float(iv)
                        except ValueError:
                            d[ik] = iv
            out["cmdstat"][k[8:]] = d
        elif k == "db0":
            out["db0"] = v
    return out


def collect_rustfs_node() -> tuple[dict, str]:
    selected = Path('/srv/ax-cluster/direct/runtime/rustfs-backend.json')
    backend = json.loads(selected.read_text()) if selected.exists() else {
        'name': 'dynamic', 'metrics_port': 9464, 'process_executable': '/usr/bin/rustfs'}
    out = {'backend': backend['name'], 'metrics_port': backend['metrics_port']}
    raw_prom = ""
    try:
        raw_prom = scrape_http(f"http://127.0.0.1:{backend['metrics_port']}/metrics", timeout=1.5)
        out["metrics"] = parse_prom_summary(raw_prom, ("rustfs_",))
    except Exception as e:
        out["scrape_error"] = type(e).__name__

    # Also read RustFS container process CPU (utime+stime ticks) & RSS directly from /proc
    try:
        # Find rustfs process PID via /proc scan for executable/cmdline containing 'rustfs'
        for p in Path("/proc").iterdir():
            if not p.name.isdigit():
                continue
            cmd = read_text_fast(p / "cmdline")
            executable = cmd.split('\x00', 1)[0]
            official = executable == '/srv/ax-cluster/releases/rustfs-1.0.5/rustfs'
            if Path(executable).name == 'rustfs' and official == (backend['name'] == 'official'):
                stat_f = read_text_fast(p / "stat").rsplit(")", 1)[-1].split()
                utime_ticks = int(stat_f[11])
                stime_ticks = int(stat_f[12])
                clk = os.sysconf("SC_CLK_TCK")
                st_txt = read_text_fast(p / "status")
                rss_kib = 0
                for ln in st_txt.splitlines():
                    if ln.startswith("VmRSS:"):
                        rss_kib = int(ln.split()[1])
                        break
                out["process"] = {
                    "pid": int(p.name),
                    "cpu_user_seconds": utime_ticks / clk,
                    "cpu_system_seconds": stime_ticks / clk,
                    "cpu_total_seconds": (utime_ticks + stime_ticks) / clk,
                    "rss_kib": rss_kib,
                }
                break
    except Exception as e:
        out["proc_error"] = type(e).__name__

    return out, (f"### rustfs_{backend['name']} 127.0.0.1:{backend['metrics_port']}\n{raw_prom}\n" if raw_prom else "")


class SegmentCompressor:
    def __init__(self, out_dir: Path):
        self.out_dir = out_dir
        self.sample_buf: list[str] = []
        self.prom_buf: list[str] = []
        self.seg_idx = len(list(out_dir.glob("samples-seg-*.jsonl.zst")))

    def append(self, sample_json_line: str, prom_chunk: str):
        self.sample_buf.append(sample_json_line)
        if prom_chunk:
            self.prom_buf.append(prom_chunk)
        if len(self.sample_buf) >= 60:
            self.flush()

    def flush(self):
        if not self.sample_buf and not self.prom_buf:
            return
        self.seg_idx += 1
        if self.sample_buf:
            raw_s = "".join(self.sample_buf).encode("utf-8")
            self.sample_buf.clear()
            dst = self.out_dir / f"samples-seg-{self.seg_idx:05d}.jsonl.zst"
            subprocess.run(["zstd", "--no-check", "-1", "-q", "-f", "-o", str(dst)], input=raw_s, check=True)
        if self.prom_buf:
            raw_p = "".join(self.prom_buf).encode("utf-8")
            self.prom_buf.clear()
            dst_p = self.out_dir / f"prom-seg-{self.seg_idx:05d}.txt.zst"
            subprocess.run(["zstd", "--no-check", "-1", "-q", "-f", "-o", str(dst_p)], input=raw_p, check=True)


def load_redis_pw() -> str:
    env_p = Path("/srv/ax-cluster/private/redis.env")
    if not env_p.is_file():
        return ""
    for ln in env_p.read_text().splitlines():
        if ln.startswith("REDIS_PASSWORD="):
            return ln.split("=", 1)[1].strip()
    return ""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", required=True)
    ap.add_argument("--out-dir", type=Path, required=True)
    ap.add_argument("--sock", type=Path, default=Path("/srv/ax-cluster/direct/runtime/collector.sock"))
    ap.add_argument("--interval", type=float, default=5.0)
    ap.add_argument("--has-redis", action="store_true")
    ap.add_argument("--has-rustfs", action="store_true")
    ap.add_argument("--has-tikv", action="store_true")
    args = ap.parse_args()

    args.out_dir.mkdir(parents=True, exist_ok=True)
    args.sock.parent.mkdir(parents=True, exist_ok=True)
    if args.sock.exists():
        args.sock.unlink()

    phys_devs = discover_physical_block_devs()
    redis_pw = load_redis_pw() if args.has_redis else ""
    compressor = SegmentCompressor(args.out_dir)
    events_path = args.out_dir / "events.jsonl"

    active_mounts: list[dict] = []
    peak_tracker: dict[str, int] = {}
    paused = False
    sample_index = 0

    def take_sample(trigger: str = "periodic", run_id: str = "", label: str = "") -> dict:
        nonlocal sample_index
        t_mono_start = time.monotonic_ns()
        t_utc = time.time_ns()

        cpu = parse_proc_stat(read_text_fast(Path("/proc/stat")))
        mem = parse_meminfo(read_text_fast(Path("/proc/meminfo")))
        disks = parse_diskstats(read_text_fast(Path("/proc/diskstats")), phys_devs)
        net = parse_netdev(read_text_fast(Path("/proc/net/dev")))
        psi = {
            "cpu": read_text_fast(Path("/proc/pressure/cpu")).strip(),
            "memory": read_text_fast(Path("/proc/pressure/memory")).strip(),
            "io": read_text_fast(Path("/proc/pressure/io")).strip(),
        }
        vfs = os.statvfs("/srv/ax-cluster")
        fs_cap = {
            "bytes_free": vfs.f_bavail * vfs.f_frsize,
            "bytes_total": vfs.f_blocks * vfs.f_frsize,
            "inodes_free": vfs.f_favail,
        }

        prom_chunks = [f"=== sample={sample_index} utc_ns={t_utc} trigger={trigger} ===\n"]
        mounts_out = []
        if active_mounts:
            with ThreadPoolExecutor(max_workers=min(16, len(active_mounts))) as pool:
                futs = [pool.submit(collect_mount, m, peak_tracker) for m in active_mounts]
                for fut in futs:
                    m_rec, m_prom = fut.result()
                    mounts_out.append(m_rec)
                    if m_prom:
                        prom_chunks.append(m_prom)

        redis_out = collect_redis_info(redis_pw) if args.has_redis else None
        rustfs_out = None
        if args.has_rustfs:
            rustfs_out, r_prom = collect_rustfs_node()
            if r_prom:
                prom_chunks.append(r_prom)

        tikv_out = None
        if args.has_tikv:
            try:
                raw = OPENER.open("http://127.0.0.1:20180/metrics", timeout=2).read().decode()
                tikv_out = parse_prom_summary(raw, ("tikv_", "grpc_"))
                prom_chunks.append("### tikv 127.0.0.1:20180\n" + raw)
            except Exception as exc:
                tikv_out = {"error": str(exc)}
        t_mono_end = time.monotonic_ns()
        rec = {
            "index": sample_index,
            "host": args.host,
            "trigger": trigger,
            "run_id": run_id,
            "label": label,
            "utc_ns": t_utc,
            "monotonic_start_ns": t_mono_start,
            "monotonic_end_ns": t_mono_end,
            "collector_cpu_seconds": time.process_time(),
            "cpu": cpu,
            "memory": mem,
            "disks": disks,
            "net": net,
            "psi": psi,
            "capacity": fs_cap,
            "mounts": mounts_out,
            "redis": redis_out,
            "tikv": tikv_out,
            "rustfs": rustfs_out,
        }
        compressor.append(json.dumps(rec, separators=(",", ":")) + "\n", "".join(prom_chunks))
        sample_index += 1
        return rec

    srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    srv.bind(str(args.sock))
    os.chmod(args.sock, 0o600)
    srv.listen(32)

    # Initial ready sample
    first = take_sample(trigger="init")
    (args.out_dir / "collector-ready.json").write_text(
        json.dumps({"host": args.host, "pid": os.getpid(), "first_utc_ns": first["utc_ns"]}, indent=2) + "\n"
    )

    next_tick = time.monotonic() + args.interval
    while True:
        wait_s = max(0.0, next_tick - time.monotonic()) if not paused else 3600.0
        rlist, _, _ = select.select([srv], [], [], wait_s)
        if not rlist:
            if not paused:
                take_sample(trigger="periodic")
                next_tick = time.monotonic() + args.interval
            continue

        conn, _ = srv.accept()
        with conn:
            try:
                raw_msg = conn.makefile("rb").readline(4 * 1024 * 1024)
                if not raw_msg:
                    continue
                msg = json.loads(raw_msg)
                act = msg.get("action")
                if act == "status":
                    reply = {
                        "ok": True,
                        "host": args.host,
                        "pid": os.getpid(),
                        "paused": paused,
                        "samples": sample_index,
                        "active_mounts": len(active_mounts),
                    }
                elif act == "pause":
                    paused = True
                    compressor.flush()
                    reply = {"ok": True, "paused": True, "samples": sample_index}
                elif act == "resume":
                    paused = False
                    s = take_sample(trigger="resume")
                    next_tick = time.monotonic() + args.interval
                    reply = {"ok": True, "paused": False, "samples": sample_index, "utc_ns": s["utc_ns"]}
                elif act == "set_mounts":
                    new_mounts = msg.get("mounts", [])
                    new_ids = {m["id"] for m in new_mounts}
                    for old_id in list(peak_tracker.keys()):
                        if old_id not in new_ids:
                            del peak_tracker[old_id]
                    active_mounts = new_mounts
                    reply = {"ok": True, "active_mounts": len(active_mounts)}
                elif act == "mark":
                    run_id = str(msg.get("run_id", ""))
                    label = str(msg.get("label", ""))
                    s = take_sample(trigger="mark", run_id=run_id, label=label)
                    next_tick = time.monotonic() + args.interval
                    ev = {
                        "host": args.host,
                        "run_id": run_id,
                        "label": label,
                        "sample_index": s["index"],
                        "utc_ns": s["utc_ns"],
                        "monotonic_ns": s["monotonic_end_ns"],
                    }
                    with events_path.open("a", encoding="utf-8") as ef:
                        ef.write(json.dumps(ev, separators=(",", ":")) + "\n")
                    if label in ("stopped", "verify_done"):
                        compressor.flush()
                    reply = {"ok": True, "sample": s}
                elif act == "flush":
                    compressor.flush()
                    reply = {"ok": True, "segments": compressor.seg_idx}
                elif act == "stop":
                    s = take_sample(trigger="stop")
                    compressor.flush()
                    (args.out_dir / "collector-stopped.json").write_text(
                        json.dumps({"host": args.host, "samples": sample_index, "segments": compressor.seg_idx}, indent=2) + "\n"
                    )
                    conn.sendall((json.dumps({"ok": True, "samples": sample_index}) + "\n").encode())
                    break
                else:
                    reply = {"ok": False, "error": f"unknown action {act}"}
            except Exception as exc:
                reply = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
            conn.sendall((json.dumps(reply, separators=(",", ":")) + "\n").encode("utf-8"))


if __name__ == "__main__":
    main()
