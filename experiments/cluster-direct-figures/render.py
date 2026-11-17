"""Extract cluster-direct metrics and render publication figures + LaTeX macros (hash-free)."""
import argparse
import csv
import json
import statistics
import subprocess
import sys
from itertools import combinations
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.lines import Line2D
from matplotlib.patches import Patch
from matplotlib.ticker import FixedLocator, FixedFormatter, NullLocator

ROOT = Path(__file__).resolve().parent
INK = "#222222"
BREWFS = "#D55E00"   # Okabe-Ito vermilion (primary BrewFS)
JFS = "#0072B2"      # Okabe-Ito deep blue (JuiceFS Redis)
OVL = "#009E73"      # Okabe-Ito bluish green (JuiceFS Overlay / secondary)
SLATE = "#595959"    # Neutral slate


def check_bounds_and_collisions(fig, width_pt: float, height_pt: float) -> None:
    fig.canvas.draw()
    renderer = fig.canvas.get_renderer()
    scale = 72.0 / fig.dpi
    text_boxes = []
    for txt in fig.findobj(matplotlib.text.Text):
        s = txt.get_text().strip()
        if not s or not txt.get_visible():
            continue
        bb = txt.get_window_extent(renderer=renderer)
        x0, y0, x1, y1 = bb.x0 * scale, bb.y0 * scale, bb.x1 * scale, bb.y1 * scale
        if x0 < 0.5 or x1 > width_pt - 0.5 or y0 < 0.5 or y1 > height_pt - 0.5:
            raise RuntimeError(
                f"Text '{s}' out of canvas bounds [0,{width_pt}]x[0,{height_pt}]: "
                f"({x0:.1f}, {y0:.1f}, {x1:.1f}, {y1:.1f})"
            )
        text_boxes.append((s, (x0, y0, x1, y1)))

    for (s1, b1), (s2, b2) in combinations(text_boxes, 2):
        ix0, iy0 = max(b1[0], b2[0]), max(b1[1], b2[1])
        ix1, iy1 = min(b1[2], b2[2]), min(b1[3], b2[3])
        if ix1 - ix0 > 0.8 and iy1 - iy0 > 0.8:
            raise RuntimeError(f"Text collision between '{s1}' {b1} and '{s2}' {b2}")


def audit_and_save(fig, stem: Path, qa_dir: Path, qa_tools: Path, tag: str, width_pt: float, height_pt: float) -> None:
    check_bounds_and_collisions(fig, width_pt, height_pt)
    sys.path.insert(0, str(qa_tools))
    from audit_panel_alignment import require_matplotlib_panel_alignment

    require_matplotlib_panel_alignment(
        fig,
        json_out=qa_dir / f"{tag}-alignment.json",
        overlay_svg=qa_dir / f"{tag}-alignment.svg",
        tolerance_pt=1.5,
        gutter_tolerance_pt=1.5,
        strict=True,
    )
    fig.savefig(stem.with_suffix(".pdf"), metadata={"CreationDate": None, "ModDate": None})
    fig.savefig(stem.with_suffix(".svg"))
    fig.savefig(stem.with_suffix(".png"), dpi=600)
    plt.close(fig)

    svg_path = stem.with_suffix(".svg")
    svg_path.write_text(
        "\n".join(line.rstrip() for line in svg_path.read_text(encoding="utf-8").splitlines()) + "\n",
        encoding="utf-8",
    )

    res_text = subprocess.run(
        [sys.executable, str(qa_tools / "audit_pdf_text.py"), str(stem.with_suffix(".pdf")), "--min-pt", "8.5"],
        capture_output=True,
        text=True,
        check=False,
    )
    (qa_dir / f"{tag}-pdf-text.json").write_text(res_text.stdout, encoding="utf-8")
    if res_text.returncode != 0:
        raise RuntimeError(f"audit_pdf_text failed for {tag}:\n{res_text.stdout}\n{res_text.stderr}")


