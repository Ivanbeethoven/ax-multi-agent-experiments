"""Render the two archived workload captures in one compact Background figure."""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
from pathlib import Path
import sys

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import font_manager
from matplotlib.patches import Patch

HERE = Path(__file__).resolve().parent
GROUPS = ("Core", "Exploratory")
GROUP_LABELS = ("Initial runs", "Extended runs")
GROUP_COLORS = ("#236DA0", "#C46A2C")
OPS = {"Metadata": "#2D6084", "Read": "#7D9FB6",
       "Write": "#B65F2D", "Other": "#DEE3E7"}
TASKS = [
    ("django__django-10097", "Django"),
    ("matplotlib__matplotlib-13989", "Matplotlib"),
    ("pallets__flask-5014", "Flask"),
    ("psf__requests-1142", "Requests"),
    ("sphinx-doc__sphinx-10323", "Sphinx"),
    ("astropy__astropy-13398", "Astropy"),
    ("pytest-dev__pytest-5787", "Pytest"),
    ("sympy__sympy-12489", "SymPy"),
]


def save_json(path, data):
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n",
                    encoding="utf-8", newline="\n")


def read_csv(path):
    with path.open(encoding="utf-8-sig", newline="") as stream:
        return list(csv.DictReader(stream))


def load_data():
    data_dir = HERE.parent / "agent-fuse-observations/derived"
    files = [data_dir / name for name in
             ("summary.json", "request-sizes.csv", "timeseries-1s.csv", "tasks.csv")]
    files.append(HERE.parent / "agent-toolchain-observations/data.json")
    summary = json.loads(files[0].read_text(encoding="utf-8"))
    sizes, times = read_csv(files[1]), read_csv(files[2])
    tasks = {r["task"]: r for r in read_csv(files[3])}
    telemetry = json.loads(files[4].read_text(encoding="utf-8"))
    metadata = set(summary["metadata6_methods"])
    assert metadata == set(telemetry["metadata_methods"])
    assert set(tasks) == {task for task, _ in TASKS}
    assert sum(int(r["events"]) for r in tasks.values()) == summary["total_events"]
    mix, lengths, bursts = [], [], []
    for group in GROUPS:
        record = summary["groups"][group]
        counts = [sum(record["methods"].get(m, 0) for m in metadata),
                  record["methods"]["read"], record["methods"]["write"]]
        counts.append(record["events"] - sum(counts))
        errors = [sum(record["method_status"].get(m, {}).get("error", 0)
                      for m in metadata), 0, 0]
        errors.append(record["statuses"]["error"] - sum(errors))
        assert sum(counts) == record["events"]
        assert sum(errors) == record["statuses"]["error"]
        assert all(0 <= e <= n for e, n in zip(errors, counts))
        for category, count, error in zip(OPS, counts, errors):
            mix.append(dict(group=group, category=category, count=count,
                            error=error, denominator=record["events"]))
        for method in ("read", "write"):
            source = [(int(r["request_bytes"]), int(r["count"])) for r in sizes
                      if r["scope"] == group and r["method"] == method]
            assert min(size for size, _ in source) > 0
            bins = [sum(n for size, n in source if size <= 4096),
                    sum(n for size, n in source if 4096 < size <= 65536),
                    sum(n for size, n in source if size > 65536)]
            total = record["size"][method]["n"]
            assert sum(bins) == total == sum(n for _, n in source)
            lengths.extend(dict(group=group, method=method, bin=i,
                                count=n, denominator=total) for i, n in enumerate(bins))
    for task, label in TASKS:
        counts = [int(r["calls"]) for r in times if r["task"] == task]
        assert sum(counts) == int(tasks[task]["events"])
        selected = math.ceil(.1 * len(counts))
        pct = 100 * sum(sorted(counts, reverse=True)[:selected]) / sum(counts)
        assert abs(pct - float(tasks[task]["top_decile_bin_call_pct"])) < 1e-9
        bursts.append(dict(task=task, label=label, windows=len(counts),
                           selected_windows=selected, calls=sum(counts), burst_pct=pct))
    assert sum(r["events"] for r in telemetry["rows"]) == telemetry["events"]
    for row in telemetry["rows"]:
        assert sum(row["classes"].values()) == row["events"]
        assert row["classes"]["Metadata"] == sum(row["operations"].get(m, 0) for m in metadata)
        assert abs(row["metadata_pct"] - 100*row["classes"]["Metadata"]/row["events"]) < 1e-9
        for method in ("read", "write"):
            assert row[method+"_mib"] == row["io"][method+"_bytes"] / 2**20
    return dict(task_events=summary["total_events"], mix=mix, sizes=lengths,
                bursts=bursts, telemetry=telemetry,
                source_hashes={str(p.relative_to(HERE.parent)): hashlib.sha256(p.read_bytes()).hexdigest()
                               for p in files},
                exclusions=[], uncertainty="One observation per task/profile; no replicate intervals")


