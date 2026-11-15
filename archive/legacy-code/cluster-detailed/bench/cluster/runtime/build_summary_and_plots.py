#!/usr/bin/env python3
"""Generates summary.csv, comparisons.csv, publication-clean SVG figures/, and run-001/README.md.

Strictly zero hashes/checksums. Operates on either remote A366 (/srv/ax-cluster/runs/direct/run-001)
or local Windows (results/cluster-c1-c3/run-001).
"""

import argparse
import csv
import html
import json
import math
from pathlib import Path
import statistics
from typing import Any


SUMMARY_COLUMNS = [
    "group",
    "condition_id",
    "family",
    "kind",
    "system",
    "repeat",
    "seed",
    "status",
    "execution_mode",
    "shared_backend_group",
    "agents",
    "files",
    "workload",
    "block_kib",
    "read_kib",
    "quantum",
    "pacing",
    "writes",
    "prepare_or_fork_seconds",
    "mount_ready_seconds",
    "app_done_seconds",
    "upload_drain_seconds",
    "remote_ready_seconds",
    "verify_seconds",
    "remote_ready_with_verify_seconds",
    "operations",
    "fuse_requests",
    "throughput_mib_s",
    "iops",
    "tasks_per_hour",
    "task_seconds",
    "first_batch_64_seconds",
    "space_delta_bytes",
    "cumulative_prefix_1_seconds",
    "cumulative_prefix_4_seconds",
    "cumulative_prefix_16_seconds",
    "op_p50_us",
    "op_p95_us",
    "op_p99_us",
    "actual_read_bytes",
    "actual_write_bytes",
    "unique_read_bytes",
    "repeated_read_bytes",
    "repeated_read_ratio",
    "s3_get_bytes",
    "s3_put_bytes",
    "backend_read_bytes",
    "backend_write_bytes",
    "read_amplification_s3",
    "read_amplification_phys",
    "upload_amplification_s3",
    "upload_amplification_phys",
    "metadata_requests",
    "metadata_bytes",
    "decode_cpu_seconds",
    "rustfs_cpu_seconds",
    "client_peak_memory_bytes",
    "parameters_json",
]


def _num(val: Any) -> float | int | None:
    if val is None or val == "":
        return None
    if isinstance(val, bool):
        return int(val)
    if isinstance(val, (int, float)):
        if math.isnan(val) or math.isinf(val):
            return None
        return val
    try:
        f = float(val)
        return int(f) if f.is_integer() else f
    except Exception:
        return None


