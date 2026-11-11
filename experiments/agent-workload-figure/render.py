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
from matplotlib.lines import Line2D
from matplotlib.patches import Patch, Rectangle
import numpy as np

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
    width, height = 504, 188
    fig = plt.figure(figsize=(width/72, height/72))
    def axes(x, y, w, h):
        ax = fig.add_axes([x/width, y/height, w/width, h/height])
        ax.tick_params(axis="both", length=2, pad=2)
        ax.tick_params(axis="y", length=0)
        ax.grid(axis="x", color="#E5E9EB", lw=.45)
        ax.set_axisbelow(True)
        return ax
    a, b = axes(48, 92, 60, 44), axes(48, 30, 60, 30)
    c, d, e = axes(126, 30, 107, 92), axes(305, 30, 88, 92), axes(412, 30, 88, 92)
    def text(x, y, s, **kw):
        return fig.text(x/width, y/height, s, **kw)
    text(120, 179, "Eight coding-agent task runs", ha="center", weight="bold", fontsize=9)
    text(377, 179, "Five agent/model profiles", ha="center", weight="bold", fontsize=9)
    fig.add_artist(Line2D([247/width]*2, [21/height, 184/height],
                          transform=fig.transFigure, color="#D5DBDF", lw=.6))
    fig.legend(handles=[Patch(facecolor=color, label=label)
                        for label, color in zip(GROUP_LABELS, GROUP_COLORS)],
               loc="center", bbox_to_anchor=(120/width, 165/height), ncol=2,
               handlelength=1, handletextpad=.4, columnspacing=1.4, borderaxespad=0)
    handles = [Patch(facecolor=OPS["Metadata"], label="Metadata"),
               Line2D([], [], color=OPS["Read"], marker="o", linestyle="None", ms=4, label="Read"),
               Line2D([], [], color=OPS["Write"], marker="s", linestyle="None", ms=4, label="Write"),
               Patch(facecolor=OPS["Other"], label="Other")]
    fig.legend(handles=handles, loc="center", bbox_to_anchor=(377/width, 165/height),
               ncol=4, handlelength=.85, handletextpad=.35, columnspacing=.9, borderaxespad=0)
    for x, y, title in [(2,147,"a  Call mix (%)"), (2,72,"b  Request sizes (%)"),
                        (126,147,"c  Burstiness (%)"), (305,147,"d  Calls (×1,000)"),
                        (412,147,"e  I/O (MiB)")]:
        text(x, y, title, weight="bold", fontsize=8.6)
    text(126,134,"Busiest 10% of 1-s bins",fontsize=8.2)
    text(305,134,"Metadata share labeled",fontsize=8.2)
    text(412,134,"Recorded bytes",fontsize=8.2)

    a.set(xlim=(0,70), ylim=(3.55,-.55), xticks=[0,30,60],
          yticks=range(4), yticklabels=list(OPS))
    # Finite hatch strokes avoid PDF pattern tiles extending beyond each bar.
    for gi, (group, color) in enumerate(zip(GROUPS, GROUP_COLORS)):
        for i, row in enumerate(r for r in data["mix"] if r["group"] == group):
            total, error = (100*row[k]/row["denominator"] for k in ("count","error"))
            y = i+(gi-.5)*.31
            a.barh(y,total,height=.27,color=color,lw=0)
            if error == 0:
                continue
            a.barh(y,error,left=total-error,height=.27,facecolor="white",edgecolor=color,lw=.6)
            x0, y0 = a.transData.transform((total-error,y+.135))
            x1, y1 = a.transData.transform((total,y-.135))
            rise = y1-y0
            for base in np.arange(x0-rise,x1,5*fig.dpi/72):
                start,end=max(x0,base),min(x1,base+rise)
                if end > start:
                    points=a.transData.inverted().transform([(start,y0+start-base),(end,y0+end-base)])
                    a.plot(points[:,0],points[:,1],color=color,lw=.55,zorder=3)
    fig.add_artist(Rectangle((85/width,147/height),5/width,5/height,
                             transform=fig.transFigure,facecolor="white",edgecolor="#666666",lw=.6))
    fig.add_artist(Line2D([85/width,90/width],[147/height,152/height],
                          transform=fig.transFigure,color="#666666",lw=.55))
    text(94,147,"Error",fontsize=7.8)

    for mi, method in enumerate(("read","write")):
        for gi, (group,color) in enumerate(zip(GROUPS,GROUP_COLORS)):
            rows=[r for r in data["sizes"] if r["group"] == group and r["method"] == method]
            left=0
            for row,alpha in zip(rows,(1,.3,0)):
                pct=100*row["count"]/row["denominator"]
                b.barh(mi*1.9+gi*.48,pct,left=left,height=.34,
                       color=matplotlib.colors.to_rgba(color,alpha),edgecolor=color,lw=.6)
                left+=pct
            assert abs(left-100) < 1e-9
    b.set(xlim=(0,100),ylim=(2.75,-.45),xticks=[0,50,100],
          yticks=[.24,2.14],yticklabels=["Read","Write"])
    fig.legend(handles=[Patch(facecolor=col,edgecolor="#666666",label=lab)
                        for col,lab in [("#666666","≤4"),("#DDDDDD","4–64"),("white",">64 KiB")]],
               loc="lower left",bbox_to_anchor=(1/width,0),ncol=3,fontsize=7.8,
               handlelength=.7,handletextpad=.25,columnspacing=.55,borderaxespad=0)
    c.set(xlim=(0,100),ylim=(7.65,-.65),xticks=[0,50,100],yticks=[])
    labels=[]
    for i,row in enumerate(data["bursts"]):
        labels.append(c.text(1.5,i,row["label"],ha="left",va="center",zorder=3))
        c.plot(row["burst_pct"],i,marker="o",ms=4,color="#555555",zorder=3)
    fig.canvas.draw()
    clearances=[]
    for i,(label,row) in enumerate(zip(labels,data["bursts"])):
        end=label.get_window_extent(fig.canvas.get_renderer()).x1
        px=c.transData.transform((row["burst_pct"],i))[0]
        clearance=(px-end)*72/fig.dpi-2
        assert clearance >= 3, (label.get_text(),clearance)
        start=c.transData.inverted().transform((end+3*fig.dpi/72,0))[0]
        c.plot([start,row["burst_pct"]],[i,i],color="#BBBBBB",lw=.65,zorder=1)
        clearances.append(dict(task=row["label"],clearance_pt=clearance))

    rows=data["telemetry"]["rows"]
    max_calls=math.ceil(max(r["events"] for r in rows)/10000)*10
    max_io=math.ceil(max(max(r["read_mib"],r["write_mib"]) for r in rows)/100)*100
    io_step=max(100,math.floor(max_io/200)*100)
    for ax in (d,e):
        ax.set(ylim=(4.55,-.55),yticks=range(5))
    d.set(xlim=(0,max_calls),xticks=[0,max_calls/2,max_calls],yticklabels=[r["label"] for r in rows])
    e.set(xlim=(0,max_io),xticks=[0,io_step,2*io_step],yticklabels=[])
    for i,row in enumerate(rows):
        left=0
        for kind,color in OPS.items():
            value=row["classes"][kind]/1000
            d.barh(i,value,left=left,height=.58,color=color,edgecolor="white",lw=.4,zorder=3)
            left+=value
        d.text(row["classes"]["Metadata"]/2000,i,f'{row["metadata_pct"]:.0f}%',
               ha="center",va="center",color="white",fontsize=7.8,zorder=4)
        for kind,marker in [("Read","o"),("Write","s")]:
            e.plot(row[kind.lower()+"_mib"],i,marker=marker,ms=4.3,
                   color=OPS[kind],mec="#38434B",mew=.6,linestyle="None",zorder=3)
    fig.canvas.draw()
    sys.path.insert(0,str(qa_scripts))
    from audit_panel_alignment import require_matplotlib_panel_alignment
    require_matplotlib_panel_alignment(fig,json_out=qa/"alignment.json",
        panel_ids=list("abcde"),row_groups=[["d","e"]],column_groups=[["a","b"]],
        tolerance_pt=1.5,gutter_tolerance_pt=1.5,strict=True)
    for ext in ("pdf","svg"):
        fig.savefig(out/f"agent-workloads.{ext}")
    fig.savefig(out/"agent-workloads.png",dpi=600)
    plt.close(fig)
    svg=out/"agent-workloads.svg"
    svg.write_text("\n".join(line.rstrip() for line in svg.read_text(encoding="utf-8").splitlines())+"\n",encoding="utf-8")
    save_json(qa/"plotted-data.json",data)
    save_json(qa/"label-clearances.json",clearances)
    print(json.dumps(dict(canvas_pt=[width,height],task_events=data["task_events"],
                          profile_events=data["telemetry"]["events"],output=str(out/"agent-workloads.pdf"))))


if __name__ == "__main__":
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--paper",type=Path,default=HERE.parents[1]/"paper")
    parser.add_argument("--qa-scripts",type=Path,default=Path.home()/".codex/skills/nature-figure/scripts")
    args=parser.parse_args()
    if not (args.paper/"main.tex").is_file():
        parser.error("--paper must point to an initialized manuscript checkout")
    render(load_data(),args.paper.resolve(),args.qa_scripts)
