"""Generate manuscript values and compact single-column plots from pilot ledgers."""
import argparse
import json
import math
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

PHASES = {
    "cold-read": "Initial read", "lower-prewarm": "Read warm-up",
    "hot-read": "Hot read", "cow": "First write",
    "upper-prewarm": "Read after write", "hot-write": "Hot write",
    "upper-rewarm": "Reread after write", "mixed": "Mixed read/write",
}
ARMS = {"brewfs_native": "BrewFS native", "juicefs": "JuiceFS", "local_ext4": "Local ext4"}
CASES = {"fio-read": "Seq. read", "fio-randrw": "Mixed I/O", "pytest-full-02-n1": "Replay"}

# Match the workload characterization: one accent, neutral controls, crisp ink.
ACCENT, CONTROL, LIGHT, INK = '#D55E00', '#595959', '#BDBDBD', '#222222'


def save_plot(fig, out, name, qa, qa_tools=None):
    """Audit final axes geometry, then export live-text vectors."""
    import sys
    directory=qa_tools or Path.home()/'.codex/skills/nature-figure/scripts'
    if qa_tools or (directory/'audit_panel_alignment.py').exists():
        sys.path.insert(0,str(directory))
        from audit_panel_alignment import require_matplotlib_panel_alignment
        fig.canvas.draw()
        require_matplotlib_panel_alignment(
            fig, json_out=qa/f'{name}-alignment.json',
            overlay_svg=qa/f'{name}-alignment.svg',
            tolerance_pt=1.5,gutter_tolerance_pt=1.5,strict=True)
    else:
        print(f'{name}: exporting without alignment audit; supply --qa-tools to enable it.')
    fig.savefig(out/f'{name}.pdf',metadata={'CreationDate':None,'ModDate':None})
    fig.savefig(out/f'{name}.svg')
    svg=out/f'{name}.svg'
    svg.write_text('\n'.join(line.rstrip() for line in svg.read_text(encoding='utf-8').splitlines())+'\n',encoding='utf-8')
    fig.savefig(out/f'{name}.png',dpi=300)
    plt.close(fig)


def plain_axes(ax):
    ax.spines[['top','right']].set_visible(False)
    for side in ('left','bottom'):
        ax.spines[side].set_color(INK)
        ax.spines[side].set_linewidth(.65)
    ax.tick_params(axis='y',length=0,pad=3,labelsize=8.6)
    ax.tick_params(axis='x',length=2,pad=2,labelsize=8.6)


def cost_plot(out, qa, sf, qa_tools):
    """Absolute seconds, three separate zero-based linear scales."""
    from matplotlib.patches import Patch
    from matplotlib.ticker import MaxNLocator
    fig,axes=plt.subplots(1,3,figsize=(240/72,72/72))
    fig.subplots_adjust(left=.095,right=.99,top=.72,bottom=.225,wspace=.48)
    items=[('Prepare','prepare_wall_s',650,[0,250]),
           ('Execute','workload_wall_s',260,[0,100]),
           ('Verify','verify_wall_s',15,[0,10])]
    colors=[ACCENT,CONTROL,LIGHT]
    for ax,(label,key,ceiling,ticks) in zip(axes,items):
        ax.patch.set_visible(False)
        vals=[sf[arm][key] for arm in ARMS]
        ax.bar(range(3),vals,width=.64,color=colors,edgecolor=INK,linewidth=.65)
        limit=max(ceiling,max(vals)*1.36)
        ax.set_xlim(-.65,2.65);ax.set_ylim(0,limit)
        if limit==ceiling:
            ax.set_yticks(ticks)
        else:
            ax.yaxis.set_major_locator(MaxNLocator(nbins=2,min_n_ticks=2))
        ax.set_xticks([])
        ax.set_xlabel(label+' (s)',fontsize=8.6,labelpad=3)
        plain_axes(ax)
        for x,value in enumerate(vals):
            s=f'{value:.0f}' if value>=20 else f'{value:.2f}'.rstrip('0').rstrip('.')
            ax.annotate(s,(x,value),xytext=(0,4),textcoords='offset points',
                        ha='center',va='bottom',fontsize=8.5)
    handles=[Patch(facecolor=c,edgecolor=INK,linewidth=.65,label=label)
             for c,label in zip(colors,['BrewFS native','JuiceFS','Local ext4'])]
    fig.legend(handles=handles,loc='upper center',bbox_to_anchor=(.52,1.02),
               ncol=3,frameon=False,handlelength=.9,handleheight=.7,
               handletextpad=.35,columnspacing=.8,fontsize=8.6)
    save_plot(fig,out,'smallfile-costs',qa,qa_tools)
    return dict(name='smallfile-costs',width_pt=240,height_pt=72,
                encoding='zero-based linear bars; each panel has its own seconds scale',
                labels=[x[0] for x in items],
                series=[dict(label=ARMS[arm],seconds=[sf[arm][x[1]] for x in items]) for arm in ARMS])