def build_dataset(cluster_dir: Path, data_out: Path) -> dict:
    if cluster_dir.exists():
        rows = list(csv.DictReader((cluster_dir / "run-002-dynamic-agent-times.csv").open(encoding="utf-8")))
        mounts = json.loads((cluster_dir / "run-002-dynamic-mount-times.json").read_text(encoding="utf-8"))
        c2_obs_path = (
            cluster_dir / "run-002/diagnostics/c2-read-retest-20261117/runs/1116232545/observations.json"
        )
        c2_obs = json.loads(c2_obs_path.read_text(encoding="utf-8"))

        case_short = {
            "office-excel-01": "Excel",
            "tb-access-logs-01": "Logs",
            "bfcl-travel-01": "BFCL",
            "ml-vectorization-01": "ML-Vec",
            "dabench-39-01": "DABench",
            "appworld-export-01": "AppWorld",
            "pytest-full-02": "Pytest",
            "sklearn": "Sklearn",
        }
        by_case = {}
        for r in rows:
            by_case.setdefault(r["case"], []).append(r)
        trace_medians = []
        for case_key, rs in sorted(
            by_case.items(),
            key=lambda kv: statistics.median([float(x["wall_off_s"]) for x in kv[1]]),
        ):
            med_on = statistics.median([float(x["wall_on_s"]) for x in rs])
            med_off = statistics.median([float(x["wall_off_s"]) for x in rs])
            min_s = min(min(float(x["wall_on_s"]), float(x["wall_off_s"])) for x in rs)
            trace_medians.append({
                "case": case_key,
                "label": case_short[case_key],
                "med_on_s": round(med_on, 3),
                "med_off_s": round(med_off, 3),
                "min_s": round(min_s, 3),
            })

        wall_diffs = [float(r["wall_off_minus_on_s"]) for r in rows]
        faster_off = sum(1 for d in wall_diffs if d < 0)
        verify_on_med = statistics.median([float(r["verify_on_s"]) for r in rows])
        verify_off_med = statistics.median([float(r["verify_off_s"]) for r in rows])

        lookup_on_med = statistics.median([v["on"]["lookup_s"] for v in mounts.values()])
        lookup_off_med = statistics.median([v["off"]["lookup_s"] for v in mounts.values()])
        getx_on_med = statistics.median([v["on"]["getxattr_s"] for v in mounts.values()])
        getx_off_med = statistics.median([v["off"]["getxattr_s"] for v in mounts.values()])
        flush_on_med = statistics.median([v["on"]["flush_s"] for v in mounts.values()])
        flush_off_med = statistics.median([v["off"]["flush_s"] for v in mounts.values()])
        flush_diff_med = statistics.median([v["off"]["flush_s"] - v["on"]["flush_s"] for v in mounts.values()])
        put_n_on = sum(v["on"]["put_n"] for v in mounts.values())
        put_n_off = sum(v["off"]["put_n"] for v in mounts.values())
        put_ms_on = sum(v["on"]["put_us"] for v in mounts.values()) / put_n_on / 1000.0
        put_ms_off = sum(v["off"]["put_us"] for v in mounts.values()) / put_n_off / 1000.0

        c2_conds = {Path(c["path"]).name: c for c in c2_obs["conditions"]}
        brew_parent_reads = [
            round(c2_conds["00-brewfs-old-parent"]["wall_seconds"], 3),
            round(c2_conds["11-brewfs-old-parent"]["wall_seconds"], 3),
        ]
        brew_child_reads = [
            round(c2_conds["01-brewfs-old-child"]["wall_seconds"], 3),
            round(c2_conds["10-brewfs-old-child"]["wall_seconds"], 3),
        ]
        ovl_child_reads = [
            round(c2_conds["05-overlay-old-child"]["wall_seconds"], 3),
            round(c2_conds["06-overlay-old-child"]["wall_seconds"], 3),
        ]

        db = {
            "c1": {
                "seq_read_dyn_mib_s": 747.718,
                "seq_read_off_mib_s": 487.735,
                "seq_write_dyn_mib_s": 182.792,
                "seq_write_off_mib_s": 237.974,
                "seq_write_jfs_mib_s": 126.599,
                "rand_read_dyn_mib_s": 9.277,
                "rand_read_off_mib_s": 3.960,
                "rand_read_64k_mib_s": 10.261,
                "rand_read_4m_mib_s": 10.222,
                "rand_read_jfs_mib_s": 9.273,
                "rand_write_dyn_mib_s": 0.413,
                "rand_write_off_mib_s": 1.533,
                "rand_write_jfs_mib_s": 0.352,
                "rand_write_off_p99_ms": 82.313,
                "rand_write_jfs_p99_ms": 227.541,
                "zerogap_flat_p99_ms": 35.389,
                "zerogap_ws_p99_ms": 31.064,
                "zerogap_jfs_pre_p99_ms": 62.652,
                "zerogap_jfs_post_p99_ms": 70.779,
                "q_off_mib_s": 9.85,
                "q_on_mib_s": 14.52,
                "fsync_1w_mib_s": 1.02,
                "fsync_8w_mib_s": 2.385,
                "fsync_1w_p99_ms": 120.585,
                "fsync_8w_p99_ms": 41.68,
            },
            "c2": {
                "fanouts": [1, 16, 64],
                "brewfs_fork_s": [0.027, 0.062, 0.178],
                "jfs_clone_s": [4.455, 70.669, 287.680],
                "jfs_overlay_s": [0.076, 1.164, 4.736],
                "brewfs_parent_read_s": brew_parent_reads,
                "brewfs_child_read_s": brew_child_reads,
                "ovl_child_read_s": ovl_child_reads,
                "brewfs_parent_write_s": 23.742,
                "brewfs_child_write_s": 23.409,
            },
            "c3": {
                "packed_dyn_s": 319.666,
                "packed_off_s": 335.794,
                "jfs_redis_s": 1031.938,
                "jfs_tikv_s": 1056.241,
                "native_tikv_s": 9568.590,
            },
            "c4": {
                "agents": [1, 8, 16, 64],
                "brewfs_dyn_s": [4473.070, 2582.904, 2746.062, 4914.838],
                "brewfs_tasks_n": [8, 8, 16, 64],
                "brewfs_off_64_s": 3549.335,
                "fuse_ops_64": 92940160,
                "read_gb_64": 17.902,
                "write_gb_64": 5.228,
                "faster_off_count": faster_off,
                "wall_diff_med_s": round(statistics.median(wall_diffs), 3),
                "verify_on_med_s": round(verify_on_med, 2),
                "verify_off_med_s": round(verify_off_med, 2),
                "lookup_on_med_s": round(lookup_on_med, 2),
                "lookup_off_med_s": round(lookup_off_med, 2),
                "getx_on_med_s": round(getx_on_med, 2),
                "getx_off_med_s": round(getx_off_med, 2),
                "flush_on_med_s": round(flush_on_med, 2),
                "flush_off_med_s": round(flush_off_med, 2),
                "flush_diff_med_s": round(flush_diff_med, 2),
                "put_n_on": put_n_on,
                "put_n_off": put_n_off,
                "put_ms_on": round(put_ms_on, 2),
                "put_ms_off": round(put_ms_off, 2),
                "a007_flush_on_s": round(mounts["a007"]["on"]["flush_s"], 2),
                "a007_flush_off_s": round(mounts["a007"]["off"]["flush_s"], 2),
                "trace_medians": trace_medians,
            },
        }
        data_out.write_text(json.dumps(db, indent=2) + "\n", encoding="utf-8")
        return db
    return json.loads(data_out.read_text(encoding="utf-8"))