def extract_unified_row(group: str, cond: dict, system: str, rep: int, seed: int | None, run_doc: dict | None, state_fallback: str) -> dict[str, Any]:
    params = cond.get("parameters", {})
    status = state_fallback
    if run_doc:
        status = run_doc.get("state") or run_doc.get("status") or state_fallback

    base = {col: "" for col in SUMMARY_COLUMNS}
    base.update(
        {
            "group": group,
            "condition_id": cond["id"],
            "family": cond["family"],
            "kind": cond["kind"],
            "system": system,
            "repeat": rep,
            "seed": run_doc.get("seed", seed) if run_doc else (seed or ""),
            "status": status,
            "execution_mode": run_doc.get('execution_mode', 'exclusive') if run_doc else '',
            "shared_backend_group": run_doc.get('shared_backend_group') or '' if run_doc else '',
            "agents": params.get("agents", 1 if group == "C1" else ""),
            "files": params.get("files", ""),
            "workload": params.get("profile") or params.get("rw") or params.get("workload") or cond["family"],
            "block_kib": params.get("block_kib", ""),
            "read_kib": params.get("read_kib") or params.get("io_kib", ""),
            "quantum": params.get("quantum", ""),
            "pacing": params.get("pacing", ""),
            "writes": params.get("writes", ""),
            "parameters_json": json.dumps(params, separators=(",", ":"), sort_keys=True),
        }
    )
    if not run_doc or status != "passed":
        return base

    t = run_doc.get("timing") or {}
    if group == "C1":
        app = run_doc.get("application") or {}
        bd = run_doc.get("backend_delta") or {}
        lat = app.get("latency_us") or {}
        cm = run_doc.get("client_mount") or {}
        mp = run_doc.get("mount_metrics_delta") or {}
        s3_get = bd.get("client_reported_s3_get_bytes") or bd.get("s3_get_bytes") or 0
        s3_put = bd.get("client_reported_s3_put_bytes") or bd.get("s3_put_bytes") or 0
        fuse_reqs = int(
            sum(v for k, v in mp.items() if k.startswith("brewfs_fuse_op_total"))
            or sum(v for k, v in mp.items() if k.startswith("juicefs_fuse_ops_durations_histogram_seconds_count"))
        )
        meta_reqs = int(bd.get("redis_commands_delta", 0))
        meta_bytes = int(bd.get("redis_net_input_bytes_delta", 0)) + int(bd.get("redis_net_output_bytes_delta", 0))
        base.update(
            {
                "prepare_or_fork_seconds": t.get("prepare_or_fork_seconds", ""),
                "mount_ready_seconds": t.get("mount_ready_seconds", ""),
                "app_done_seconds": t.get("app_done_seconds", ""),
                "upload_drain_seconds": t.get("upload_drain_seconds", ""),
                "remote_ready_seconds": t.get("remote_ready_no_verify_seconds", ""),
                "verify_seconds": t.get("verify_seconds", ""),
                "remote_ready_with_verify_seconds": t.get("remote_ready_with_verify_seconds", ""),
                "operations": app.get("operations", ""),
                "fuse_requests": fuse_reqs if fuse_reqs > 0 else "",
                "throughput_mib_s": app.get("throughput_mib_s", ""),
                "iops": app.get("iops", ""),
                "op_p50_us": lat.get("p50_us", ""),
                "op_p95_us": lat.get("p95_us", ""),
                "op_p99_us": lat.get("p99_us", ""),
                "actual_read_bytes": app.get("read_bytes", ""),
                "actual_write_bytes": app.get("write_bytes", ""),
                "s3_get_bytes": s3_get,
                "s3_put_bytes": s3_put,
                "backend_read_bytes": bd.get("backend_physical_read_bytes", ""),
                "backend_write_bytes": bd.get("backend_physical_write_bytes", ""),
                "read_amplification_s3": bd.get("read_amplification_s3", ""),
                "read_amplification_phys": bd.get("read_amplification_physical", ""),
                "upload_amplification_s3": bd.get("write_amplification_s3", ""),
                "upload_amplification_phys": bd.get("write_amplification_physical", ""),
                "metadata_requests": meta_reqs,
                "metadata_bytes": meta_bytes,
                "rustfs_cpu_seconds": bd.get("rustfs_cpu_seconds", ""),
                "client_peak_memory_bytes": cm.get("peak_memory_bytes", ""),
            }
        )
        if base['execution_mode'] == 'shared-backend-concurrent':
            for field in ['s3_get_bytes', 's3_put_bytes', 'backend_read_bytes', 'backend_write_bytes',
                          'read_amplification_s3', 'read_amplification_phys', 'upload_amplification_s3',
                          'upload_amplification_phys', 'metadata_requests', 'metadata_bytes', 'rustfs_cpu_seconds']:
                base[field] = ''

    elif group == "C2":
        m = run_doc.get("metrics") or {}
        pd = run_doc.get("phase_delta") or {}
        lat = m.get("latency_us") or {}
        base.update(
            {
                "prepare_or_fork_seconds": t.get("workspace_prepare_seconds", ""),
                "app_done_seconds": t.get("app_done_seconds", ""),
                "upload_drain_seconds": t.get("upload_drain_max_seconds", ""),
                "remote_ready_seconds": t.get("remote_ready_no_verify_seconds", ""),
                "verify_seconds": t.get("verify_seconds", ""),
                "remote_ready_with_verify_seconds": t.get("remote_ready_with_verify_seconds", ""),
                "operations": m.get("trace_operations", ""),
                "fuse_requests": m.get("fuse_requests", ""),
                "tasks_per_hour": m.get("tasks_per_hour_remote_ready", ""),
                "task_seconds": m.get("task_seconds", ""),
                "op_p50_us": lat.get("p50_us", ""),
                "op_p95_us": lat.get("p95_us", ""),
                "op_p99_us": lat.get("p99_us", ""),
                "actual_read_bytes": m.get("actual_read_bytes", ""),
                "actual_write_bytes": m.get("actual_write_bytes", ""),
                "unique_read_bytes": m.get("unique_read_bytes", ""),
                "repeated_read_bytes": m.get("repeated_read_bytes", ""),
                "repeated_read_ratio": m.get("repeated_read_ratio", ""),
                "s3_get_bytes": m.get("s3_get_bytes", ""),
                "s3_put_bytes": m.get("s3_put_bytes", ""),
                "backend_read_bytes": m.get("backend_read_bytes", ""),
                "backend_write_bytes": m.get("backend_write_bytes", ""),
                "read_amplification_s3": pd.get("read_amplification_s3", ""),
                "read_amplification_phys": pd.get("read_amplification_phys", ""),
                "upload_amplification_s3": pd.get("upload_amplification_s3", ""),
                "upload_amplification_phys": pd.get("upload_amplification_phys", ""),
                "metadata_requests": m.get("metadata_requests", ""),
                "metadata_bytes": m.get("metadata_bytes", ""),
                "decode_cpu_seconds": m.get("client_cpu_seconds", ""),
                "rustfs_cpu_seconds": pd.get("rustfs_cpu_seconds", ""),
                "client_peak_memory_bytes": m.get("client_peak_memory_bytes_sum", ""),
            }
        )

    elif group == "C3":
        m = run_doc.get("metrics") or {}
        pd = run_doc.get("phase_delta") or {}
        lat = m.get("latency_us") or {}
        rb = m.get("actual_read_bytes") or m.get("source_bytes" if False else "nonexistent")
        wb = m.get("actual_write_bytes") or (m.get("source_bytes") if cond["family"] == "prepare" else "")
        base.update(
            {
                "prepare_or_fork_seconds": t.get("fork_seconds") or t.get("prepare_seconds", ""),
                "mount_ready_seconds": t.get("mount_wall_seconds", ""),
                "app_done_seconds": t.get("app_done_seconds", ""),
                "upload_drain_seconds": t.get("upload_drain_max_seconds") or t.get("upload_drain_seconds", ""),
                "remote_ready_seconds": t.get("remote_ready_no_verify_seconds", ""),
                "verify_seconds": t.get("verify_seconds", ""),
                "remote_ready_with_verify_seconds": t.get("remote_ready_with_verify_seconds", ""),
                "first_batch_64_seconds": m.get("first_batch_64_seconds") or m.get("first_batch_64_mean_seconds", ""),
                "space_delta_bytes": m.get("space_delta_bytes") or m.get("stored_s3_bytes", ""),
                "cumulative_prefix_1_seconds": m.get("cumulative_prefix_1_seconds", ""),
                "cumulative_prefix_4_seconds": m.get("cumulative_prefix_4_seconds", ""),
                "cumulative_prefix_16_seconds": m.get("cumulative_prefix_16_seconds", ""),
                "op_p50_us": lat.get("p50_us", ""),
                "op_p95_us": lat.get("p95_us", ""),
                "op_p99_us": lat.get("p99_us", ""),
                "actual_read_bytes": rb if rb is not None else "",
                "actual_write_bytes": wb if wb is not None else "",
                "s3_get_bytes": m.get("s3_get_bytes", ""),
                "s3_put_bytes": m.get("s3_put_bytes", ""),
                "backend_read_bytes": m.get("backend_read_bytes", pd.get("backend_physical_read_bytes", "")),
                "backend_write_bytes": m.get("backend_write_bytes", pd.get("backend_physical_write_bytes", "")),
                "read_amplification_s3": pd.get("read_amplification_s3", ""),
                "read_amplification_phys": pd.get("read_amplification_phys", ""),
                "upload_amplification_s3": pd.get("upload_amplification_s3", ""),
                "upload_amplification_phys": pd.get("upload_amplification_phys", ""),
                "metadata_requests": m.get("metadata_requests", ""),
                "metadata_bytes": (int(pd.get("redis_net_input_bytes_delta", 0)) + int(pd.get("redis_net_output_bytes_delta", 0))) if pd else "",
                "decode_cpu_seconds": m.get("decode_cpu_seconds", ""),
                "rustfs_cpu_seconds": pd.get("rustfs_cpu_seconds", ""),
                "client_peak_memory_bytes": m.get("client_peak_memory_bytes_sum", ""),
            }
        )
    return base