def phase_panel(ax,b,j):
    """Normalize each phase to its measured JuiceFS counterpart."""
    ratios=[b[p]['wall_seconds']/j[p]['wall_seconds'] for p in PHASES]
    ax.barh(range(8),ratios,height=.64,color=ACCENT,edgecolor=INK,linewidth=.65,zorder=3)
    ax.axvline(1,color=CONTROL,lw=.85,ls=(0,(3,2)),zorder=2)
    ceiling=max(1.25,max(ratios)*1.15)
    # Short labels leave room for the measurements at single-column width.
    # Full stage names and every unrounded value remain in plot-data.json.
    display=['Initial R','Warm-up','Hot R','First W','R after W',
             'Hot W','Reread','Mixed R/W']
    ax.set_yticks(range(8),display);ax.set_ylim(7.55,-.55)
    ax.set_xlim(0,ceiling);ax.set_xticks([0,.5,1])
    ax.set_xlabel('Elapsed time / JuiceFS',fontsize=8.6,labelpad=3)
    plain_axes(ax)
    return dict(name='smallfile-phases',figure='access-comparison',panel='a',
                encoding='zero-based linear ratio bars; JuiceFS=1',
                labels=list(PHASES.values()),ratios=ratios,
                series=[dict(label=name,seconds=[ph[p]['wall_seconds'] for p in PHASES])
                        for name,ph in [('BrewFS native',b),('JuiceFS',j)]])


def paired_panel(ax,times):
    """Keep missing packed cells absent and the 14 measured seconds auditable."""
    keys=[(backend,case) for backend in ['redis','tikv'] for case in CASES]
    labels=[('Redis' if backend=='redis' else 'TiKV')+' · '+CASES[case] for backend,case in keys]
    ax.axvline(1,color=CONTROL,lw=.85,ls=(0,(3,2)),zorder=2)
    ratios={}
    for si,(arm,color,name) in enumerate([('native',ACCENT,'BrewFS native'),('packed','white','BrewFS packed')]):
        ratios[arm]=[times[k].get(arm,None)/times[k]['juicefs'] if arm in times[k] else None for k in keys]
        points=[(i,v) for i,v in enumerate(ratios[arm]) if v is not None]
        if any(not math.isfinite(v) or v<=0 for _,v in points):
            raise ValueError('Invalid normalized campaign time')
        ax.barh([i+(-.18 if si==0 else .18) for i,_ in points],
                [v for _,v in points],height=.30,color=color,edgecolor=INK,
                linewidth=.65,label=name,zorder=3)
    short_cases={'fio-read':'R','fio-randrw':'R/W','pytest-full-02-n1':'replay'}
    display=[('Redis' if backend=='redis' else 'TiKV')+' '+short_cases[case]
             for backend,case in keys]
    ax.set_yticks(range(6),display);ax.set_ylim(5.55,-.55)
    highest=max(v for values in ratios.values() for v in values if v is not None)
    ax.set_xlim(0,max(3.3,highest*1.12));ax.set_xticks([0,1,2,3])
    ax.set_xlabel('Elapsed time / JuiceFS',fontsize=8.6,labelpad=3)
    plain_axes(ax)
    return dict(name='paired-times',figure='access-comparison',panel='b',
                encoding='zero-based linear ratio bars; JuiceFS=1',
                labels=labels,ratios=ratios,missing='no bar; never zero',
                series=[dict(label=name,seconds=[times[k].get(arm) for k in keys])
                        for arm,name in [('native','BrewFS native'),('juicefs','JuiceFS'),('packed','BrewFS packed')]])


