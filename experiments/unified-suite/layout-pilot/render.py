"""Four-panel layout pilot. Source ratios are separate from presentation."""
import argparse
import json
import math
import sys
from pathlib import Path
from decimal import Decimal, ROUND_HALF_UP

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

INK, ACCENT, RANGE = '#222222', '#D55E00', '#BDBDBD'
PANELS = {
    'a': ['legacy-01', 'legacy-02'],
    'b': ['legacy-03', 'legacy-05'],
    'c': ['legacy-11', 'legacy-09', 'legacy-10'],
    'd': ['legacy-12', 'legacy-14', 'legacy-16', 'legacy-17',
          'legacy-15', 'legacy-13', 'legacy-18'],
}
TEXT_ROWS = {
    'layoutWholeWarmOne': 'legacy-04',
    'layoutWholeCold': 'legacy-06',
    'layoutWholeWarmEight': 'legacy-07',
    'layoutWarmAll': 'legacy-08',
}

def bounded_hatch(ax, left, right, bottom, top):
    """Draw clipped diagonals explicitly; PDF patterns hide clipping from audits."""
    x0,y0=ax.transData.transform((left,bottom))
    x1,y1=ax.transData.transform((right,top))
    x0,x1=sorted((x0,x1));y0,y1=sorted((y0,y1))
    step=4*ax.figure.dpi/72
    for i in range(math.floor((x0-y1)/step),math.ceil((x1-y0)/step)+1):
        k=i*step
        pts=[]
        for x,y in [(x0,x0-k),(x1,x1-k),(y0+k,y0),(y1+k,y1)]:
            if x0-1e-7<=x<=x1+1e-7 and y0-1e-7<=y<=y1+1e-7:
                if not pts or abs(x-pts[0][0])+abs(y-pts[0][1])>1e-7:
                    pts.append((x,y))
        if len(pts)==2:
            a,b=ax.transData.inverted().transform(pts)
            ax.plot([a[0],b[0]],[a[1],b[1]],color=INK,lw=.4,zorder=4,
                    solid_capstyle='butt')

def control_panel(ax, rows):
    """Horizontal controls keep seven workload labels readable in half a column."""
    labels=['Hot set','Warm R','Seq. R','New file','Mix R','Mix W','Seq. W']
    limit=max(1.5,max(rows[i]['high'] for i in PANELS['d'])*1.45)
    ax.set_xlim(0,limit);ax.set_ylim(6.6,-.6)
    ax.set_yticks(range(7),labels);ax.set_xticks([0,1])
    ax.tick_params(axis='x',length=2.5,pad=2,labelsize=8.5)
    ax.tick_params(axis='y',length=0,pad=3,labelsize=8.3)
    ax.set_xlabel('Throughput (×)',fontsize=8.5,labelpad=2)
    ax.set_title('(d) BrewFS controls',loc='left',fontsize=9,pad=5)
    ax.spines[['right','top']].set_visible(False)
    ax.axvline(1,color='#595959',lw=.85,ls=(0,(3,2)),zorder=2)
    for y,ident in enumerate(PANELS['d']):
        r=rows[ident];lo,hi=r['low'],r['high']
        ax.barh(y,lo,height=.56,color=ACCENT,edgecolor=INK,linewidth=.65,zorder=3)
        if hi>lo:
            ax.barh(y,hi-lo,left=lo,height=.56,color=RANGE,edgecolor=INK,linewidth=.65,zorder=3)
            bounded_hatch(ax,lo,hi,y-.28,y+.28)
        rounded=lambda value:str(Decimal(str(value)).quantize(Decimal('0.01'),rounding=ROUND_HALF_UP))
        label=rounded(lo) if hi==lo else f'{rounded(lo)}–{rounded(hi)}'
        ax.text(limit-.025,y,label,ha='right',va='center',fontsize=8.3)