def render_c1_c3(db: dict, out_dir: Path, qa_dir: Path, qa_tools: Path) -> None:
    width_pt, height_pt = 240.0, 204.0
    fig, axes = plt.subplots(2, 2, figsize=(width_pt / 72.0, height_pt / 72.0))
    fig.subplots_adjust(
        left=39.0 / width_pt,
        right=235.0 / width_pt,
        bottom=20.0 / height_pt,
        top=164.0 / height_pt,
        wspace=0.66,
        hspace=0.62,
    )

    # (a) C2: Batch fork latency across 1, 16, 64 workspaces (log seconds)
    ax_a = axes[0, 0]
    c2 = db["c2"]
    xs = [0, 1, 2]
    ax_a.plot(
        xs, c2["jfs_clone_s"],
        color=JFS, marker="s", mfc="white", mec=JFS, mew=0.8,
        ms=3.2, lw=0.9, ls=(0, (2.6, 1.8)), zorder=2,
    )
    ax_a.plot(
        xs, c2["jfs_overlay_s"],
        color=OVL, marker="^", mfc="white", mec=OVL, mew=0.8,
        ms=3.4, lw=0.95, ls="-.", zorder=3,
    )
    ax_a.plot(
        xs, c2["brewfs_fork_s"],
        color=BREWFS, marker="o", mfc=BREWFS, mec=INK, mew=0.45,
        ms=3.4, lw=1.15, ls="-", zorder=4,
    )
    ax_a.set_yscale("log")
    ax_a.set_ylim(0.012, 900.0)
    ax_a.yaxis.set_major_locator(FixedLocator([0.03, 1.0, 30.0, 300.0]))
    ax_a.yaxis.set_major_formatter(FixedFormatter(["0.03", "1", "30", "300"]))
    ax_a.yaxis.set_minor_locator(NullLocator())
    ax_a.set_xlim(-0.25, 2.35)
    ax_a.set_xticks(xs, ["1", "16", "64"])
    ax_a.spines[["top", "right"]].set_visible(False)
    ax_a.tick_params(length=2.2, pad=2.0)
    ax_a.set_title("(a) Fork 100K (s)", loc="left", x=-0.22, fontsize=8.8, pad=3.5)
    ax_a.annotate(
        "1,616\u00d7",
        xy=(2.0, c2["brewfs_fork_s"][2]),
        xytext=(0.75, 0.23),
        color=BREWFS,
        fontsize=8.6,
        ha="left",
        va="center",
        arrowprops=dict(arrowstyle="-", color=BREWFS, lw=0.55, shrinkA=1.5, shrinkB=2.0),
    )

    # (b) C2: Zero child-overlay penalty (Parent vs. Child seconds, lower is better)
    ax_b = axes[0, 1]
    p_r_mean = statistics.mean(c2["brewfs_parent_read_s"])
    c_r_mean = statistics.mean(c2["brewfs_child_read_s"])
    ovl_c_mean = statistics.mean(c2["ovl_child_read_s"])
    rows_b = [
        ("10K R", p_r_mean, c_r_mean, BREWFS),
        ("Ovl R", 71.594, ovl_c_mean, OVL),
        ("1K W", c2["brewfs_parent_write_s"], c2["brewfs_child_write_s"], BREWFS),
    ]
    yb = list(range(len(rows_b) - 1, -1, -1))
    bh = 0.34
    for y0, (_, pv, cv, col) in zip(yb, rows_b):
        ax_b.barh(y0 + bh / 2, pv, height=bh, facecolor="white", edgecolor=col, linewidth=0.8, zorder=2)
        ax_b.barh(y0 - bh / 2, cv, height=bh, facecolor=col, edgecolor=INK, linewidth=0.5, zorder=3)
        ax_b.text(max(pv, cv) + 2.5, y0, f"{cv:.1f}s", ha="left", va="center", fontsize=8.6, color=INK)
    ax_b.set_yticks(yb, [r[0] for r in rows_b])
    ax_b.set_xlim(0.0, 128.0)
    ax_b.set_xticks([0, 50, 100])
    ax_b.set_ylim(-0.62, len(rows_b) - 0.38)
    ax_b.spines[["top", "right"]].set_visible(False)
    ax_b.tick_params(length=2.2, pad=2.0)
    ax_b.set_title("(b) Parent/child (s)", loc="left", x=-0.22, fontsize=8.8, pad=3.5)

    # (c) C3: 100K small-file publish / prepare time (minutes, lower is better)
    ax_c = axes[1, 0]
    c3 = db["c3"]
    rows_c = [
        ("Packed", c3["packed_dyn_s"] / 60.0, BREWFS, True),
        ("Pack 1M", c3["packed_off_s"] / 60.0, BREWFS, False),
        ("JFS-R", c3["jfs_redis_s"] / 60.0, JFS, False),
        ("JFS-T", c3["jfs_tikv_s"] / 60.0, SLATE, False),
    ]
    yc = list(range(len(rows_c) - 1, -1, -1))
    for y0, (_, val, col, solid) in zip(yc, rows_c):
        ax_c.barh(
            y0, val, height=0.56,
            facecolor=col if solid else "white",
            edgecolor=INK if solid else col,
            linewidth=0.55 if solid else 0.85,
            zorder=3,
        )
        lbl = f"{val:.1f}m" if y0 < 3 else f"{val:.1f}m (3.3\u00d7)"
        ax_c.text(val + 0.55, y0, lbl, ha="left", va="center", fontsize=8.6, color=BREWFS if y0 == 3 else INK)
    ax_c.set_yticks(yc, [r[0] for r in rows_c])
    ax_c.set_xlim(0.0, 26.5)
    ax_c.set_xticks([0, 10, 20])
    ax_c.set_ylim(-0.60, len(rows_c) - 0.35)
    ax_c.spines[["top", "right"]].set_visible(False)
    ax_c.tick_params(length=2.2, pad=2.0)
    ax_c.set_title("(c) 100K prep (min)", loc="left", x=-0.22, fontsize=8.8, pad=3.5)

    # (d) C1: 4-node fio throughput speedup over JuiceFS Redis (= 1.0x dashed)
    ax_d = axes[1, 1]
    c1 = db["c1"]
    rows_d = [
        ("4K W*", c1["rand_write_off_mib_s"] / c1["rand_write_jfs_mib_s"], BREWFS),
        ("Seq W*", c1["seq_write_off_mib_s"] / c1["seq_write_jfs_mib_s"], BREWFS),
        ("Seq W", c1["seq_write_dyn_mib_s"] / c1["seq_write_jfs_mib_s"], BREWFS),
        ("4K W", c1["rand_write_dyn_mib_s"] / c1["rand_write_jfs_mib_s"], BREWFS),
        ("4K R", c1["rand_read_64k_mib_s"] / c1["rand_read_jfs_mib_s"], BREWFS),
    ]
    yd = list(range(len(rows_d) - 1, -1, -1))
    ax_d.axvline(1.0, color=SLATE, ls=(0, (2.2, 1.8)), lw=0.75, zorder=1)
    for idx_r, (y0, (_, ratio, col)) in enumerate(zip(yd, rows_d)):
        solid = idx_r in (2, 3, 4)
        ax_d.barh(
            y0, ratio, height=0.58,
            facecolor=col if solid else "white",
            edgecolor=INK if solid else col,
            linewidth=0.55 if solid else 0.85,
            zorder=3,
        )
        ax_d.text(ratio + 0.12, y0, f"{ratio:.2f}\u00d7", ha="left", va="center", fontsize=8.6, color=INK)
    ax_d.set_yticks(yd, [r[0] for r in rows_d])
    ax_d.set_xlim(0.0, 6.4)
    ax_d.set_xticks([0, 1, 3, 5])
    ax_d.set_ylim(-0.62, len(rows_d) - 0.35)
    ax_d.spines[["top", "right"]].set_visible(False)
    ax_d.tick_params(length=2.2, pad=2.0)
    ax_d.set_title("(d) fio (\u00d7 JuiceFS)", loc="left", x=-0.22, fontsize=8.8, pad=3.5)

    handles = [
        Line2D([], [], color=BREWFS, marker="o", mfc=BREWFS, mec=INK, mew=0.45, ms=3.2, lw=1.1, label="BrewFS"),
        Line2D([], [], color=OVL, marker="^", mfc="white", mec=OVL, mew=0.8, ms=3.3, ls="-.", lw=0.9, label="JFS Overlay"),
        Line2D([], [], color=JFS, marker="s", mfc="white", mec=JFS, mew=0.8, ms=3.1, ls=(0, (2.6, 1.8)), lw=0.9, label="JFS Redis"),
        Patch(facecolor="white", edgecolor=BREWFS, linewidth=0.8, label="Parent / 8-sync*"),
    ]
    fig.legend(
        handles=handles,
        loc="upper center",
        bbox_to_anchor=(0.50, 0.995),
        ncol=2,
        frameon=False,
        handlelength=1.25,
        columnspacing=0.9,
        handletextpad=0.4,
        labelspacing=0.20,
    )

    audit_and_save(fig, out_dir / "cluster-c1-c3", qa_dir, qa_tools, "c1-c3", width_pt, height_pt)