def build_summary_rows(run_root: Path) -> list[dict[str, Any]]:
    status_doc = json.loads((run_root / "status.json").read_text(encoding="utf-8"))
    rows: list[dict[str, Any]] = []
    c1_seeds = {1: 2026111401, 2: 2026111402, 3: 2026111403}

    for group in ("C1", "C2", "C3"):
        plan = json.loads((run_root / "plan" / f"{group}.json").read_text(encoding="utf-8"))
        cond_by_id = {c["id"]: c for c in plan["conditions"]}
        if "jobs" in plan:
            ordered = [(j["condition"], j["system"], int(j["repetition"]), j.get("seed")) for j in plan["jobs"]]
        else:
            ordered = [
                (c["id"], s, r, c1_seeds.get(r))
                for c in plan["conditions"]
                for r in range(1, int(c["repetitions"]) + 1)
                for s in c["systems"]
            ]

        for cid, sys_name, rep, seed in ordered:
            cond = cond_by_id[cid]
            st_fallback = (
                status_doc["groups"][group]["conditions"]
                .get(cid, {})
                .get("runs", {})
                .get(f"{sys_name}/r{rep}", {})
                .get("state", "planned")
            )
            rpath = run_root / group / cid / sys_name / f"r{rep}" / "result.json"
            rdoc = json.loads(rpath.read_text(encoding="utf-8")) if rpath.is_file() else None
            rows.append(extract_unified_row(group, cond, sys_name, rep, seed, rdoc, st_fallback))
    return rows


def resolve_metric_value(row: dict[str, Any], metric_name: str) -> float | None:
    mapping = {
        "backend_read_bytes": "backend_read_bytes",
        "remote_ready_seconds": "remote_ready_seconds",
        "s3_get_bytes": "s3_get_bytes",
        "s3_put_bytes": "s3_put_bytes",
        "metadata_requests": "metadata_requests",
        "metadata_bytes": "metadata_bytes",
        "task_seconds": "task_seconds",
        "decode_cpu_seconds": "decode_cpu_seconds",
        "tasks_per_hour": "tasks_per_hour",
        "throughput_mib_s": "throughput_mib_s",
        "app_done_seconds": "app_done_seconds",
        "cumulative_prefix_16_seconds": "cumulative_prefix_16_seconds",
        "space_delta_bytes": "space_delta_bytes",
    }
    col = mapping.get(metric_name, metric_name)
    v = _num(row.get(col))
    return float(v) if v is not None else None