def draw(data, paper, qa_tools=None):
    records = json.loads(data.read_text(encoding='utf-8'))
    rows = {r['id']: r for r in records['rows']}
    plotted = [i for group in PANELS.values() for i in group]
    assert len(rows) == len(records['rows'])
    assert len(plotted) == len(set(plotted))
    assert set(plotted).isdisjoint(TEXT_ROWS.values())
    assert set(plotted) | set(TEXT_ROWS.values()) == set(rows)
    for r in rows.values():
        if not (0 < r['low'] <= r['high'] and math.isfinite(r['high'])):
            raise ValueError(f"Invalid ratio interval: {r['id']}")
    out, qa = paper/'figures/pilot', paper/'validation/layout-pilot'
    out.mkdir(parents=True, exist_ok=True)
    qa.mkdir(parents=True, exist_ok=True)
    plt.rcParams.update({
        'font.family': 'Arial', 'font.size': 9.6,
        'pdf.fonttype': 42, 'svg.fonttype': 'none',
        'text.color': INK, 'axes.labelcolor': INK,
        'xtick.color': INK, 'ytick.color': INK,
        'axes.linewidth': 0.65, 'axes.edgecolor': INK, 'hatch.linewidth': 0.45,
    })
    fig = plt.figure(figsize=(240/72, 210/72))
    # Single-column 2×2: backend above client; all four plot areas are equal.
    grid = fig.add_gridspec(2,2)
    a,b,c,d = [fig.add_subplot(grid[row,col]) for row in range(2) for col in range(2)]
    fig.subplots_adjust(left=0.12, right=0.99, bottom=0.13,
                        top=0.90, wspace=0.58, hspace=0.45)
    specs = [
        (a, 'a', 'RustFS reads',
         ['64 KiB', '4 MiB'], 'Throughput (×)', (0,40), [0,20,40]),
        (b, 'b', 'RustFS CPU',
         ['Cold', 'Warm'], 'CPU time (×)', (0,1.35), [0,0.5,1]),
        (c, 'c', 'BrewFS reads',
         ['4 KiB\n1 job', '4 KiB\n8 jobs', '64 KiB\n4 jobs'],
         'Throughput (×)', (0,40), [0,20,40]),
        (d, 'd', 'BrewFS controls',
         ['Hot\nset', 'Warm\nR', 'Seq.\nR', 'New\nfile',
          'Mix\nR', 'Mix\nW', 'Seq.\nW'],
         'Throughput (×)', (0,1.35), [0,0.5,1]),
    ]
    for ax, key, title, labels, ylabel, ylim, yticks in specs:
        ax.patch.set_visible(False)
        if key=='d':
            control_panel(ax,rows)
            continue
        ax.set_ylim(*ylim)
        ax.set_xlim(-0.6,len(labels)-0.4)
        ax.set_xticks(range(len(labels)), labels)
        ax.set_yticks(yticks, [f'{v:g}' for v in yticks])
        ax.tick_params(axis='both', length=2.5, pad=2, labelsize=8.5)
        if key=='d':
            ax.tick_params(axis='x', labelsize=8.3)
        ax.set_ylabel(ylabel, fontsize=8.5, labelpad=2)
        ax.set_title(f'({key}) {title}', loc='left', fontsize=9, pad=5)
        for side in ('right','top'):
            ax.spines[side].set_visible(False)
        ax.set_axisbelow(True)
        width=0.52 if key!='d' else 0.57
        ax.axhline(1,color='#595959',lw=.85,ls=(0,(3,2)),zorder=2)
        for x,ident in enumerate(PANELS[key]):
            r=rows[ident]
            lo,hi=(1/r['low'],1/r['high']) if key=='b' else (r['low'],r['high'])
            ax.bar(x,lo,width=width,color=ACCENT,edgecolor=INK,linewidth=.65,zorder=3)
            if hi>lo:
                # A hatched cap is the range across workload cells, not error.
                ax.bar(x,hi-lo,bottom=lo,width=width,color=RANGE,
                       edgecolor=INK,linewidth=.65,zorder=3)
                bounded_hatch(ax,x-width/2,x+width/2,lo,hi)
            label=str(Decimal(str(lo)).quantize(Decimal('0.01'),rounding=ROUND_HALF_UP)) if key in ('b','d') else (
                f'{lo:.1f}–\n{hi:.1f}' if hi>lo else f'{lo:.2f}'.rstrip('0').rstrip('.'))
            if key in ('b','d'):
                # Labels share a baseline above both bars and the 1x reference.
                ceiling=1 if key=='b' else max(1,max(rows[i]['high'] for i in PANELS[key]))
                ax.text(x,ceiling+0.055,label,ha='center',va='bottom',fontsize=8.3)
            else:
                ax.annotate(label,(x,hi),xytext=(0,3),textcoords='offset points',
                            ha='center',va='bottom',fontsize=8.5,linespacing=1.18)
    fig.canvas.draw()
    if qa_tools:
        sys.path.insert(0,str(qa_tools))
        from audit_panel_alignment import require_matplotlib_panel_alignment
        require_matplotlib_panel_alignment(fig, panel_ids=['a','b','c','d'],
            json_out=qa/'alignment.json',overlay_svg=qa/'alignment.svg',
            tolerance_pt=1.5,gutter_tolerance_pt=1.5,strict=True)
    fig.savefig(out/'layout-comparison.pdf',metadata={'CreationDate':None,'ModDate':None})
    fig.savefig(out/'layout-comparison.svg')
    svg=out/'layout-comparison.svg'
    svg.write_text('\n'.join(line.rstrip() for line in svg.read_text(encoding='utf-8').splitlines())+'\n',encoding='utf-8')
    fig.savefig(out/'layout-comparison.png',dpi=300)
    plt.close(fig)
    macros=['% Generated from the same layout-pilot ratio ledger as the figure.']
    macros = [macros[0]] + [
        '\\newcommand{\\'+name+'}{'+f"{rows[ident]['low']:.2f}--{rows[ident]['high']:.2f}"+'}'
        for name,ident in TEXT_ROWS.items()]
    for name,ident in [('layoutCpuCold','legacy-03'),('layoutCpuWarm','legacy-05')]:
        macros.append('\\newcommand{\\'+name+'}{'+f"{1/rows[ident]['low']:.2f}"+'}')
    (out/'layout-values.tex').write_text('\n'.join(macros)+'\n',encoding='utf-8')
    (qa/'data-contract.json').write_text(json.dumps({
        'source':records['source'], 'input_rows':len(rows),
        'plotted_rows':len(plotted), 'plotted_ids':plotted,
        'supporting_rows_in_text':TEXT_ROWS, 'excluded_from_manuscript':[],
        'reason_for_text_rows':'Whole-object controls and the aggregate warm envelope accompany the backend paragraph; figure isolates cold-read gains, CPU cost, and client controls.',
        'interval':'hatched caps span workload-cell min/max, not confidence intervals',
        'cpu_transform':'candidate/baseline = 1 / archived baseline/candidate',
        'rows':records['rows'],
    },indent=2),encoding='utf-8')

if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--data',type=Path,default=Path(__file__).parent/'data/2026-11-11/ratios.json')
    parser.add_argument('--paper',type=Path,required=True)
    parser.add_argument('--qa-tools',type=Path)
    args=parser.parse_args()
    draw(args.data,args.paper,args.qa_tools)