def render_c4_agents(db: dict, out_dir: Path, qa_dir: Path, qa_tools: Path) -> None:
    width_pt, height_pt = 240.0, 204.0
    fig, axes = plt.subplots(2, 2, figsize=(width_pt / 72.0, height_pt / 72.0))
    fig.subplots_adjust(
        left=42.0 / width_pt,
        right=235.0 / width_pt,
        bottom=20.0 / height_pt,
        top=171.0 / height_pt,
        wspace=0.70,
        hspace=0.60,
    )

    c4 = db["c4"]
    c1 = db["c1"]

    # (a) C4: Aggregate verified agent throughput scaling (tasks/h) across 1, 8, 16, 64 agents
    ax_a = axes[0, 0]
    xs = [0, 1, 2, 3]
    rates_dyn = [n * 3600.0 / t for n, t in zip(c4["brewfs_tasks_n"], c4["brewfs_dyn_s"])]
    rate_off_64 = 64 * 3600.0 / c4["brewfs_off_64_s"]
    ax_a.plot(
        xs, rates_dyn,
        color=BREWFS, marker="o", mfc="white", mec=BREWFS, mew=0.85,
        ms=3.3, lw=0.95, ls=(0, (2.6, 1.8)), zorder=3,
    )
    ax_a.plot(
        [3], [rate_off_64],
        color=BREWFS, marker="o", mfc=BREWFS, mec=INK, mew=0.5,
        ms=3.8, lw=0.0, zorder=4,
    )
    ax_a.plot([3, 3], [rates_dyn[-1], rate_off_64], color=BREWFS, lw=0.85, zorder=2)
    ax_a.annotate(
        f"{rate_off_64:.1f}/h\n({rate_off_64 / rates_dyn[0]:.1f}\u00d7)",
        xy=(3.0, rate_off_64),
        xytext=(0.55, 55.5),
        color=BREWFS,
        fontsize=8.5,
        ha="left",
        va="center",
        arrowprops=dict(arrowstyle="-", color=BREWFS, lw=0.55, shrinkA=1.5, shrinkB=2.5),
    )
    ax_a.set_xlim(-0.25, 3.35)
    ax_a.set_xticks(xs, ["1", "8", "16", "64"])
    ax_a.set_ylim(0.0, 74.0)
    ax_a.set_yticks([0, 30, 60])
    ax_a.spines[["top", "right"]].set_visible(False)
    ax_a.tick_params(length=2.2, pad=2.0)
    ax_a.set_title("(a) Replay (tasks/h)", loc="left", x=-0.24, fontsize=8.8, pad=3.5)

    # (b) 64-agent median task duration across the 8 benchmark families (minutes, dumbbell plot)
    ax_b = axes[0, 1]
    tm = c4["trace_medians"]
    # Group into 6 clean rows: Sklearn, Pytest, AppWorld, BFCL, Logs, Fast-3 (DABench/ML-Vec/Excel)
    fast3_on = statistics.mean([x["med_on_s"] for x in tm if x["label"] in ("Excel", "ML-Vec", "DABench")]) / 60.0
    fast3_off = statistics.mean([x["med_off_s"] for x in tm if x["label"] in ("Excel", "ML-Vec", "DABench")]) / 60.0
    by_lbl = {x["label"]: x for x in tm}
    dumbbells = [
        ("Sklearn", by_lbl["Sklearn"]["med_on_s"] / 60.0, by_lbl["Sklearn"]["med_off_s"] / 60.0),
        ("Pytest", by_lbl["Pytest"]["med_on_s"] / 60.0, by_lbl["Pytest"]["med_off_s"] / 60.0),
        ("AppWld", by_lbl["AppWorld"]["med_on_s"] / 60.0, by_lbl["AppWorld"]["med_off_s"] / 60.0),
        ("BFCL", by_lbl["BFCL"]["med_on_s"] / 60.0, by_lbl["BFCL"]["med_off_s"] / 60.0),
        ("Logs", by_lbl["Logs"]["med_on_s"] / 60.0, by_lbl["Logs"]["med_off_s"] / 60.0),
        ("Other 3", fast3_on, fast3_off),
    ]
    yb = list(range(len(dumbbells) - 1, -1, -1))
    for y0, (_, v_on, v_off) in zip(yb, dumbbells):
        ax_b.plot([v_off, v_on], [y0, y0], color=SLATE, lw=0.85, zorder=2)
        ax_b.plot(v_on, y0, marker="o", mfc="white", mec=BREWFS, mew=0.8, ms=3.0, zorder=3)
        ax_b.plot(v_off, y0, marker="o", mfc=BREWFS, mec=INK, mew=0.45, ms=3.2, zorder=4)
    ax_b.set_yticks(yb, [d[0] for d in dumbbells])
    ax_b.set_xlim(0.0, 74.0)
    ax_b.set_xticks([0, 25, 50, 70])
    ax_b.set_ylim(-0.65, len(dumbbells) - 0.35)
    ax_b.spines[["top", "right"]].set_visible(False)
    ax_b.tick_params(length=2.2, pad=2.0)
    ax_b.set_title("(b) Task med. (min)", loc="left", x=-0.26, fontsize=8.8, pad=3.5)

    # (c) 128-mount median per-agent FUSE & cross-host verify breakdown (log seconds)
    ax_c = axes[1, 0]
    rows_c = [
        ("flush", c4["flush_on_med_s"], c4["flush_off_med_s"]),
        ("verify", c4["verify_on_med_s"], c4["verify_off_med_s"]),
        ("lookup", c4["lookup_on_med_s"], c4["lookup_off_med_s"]),
        ("xattr", c4["getx_on_med_s"], c4["getx_off_med_s"]),
    ]
    yc = list(range(len(rows_c) - 1, -1, -1))
    bh = 0.34
    for y0, (_, v_on, v_off) in zip(yc, rows_c):
        ax_c.barh(y0 + bh / 2, v_on, height=bh, facecolor="white", edgecolor=BREWFS, linewidth=0.8, zorder=2)
        ax_c.barh(y0 - bh / 2, v_off, height=bh, facecolor=BREWFS, edgecolor=INK, linewidth=0.5, zorder=3)
    ax_c.text(
        c4["flush_on_med_s"] * 1.18, yc[0], "\u221246%",
        color=BREWFS, fontsize=8.5, ha="left", va="center",
    )
    ax_c.text(
        c4["lookup_off_med_s"] * 1.25, yc[2], "13.8s",
        color=INK, fontsize=8.5, ha="left", va="center",
    )
    ax_c.set_xscale("log")
    ax_c.set_xlim(1.2, 9500.0)
    ax_c.xaxis.set_major_locator(FixedLocator([2, 20, 200, 1000]))
    ax_c.xaxis.set_major_formatter(FixedFormatter(["2", "20", "200", "1k"]))
    ax_c.xaxis.set_minor_locator(NullLocator())
    ax_c.set_yticks(yc, [r[0] for r in rows_c])
    ax_c.set_ylim(-0.62, len(rows_c) - 0.38)
    ax_c.spines[["top", "right"]].set_visible(False)
    ax_c.tick_params(length=2.2, pad=2.0)
    ax_c.set_title("(c) Mount med. (s)", loc="left", x=-0.24, fontsize=8.8, pad=3.5)

    # (d) Sync-lane concurrency impact (8-worker vs. 1-worker speedup factor)
    ax_d = axes[1, 1]
    rows_d = [
        ("4K p99", c1["fsync_1w_p99_ms"] / c1["fsync_8w_p99_ms"]),
        ("PUT spd", c4["put_ms_on"] / c4["put_ms_off"]),
        ("4K W", c1["fsync_8w_mib_s"] / c1["fsync_1w_mib_s"]),
        ("64-ag.", rate_off_64 / rates_dyn[-1]),
    ]
    yd = list(range(len(rows_d) - 1, -1, -1))
    ax_d.axvline(1.0, color=SLATE, ls=(0, (2.2, 1.8)), lw=0.75, zorder=1)
    for y0, (_, ratio) in zip(yd, rows_d):
        ax_d.barh(y0, ratio, height=0.56, facecolor=BREWFS, edgecolor=INK, linewidth=0.55, zorder=3)
        ax_d.text(ratio + 0.08, y0, f"{ratio:.2f}\u00d7", ha="left", va="center", fontsize=8.6, color=INK)
    ax_d.set_yticks(yd, [r[0] for r in rows_d])
    ax_d.set_xlim(0.0, 4.4)
    ax_d.set_xticks([0, 1, 2, 3, 4])
    ax_d.set_ylim(-0.62, len(rows_d) - 0.35)
    ax_d.spines[["top", "right"]].set_visible(False)
    ax_d.tick_params(length=2.2, pad=2.0)
    ax_d.set_title("(d) 8-sync gain (\u00d7)", loc="left", x=-0.26, fontsize=8.8, pad=3.5)

    handles = [
        Line2D([], [], color=BREWFS, marker="o", mfc="white", mec=BREWFS, mew=0.85,
               ms=3.2, ls=(0, (2.6, 1.8)), lw=0.9, label="BrewFS (1-sync / Dyn)"),
        Line2D([], [], color=BREWFS, marker="o", mfc=BREWFS, mec=INK, mew=0.45,
               ms=3.3, ls="-", lw=1.05, label="BrewFS (8-sync)"),
    ]
    fig.legend(
        handles=handles,
        loc="upper center",
        bbox_to_anchor=(0.50, 0.995),
        ncol=2,
        frameon=False,
        handlelength=1.25,
        columnspacing=0.9,
        handletextpad=0.4,
        labelspacing=0.20,
    )

    audit_and_save(fig, out_dir / "cluster-c4-agents", qa_dir, qa_tools, "c4-agents", width_pt, height_pt)


