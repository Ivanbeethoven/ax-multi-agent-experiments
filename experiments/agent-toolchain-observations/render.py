"""Reconcile ScorpioFS profile summaries and render the Background figure."""
import argparse
from collections import Counter
import csv
import hashlib
import json
import math
from pathlib import Path
import subprocess
import sys

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import font_manager
from matplotlib.lines import Line2D
from matplotlib.patches import Patch

HERE = Path(__file__).resolve().parent
META = ["lookup", "getattr", "getxattr", "readdirplus", "opendir", "releasedir"]
CONFIGS = [
    ("claudecode-minimax-m3", "Claude Code", "MiniMax M3", "machine2"),
    ("codex-gpt56-sol", "Codex", "GPT-5.6 Sol", "machine2"),
    ("dsh-v41-flash", "DeepSeek", "DeepSeek V4.1 Flash", "machine1"),
    ("kimi-k3", "Kimi*", "Kimi K3", "machine1"),
    ("zcode-glm-5.3", "ZCode", "GLM-5.3", "machine2"),
]
COLORS = {"Metadata": "#2D6084", "Read": "#7D9FB6",
          "Write": "#B65F2D", "Other": "#DEE3E7"}


def save_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False)+"\n",
                    encoding="utf-8", newline="\n")


def extract(source):
    """Use numerical source records, not the dashboard's prose or rankings."""
    inputs = ["summary.json", "profile_operations.csv"]
    dirty = subprocess.check_output(
        ["git", "-C", str(source), "status", "--porcelain", "--", *inputs],
        text=True).strip()
    if dirty:
        raise ValueError("Commit source data before creating a versioned snapshot")
    commit = subprocess.check_output(
        ["git", "-C", str(source), "rev-parse", "HEAD"], text=True).strip()
    summary = json.loads((source/"summary.json").read_text(encoding="utf-8"))
    with (source/"profile_operations.csv").open(encoding="utf-8-sig", newline="") as f:
        csv_rows = list(csv.DictReader(f))
    csv_counts = {}
    for r in csv_rows:
        key = (r["run"], r["agent"])
        counts = csv_counts.setdefault(key, Counter())
        assert r["operation"] not in counts, ("duplicate operation", r)
        counts[r["operation"]] = int(r["count"])
    profiles, excluded = {}, []
    expected = {c[0] for c in CONFIGS}
    for run in summary["runs"]:
        for p in run["profiles"]:
            agent = p["metadata"]["agent"]
            counts = p["operations"]
            assert dict(csv_counts[(run["run"], agent)]) == counts
            assert sum(counts.values()) == sum(p["status"].values()) == p["events"]
            assert sum(p["uids"].values()) == p["events"]
            assert int(p["metadata"]["events_written"]) == p["events"]
            assert int(p["metadata"]["dropped_events"]) == p["malformed_rows"] == 0
            assert counts["read"] == p["io"]["read_events"]
            assert counts["write"] == p["io"]["write_events"]
            timeline_counts = Counter()
            for frame in p["timeline"]["frames"]:
                timeline_counts.update(frame["counts"])
            assert dict(timeline_counts) == counts
            if agent == "permission-align":
                excluded.append({"run": run["run"], "events": p["events"],
                                 "reason": "Permission reference, not an additional agent"})
                continue
            assert agent in expected and agent not in profiles
            profiles[agent] = {
                "run": run["run"], "agent_id": agent, "task": p["metadata"]["task"],
                "events": p["events"], "operations": counts, "status": p["status"],
                "errno": p["errno"], "uids": p["uids"], "io": p["io"],
            }
    assert set(profiles) == expected
    rows = []
    for agent, label, model, machine in CONFIGS:
        r = profiles[agent]
        r.update(label=label, model=model, machine=machine)
        r["classes"] = {
            "Metadata": sum(r["operations"].get(k, 0) for k in META),
            "Read": r["io"]["read_events"], "Write": r["io"]["write_events"],
        }
        r["classes"]["Other"] = r["events"]-sum(r["classes"].values())
        assert r["classes"]["Other"] >= 0
        r["metadata_pct"] = 100*r["classes"]["Metadata"]/r["events"]
        r["read_mib"] = r["io"]["read_bytes"]/2**20
        r["write_mib"] = r["io"]["write_bytes"]/2**20
        rows.append(r)
    return {
        "source": {
            "repository": "https://github.com/Ivanbeethoven/scorpiofs-agent-fs-telemetry",
            "commit": commit, "summary_generated_at": summary["generated_at"],
            "files": {name: {"sha256": hashlib.sha256((source/name).read_bytes()).hexdigest(),
                             "bytes": (source/name).stat().st_size} for name in inputs},
            "checked": ["CSV vs JSON operations", "status and UID totals", "read/write counts",
                        "profile header event/dropped counts", "32-bin count reconciliation"],
            "raw_profiles_available_in_repository": False,
            "request_byte_field": "io.*_bytes sums profile bytes, not requested_size",
        },
        "metadata_methods": META, "rows": rows, "excluded": excluded,
        "events": sum(r["events"] for r in rows), "profiles": len(rows),
        "scope": {
            "unit": "One complete mounted profile per agent/model configuration",
            "statuses": "All; setup and cleanup retained",
            "baseline": "Permission reference excluded from agent totals",
            "supplemental": "Retries, diagnostic and terminated traces not mixed into core profiles",
            "kimi": "Discovery profile; most events use UID 0; distinct task label",
            "comparison": "Observed workload footprints, without a controlled agent speed comparison",
            "task_description": "Repository documents disagree about exact edited file; use only shared inspection/edit/verification scope",
            "uncertainty": "No replicate-based interval; events are not independent repeated runs",
        },
    }