def render(data, paper, qa_scripts):
    out, qa = paper / "figures/background", paper / "validation/agent-workloads"
    out.mkdir(parents=True, exist_ok=True)
    qa.mkdir(parents=True, exist_ok=True)
    for filename in ("arial.ttf", "arialbd.ttf"):
        path = Path("C:/Windows/Fonts") / filename
        if path.exists():
            font_manager.fontManager.addfont(str(path))
    font_manager.findfont("Arial", fallback_to_default=False)
    plt.rcParams.update({
        "font.family": "Arial", "font.size": 8.2, "axes.labelsize": 8.2,
        "axes.titlesize": 8.6, "xtick.labelsize": 8.2, "ytick.labelsize": 8.2,
        "legend.fontsize": 8.2, "legend.frameon": False,
        "axes.spines.top": False, "axes.spines.right": False,
        "axes.spines.left": False, "axes.linewidth": .6,
        "axes.edgecolor": "#7B858C", "xtick.color": "#303943",
        "ytick.color": "#303943", "pdf.fonttype": 42, "ps.fonttype": 42,
        "svg.fonttype": "none", "svg.hashsalt": "agent-workloads",
    })
    # Two distinct questions: metadata service demand and data request granularity.
    width, height = 240, 172
    fig = plt.figure(figsize=(width/72, height/72))
    def axes(x, y, w, h):
        ax = fig.add_axes([x/width, y/height, w/width, h/height])
        ax.tick_params(axis="both", length=2, pad=2)
        ax.tick_params(axis="y", length=0)
        ax.grid(axis="x", color="#E5E9EB", lw=.45)
        ax.set_axisbelow(True)
        return ax
    a, b = axes(64, 103, 166, 50), axes(64, 28, 166, 42)
    fig.text(4/width, 162/height, "(a) Metadata requests (%)", weight="bold", fontsize=8.6)
    fig.text(4/width, 80/height, "(b) Read/write request sizes (%)", weight="bold", fontsize=8.6)

    a.grid(False)
    profiles = data["telemetry"]["rows"]
    a.set(xlim=(0,60), ylim=(4.55,-.55), xticks=[0,20,40,60],
          yticks=range(5), yticklabels=[r["label"] for r in profiles])
    labels = []
    for i, row in enumerate(profiles):
        share = 100*row["classes"]["Metadata"]/row["events"]
        a.barh(i, share, height=.56, color=OPS["Metadata"], linewidth=0)
        labels.append((a.text(share+1.8, i, f"{share:.1f}", va="center", fontsize=8.2), share, i))

    size_colors = ("#2D6084", "#93B0C2", "#E1E9EE")
    size_labels = ("≤4 KiB", "4–64 KiB", ">64 KiB")
    row_names, row_positions = [], []
    for gi, (group, group_label) in enumerate(zip(GROUPS, ("Initial", "Extended"))):
        for mi, method in enumerate(("read", "write")):
            y = gi*2.35+mi
            row_positions.append(y)
            row_names.append(group_label+" "+method)
            rows = [r for r in data["sizes"] if r["group"]==group and r["method"]==method]
            left = 0
            for row, color in zip(rows, size_colors):
                pct = 100*row["count"]/row["denominator"]
                b.barh(y, pct, left=left, height=.56, color=color,
                       edgecolor="white", linewidth=.4)
                left += pct
            assert abs(left-100)<1e-9
    b.set(xlim=(0,100), ylim=(3.95,-.6), xticks=[0,50,100],
          yticks=row_positions, yticklabels=row_names)
    fig.legend(handles=[Patch(facecolor=c, edgecolor="#71838E", linewidth=.4, label=l)
                        for c,l in zip(size_colors,size_labels)],
               loc="center", bbox_to_anchor=(144/width,5.6/height), ncol=3,
               fontsize=7.8, handlelength=.85, handletextpad=.35,
               columnspacing=1, borderaxespad=0)
    fig.canvas.draw()
    renderer = fig.canvas.get_renderer()
    clearances = []
    for label, share, y in labels:
        box = label.get_window_extent(renderer)
        end = a.transData.transform((share,y))[0]
        gap = (box.x0-end)*72/fig.dpi
        assert gap>=3
        assert box.x1<=fig.bbox.x1
        clearances.append(dict(label=label.get_text(),bar_label_gap_pt=gap))
    sys.path.insert(0,str(qa_scripts))
    from audit_panel_alignment import require_matplotlib_panel_alignment
    require_matplotlib_panel_alignment(fig,json_out=qa/"alignment.json",
        panel_ids=["a","b"],column_groups=[["a","b"]],
        tolerance_pt=1.5,gutter_tolerance_pt=1.5,strict=True)
    for ext in ("pdf","svg"):
        fig.savefig(out/f"agent-workloads.{ext}")
    fig.savefig(out/"agent-workloads.png",dpi=600)
    fig.savefig(qa/"figure-preview.png",dpi=300)
    plt.close(fig)
    svg=out/"agent-workloads.svg"
    svg.write_text("\n".join(line.rstrip() for line in svg.read_text(encoding="utf-8").splitlines())+"\n",encoding="utf-8")
    data["main_figure_panels"] = {
        "a":"Per-profile metadata share; five independent agent/model profiles",
        "b":"Read/write request-size distributions; eight task runs in two capture groups"}
    data["retained_not_plotted"] = {
        "eight_task_call_mix":"The pooled metadata share and successful-only share remain in prose; a second call-mix chart repeated panel a's argument.",
        "per_task_burstiness":"The observed range remains in prose; per-task values and bin semantics remain in the ledger.",
        "profile_call_counts_and_io_bytes":"Preserved in source data; absolute totals do not establish a separate design requirement."}
    save_json(qa/"plotted-data.json",data)
    save_json(qa/"label-clearances.json",clearances)
    print(json.dumps(dict(canvas_pt=[width,height],panels=2,task_events=data["task_events"],
                          profile_events=data["telemetry"]["events"],output=str(out/"agent-workloads.pdf"))))


if __name__ == "__main__":
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--paper",type=Path,default=HERE.parents[1]/"paper")
    parser.add_argument("--qa-scripts",type=Path,default=Path.home()/".codex/skills/nature-figure/scripts")
    args=parser.parse_args()
    if not (args.paper/"main.tex").is_file():
        parser.error("--paper must point to an initialized manuscript checkout")
    render(load_data(),args.paper.resolve(),args.qa_scripts)
