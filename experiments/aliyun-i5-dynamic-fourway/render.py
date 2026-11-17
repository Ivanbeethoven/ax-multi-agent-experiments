"""Render standalone four-way object-storage evaluation figure and LaTeX macros."""
import argparse
import json
import subprocess
import sys
from itertools import combinations
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.lines import Line2D
from matplotlib.ticker import FixedLocator, FixedFormatter, NullLocator

ROOT = Path(__file__).resolve().parent
INK = "#222222"
RUSTFS = "#D55E00"   # Okabe-Ito vermilion (matches manuscript palette)
MINIO = "#0072B2"    # Okabe-Ito deep blue (colorblind-safe & grayscale-distinct)
SLATE = "#595959"    # Neutral slate for engine-shared layout savings


def pct_change(new, base):
    return (new / base - 1.0) * 100.0


def size_kib(label):
    if label.endswith("KiB"):
        return int(label[:-3])
    if label.endswith("MiB"):
        return int(label[:-3]) * 1024
    raise ValueError(label)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--paper", type=Path, default=ROOT.parents[1] / "paper")
    ap.add_argument("--data", type=Path, default=ROOT / "data.json")
    ap.add_argument(
        "--qa-tools",
        type=Path,
        default=Path.home() / ".codex/skills/nature-figure/scripts",
    )
    args = ap.parse_args()

    db = json.loads(args.data.read_text(encoding="utf-8"))
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

    width_pt, height_pt = 240.0, 268.0
    fig, axes = plt.subplots(3, 2, figsize=(width_pt / 72.0, height_pt / 72.0))
    fig.subplots_adjust(
        left=36.0 / width_pt,
        right=234.5 / width_pt,
        bottom=19.0 / height_pt,
        top=229.0 / height_pt,
        wspace=0.62,
        hspace=0.64,
    )

    range_sizes = db["rangeSizes"][:7]  # 4KiB .. 1MiB matched across all four contenders
    x_kib = [size_kib(s) for s in range_sizes]

    line_styles = [
        ("r_base", RUSTFS, "o", "white", (0, (2.6, 1.8)), 0.85, 3.0),
        ("r_dyn",  RUSTFS, "o", RUSTFS,  "-",             1.05, 3.1),
        ("m_base", MINIO,  "s", "white", (0, (2.6, 1.8)), 0.85, 2.9),
        ("m_dyn",  MINIO,  "s", MINIO,   "-",             1.05, 3.0),
    ]

    # Panels (a) and (b): Cold Range GET throughput (kreq/s) for EC 8+8 and EC 12+4
    for idx, (ec_key, m_base_key, title) in enumerate([
        ("r88",  "minioStock88", "(a) 8+8 range (kreq/s)"),
        ("r124", "minio",        "(b) 12+4 range (kreq/s)"),
    ]):
        ax = axes[0, idx]
        src = db[ec_key]
        series_map = {
            "r_base": [src["plain"][s]["rps"] / 1000.0 for s in range_sizes],
            "r_dyn":  [src["dyn"][s]["rps"] / 1000.0 for s in range_sizes],
            "m_base": [src[m_base_key][s]["rps"] / 1000.0 for s in range_sizes],
            "m_dyn":  [src["minioDyn"][s]["rps"] / 1000.0 for s in range_sizes],
        }
        for key, col, mk, mfc, ls, lw, ms in line_styles:
            mec = INK if mfc != "white" else col
            mew = 0.45 if mfc != "white" else 0.75
            z = 3 if mfc != "white" else 2
            ax.plot(
                x_kib, series_map[key],
                color=col, marker=mk, mfc=mfc, mec=mec, mew=mew,
                ms=ms, lw=lw, ls=ls, zorder=z,
            )

        ax.set_xscale("log", base=2)
        ax.set_xlim(2.7, 1500.0)
        ax.xaxis.set_major_locator(FixedLocator([4, 64, 1024]))
        ax.xaxis.set_major_formatter(FixedFormatter(["4K", "64K", "1M"]))
        ax.xaxis.set_minor_locator(NullLocator())
        ax.set_ylim(0.0, 9.8)
        ax.set_yticks([0, 3, 6, 9])
        ax.spines[["top", "right"]].set_visible(False)
        ax.tick_params(length=2.2, pad=2.0)
        ax.set_title(title, loc="left", x=-0.18 if idx == 1 else -0.16, fontsize=8.8, pad=3.5)

        # Annotate 4 KiB gains symmetrically in open regions above/below the curves
        m_gain = pct_change(series_map["m_dyn"][0], series_map["m_base"][0])
        r_gain = pct_change(series_map["r_dyn"][0], series_map["r_base"][0])
        ax.annotate(
            f"+{m_gain:.1f}%",
            xy=(4.0, series_map["m_dyn"][0]),
            xytext=(15.0, 8.65),
            color=MINIO, fontsize=8.6, ha="left", va="center",
            arrowprops=dict(arrowstyle="-", color=MINIO, lw=0.55, shrinkA=1.5, shrinkB=2.5),
        )
        ax.plot(
            [4.0, 4.0], [series_map["r_base"][0], series_map["r_dyn"][0]],
            color=RUSTFS, lw=0.75, zorder=1,
        )
        ax.annotate(
            f"+{r_gain:.1f}%",
            xy=(4.0, series_map["r_base"][0]),
            xytext=(11.5, 1.25 if idx == 0 else 1.15),
            color=RUSTFS, fontsize=8.6, ha="left", va="center",
            arrowprops=dict(arrowstyle="-", color=RUSTFS, lw=0.55, shrinkA=1.5, shrinkB=2.5),
        )

    # Panel (c): Cold 4 KiB Range GET p99 tail latency (ms, lower is better)
    ax_c = axes[1, 0]
    p99_groups = [
        ("M 12+4", db["r124"]["minio"]["4KiB"]["p99"],       db["r124"]["minioDyn"]["4KiB"]["p99"], MINIO),
        ("R 12+4", db["r124"]["plain"]["4KiB"]["p99"],       db["r124"]["dyn"]["4KiB"]["p99"],      RUSTFS),
        ("M 8+8",  db["r88"]["minioStock88"]["4KiB"]["p99"], db["r88"]["minioDyn"]["4KiB"]["p99"],  MINIO),
        ("R 8+8",  db["r88"]["plain"]["4KiB"]["p99"],        db["r88"]["dyn"]["4KiB"]["p99"],       RUSTFS),
    ]
    cy_pos = list(range(len(p99_groups) - 1, -1, -1))
    bh = 0.34
    for y0, (_, base_v, dyn_v, col) in zip(cy_pos, p99_groups):
        ax_c.barh(
            y0 + bh / 2, base_v, height=bh,
            facecolor="white", edgecolor=col, linewidth=0.8, zorder=2,
        )
        ax_c.barh(
            y0 - bh / 2, dyn_v, height=bh,
            facecolor=col, edgecolor=INK, linewidth=0.55, zorder=3,
        )
        drop = abs(pct_change(dyn_v, base_v))
        ax_c.text(
            max(base_v, dyn_v) + 1.5, y0, f"\u2212{drop:.0f}%",
            ha="left", va="center", fontsize=8.6, color=col,
        )

    ax_c.set_yticks(cy_pos, [g[0] for g in p99_groups])
    ax_c.set_xlim(0.0, 58.0)
    ax_c.set_xticks([0, 20, 40])
    ax_c.set_ylim(-0.62, len(p99_groups) - 0.38)
    ax_c.spines[["top", "right"]].set_visible(False)
    ax_c.tick_params(length=2.2, pad=2.0)
    ax_c.set_title("(c) 4K range p99 (ms)", loc="left", x=-0.16, fontsize=8.8, pad=3.5)

    # Panel (d): Representative whole-object & mixed throughput gains (% vs fixed 1 MiB, EC 12+4)
    ax_d = axes[1, 1]
    fl = db["full"]
    rep_bars = [
        ("64M PUT",  pct_change(fl["minioDyn"]["put"]["64MiB"]["tp"],    fl["minio"]["put"]["64MiB"]["tp"]),    MINIO),
        ("128K Mix", pct_change(fl["minioDyn"]["mixed"]["128KiB"]["rps"], fl["minio"]["mixed"]["128KiB"]["rps"]), MINIO),
        ("4K GET",   pct_change(fl["minioDyn"]["get"]["4KiB"]["tp"],     fl["minio"]["get"]["4KiB"]["tp"]),     MINIO),
        ("16K PUT",  pct_change(fl["dyn"]["put"]["16KiB"]["tp"],         fl["plain"]["put"]["16KiB"]["tp"]),    RUSTFS),
        ("16K Mix",  pct_change(fl["dyn"]["mixed"]["16KiB"]["rps"],      fl["plain"]["mixed"]["16KiB"]["rps"]), RUSTFS),
    ]
    y_pos = list(range(len(rep_bars) - 1, -1, -1))
    for y0, (lbl, gain, col) in zip(y_pos, rep_bars):
        ax_d.barh(
            y0, gain, height=0.58,
            facecolor=col, edgecolor=INK, linewidth=0.55, zorder=3,
        )
        ax_d.text(
            gain + 0.4, y0, f"+{gain:.1f}%",
            ha="left", va="center", fontsize=8.6, color=INK,
        )

    ax_d.set_yticks(y_pos, [b[0] for b in rep_bars])
    ax_d.set_xlim(0.0, 17.8)
    ax_d.set_xticks([0, 5, 10, 15])
    ax_d.set_ylim(-0.6, len(rep_bars) - 0.35)
    ax_d.spines[["top", "right"]].set_visible(False)
    ax_d.tick_params(length=2.2, pad=2.0)
    ax_d.set_title("(d) Whole-object (%)", loc="left", x=-0.18, fontsize=8.8, pad=3.5)

    # Panel (e): Aggregate 16-NVMe cold Range GET read bandwidth (GiB/s, EC 12+4; lower is better)
    ax_e = axes[2, 0]
    d124 = db["disk"]["range"]["12+4"]
    d88 = db["disk"]["range"]["8+8"]
    bw_map = {
        "r_base": [d124[s]["rPlainDiskBW"] / 1024.0 for s in range_sizes],
        "r_dyn":  [d124[s]["rDynDiskBW"] / 1024.0 for s in range_sizes],
        "m_base": [d124[s]["mStockDiskBW"] / 1024.0 for s in range_sizes],
        "m_dyn":  [d124[s]["mDynDiskBW"] / 1024.0 for s in range_sizes],
    }
    for key, col, mk, mfc, ls, lw, ms in line_styles:
        mec = INK if mfc != "white" else col
        mew = 0.45 if mfc != "white" else 0.75
        z = 3 if mfc != "white" else 2
        ax_e.plot(
            x_kib, bw_map[key],
            color=col, marker=mk, mfc=mfc, mec=mec, mew=mew,
            ms=ms, lw=lw, ls=ls, zorder=z,
        )

    ax_e.set_xscale("log", base=2)
    ax_e.set_xlim(2.7, 1500.0)
    ax_e.xaxis.set_major_locator(FixedLocator([4, 64, 1024]))
    ax_e.xaxis.set_major_formatter(FixedFormatter(["4K", "64K", "1M"]))
    ax_e.xaxis.set_minor_locator(NullLocator())
    ax_e.set_ylim(0.0, 6.2)
    ax_e.set_yticks([0, 2, 4, 6])
    ax_e.spines[["top", "right"]].set_visible(False)
    ax_e.tick_params(length=2.2, pad=2.0)
    ax_e.set_title("(e) 12+4 disk BW (GiB/s)", loc="left", x=-0.16, fontsize=8.8, pad=3.5)

    m_bw_drop = -pct_change(bw_map["m_dyn"][0], bw_map["m_base"][0])
    r_bw_drop = -pct_change(bw_map["r_dyn"][0], bw_map["r_base"][0])
    ax_e.annotate(
        f"\u2212{m_bw_drop:.1f}%",
        xy=(4.0, bw_map["m_base"][0]),
        xytext=(10.5, 5.25),
        color=MINIO, fontsize=8.6, ha="left", va="center",
        arrowprops=dict(arrowstyle="-", color=MINIO, lw=0.55, shrinkA=1.5, shrinkB=2.5),
    )
    ax_e.plot(
        [4.0, 4.0], [bw_map["r_dyn"][0], bw_map["r_base"][0]],
        color=RUSTFS, lw=0.75, zorder=1,
    )
    ax_e.annotate(
        f"\u2212{r_bw_drop:.1f}%",
        xy=(4.0, bw_map["r_dyn"][0]),
        xytext=(12.0, 1.55),
        color=RUSTFS, fontsize=8.6, ha="left", va="center",
        arrowprops=dict(arrowstyle="-", color=RUSTFS, lw=0.55, shrinkA=1.5, shrinkB=2.5),
    )

    # Panel (f): Backend disk & memory resource savings (% reduction vs fixed 1 MiB, EC 12+4)
    ax_f = axes[2, 1]
    wcap = {w["short"]: w for w in db["disk"]["writeCap"]}
    sav_rows = [
        ("4K B/req", -pct_change(d124["4KiB"]["rustDynKB"], d124["4KiB"]["stockKB"]), None, SLATE),
        ("\u226464K buf", -wcap["4KiB"]["bufDeltaPct"], None, SLATE),
        ("4K drv", -pct_change(d124["4KiB"]["dynDrives"], d124["4KiB"]["stockDrives"]), None, SLATE),
        (
            "4K IOPS",
            -pct_change(d124["4KiB"]["rDynDiskIOPS"], d124["4KiB"]["rPlainDiskIOPS"]),
            -pct_change(d124["4KiB"]["mDynDiskIOPS"], d124["4KiB"]["mStockDiskIOPS"]),
            None,
        ),
        ("64M ops", -wcap["64MiB (4M)"]["chunkDeltaPct"], None, SLATE),
    ]
    fy_pos = list(range(len(sav_rows) - 1, -1, -1))
    for y0, (lbl, v1, v2, col) in zip(fy_pos, sav_rows):
        if v2 is None:
            ax_f.barh(
                y0, v1, height=0.58,
                facecolor=col, edgecolor=INK, linewidth=0.55, zorder=3,
            )
            ax_f.text(
                v1 + 3.8, y0, f"{v1:.1f}%",
                ha="left", va="center", fontsize=8.6, color=INK,
            )
        else:
            ax_f.barh(
                y0 + 0.16, v1, height=0.30,
                facecolor=RUSTFS, edgecolor=INK, linewidth=0.5, zorder=3,
            )
            ax_f.barh(
                y0 - 0.16, v2, height=0.30,
                facecolor=MINIO, edgecolor=INK, linewidth=0.5, zorder=3,
            )
            ax_f.text(
                max(v1, v2) + 3.8, y0, f"{v1:.0f}/{v2:.0f}%",
                ha="left", va="center", fontsize=8.6, color=INK,
            )

    ax_f.set_yticks(fy_pos, [r[0] for r in sav_rows])
    ax_f.set_xlim(0.0, 160.0)
    ax_f.set_xticks([0, 50, 100])
    ax_f.set_ylim(-0.6, len(sav_rows) - 0.35)
    ax_f.spines[["top", "right"]].set_visible(False)
    ax_f.tick_params(length=2.2, pad=2.0)
    ax_f.set_title("(f) 12+4 savings (%)", loc="left", x=-0.18, fontsize=8.8, pad=3.5)

    # Top 2x2 legend
    handles = [
        Line2D([], [], color=RUSTFS, marker="o", mfc="white", mec=RUSTFS, mew=0.75,
               ms=3.2, ls=(0, (2.6, 1.8)), lw=0.85, label="RustFS (1 MiB)"),
        Line2D([], [], color=RUSTFS, marker="o", mfc=RUSTFS, mec=INK, mew=0.45,
               ms=3.2, ls="-", lw=1.05, label="RustFS (dynamic)"),
        Line2D([], [], color=MINIO, marker="s", mfc="white", mec=MINIO, mew=0.75,
               ms=3.1, ls=(0, (2.6, 1.8)), lw=0.85, label="MinIO (1 MiB)"),
        Line2D([], [], color=MINIO, marker="s", mfc=MINIO, mec=INK, mew=0.45,
               ms=3.1, ls="-", lw=1.05, label="MinIO (dynamic)"),
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
        labelspacing=0.22,
    )

    fig.canvas.draw()
    renderer = fig.canvas.get_renderer()
    scale = 72.0 / fig.dpi

    # Strict canvas-bounds and text-collision check across all visible text artists
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

    # Audit panel alignment
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

    stem = out_dir / "backend-dynamic-standalone"
    fig.savefig(stem.with_suffix(".pdf"), metadata={"CreationDate": None, "ModDate": None})
    fig.savefig(stem.with_suffix(".svg"))
    fig.savefig(stem.with_suffix(".png"), dpi=600)
    plt.close(fig)

    svg_path = stem.with_suffix(".svg")
    svg_path.write_text(
        "\n".join(line.rstrip() for line in svg_path.read_text(encoding="utf-8").splitlines()) + "\n",
        encoding="utf-8",
    )

    # Run PDF font-size floor audit (>= 8.5 pt)
    res_text = subprocess.run(
        [sys.executable, str(args.qa_tools / "audit_pdf_text.py"), str(stem.with_suffix(".pdf")), "--min-pt", "8.5"],
        capture_output=True, text=True, check=False,
    )
    (qa_dir / "pdf_text.json").write_text(res_text.stdout, encoding="utf-8")
    if res_text.returncode != 0:
        raise RuntimeError(f"audit_pdf_text failed:\n{res_text.stdout}\n{res_text.stderr}")

    # Export generated LaTeX macros so manuscript text matches plotted numbers exactly
    r88, r124, full = db["r88"], db["r124"], db["full"]
    r88_r_gains = [pct_change(r88["dyn"][s]["rps"], r88["plain"][s]["rps"]) for s in range_sizes]
    r88_m_gains = [pct_change(r88["minioDyn"][s]["rps"], r88["minioStock88"][s]["rps"]) for s in range_sizes]
    r124_r_gains = [pct_change(r124["dyn"][s]["rps"], r124["plain"][s]["rps"]) for s in range_sizes]
    r124_m_gains = [pct_change(r124["minioDyn"][s]["rps"], r124["minio"][s]["rps"]) for s in range_sizes]

    macros = {
        "dynClusterRuns": str(db["cluster"]["total_runs_reported"]),
        "dynRfsEightFourKBase": f"{r88['plain']['4KiB']['rps'] / 1000.0:.2f}",
        "dynRfsEightFourKDyn": f"{r88['dyn']['4KiB']['rps'] / 1000.0:.2f}",
        "dynRfsEightFourKPct": f"{r88_r_gains[0]:.1f}",
        "dynRfsEightSpanPct": f"{min(r88_r_gains):.1f}--{max(r88_r_gains):.1f}",
        "dynRfsEightPnnDrop": f"{-pct_change(r88['dyn']['4KiB']['p99'], r88['plain']['4KiB']['p99']):.1f}",
        "dynMinioEightFourKBase": f"{r88['minioStock88']['4KiB']['rps'] / 1000.0:.2f}",
        "dynMinioEightFourKDyn": f"{r88['minioDyn']['4KiB']['rps'] / 1000.0:.2f}",
        "dynMinioEightFourKPct": f"{r88_m_gains[0]:.1f}",
        "dynMinioEightSpanPct": f"{min(r88_m_gains):.1f}--{max(r88_m_gains):.1f}",
        "dynMinioEightPnnDrop": f"{-pct_change(r88['minioDyn']['4KiB']['p99'], r88['minioStock88']['4KiB']['p99']):.1f}",
        "dynRfsTwelveFourKBase": f"{r124['plain']['4KiB']['rps'] / 1000.0:.2f}",
        "dynRfsTwelveFourKDyn": f"{r124['dyn']['4KiB']['rps'] / 1000.0:.2f}",
        "dynRfsTwelveFourKPct": f"{r124_r_gains[0]:.1f}",
        "dynRfsTwelveSpanPct": f"{min(r124_r_gains):.1f}--{max(r124_r_gains):.1f}",
        "dynRfsTwelvePnnBase": f"{r124['plain']['4KiB']['p99']:.1f}",
        "dynRfsTwelvePnnDyn": f"{r124['dyn']['4KiB']['p99']:.1f}",
        "dynRfsTwelvePnnDrop": f"{-pct_change(r124['dyn']['4KiB']['p99'], r124['plain']['4KiB']['p99']):.1f}",
        "dynMinioTwelveFourKBase": f"{r124['minio']['4KiB']['rps'] / 1000.0:.2f}",
        "dynMinioTwelveFourKDyn": f"{r124['minioDyn']['4KiB']['rps'] / 1000.0:.2f}",
        "dynMinioTwelveFourKPct": f"{r124_m_gains[0]:.1f}",
        "dynMinioTwelveFourKRatio": f"{r124['minioDyn']['4KiB']['rps'] / r124['minio']['4KiB']['rps']:.2f}",
        "dynMinioTwelveSpanPct": f"{min(r124_m_gains):.1f}--{max(r124_m_gains):.1f}",
        "dynMinioTwelvePnnBase": f"{r124['minio']['4KiB']['p99']:.1f}",
        "dynMinioTwelvePnnDyn": f"{r124['minioDyn']['4KiB']['p99']:.1f}",
        "dynMinioTwelvePnnDrop": f"{-pct_change(r124['minioDyn']['4KiB']['p99'], r124['minio']['4KiB']['p99']):.1f}",
        "dynMinioPutSixtyFourBase": f"{full['minio']['put']['64MiB']['tp']:,.0f}",
        "dynMinioPutSixtyFourDyn": f"{full['minioDyn']['put']['64MiB']['tp']:,.0f}",
        "dynMinioPutSixtyFourPct": f"{pct_change(full['minioDyn']['put']['64MiB']['tp'], full['minio']['put']['64MiB']['tp']):.1f}",
        "dynMinioPutSixtyFourPnnDyn": f"{full['minioDyn']['put']['64MiB']['p99']:,.0f}",
        "dynMinioPutSixtyFourPnnBase": f"{full['minio']['put']['64MiB']['p99']:,.0f}",
        "dynMinioGetSixtyFourDyn": f"{full['minioDyn']['get']['64MiB']['tp']:,.0f}",
        "dynMinioGetSixtyFourPct": f"{pct_change(full['minioDyn']['get']['64MiB']['tp'], full['minio']['get']['64MiB']['tp']):.1f}",
        "dynMinioMixOneTwoEightPct": f"{pct_change(full['minioDyn']['mixed']['128KiB']['rps'], full['minio']['mixed']['128KiB']['rps']):.1f}",
        "dynRfsPutSixteenKPct": f"{pct_change(full['dyn']['put']['16KiB']['tp'], full['plain']['put']['16KiB']['tp']):.1f}",
        "dynRfsMixSixteenKPct": f"{pct_change(full['dyn']['mixed']['16KiB']['rps'], full['plain']['mixed']['16KiB']['rps']):.1f}",
        "dynDiskTwelveFourKStockKB": f"{d124['4KiB']['stockKB']:,.0f}",
        "dynDiskTwelveFourKMinioKB": f"{d124['4KiB']['minioDynKB']:.2f}",
        "dynDiskTwelveFourKRfsKB": f"{d124['4KiB']['rustDynKB']:.2f}",
        "dynDiskTwelveFourKKBDrop": f"{-pct_change(d124['4KiB']['rustDynKB'], d124['4KiB']['stockKB']):.1f}",
        "dynDiskTwelveFourKStockAmp": f"{d124['4KiB']['stockAmp']:.0f}",
        "dynDiskTwelveFourKMinioAmp": f"{d124['4KiB']['minioDynAmp']:.2f}",
        "dynDiskTwelveFourKRfsAmp": f"{d124['4KiB']['rustDynAmp']:.2f}",
        "dynDiskTwelveDrivesDyn": f"{d124['4KiB']['dynDrives']:.2f}",
        "dynDiskTwelveDrivesDrop": f"{-pct_change(d124['4KiB']['dynDrives'], d124['4KiB']['stockDrives']):.1f}",
        "dynDiskEightFourKMinioKB": f"{d88['4KiB']['minioDynKB']:.2f}",
        "dynDiskEightFourKRfsKB": f"{d88['4KiB']['rustDynKB']:.2f}",
        "dynDiskEightFourKMinioAmp": f"{d88['4KiB']['minioDynAmp']:.2f}",
        "dynDiskEightFourKRfsAmp": f"{d88['4KiB']['rustDynAmp']:.2f}",
        "dynDiskEightDrivesDyn": f"{d88['4KiB']['dynDrives']:.2f}",
        "dynDiskEightDrivesDrop": f"{-pct_change(d88['4KiB']['dynDrives'], d88['4KiB']['stockDrives']):.1f}",
        "dynDiskBwTwelveMinioBaseGiB": f"{d124['4KiB']['mStockDiskBW'] / 1024.0:.2f}",
        "dynDiskBwTwelveMinioDynGiB": f"{d124['4KiB']['mDynDiskBW'] / 1024.0:.2f}",
        "dynDiskBwTwelveMinioBaseMiB": f"{d124['4KiB']['mStockDiskBW']:,.0f}",
        "dynDiskBwTwelveMinioDynMiB": f"{d124['4KiB']['mDynDiskBW']:.1f}",
        "dynDiskBwTwelveMinioDrop": f"{-pct_change(d124['4KiB']['mDynDiskBW'], d124['4KiB']['mStockDiskBW']):.1f}",
        "dynDiskBwTwelveRfsBaseGiB": f"{d124['4KiB']['rPlainDiskBW'] / 1024.0:.2f}",
        "dynDiskBwTwelveRfsDynGiB": f"{d124['4KiB']['rDynDiskBW'] / 1024.0:.2f}",
        "dynDiskBwTwelveRfsBaseMiB": f"{d124['4KiB']['rPlainDiskBW']:,.0f}",
        "dynDiskBwTwelveRfsDynMiB": f"{d124['4KiB']['rDynDiskBW']:.1f}",
        "dynDiskBwTwelveRfsDrop": f"{-pct_change(d124['4KiB']['rDynDiskBW'], d124['4KiB']['rPlainDiskBW']):.1f}",
        "dynDiskBwTwelveOneMMinioDrop": f"{-pct_change(d124['1MiB']['mDynDiskBW'], d124['1MiB']['mStockDiskBW']):.1f}",
        "dynDiskBwTwelveOneMRfsDrop": f"{-pct_change(d124['1MiB']['rDynDiskBW'], d124['1MiB']['rPlainDiskBW']):.1f}",
        "dynDiskBwEightMinioDrop": f"{-pct_change(d88['4KiB']['mDynDiskBW'], d88['4KiB']['mStockDiskBW']):.1f}",
        "dynDiskBwEightRfsDrop": f"{-pct_change(d88['4KiB']['rDynDiskBW'], d88['4KiB']['rPlainDiskBW']):.1f}",
        "dynDiskIopsTwelveMinioDrop": f"{-pct_change(d124['4KiB']['mDynDiskIOPS'], d124['4KiB']['mStockDiskIOPS']):.1f}",
        "dynDiskIopsTwelveRfsDrop": f"{-pct_change(d124['4KiB']['rDynDiskIOPS'], d124['4KiB']['rPlainDiskIOPS']):.1f}",
        "dynDiskIopsEightMinioDrop": f"{-pct_change(d88['4KiB']['mDynDiskIOPS'], d88['4KiB']['mStockDiskIOPS']):.1f}",
        "dynDiskIopsEightRfsDrop": f"{-pct_change(d88['4KiB']['rDynDiskIOPS'], d88['4KiB']['rPlainDiskIOPS']):.1f}",
        "dynDiskWrOpsSixtyFourBase": f"{wcap['64MiB (4M)']['stock']['chunkOps']:,d}",
        "dynDiskWrOpsSixtyFourDyn": f"{wcap['64MiB (4M)']['dyn']['chunkOps']:,d}",
        "dynDiskWrOpsSixtyFourDrop": f"{-wcap['64MiB (4M)']['chunkDeltaPct']:.1f}",
        "dynDiskBufSmallDrop": f"{-wcap['4KiB']['bufDeltaPct']:.1f}",
        "dynDiskBufOneTwoEightDrop": f"{-wcap['128KiB']['bufDeltaPct']:.1f}",
        "dynDiskCapHintLogicalPct": f"{wcap['16MiB (64K Hint)']['logicalDeltaPct']:.2f}",
        "dynDiskCapHintXfsPct": f"{wcap['16MiB (64K Hint)']['xfsDeltaPct']:.2f}",
    }

    tex_lines = [
        "% Generated from experiments/aliyun-i5-dynamic-fourway/data.json; do not edit manually.",
        *[f"\\newcommand{{\\{k}}}{{{v}}}" for k, v in macros.items()],
    ]
    (out_dir / "backend-dynamic-values.tex").write_text("\n".join(tex_lines) + "\n", encoding="utf-8")
    (qa_dir / "plot-data.json").write_text(json.dumps(macros, indent=2) + "\n", encoding="utf-8")
    print("Successfully rendered and verified backend-dynamic-standalone.{pdf,svg,png}")


if __name__ == "__main__":
    main()