def render(data, paper, qa_scripts):
    out, qa = paper/"figures/background", paper/"validation/agent-toolchains"
    out.mkdir(parents=True, exist_ok=True)
    qa.mkdir(parents=True, exist_ok=True)
    for filename in ["arial.ttf", "arialbd.ttf"]:
        path = Path("C:/Windows/Fonts")/filename
        if path.exists():
            font_manager.fontManager.addfont(str(path))
    font_manager.findfont("Arial", fallback_to_default=False)
    plt.rcParams.update({
        "font.family": "Arial", "font.size": 8.4, "axes.titlesize": 8.4,
        "xtick.labelsize": 8.2, "ytick.labelsize": 8.2,
        "axes.spines.top": False, "axes.spines.right": False,
        "axes.spines.left": False, "axes.linewidth": .6,
        "axes.edgecolor": "#7B858C", "xtick.color": "#303943",
        "ytick.color": "#303943", "pdf.fonttype": 42, "ps.fonttype": 42,
        "svg.fonttype": "none", "svg.hashsalt": "agent-toolchain-observations",
    })
    width, height = 240, 148
    fig = plt.figure(figsize=(width/72, height/72))
    grid = fig.add_gridspec(1, 2, left=54/width, right=234/width,
                           bottom=26/height, top=111/height, wspace=24/78)
    axes = [fig.add_subplot(grid[0, 0]), fig.add_subplot(grid[0, 1])]
    for ax in axes:
        ax.set_ylim(4.55, -.55)
        ax.set_yticks(range(5))
        ax.tick_params(axis="both", length=2, pad=2)
        ax.tick_params(axis="y", length=0)
        ax.set_axisbelow(True)
        ax.grid(axis="x", color="#E5E9EB", lw=.45)
    axes[0].set_yticklabels([r["label"] for r in data["rows"]])
    axes[1].set_yticklabels([])
    max_calls = math.ceil(max(r["events"] for r in data["rows"])/10000)*10
    max_io = math.ceil(max(max(r["read_mib"], r["write_mib"]) for r in data["rows"])/100)*100
    io_step = max(100, math.floor(max_io/200)*100)
    axes[0].set_xlim(0, max_calls)
    axes[0].set_xticks([0, max_calls/2, max_calls])
    axes[1].set_xlim(0, max_io)
    axes[1].set_xticks([0, io_step, 2*io_step])
    for i, row in enumerate(data["rows"]):
        left = 0
        for key, color in COLORS.items():
            value = row["classes"][key]/1000
            axes[0].barh(i, value, left=left, height=.58, color=color,
                         linewidth=.4, edgecolor="white", zorder=3)
            left += value
        axes[0].text(row["classes"]["Metadata"]/2000, i,
                     f'{row["metadata_pct"]:.0f}%', color="white",
                     ha="center", va="center", fontsize=7.8, zorder=4)
        for kind, marker in [("Read", "o"), ("Write", "s")]:
            axes[1].plot(row[kind.lower()+"_mib"], i, linestyle="None",
                         marker=marker, ms=4.3, color=COLORS[kind], mew=.6,
                         markeredgecolor="#38434B", zorder=3)
    fig.text(54/width, 121/height, "a  Calls (×1,000)", weight="bold", fontsize=8.4)
    fig.text(156/width, 121/height, "b  I/O (MiB)", weight="bold", fontsize=8.4)
    handles = [
        Patch(facecolor=COLORS["Metadata"], label="Metadata"),
        Line2D([], [], color=COLORS["Read"], marker="o", linestyle="None", ms=4, label="Read"),
        Line2D([], [], color=COLORS["Write"], marker="s", linestyle="None", ms=4, label="Write"),
        Patch(facecolor=COLORS["Other"], label="Other"),
    ]
    fig.legend(handles=handles, loc="upper center", bbox_to_anchor=(.5, .99),
               ncol=4, fontsize=8.2, frameon=False, handlelength=.85,
               handletextpad=.35, columnspacing=.85, borderaxespad=0)
    fig.canvas.draw()
    sys.path.insert(0, str(qa_scripts))
    from audit_panel_alignment import require_matplotlib_panel_alignment
    require_matplotlib_panel_alignment(
        fig, json_out=qa/"alignment.json", panel_ids=["a", "b"],
        row_groups=[["a", "b"]], tolerance_pt=1.5, strict=True)
    for ext in ["pdf", "svg"]:
        fig.savefig(out/f"agent-toolchains.{ext}")
    fig.savefig(out/"agent-toolchains.png", dpi=600)
    plt.close(fig)
    svg = out/"agent-toolchains.svg"
    svg.write_text("\n".join(line.rstrip() for line in svg.read_text(encoding="utf-8").splitlines())+"\n",
                   encoding="utf-8")
    values = {
        "telemetryEvents": f'{data["events"]:,}',
        "telemetryProfiles": str(data["profiles"]),
        "telemetryMetaLow": f'{min(r["metadata_pct"] for r in data["rows"]):.1f}',
        "telemetryMetaHigh": f'{max(r["metadata_pct"] for r in data["rows"]):.1f}',
        "telemetryReadLow": f'{min(r["read_mib"] for r in data["rows"]):.1f}',
        "telemetryReadHigh": f'{max(r["read_mib"] for r in data["rows"]):.1f}',
        "telemetryWriteLow": f'{min(r["write_mib"] for r in data["rows"]):.1f}',
        "telemetryWriteHigh": f'{max(r["write_mib"] for r in data["rows"]):.1f}',
        "telemetryKimiRoot": f'{100*data["rows"][3]["uids"]["0"]/data["rows"][3]["events"]:.1f}',
    }
    (out/"telemetry-values.tex").write_text(
        "% Generated from the pinned agent telemetry snapshot.\n"+
        "".join("\\newcommand{\\"+k+"}{"+v+"}\n" for k,v in values.items()),
        encoding="utf-8")
    save_json(qa/"provenance.json", {k:v for k,v in data.items() if k != "rows"})
    save_json(qa/"plotted-data.json", {
        "rows": [{k:r[k] for k in ["run", "label", "model", "events", "classes",
                                   "metadata_pct", "read_mib", "write_mib"]}
                 for r in data["rows"]], "values": values,
        "caption_scope": "Counts include all statuses; metadata percentages use each profile total",
    })
    print(json.dumps({"events": data["events"], "profiles": data["profiles"],
                      "metadata_pct": [values["telemetryMetaLow"], values["telemetryMetaHigh"]],
                      "output": str(out/"agent-toolchains.pdf")}, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, help="Refresh the snapshot from a clean telemetry checkout")
    parser.add_argument("--paper", type=Path, default=HERE.parents[1]/"paper")
    parser.add_argument("--qa-scripts", type=Path,
                        default=Path.home()/".codex/skills/nature-figure/scripts")
    args = parser.parse_args()
    if not (args.paper/"main.tex").exists():
        parser.error("--paper must identify an initialized manuscript checkout")
    if args.source:
        snapshot = extract(args.source.resolve())
        save_json(HERE/"data.json", snapshot)
    else:
        snapshot = json.loads((HERE/"data.json").read_text(encoding="utf-8"))
    render(snapshot, args.paper.resolve(), args.qa_scripts)