def build_comparisons_rows(rows: list[dict[str, Any]], repo_root: Path) -> list[dict[str, Any]]:
    rows = [row for row in rows if row.get('execution_mode') != 'shared-backend-concurrent']
    by_key = {(r["condition_id"], r["system"], int(r["repeat"])): r for r in rows}
    comp_spec = json.loads((repo_root / "configs/comparisons.json").read_text(encoding="utf-8"))
    out: list[dict[str, Any]] = []

    def _emit_pair(
        pair_id: str,
        group: str,
        kind: str,
        mechanism: str,
        ctrl_cond: str,
        ctrl_sys: str,
        var_cond: str,
        var_sys: str,
        changed_params: str,
        primary_metric: str,
    ):
        c_rows = [by_key.get((ctrl_cond, ctrl_sys, r)) for r in (1, 2, 3)]
        v_rows = [by_key.get((var_cond, var_sys, r)) for r in (1, 2, 3)]
        statuses = {x["status"] for x in (c_rows + v_rows) if x is not None}
        if "pending-feature" in statuses:
            pair_status = "pending-feature"
        elif all(x is not None and x["status"] == "passed" for x in c_rows + v_rows):
            pair_status = "passed"
        else:
            pair_status = "incomplete"

        rec: dict[str, Any] = {
            "comparison_id": pair_id,
            "group": group,
            "kind": kind,
            "mechanism": mechanism,
            "control_condition": ctrl_cond,
            "control_system": ctrl_sys,
            "variant_condition": var_cond,
            "variant_system": var_sys,
            "changed_parameters": changed_params,
            "primary_metric": primary_metric,
            "status": pair_status,
            "r1_control": "",
            "r1_variant": "",
            "r1_ratio": "",
            "r2_control": "",
            "r2_variant": "",
            "r2_ratio": "",
            "r3_control": "",
            "r3_variant": "",
            "r3_ratio": "",
            "control_median": "",
            "variant_median": "",
            "paired_ratio_median": "",
            "paired_ratio_min": "",
            "paired_ratio_max": "",
            "paired_delta_median": "",
        }
        if pair_status != "passed":
            out.append(rec)
            return

        c_vals: list[float] = []
        v_vals: list[float] = []
        ratios: list[float] = []
        deltas: list[float] = []
        for r_idx in (1, 2, 3):
            cv = resolve_metric_value(c_rows[r_idx - 1], primary_metric)
            vv = resolve_metric_value(v_rows[r_idx - 1], primary_metric)
            rec[f"r{r_idx}_control"] = round(cv, 6) if cv is not None else ""
            rec[f"r{r_idx}_variant"] = round(vv, 6) if vv is not None else ""
            if cv is not None and vv is not None:
                c_vals.append(cv)
                v_vals.append(vv)
                deltas.append(vv - cv)
                if abs(cv) > 1e-12:
                    rt = vv / cv
                    ratios.append(rt)
                    rec[f"r{r_idx}_ratio"] = round(rt, 6)
        if c_vals:
            rec["control_median"] = round(statistics.median(c_vals), 6)
        if v_vals:
            rec["variant_median"] = round(statistics.median(v_vals), 6)
        if deltas:
            rec["paired_delta_median"] = round(statistics.median(deltas), 6)
        if ratios:
            rec["paired_ratio_median"] = round(statistics.median(ratios), 6)
            rec["paired_ratio_min"] = round(min(ratios), 6)
            rec["paired_ratio_max"] = round(max(ratios), 6)
        out.append(rec)

    # 1. All C1-C3 declared ablation & sensitivity pairs from configs/comparisons.json
    for p in comp_spec["pairs"]:
        if p["group"] not in {"C1", "C2", "C3"}:
            continue
        changed = ";".join(p["changed_parameters"])
        for sys_name in p["systems"]:
            _emit_pair(
                pair_id=f"{p['id']}:{sys_name}",
                group=p["group"],
                kind=p["kind"],
                mechanism=p["mechanism"],
                ctrl_cond=p["control"],
                ctrl_sys=sys_name,
                var_cond=p["variant"],
                var_sys=sys_name,
                changed_params=changed,
                primary_metric=p["primary_metric"],
            )

    # 2. Main cross-system comparisons (BrewFS vs JuiceFS & JuiceFS Overlay across C1, C2, C3 main conditions)
    seen_conds = []
    for r in rows:
        if r["kind"] == "main" and r["condition_id"] not in seen_conds:
            seen_conds.append((r["group"], r["condition_id"], r["family"]))

    for grp, cid, fam in seen_conds:
        brew_sys = "brewfs-packed" if grp == "C3" else "brewfs-native-overlay"
        if grp == "C1":
            pm = "remote_ready_seconds"
        elif grp == "C2":
            pm = "tasks_per_hour"
        elif fam == "reuse":
            pm = "cumulative_prefix_16_seconds"
        else:
            pm = "remote_ready_seconds"
        for ref_sys in ("juicefs", "juicefs-overlay"):
            if (cid, brew_sys, 1) in by_key and (cid, ref_sys, 1) in by_key:
                _emit_pair(
                    pair_id=f"main-{cid}:{brew_sys}-vs-{ref_sys}",
                    group=grp,
                    kind="main-comparison",
                    mechanism=fam,
                    ctrl_cond=cid,
                    ctrl_sys=ref_sys,
                    var_cond=cid,
                    var_sys=brew_sys,
                    changed_params="system",
                    primary_metric=pm,
                )
    return out


# ==============================================================================
# Clean Publication-Style SVG Plot Primitives (Zero External Dependencies)
# ==============================================================================
PALETTE = {
    "brewfs-native-overlay": "#1f77b4",
    "brewfs-packed": "#1f77b4",
    "juicefs": "#ff7f0e",
    "juicefs-overlay": "#2ca02c",
    "q-on": "#1f77b4",
    "q-off": "#d62728",
    "B64K": "#1f77b4",
    "B256K": "#2ca02c",
    "B1M": "#ff7f0e",
    "B4M": "#d62728",
}


