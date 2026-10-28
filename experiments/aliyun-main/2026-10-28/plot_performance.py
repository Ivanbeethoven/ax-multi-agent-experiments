#!/usr/bin/env python3
"""Plot verified trace measurements, preserving missing and supplemental cells.

Contract: compare actual replay latency for the same trace on paired machines.
One quantitative panel; markers are individual machines, ticks are medians.
Unstarted/failed preflights stay in the source data and are never plotted as zero.
"""
import argparse
import hashlib
import importlib.util
import json
import math
from pathlib import Path
import statistics

import matplotlib as mpl
mpl.use("Agg")
import matplotlib.pyplot as plt

METHODS = ["native", "native-overlay", "native-overlay-pack", "juicefs"]
LABELS = ["Native", "Native + Overlay", "Native + Overlay + Pack", "JuiceFS"]
NODES = ["node-a", "node-b", "node-c"]
COLORS = ["#366D8E", "#C47D3F", "#66845B"]
MARKERS = ["o", "s", "^"]
EXPECTED = (1747109, 281774624, 194706661, 0)

def load_measurements(path):
    data = json.loads(path.read_text(encoding="utf-8"))
    rows = [r for r in data["rows"] if r["selected_for_comparison"]]
    keys = [(r["node"], r["method"]) for r in rows]
    if len(keys) != len(set(keys)):
        raise ValueError("Repeated successful measurements require an explicit analysis policy.")
    expected = {(n, m) for n in NODES for m in METHODS}
    missing = expected - set(keys)
    if set(keys) - expected or missing not in (set(), {("node-c", "native")}):
        raise ValueError("Unexpected or incomplete comparison; inspect source evidence.")
    for r in rows:
        if r["status"] != "ok" or r["strict_correctness"] is not False:
            raise ValueError("Performance-only successful replay required.")
        observed = tuple(r[k] for k in ("operations", "read_bytes", "write_bytes", "remaining_handles"))
        if observed != EXPECTED or any(type(v) is not int for v in observed):
            raise ValueError("Trace work counts or handle count differ.")
        if not math.isfinite(r["execution_seconds"]) or r["execution_seconds"] <= 0:
            raise ValueError("Replay time must be finite and positive.")
        if len(r["archive_sha256"]) != 64:
            raise ValueError("Verified source archive hash required.")
    return data, rows, missing

