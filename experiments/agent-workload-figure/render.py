"""Render compact single-row 1x2 Figure 2 (agent filesystem demand) for GraftFS."""
import argparse
import json
import subprocess
import sys
from itertools import combinations
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Patch

ROOT = Path(__file__).resolve().parent
INK = "#222222"
META_GRAY = "#595959"
BIN_COLORS = ["#D55E00", "#BDBDBD", "#FFFFFF"]
BIN_LABELS = ["\u22644 KiB", "4\u201364 KiB", ">64 KiB"]


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--paper", type=Path, default=ROOT.parents[1] / "paper")
    ap.add_argument(
        "--qa-tools",
        type=Path,
        default=Path.home() / ".codex/skills/nature-figure/scripts",
    )
    args = ap.parse_args()

    src_path = args.paper / "validation/agent-workloads/plotted-data.json"
    data = json.loads(src_path.read_text(encoding="utf-8"))
    out_dir = args.paper / "figures/background"
    out_dir.mkdir(parents=True, exist_ok=True)
    qa_dir = args.paper / "validation/agent-workloads"

    matplotlib.rcParams.update({
        "font.family": "Arial",
        "font.size": 8.4,
        "axes.labelsize": 8.4,
        "xtick.labelsize": 8.4,
        "ytick.labelsize": 8.4,
        "legend.fontsize": 8.2,
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

    width_pt, height_pt = 240.0, 106.0
    fig, (ax_a, ax_b) = plt.subplots(1, 2, figsize=(width_pt / 72.0, height_pt / 72.0))
    fig.subplots_adjust(
        left=58.5 / width_pt,
        right=231.0 / width_pt,
        bottom=29.0 / height_pt,
        top=91.5 / height_pt,
        wspace=0.56,
    )

    # Panel (a): Metadata share of all calls across five agent/model profiles
    rows = data["telemetry"]["rows"]
    a_labels = [r["label"] for r in rows]
    a_vals = [float(r["metadata_pct"]) for r in rows]
    ay = list(range(len(rows) - 1, -1, -1))
    for y0, val in zip(ay, a_vals):
        ax_a.barh(
            y0, val, height=0.58,
            facecolor=META_GRAY, edgecolor=INK, linewidth=0.55, zorder=3,
        )
        ax_a.text(
            val + 1.8, y0, f"{val:.1f}",
            ha="left", va="center", fontsize=8.3, color=INK,
        )

    ax_a.set_yticks(ay, a_labels)
    ax_a.set_xlim(0.0, 66.0)
    ax_a.set_xticks([0, 20, 40, 60])
    ax_a.set_ylim(-0.58, len(rows) - 0.42)
    ax_a.spines[["top", "right"]].set_visible(False)
    ax_a.tick_params(length=2.2, pad=2.0)
    ax_a.set_title("(a) Metadata (%)", loc="left", x=-0.82, fontsize=8.8, pad=3.5)

    # Panel (b): Read/write request-size distributions in eight task runs
    size_lookup = {
        (rec["group"], rec["method"], rec["bin"]): 100.0 * rec["count"] / rec["denominator"]
        for rec in data["sizes"]
    }
    b_rows = [
        ("Init. R", ("Core", "read")),
        ("Init. W", ("Core", "write")),
        ("Ext. R",  ("Exploratory", "read")),
        ("Ext. W",  ("Exploratory", "write")),
    ]
    by = [3.15, 2.15, 0.95, -0.05]
    ax_b.axvline(50.0, color="#E2E2E2", lw=0.55, zorder=1)
    for y0, (_, (grp, mth)) in zip(by, b_rows):
        left_acc = 0.0
        for b_idx, fc in enumerate(BIN_COLORS):
            pct = size_lookup[(grp, mth, b_idx)]
            ax_b.barh(
                y0, pct, left=left_acc, height=0.56,
                facecolor=fc, edgecolor=INK, linewidth=0.55, clip_on=False, zorder=3,
            )
            left_acc += pct

    ax_b.set_yticks(by, [lbl for lbl, _ in b_rows])
    ax_b.set_xlim(0.0, 100.0)
    ax_b.set_xticks([0, 50, 100])
    ax_b.set_ylim(-0.58, 3.72)
    ax_b.spines[["top", "right"]].set_visible(False)
    ax_b.tick_params(length=2.2, pad=2.0)
    ax_b.set_title("(b) Req. sizes (%)", loc="left", x=-0.46, fontsize=8.8, pad=3.5)

    handles = [
        Patch(facecolor=fc, edgecolor=INK, linewidth=0.55, label=lbl)
        for fc, lbl in zip(BIN_COLORS, BIN_LABELS)
    ]
    fig.legend(
        handles=handles,
        loc="lower center",
        bbox_to_anchor=(0.60, 0.005),
        ncol=3,
        frameon=False,
        handlelength=0.85,
        handleheight=0.75,
        columnspacing=0.85,
        handletextpad=0.35,
    )

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
                f"Text '{s}' out of bounds [0,{width_pt}]x[0,{height_pt}]: "
                f"({x0:.1f}, {y0:.1f}, {x1:.1f}, {y1:.1f})"
            )
        text_boxes.append((s, (x0, y0, x1, y1)))

    for (s1, b1), (s2, b2) in combinations(text_boxes, 2):
        ix0, iy0 = max(b1[0], b2[0]), max(b1[1], b2[1])
        ix1, iy1 = min(b1[2], b2[2]), min(b1[3], b2[3])
        if ix1 - ix0 > 0.8 and iy1 - iy0 > 0.8:
            raise RuntimeError(f"Text collision between '{s1}' {b1} and '{s2}' {b2}")

    sys.path.insert(0, str(args.qa_tools))
    from audit_panel_alignment import require_matplotlib_panel_alignment
    require_matplotlib_panel_alignment(
        fig,
        json_out=qa_dir / "alignment.json",
        overlay_svg=qa_dir / "alignment.svg",
        tolerance_pt=1.5,
        gutter_tolerance_pt=1.5,
        strict=True,
    )

    stem = out_dir / "agent-workloads"
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
        [sys.executable, str(args.qa_tools / "audit_pdf_text.py"), str(stem.with_suffix(".pdf")), "--min-pt", "8.0"],
        capture_output=True, text=True, check=False,
    )
    (qa_dir / "pdf-text.json").write_text(res_text.stdout, encoding="utf-8")
    if res_text.returncode != 0:
        raise RuntimeError(f"audit_pdf_text failed:\n{res_text.stdout}\n{res_text.stderr}")

    print("Successfully rendered compact 1x2 agent-workloads.{pdf,svg,png}")


if __name__ == "__main__":
    main()