class SvgCanvas:
    def __init__(self, width: int = 1080, height: int = 560, title: str = ""):
        self.w = width
        self.h = height
        self.els: list[str] = [
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}" font-family="Inter,Segoe UI,Arial,sans-serif">',
            f'<rect x="0" y="0" width="{width}" height="{height}" fill="#ffffff"/>',
        ]
        if title:
            self.text(width / 2, 30, title, size=18, weight="bold", anchor="middle", fill="#111827")

    def text(self, x: float, y: float, txt: str, size: int = 12, weight: str = "normal", anchor: str = "start", fill: str = "#374151", rotate: float = 0.0):
        esc = html.escape(str(txt))
        tr = f' transform="rotate({rotate:.1f} {x:.1f} {y:.1f})"' if rotate else ""
        self.els.append(f'<text x="{x:.1f}" y="{y:.1f}" font-size="{size}" font-weight="{weight}" text-anchor="{anchor}" fill="{fill}"{tr}>{esc}</text>')

    def line(self, x1: float, y1: float, x2: float, y2: float, stroke: str = "#d1d5db", width: float = 1.0, dash: str = ""):
        da = f' stroke-dasharray="{dash}"' if dash else ""
        self.els.append(f'<line x1="{x1:.1f}" y1="{y1:.1f}" x2="{x2:.1f}" y2="{y2:.1f}" stroke="{stroke}" stroke-width="{width}"{da}/>')

    def rect(self, x: float, y: float, w: float, h: float, fill: str, stroke: str = "none", rx: float = 2.0):
        self.els.append(f'<rect x="{x:.1f}" y="{y:.1f}" width="{max(0.0, w):.1f}" height="{max(0.0, h):.1f}" fill="{fill}" stroke="{stroke}" rx="{rx}"/>')

    def circle(self, cx: float, cy: float, r: float, fill: str, stroke: str = "#ffffff"):
        self.els.append(f'<circle cx="{cx:.1f}" cy="{cy:.1f}" r="{r:.1f}" fill="{fill}" stroke="{stroke}" stroke-width="1.2"/>')

    def polyline(self, pts: list[tuple[float, float]], stroke: str, width: float = 2.4, dash: str = ""):
        if len(pts) < 2:
            return
        p_str = " ".join(f"{x:.1f},{y:.1f}" for x, y in pts)
        da = f' stroke-dasharray="{dash}"' if dash else ""
        self.els.append(f'<polyline fill="none" stroke="{stroke}" stroke-width="{width}" points="{p_str}"{da}/>')

    def grouped_bars(
        self,
        bx: float,
        by: float,
        bw: float,
        bh: float,
        subtitle: str,
        y_label: str,
        categories: list[str],
        series: list[tuple[str, str, list[tuple[float, float, float]]]],  # (label, color, [(med, lo, hi), ...])
    ):
        self.text(bx + bw / 2, by - 12, subtitle, size=14, weight="bold", anchor="middle", fill="#1f2937")
        self.text(bx - 48, by + bh / 2, y_label, size=12, anchor="middle", rotate=-90)
        max_v = max((hi for _, _, vals in series for _, _, hi in vals), default=1.0)
        max_v = max(max_v * 1.15, 1e-6)

        for i in range(6):
            frac = i / 5.0
            yy = by + bh - frac * bh
            val = frac * max_v
            self.line(bx, yy, bx + bw, yy, stroke="#e5e7eb" if i > 0 else "#374151", width=1.0 if i > 0 else 1.4)
            lbl = f"{val:.0f}" if max_v >= 20 else f"{val:.2f}"
            self.text(bx - 8, yy + 4, lbl, size=11, anchor="end", fill="#4b5563")
        self.line(bx, by, bx, by + bh, stroke="#374151", width=1.4)

        n_cat = max(1, len(categories))
        n_ser = max(1, len(series))
        slot_w = bw / n_cat
        bar_w = min(28.0, (slot_w * 0.74) / n_ser)
        for c_idx, cat in enumerate(categories):
            cx = bx + (c_idx + 0.5) * slot_w
            start_x = cx - (n_ser * bar_w) / 2.0
            for s_idx, (_, col, vals) in enumerate(series):
                med, lo, hi = vals[c_idx]
                h_med = (med / max_v) * bh
                x0 = start_x + s_idx * bar_w
                y0 = by + bh - h_med
                self.rect(x0, y0, bar_w - 2.0, h_med, fill=col)
                # Min-max whiskers for 3 repetitions
                if hi > lo:
                    x_mid = x0 + (bar_w - 2.0) / 2.0
                    y_hi = by + bh - (hi / max_v) * bh
                    y_lo = by + bh - (lo / max_v) * bh
                    self.line(x_mid, y_hi, x_mid, y_lo, stroke="#111827", width=1.2)
                    self.line(x_mid - 3, y_hi, x_mid + 3, y_hi, stroke="#111827", width=1.2)
                    self.line(x_mid - 3, y_lo, x_mid + 3, y_lo, stroke="#111827", width=1.2)
            self.text(cx, by + bh + 18, cat, size=11, anchor="middle", fill="#1f2937")

        # Legend
        lx = bx + 8
        ly = by + 14
        for s_lbl, col, _ in series:
            self.rect(lx, ly - 9, 12, 10, fill=col)
            self.text(lx + 16, ly, s_lbl, size=11, fill="#1f2937")
            lx += 18 + len(s_lbl) * 6.8

    def line_panel(
        self,
        bx: float,
        by: float,
        bw: float,
        bh: float,
        subtitle: str,
        x_label: str,
        y_label: str,
        x_ticks: list[str],
        series: list[tuple[str, str, list[tuple[float, float, float]], str]],  # (label, color, [(med,lo,hi)], dash)
    ):
        self.text(bx + bw / 2, by - 12, subtitle, size=14, weight="bold", anchor="middle", fill="#1f2937")
        self.text(bx + bw / 2, by + bh + 36, x_label, size=12, anchor="middle", fill="#374151")
        self.text(bx - 48, by + bh / 2, y_label, size=12, anchor="middle", rotate=-90)
        max_v = max((hi for _, _, vals, _ in series for _, _, hi in vals), default=1.0)
        max_v = max(max_v * 1.15, 1e-6)

        for i in range(6):
            frac = i / 5.0
            yy = by + bh - frac * bh
            val = frac * max_v
            self.line(bx, yy, bx + bw, yy, stroke="#e5e7eb" if i > 0 else "#374151", width=1.0 if i > 0 else 1.4)
            lbl = f"{val:.0f}" if max_v >= 20 else f"{val:.2f}"
            self.text(bx - 8, yy + 4, lbl, size=11, anchor="end", fill="#4b5563")
        self.line(bx, by, bx, by + bh, stroke="#374151", width=1.4)

        n_x = max(1, len(x_ticks))
        xs = [bx + (i / max(1, n_x - 1)) * bw if n_x > 1 else bx + bw / 2 for i in range(n_x)]
        for i, xt in enumerate(x_ticks):
            self.line(xs[i], by + bh, xs[i], by + bh + 4, stroke="#374151")
            self.text(xs[i], by + bh + 18, xt, size=11, anchor="middle", fill="#1f2937")

        for s_lbl, col, vals, dash in series:
            pts = []
            for i, (med, lo, hi) in enumerate(vals):
                px = xs[i]
                py = by + bh - (med / max_v) * bh
                pts.append((px, py))
                if hi > lo:
                    y_hi = by + bh - (hi / max_v) * bh
                    y_lo = by + bh - (lo / max_v) * bh
                    self.line(px, y_hi, px, y_lo, stroke=col, width=1.1)
            self.polyline(pts, stroke=col, width=2.4, dash=dash)
            for px, py in pts:
                self.circle(px, py, 3.6, fill=col)

        lx = bx + 8
        ly = by + 14
        for idx, (s_lbl, col, _, _) in enumerate(series):
            row_y = ly + (idx // 3) * 16
            col_x = bx + 8 + (idx % 3) * (bw / 3.0)
            self.rect(col_x, row_y - 9, 12, 10, fill=col)
            self.text(col_x + 16, row_y, s_lbl, size=10, fill="#1f2937")

    def save(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("\n".join(self.els + ["</svg>\n"]), encoding="utf-8")


def _med_range(rows: list[dict[str, Any]], cond_id: str, system: str, col: str, scale: float = 1.0) -> tuple[float, float, float]:
    vals = [
        float(_num(r[col])) * scale
        for r in rows
        if r["condition_id"] == cond_id and r["system"] == system and r["status"] == "passed" and _num(r[col]) is not None
    ]
    if not vals:
        return (0.0, 0.0, 0.0)
    return (statistics.median(vals), min(vals), max(vals))


def generate_figures(rows: list[dict[str, Any]], run_root: Path):
    rows = [row for row in rows if row.get('execution_mode') != 'shared-backend-concurrent']
    fig_dir = run_root / "figures"
    fig_dir.mkdir(parents=True, exist_ok=True)

    # 1. C1 Multi-size Random Read & Random Write Throughput (MiB/s)
    sizes_rand = [4, 16, 64, 256]
    c1_rand_r = {
        (int(r["block_kib"]), int(r["read_kib"])): r["condition_id"]
        for r in rows
        if r["family"] == "block-random" and r["workload"] == "randread"
    }
    c1_ref_r = {int(r["read_kib"]): r["condition_id"] for r in rows if r["family"] == "reference-random" and r["workload"] == "randread"}
    c1_rand_w = {
        (int(r["block_kib"]), int(r["read_kib"])): r["condition_id"]
        for r in rows
        if r["family"] == "block-random" and r["workload"] == "randwrite"
    }
    c1_ref_w = {int(r["read_kib"]): r["condition_id"] for r in rows if r["family"] == "reference-random" and r["workload"] == "randwrite"}

    svg1 = SvgCanvas(1140, 540, "C1 Multi-Size Random I/O Throughput (32 GiB Working Set, 3-Rep Median & Range)")
    ser_rr = []
    for bk, col in ((64, "#1f77b4"), (256, "#2ca02c"), (1024, "#9467bd"), (4096, "#d62728")):
        ser_rr.append((f"BrewFS B={bk}K", col, [_med_range(rows, c1_rand_r.get((bk, sz), ""), "brewfs-native-overlay", "throughput_mib_s") for sz in sizes_rand], ""))
    ser_rr.append(("JuiceFS", "#ff7f0e", [_med_range(rows, c1_ref_r.get(sz, ""), "juicefs", "throughput_mib_s") for sz in sizes_rand], "6,3"))
    ser_rr.append(("JFS-Overlay", "#111827", [_med_range(rows, c1_ref_r.get(sz, ""), "juicefs-overlay", "throughput_mib_s") for sz in sizes_rand], "3,3"))
    svg1.line_panel(75, 85, 455, 390, "Random Read Throughput", "Request Size (KiB)", "Throughput (MiB/s)", [f"{s}K" for s in sizes_rand], ser_rr)

    ser_rw = []
    for bk, col in ((64, "#1f77b4"), (256, "#2ca02c"), (1024, "#9467bd"), (4096, "#d62728")):
        ser_rw.append((f"BrewFS B={bk}K", col, [_med_range(rows, c1_rand_w.get((bk, sz), ""), "brewfs-native-overlay", "throughput_mib_s") for sz in sizes_rand], ""))
    ser_rw.append(("JuiceFS", "#ff7f0e", [_med_range(rows, c1_ref_w.get(sz, ""), "juicefs", "throughput_mib_s") for sz in sizes_rand], "6,3"))
    ser_rw.append(("JFS-Overlay", "#111827", [_med_range(rows, c1_ref_w.get(sz, ""), "juicefs-overlay", "throughput_mib_s") for sz in sizes_rand], "3,3"))
    svg1.line_panel(625, 85, 455, 390, "Random Write Throughput", "Request Size (KiB)", "Throughput (MiB/s)", [f"{s}K" for s in sizes_rand], ser_rw)
    svg1.save(fig_dir / "c1_multisize_read_write.svg")

    # C1 main fio remote-ready time
    svg3 = SvgCanvas(760, 540, "C1 Main FIO Profiles")
    fio_cids = ["C1-fio-01", "C1-fio-02", "C1-fio-03", "C1-fio-04", "C1-fio-05"]
    fio_lbls = ["seq-r", "seq-w", "rand-r", "rand-w", "rw70"]
    svg3.grouped_bars(
        75,
        85,
        520,
        395,
        "C1 FIO Remote-Ready Time (Lower is Better)",
        "Remote-Ready Time (s)",
        fio_lbls,
        [
            ("BrewFS", "#1f77b4", [_med_range(rows, c, "brewfs-native-overlay", "remote_ready_seconds") for c in fio_cids]),
            ("JuiceFS", "#ff7f0e", [_med_range(rows, c, "juicefs", "remote_ready_seconds") for c in fio_cids]),
            ("JFS-Overlay", "#2ca02c", [_med_range(rows, c, "juicefs-overlay", "remote_ready_seconds") for c in fio_cids]),
        ],
    )
    svg3.save(fig_dir / "c1_fio.svg")

    # 4. C2 Scale Curve (8, 32, 64, 128 Agents): Tasks/hour & Mean Task Seconds
    svg4 = SvgCanvas(1140, 540, "C2 Multi-Agent Full-Trace Replay Scaling Across 6 Cluster Hosts (8..128 Concurrent Workspaces)")
    c2_scale = ["C2-scale-01", "C2-scale-02", "C2-scale-03", "C2-scale-04"]
    ag_lbls = ["8", "32", "64", "128"]
    svg4.line_panel(
        75,
        85,
        460,
        390,
        "Cluster Remote-Ready Throughput (Tasks / Hour)",
        "Concurrent Agent Workspaces",
        "Completed Tasks / Hour",
        ag_lbls,
        [
            ("BrewFS", "#1f77b4", [_med_range(rows, c, "brewfs-native-overlay", "tasks_per_hour") for c in c2_scale], ""),
            ("JuiceFS", "#ff7f0e", [_med_range(rows, c, "juicefs", "tasks_per_hour") for c in c2_scale], "6,3"),
            ("JFS-Overlay", "#2ca02c", [_med_range(rows, c, "juicefs-overlay", "tasks_per_hour") for c in c2_scale], "3,3"),
        ],
    )
    svg4.grouped_bars(
        625,
        85,
        460,
        395,
        "Total Cohort Time Incl. Workspace Prepare (s)",
        "Seconds (Prepare + Replay + Upload Drain)",
        ["8 ag", "32 ag", "64 ag", "128 ag"],
        [
            (
                "BrewFS",
                "#1f77b4",
                [
                    tuple(a + b for a, b in zip(_med_range(rows, c, "brewfs-native-overlay", "prepare_or_fork_seconds"), _med_range(rows, c, "brewfs-native-overlay", "remote_ready_seconds")))
                    for c in c2_scale
                ],
            ),
            (
                "JuiceFS",
                "#ff7f0e",
                [
                    tuple(a + b for a, b in zip(_med_range(rows, c, "juicefs", "prepare_or_fork_seconds"), _med_range(rows, c, "juicefs", "remote_ready_seconds")))
                    for c in c2_scale
                ],
            ),
            (
                "JFS-Overlay",
                "#2ca02c",
                [
                    tuple(a + b for a, b in zip(_med_range(rows, c, "juicefs-overlay", "prepare_or_fork_seconds"), _med_range(rows, c, "juicefs-overlay", "remote_ready_seconds")))
                    for c in c2_scale
                ],
            ),
        ],
    )
    svg4.save(fig_dir / "c2_scale_and_cases.svg")

    # 5. C2 Ablations & Sensitivity (64 Agents)
    svg5 = SvgCanvas(1140, 540, "C2 64-Workspace Technical Ablations and Pacing / Writeback Sensitivity")
    sens_cids = ["C2-pacing-01", "C2-scale-03", "C2-pacing-02", "C2-writeback-01"]
    svg5.grouped_bars(
        75,
        85,
        480,
        395,
        "Pacing & Writeback Sensitivity (Tasks / Hour)",
        "Tasks / Hour",
        ["pace=none", "pace=short", "pace=long", "local-staged"],
        [
            ("BrewFS", "#1f77b4", [_med_range(rows, c, "brewfs-native-overlay", "tasks_per_hour") for c in sens_cids]),
            ("JuiceFS", "#ff7f0e", [_med_range(rows, c, "juicefs", "tasks_per_hour") for c in sens_cids]),
            ("JFS-Overlay", "#2ca02c", [_med_range(rows, c, "juicefs-overlay", "tasks_per_hour") for c in sens_cids]),
        ],
    )
    svg5.grouped_bars(
        640,
        85,
        445,
        395,
        "Metadata Ablations (64 Agents, Redis Commands x10^3)",
        "Metadata Requests (Thousands)",
        ["BrewFS def vs no-row", "BrewFS mat-ns", "JFS-Ov def vs no-fk"],
        [
            (
                "Control (Default)",
                "#1f77b4",
                [
                    _med_range(rows, "C2-scale-03", "brewfs-native-overlay", "metadata_requests", 1e-3),
                    _med_range(rows, "C2-scale-03", "brewfs-native-overlay", "metadata_requests", 1e-3),
                    _med_range(rows, "C2-scale-03", "juicefs-overlay", "metadata_requests", 1e-3),
                ],
            ),
            (
                "Ablated Variant",
                "#d62728",
                [
                    _med_range(rows, "C2-row-cache-01", "brewfs-native-overlay", "metadata_requests", 1e-3),
                    _med_range(rows, "C2-namespace-materialized-01", "brewfs-native-overlay", "metadata_requests", 1e-3),
                    _med_range(rows, "C2-fork-cache-01", "juicefs-overlay", "metadata_requests", 1e-3),
                ],
            ),
        ],
    )
    svg5.save(fig_dir / "c2_ablations_and_sensitivity.svg")

    # 6. C3 Prepare (10k, 100k, 1M) & Load (1, 32, 128 Readers)
    svg6 = SvgCanvas(1140, 540, "C3 Shared Dataset Preparation (10k..1M Files) and Concurrent 3-Epoch Loading (1..128 Readers)")
    prep_cids = ["C3-prepare-01", "C3-prepare-02", "C3-prepare-03"]
    svg6.grouped_bars(
        75,
        85,
        460,
        395,
        "Dataset Prepare Time (Remote-Ready Seconds)",
        "Seconds",
        ["10k files", "100k files", "1M files"],
        [
            ("BrewFS-Packed", "#1f77b4", [_med_range(rows, c, "brewfs-packed", "remote_ready_seconds") for c in prep_cids]),
            ("JuiceFS", "#ff7f0e", [_med_range(rows, c, "juicefs", "remote_ready_seconds") for c in prep_cids]),
            ("JFS-Overlay", "#2ca02c", [_med_range(rows, c, "juicefs-overlay", "remote_ready_seconds") for c in prep_cids]),
        ],
    )
    load_cids = ["C3-load-01", "C3-load-02", "C3-load-03"]
    svg6.grouped_bars(
        625,
        85,
        460,
        395,
        "100k-File 3-Epoch Cohort Load Time (Seconds)",
        "Cohort Load Time (s)",
        ["1 reader", "32 readers", "128 readers"],
        [
            ("BrewFS-Packed", "#1f77b4", [_med_range(rows, c, "brewfs-packed", "remote_ready_seconds") for c in load_cids]),
            ("JuiceFS", "#ff7f0e", [_med_range(rows, c, "juicefs", "remote_ready_seconds") for c in load_cids]),
            ("JFS-Overlay", "#2ca02c", [_med_range(rows, c, "juicefs-overlay", "remote_ready_seconds") for c in load_cids]),
        ],
    )
    svg6.save(fig_dir / "c3_prepare_load_fork.svg")

    # 7. C3 16-Wave Reuse Cumulative Payback Curve + Packed Ablations
    svg7 = SvgCanvas(1140, 540, "C3 16-Wave Reuse Amortization Curve (Prepare + Waves 1..16) and Packed Technical Ablations")
    reuse_series = []
    for sys_name, lbl, col, dash in (
        ("brewfs-packed", "BrewFS-Packed", "#1f77b4", ""),
        ("juicefs", "JuiceFS", "#ff7f0e", "6,3"),
        ("juicefs-overlay", "JFS-Overlay", "#2ca02c", "3,3"),
    ):
        pts_by_k: list[tuple[float, float, float]] = []
        rep_docs = []
        for rep in (1, 2, 3):
            rp = run_root / "C3" / "C3-reuse-01" / sys_name / f"r{rep}" / "result.json"
            if rp.is_file():
                d = json.loads(rp.read_text(encoding="utf-8"))
                if "waves" in d:
                    rep_docs.append(d)
        for k in (0, 1, 2, 4, 8, 12, 16):
            vals_k = []
            for d in rep_docs:
                prep_s = float(d["timing"]["prepare_seconds"])
                w_sum = sum(float(x["wave_total_seconds"]) for x in d["waves"][:k])
                vals_k.append(prep_s + w_sum)
            if vals_k:
                pts_by_k.append((statistics.median(vals_k), min(vals_k), max(vals_k)))
            else:
                pts_by_k.append((0.0, 0.0, 0.0))
        reuse_series.append((lbl, col, pts_by_k, dash))

    svg7.line_panel(
        75,
        85,
        480,
        390,
        "Cumulative Time: Prepare + k Waves (32 Readers)",
        "Completed Reader Waves (k)",
        "Cumulative Time (s)",
        ["0", "1", "2", "4", "8", "12", "16"],
        reuse_series,
    )
    pk_abl_cids = ["C3-load-02", "C3-whole-pack-cache-01", "C3-retained-payload-01", "C3-metadata-cache-01"]
    svg7.grouped_bars(
        640,
        85,
        445,
        395,
        "BrewFS-Packed Ablations (32 Readers, 100k Files)",
        "Cohort Load Time (s)",
        ["Default", "no-pack-cache", "retained=0", "meta-cache=0"],
        [
            ("BrewFS-Packed", "#1f77b4", [_med_range(rows, c, "brewfs-packed", "remote_ready_seconds") for c in pk_abl_cids]),
        ],
    )
    svg7.save(fig_dir / "c3_reuse_breakeven_and_ablations.svg")


def write_readme(rows: list[dict[str, Any]], comp_rows: list[dict[str, Any]], run_root: Path):
    status = json.loads((run_root / "status.json").read_text(encoding="utf-8"))
    groups = sorted({row["group"] for row in rows})
    condition_count = len({(row["group"], row["condition_id"]) for row in rows})
    passed = sum(row["status"] == "passed" for row in rows)
    concurrent = sum(row.get("execution_mode") == "shared-backend-concurrent" for row in rows)
    lines = [
        "# 文件系统实验结果", "",
        f"本表覆盖 {', '.join(groups)}，共 {condition_count} 个条件、{len(rows)} 次系统运行。",
        f"已完成 {passed} 次运行，其中 {concurrent} 次为共享后端并发运行。", "",
        "| 实验 | 完成条件 | 完成运行 | 待实现条件 |",
        "|---|---:|---:|---:|",
    ]
    for group in groups:
        record = status["groups"][group]
        group_rows = [row for row in rows if row["group"] == group]
        complete = sum(row["status"] == "passed" for row in group_rows)
        conditions = len({row["condition_id"] for row in group_rows})
        lines.append(f"| {group} | {record.get('completed_conditions', 0)}/{conditions} | "
                     f"{complete}/{len(group_rows)} | {record.get('pending_feature_conditions', 0)} |")
    lines += ["",
        "summary.csv 保存逐次运行结果，comparisons.csv 保存配对比较。",
        "shared-backend-concurrent.csv 单列共享后端并发结果；并发组的后端总指标保存在各组记录中。",
        "figures/ 保存单独计时条件的对比图。", "",
        "versions.json 保存实际软件版本，inventory.json 保存机器配置。",
        "hosts/ 保存主机与服务的原始采样；各实验目录保存负载输出、计时、挂载配置和内容检查结果。", "",
    ]
    (run_root / "README.md").write_text("\n".join(lines), encoding="utf-8")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-root", type=Path, required=True)
    ap.add_argument("--repo-root", type=Path, required=True)
    args = ap.parse_args()

    rows = build_summary_rows(args.run_root)
    summary_path = args.run_root / "summary.csv"
    with summary_path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=SUMMARY_COLUMNS)
        w.writeheader()
        w.writerows(rows)

    concurrent_rows = [row for row in rows if row.get('execution_mode') == 'shared-backend-concurrent']
    with (args.run_root/'shared-backend-concurrent.csv').open('w', encoding='utf-8', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=SUMMARY_COLUMNS)
        writer.writeheader()
        writer.writerows(concurrent_rows)

    comp_rows = build_comparisons_rows(rows, args.repo_root)
    comp_path = args.run_root / "comparisons.csv"
    if comp_rows:
        with comp_path.open("w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(comp_rows[0].keys()))
            w.writeheader()
            w.writerows(comp_rows)

    generate_figures(rows, args.run_root)
    write_readme(rows, comp_rows, args.run_root)
    print(f"Wrote {len(rows)} rows to {summary_path}, {len(comp_rows)} pairs to {comp_path}, and SVG figures.")


if __name__ == "__main__":
    main()