def draw(rows, missing):
    mpl.rcParams.update({
        "font.family": "sans-serif",
        "font.sans-serif": ["Arial", "Helvetica", "DejaVu Sans"],
        "font.size": 9,
        "axes.labelsize": 9,
        "xtick.labelsize": 8.5,
        "ytick.labelsize": 9.5,
        "axes.spines.top": False,
        "axes.spines.right": False,
        "axes.linewidth": 0.7,
        "legend.frameon": False,
        "svg.fonttype": "none",
        "pdf.fonttype": 42,
        "savefig.dpi": 600,
    })
    fig, ax = plt.subplots(figsize=(183 / 25.4, 105 / 25.4))
    fig.subplots_adjust(left=0.265, right=0.855, bottom=0.29, top=0.82)
    lookup = {(r["node"], r["method"]): r for r in rows}
    ys = [3, 2, 1, 0]
    for i, node in enumerate(NODES):
        offset = (1 - i) * 0.14
        xx = [lookup.get((node, m), {}).get("execution_seconds", math.nan) for m in METHODS]
        yy = [y + offset for y in ys]
        ax.plot(xx, yy, color=COLORS[i], alpha=0.32, linewidth=0.75, zorder=1)
        for j, method in enumerate(METHODS):
            row = lookup.get((node, method))
            if row is None:
                continue
            supplemental = row["source_kind"] == "supplement"
            ax.plot(xx[j], yy[j], marker=MARKERS[i], markersize=4.8,
                    markerfacecolor="white" if supplemental else COLORS[i],
                    markeredgecolor=COLORS[i], markeredgewidth=1,
                    linestyle="none", zorder=3)
        ax.plot([], [], marker=MARKERS[i], color=COLORS[i], linestyle="none",
                markersize=4.8, label="Machine " + node[-1].upper())
    stats = {}
    label_x = max(r["execution_seconds"] for r in rows) + 6
    for y, method in zip(ys, METHODS):
        values = [r["execution_seconds"] for r in rows if r["method"] == method]
        center = statistics.median(values)
        stats[method] = {"n": len(values), "median_seconds": center,
                         "min_seconds": min(values), "max_seconds": max(values)}
        ax.plot([center, center], [y - 0.23, y + 0.23],
                color="#222222", linewidth=1.4, zorder=2)
        ax.text(label_x, y, f"{center:.1f} s  (n={len(values)})",
                va="center", ha="left", fontsize=8.5, clip_on=False)
    ax.set_yticks(ys, LABELS)
    ax.tick_params(axis="y", length=0, pad=9)
    ax.set_xlim(0, 125)
    ax.set_xticks([0, 25, 50, 75, 100, 125])
    ax.set_ylim(-0.5, 3.5)
    ax.set_xlabel("Replay time (s; lower is better)", labelpad=7)
    fig.text(0.265, 0.94, "Full AI filesystem trace", fontsize=11, weight="bold")
    fig.text(0.265, 0.885,
             "1,747,109 callbacks · default kernel caches · performance mode",
             fontsize=8)
    handles, labels = ax.get_legend_handles_labels()
    handles.append(plt.Line2D([], [], color="#222222", marker="|",
                              markersize=8, linestyle="none"))
    labels.append("Median")
    fig.legend(handles, labels, loc="lower left", bbox_to_anchor=(0.255, 0.135),
               ncol=4, fontsize=7.5, handletextpad=0.35, columnspacing=1.0)
    fig.text(0.265, 0.09, "Lines pair the same machine; open marker = later supplemental replay.",
             fontsize=7.5)
    note = ("C Native: no replay measurement; preflight network failure."
            if missing else
            "All 12 configurations completed; preflight failures are retained separately.")
    fig.text(0.265, 0.045, note, fontsize=7.5, color="#555555")
    fig.canvas.draw()
    return fig, stats

def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("summary", type=Path)
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--qa-dir", type=Path, required=True)
    p.add_argument("--alignment-helper", type=Path)
    a = p.parse_args()
    data, rows, missing = load_measurements(a.summary)
    a.output.parent.mkdir(parents=True, exist_ok=True)
    a.qa_dir.mkdir(parents=True, exist_ok=True)
    fig, stats = draw(rows, missing)
    if a.alignment_helper:
        spec = importlib.util.spec_from_file_location("figure_alignment", a.alignment_helper)
        mod = importlib.util.module_from_spec(spec)
        import sys
        sys.modules[spec.name] = mod
        spec.loader.exec_module(mod)
        mod.require_matplotlib_panel_alignment(
            fig, json_out=a.qa_dir / "alignment.json",
            overlay_svg=a.qa_dir / "alignment.svg", tolerance_pt=1.5, strict=True)
    fig.savefig(f"{a.output}.pdf", facecolor="white")
    fig.savefig(f"{a.output}.svg", facecolor="white")
    fig.savefig(f"{a.output}.png", dpi=600, facecolor="white")
    plt.close(fig)
    qa = {
        "question": "How does replay latency compare under the fixed trace and normal kernel caches?",
        "archetype": "single quantitative panel",
        "backend": "Python / matplotlib",
        "summary_sha256": hashlib.sha256(a.summary.read_bytes()).hexdigest(),
        "input_rows": len(data["rows"]), "plotted_rows": len(rows),
        "unselected_rows": [r for r in data["rows"] if not r["selected_for_comparison"]],
        "exclusion_rule": "Only verified completed replays have a latency; original preflight failures remain in source rows.",
        "missing": sorted([list(k) for k in missing]),
        "replicate_unit": "paired machine; one successful replay per method and machine",
        "center": "median", "spread": "all individual observations shown; no inferential interval",
        "statistics": stats, "strict_correctness": False,
    }
    (a.qa_dir / "figure-contract-and-data.json").write_text(
        json.dumps(qa, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    print(json.dumps({"selected": len(rows), "missing": qa["missing"], "statistics": stats}))

if __name__ == "__main__":
    main()