def comparison_plot(out,qa,b,j,times,qa_tools):
    """Two equal plot areas in one row within one manuscript column."""
    fig,axes=plt.subplots(1,2,figsize=(240/72,136/72))
    fig.subplots_adjust(left=.20,right=.99,top=.75,bottom=.18,wspace=1.1082)
    phase=phase_panel(axes[0],b,j)
    paired=paired_panel(axes[1],times)
    # One axis label serves both panels; titles align above their label columns.
    for ax in axes:
        ax.set_xlabel('')
    for x,title in zip([.005,.535],['(a) Small-file phases','(b) I/O and replay']):
        fig.text(x,.815,title,ha='left',va='bottom',fontsize=9.5)
    fig.text(.52,.015,'Elapsed time / JuiceFS',ha='center',va='bottom',fontsize=8.6)
    fig.legend(*axes[1].get_legend_handles_labels(),loc='upper center',
               bbox_to_anchor=(.52,1.015),ncol=2,frameon=False,
               handlelength=.9,handleheight=.75,handletextpad=.4,
               columnspacing=.9,fontsize=8.8)
    save_plot(fig,out,'access-comparison',qa,qa_tools)
    return [phase,paired]

def read(p):
    return json.loads(p.read_text(encoding="utf-8"))

def generate(data, paper, qa_tools=None):
    batch = read(data / "c3_c4_10k_summary.json")
    suite = read(data / "pilot-summary.json")
    conditions = read(data / "conditions.json")
    sf, forks = batch["c3_smallfiles"], batch["c4_fork"]
    n, size = batch["files"], batch["file_bytes"]
    native, juice = sf["brewfs_native"], sf["juicefs"]
    if (not suite["passed"] or n != conditions["smallfiles"]["files"]
            or n * size != batch["dataset_logical_bytes"] or size % 1024):
        raise ValueError("Incomplete batch or inconsistent fixture metadata")
    for arm in ARMS:
        item = sf[arm]
        if not item["verified"] or set(item["phases"]) != PHASES.keys():
            raise ValueError(f"Unverified or incomplete workload: {arm}")
        for name, phase in item["phases"].items():
            if not math.isfinite(phase["wall_seconds"]) or phase["wall_seconds"] <= 0 or any(phase[k] != native["phases"][name][k] for k in ("reads", "writes")):
                raise ValueError(f"Unmatched work or invalid timer: {arm}/{name}")
    times = {}
    for backend in ("redis", "tikv"):
        cells = {s["id"]: s for s in conditions[backend]}
        campaign = suite[backend + "_campaign"]
        if campaign["accepted_blocks"] != len(campaign["blocks"]):
            raise ValueError("Campaign contains unaccepted blocks")
        for block in campaign["blocks"]:
            samples = [cells[i] for i in block["cells"]]
            if not block["complete_accepted_block"] or not all(s["accepted"] and s["paired_block"] == block["id"] for s in samples):
                raise ValueError("Incomplete or unmatched block")
            case, = {s["case"] for s in samples}
            key = backend, case
            if key in times:
                raise ValueError("Multiple repetitions: summarize paired blocks explicitly before publication")
            times[key] = t = {s["arm_id"]: s["wall_seconds"] for s in samples}
            for cmp in block["comparisons"]:
                if cmp["metric"] != "wall_seconds" or not math.isclose(t[cmp["baseline"]] / t[cmp["arm"]], cmp["advantage_ratio"], rel_tol=1e-6):
                    raise ValueError("Wall-time ratio disagrees with campaign ledger")
    out = paper / "figures" / "pilot"
    out.mkdir(parents=True, exist_ok=True)
    def put(name, s):
        (out / name).write_text("% Generated from pilot ledgers.\n" + s + "\n", encoding="utf-8")
    b, j = native["phases"], juice["phases"]
    vals = {
        "Files": f"{n:,}", "KiB": str(size//1024), "Workers": str(conditions["smallfiles"]["workers"]),
        "HotFiles": f'{conditions["smallfiles"]["hot_files"]:,}', "Callbacks": f'{conditions["replay"]["callbacks"]:,}',
        "WorkRatio": f'{juice["workload_wall_s"]/native["workload_wall_s"]:.2f}',
        "WorkReduction": f'{100*(1-native["workload_wall_s"]/juice["workload_wall_s"]):.1f}',
        "WorkNative": f'{native["workload_wall_s"]:.2f}',
        "WorkJuice": f'{juice["workload_wall_s"]:.2f}',
        "MixRatio": f'{j["mixed"]["wall_seconds"]/b["mixed"]["wall_seconds"]:.2f}',
        "HotRatio": f'{j["hot-read"]["wall_seconds"]/b["hot-read"]["wall_seconds"]:.2f}',
        "HotTailReduction": f'{100*(1-b["hot-read"]["io_p99_us"]/j["hot-read"]["io_p99_us"]):.1f}',
        "WorkGap": f'{juice["workload_wall_s"]-native["workload_wall_s"]:.1f}',
        "MixGap": f'{j["mixed"]["wall_seconds"]-b["mixed"]["wall_seconds"]:.1f}',
        "MixReads": f'{b["mixed"]["reads"]:,}', "MixWrites": f'{b["mixed"]["writes"]:,}',
        "FirstNative": f'{b["cow"]["wall_seconds"]:.1f}', "FirstJuice": f'{j["cow"]["wall_seconds"]:.1f}',
        "HotNative": f'{b["hot-read"]["io_p99_us"]/1000:.2f}', "HotJuice": f'{j["hot-read"]["io_p99_us"]/1000:.2f}',
        "ColdNative": f'{b["cold-read"]["io_p99_us"]/1000:.2f}', "ColdJuice": f'{j["cold-read"]["io_p99_us"]/1000:.2f}',
    }
    for macro, arm in [("Fork","brewfs_workspace_v2"),("Clone","juicefs_clone"),("LocalCopy","local_ext4_cp"),("FlatCopy","brewfs_flat_cp")]:
        vals[macro] = f'{forks[arm]["fork_time_s"]:.3f}'
    for backend, suffix in [("redis","Redis"),("tikv","TiKV")]:
        t = times[backend,"fio-randrw"]; vals["Fio"+suffix] = f'{t["juicefs"]/t["native"]:.2f}'
        t = times[backend,"pytest-full-02-n1"]; vals["Pack"+suffix] = f'{t["packed"]/t["native"]:.2f}'
    put("values.tex", "\n".join(r"\newcommand{\pilot"+k+"}{"+v+"}" for k,v in vals.items()))

    from matplotlib import font_manager
    for font in ['arial.ttf','arialbd.ttf']:
        path=Path('C:/Windows/Fonts')/font
        if path.exists():font_manager.fontManager.addfont(str(path))
    plt.rcParams.update({"font.size":9, "font.family":"sans-serif", "font.sans-serif":["Arial","DejaVu Sans"],
                         "pdf.fonttype":42,"svg.fonttype":"none",
                         "text.color":INK,"axes.labelcolor":INK,
                         "xtick.color":INK,"ytick.color":INK})
    qa=paper/'validation/paper-pilot'
    qa.mkdir(parents=True,exist_ok=True)
    plotted=[cost_plot(out,qa,sf,qa_tools),
             *comparison_plot(out,qa,b,j,times,qa_tools)]
    for item in plotted:item['repetitions']=1
    (qa/'plot-data.json').write_text(json.dumps(plotted,indent=2),encoding='utf-8')

if __name__ == "__main__":
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--data", type=Path, required=True)
    ap.add_argument("--paper", type=Path, required=True)
    ap.add_argument("--qa-tools",type=Path)
    args=ap.parse_args()
    generate(args.data, args.paper, args.qa_tools)