def write_macros(db: dict, out_dir: Path) -> None:
    c1, c2, c3, c4 = db["c1"], db["c2"], db["c3"], db["c4"]
    rate_1 = 8 * 3600.0 / c4["brewfs_dyn_s"][0]
    rate_64_on = 64 * 3600.0 / c4["brewfs_dyn_s"][3]
    rate_64_off = 64 * 3600.0 / c4["brewfs_off_64_s"]
    macros = {
        "cdForkOneMs": f"{c2['brewfs_fork_s'][0] * 1000.0:.0f}",
        "cdForkOneS": f"{c2['brewfs_fork_s'][0]:.3f}",
        "cdForkSixteenS": f"{c2['brewfs_fork_s'][1]:.3f}",
        "cdForkSixtyFourS": f"{c2['brewfs_fork_s'][2]:.3f}",
        "cdCloneSixtyFourS": f"{c2['jfs_clone_s'][2]:.1f}",
        "cdOverlaySixtyFourS": f"{c2['jfs_overlay_s'][2]:.2f}",
        "cdForkVsCloneSixtyFour": f"{c2['jfs_clone_s'][2] / c2['brewfs_fork_s'][2]:,.0f}",
        "cdForkVsOverlaySixtyFour": f"{c2['jfs_overlay_s'][2] / c2['brewfs_fork_s'][2]:.1f}",
        "cdOverlayVsCloneOne": f"{c2['jfs_clone_s'][0] / c2['jfs_overlay_s'][0]:.1f}",
        "cdOverlayVsCloneSixtyFour": f"{c2['jfs_clone_s'][2] / c2['jfs_overlay_s'][2]:.1f}",
        "cdChildReadRangeS": f"{min(c2['brewfs_child_read_s']):.2f}--{max(c2['brewfs_child_read_s']):.2f}",
        "cdParentReadRangeS": f"{min(c2['brewfs_parent_read_s']):.2f}--{max(c2['brewfs_parent_read_s']):.2f}",
        "cdPackDynS": f"{c3['packed_dyn_s']:.1f}",
        "cdPackOffS": f"{c3['packed_off_s']:.1f}",
        "cdPackVsJfsRedis": f"{c3['jfs_redis_s'] / c3['packed_dyn_s']:.2f}",
        "cdPackVsJfsTiKV": f"{c3['jfs_tikv_s'] / c3['packed_dyn_s']:.2f}",
        "cdPackVsNativeTiKV": f"{c3['native_tikv_s'] / c3['packed_dyn_s']:.1f}",
        "cdSeqWriteOffMiB": f"{c1['seq_write_off_mib_s']:.1f}",
        "cdSeqWriteDynMiB": f"{c1['seq_write_dyn_mib_s']:.1f}",
        "cdSeqWriteJfsMiB": f"{c1['seq_write_jfs_mib_s']:.1f}",
        "cdSeqWriteVsJfs": f"{c1['seq_write_off_mib_s'] / c1['seq_write_jfs_mib_s']:.2f}",
        "cdRandReadDynGain": f"{c1['rand_read_dyn_mib_s'] / c1['rand_read_off_mib_s']:.2f}",
        "cdRandReadHintGain": f"{c1['rand_read_64k_mib_s'] / c1['rand_read_off_mib_s']:.2f}",
        "cdRandWriteVsJfs": f"{c1['rand_write_off_mib_s'] / c1['rand_write_jfs_mib_s']:.2f}",
        "cdAgentRateOne": f"{rate_1:.2f}",
        "cdAgentRateSixtyFourOn": f"{rate_64_on:.1f}",
        "cdAgentRateSixtyFourOff": f"{rate_64_off:.1f}",
        "cdAgentScaleRatio": f"{rate_64_off / rate_1:.1f}",
        "cdAgentMakespanDropPct": f"{(1.0 - c4['brewfs_off_64_s'] / c4['brewfs_dyn_s'][3]) * 100.0:.1f}",
        "cdPutMsOn": f"{c4['put_ms_on']:.1f}",
        "cdPutMsOff": f"{c4['put_ms_off']:.1f}",
        "cdPutSpeedup": f"{c4['put_ms_on'] / c4['put_ms_off']:.2f}",
    }
    lines = [
        "% Generated from experiments/cluster-direct-figures/data.json; do not edit manually.",
        *[f"\\newcommand{{\\{k}}}{{{v}}}" for k, v in macros.items()],
    ]
    (out_dir / "cluster-direct-values.tex").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--cluster-dir",
        type=Path,
        default=Path(r"C:/Users/HXY/.cache/ax-agent-cluster-plan-20261114/results/cluster-direct"),
    )
    ap.add_argument("--paper", type=Path, default=ROOT.parents[1] / "paper")
    ap.add_argument("--data", type=Path, default=ROOT / "data.json")
    ap.add_argument(
        "--qa-tools",
        type=Path,
        default=Path.home() / ".codex/skills/nature-figure/scripts",
    )
    args = ap.parse_args()

    out_dir = args.paper / "figures/pilot"
    out_dir.mkdir(parents=True, exist_ok=True)
    qa_dir = ROOT / "qa"
    qa_dir.mkdir(parents=True, exist_ok=True)

    matplotlib.rcParams.update({
        "font.family": "Arial",
        "font.size": 8.6,
        "axes.labelsize": 8.6,
        "xtick.labelsize": 8.6,
        "ytick.labelsize": 8.6,
        "legend.fontsize": 8.6,
        "axes.linewidth": 0.7,
        "pdf.fonttype": 42,
        "svg.fonttype": "none",
        "figure.facecolor": "white",
        "axes.facecolor": "white",
        "text.color": INK,
        "axes.labelcolor": INK,
        "xtick.color": INK,
        "ytick.color": INK,
    })

    db = build_dataset(args.cluster_dir, args.data)
    render_c1_c3(db, out_dir, qa_dir, args.qa_tools)
    render_c4_agents(db, out_dir, qa_dir, args.qa_tools)
    write_macros(db, out_dir)
    print("Successfully rendered and verified cluster-c1-c3 and cluster-c4-agents.")


if __name__ == "__main__":
    main()
