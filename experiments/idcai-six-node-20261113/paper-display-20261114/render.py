"""Verified C1--C4 observations for the manuscript, kept separate from prior pilots.

Contract: Python; 240 pt single-column PDF/SVG; 10 pt text; no error bars for
single-run qualification data. Each display has one question: I/O costs,
replay versus small-file access, or post-publication creation versus remount.
All source rows remain archived; figures select non-redundant endpoint metrics.
"""
import argparse
import csv
import hashlib
import json
import sys
from pathlib import Path

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.lines import Line2D
from matplotlib.patches import Patch
from matplotlib.ticker import FixedLocator, FixedFormatter, NullLocator

ROOT = Path(__file__).resolve().parent
INK, GRAY, ACCENT = '#222222', '#737373', '#D55E00'


def pointer(obj, path):
    for part in path.split('/')[1:]:
        part = part.replace('~1', '/').replace('~0', '~')
        obj = obj[int(part)] if isinstance(obj, list) else obj[part]
    return obj


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--paper', type=Path, required=True)
    ap.add_argument('--metrics', type=Path, required=True)
    ap.add_argument('--source-root', type=Path, default=ROOT.parent)
    ap.add_argument('--qa-tools', type=Path, required=True)
    args = ap.parse_args()
    verification = json.loads((args.metrics/'verification.json').read_text())
    for path, digest in verification['outputs'].items():
        assert hashlib.sha256((args.metrics/path).read_bytes()).hexdigest() == digest
    sources = {}
    for path, digest in verification['sources'].items():
        content = (args.source_root/path).read_bytes()
        assert hashlib.sha256(content).hexdigest() == digest
        sources[path] = json.loads(content)
    rows = list(csv.DictReader((args.metrics/'metrics.csv').open(encoding='utf-8-sig', newline='')))
    assert len(rows) == 472
    for r in rows:
        assert float(pointer(sources[r['source_json']], r['source_pointer'])) == float(r['value'])
        assert r['formal_sample'] == 'False' and r['passed'] == 'True'
    selected = []
    def value(case, phase, metric, unit):
        hits = [r for r in rows if (r['case'], r['phase'], r['metric'], r['unit']) == (case, phase, metric, unit)]
        assert len(hits) == 1, (case, phase, metric, unit, len(hits))
        selected.append(hits[0])
        return float(hits[0]['value'])

    matplotlib.rcParams.update({'font.family': 'Arial', 'font.size': 10,
        'axes.labelsize': 10, 'axes.titlesize': 10, 'xtick.labelsize': 10,
        'ytick.labelsize': 10, 'legend.fontsize': 10, 'axes.linewidth': .8,
        'pdf.fonttype': 42, 'svg.fonttype': 'none', 'figure.facecolor': 'white',
        'axes.facecolor': 'white', 'text.color': INK, 'axes.labelcolor': INK,
        'xtick.color': INK, 'ytick.color': INK})
    out = args.paper/'figures/idcai'
    out.mkdir(parents=True, exist_ok=True)
    qa = ROOT/'qa'
    qa.mkdir(exist_ok=True)
    sys.path.insert(0, str(args.qa_tools))
    from audit_panel_alignment import require_matplotlib_panel_alignment

    def style(ax):
        ax.spines[['top', 'right']].set_visible(False)
        ax.tick_params(length=2.5, pad=2)
        ax.set_axisbelow(True)

    def save(fig, name):
        fig.canvas.draw()
        require_matplotlib_panel_alignment(fig, json_out=qa/f'{name}-alignment.json',
            overlay_svg=qa/f'{name}-alignment.svg', tolerance_pt=1.5,
            gutter_tolerance_pt=1.5, strict=True)
        stem = out/name
        fig.savefig(stem.with_suffix('.pdf'), metadata={'CreationDate': None, 'ModDate': None})
        fig.savefig(stem.with_suffix('.svg'))
        fig.savefig(stem.with_suffix('.png'), dpi=600)
        plt.close(fig)
        svg = stem.with_suffix('.svg')
        svg.write_text('\n'.join(line.rstrip() for line in svg.read_text(encoding='utf-8').splitlines())+'\n', encoding='utf-8')

    # Primary C1 comparison: eight successful matched-mode observations.
    fig, ax = plt.subplots(figsize=(240/72, 162/72))
    fig.subplots_adjust(left=74/240, right=235/240, bottom=34/162, top=140/162)
    style(ax)
    centers = [8.1, 5.5, 2.9, .3]
    ax.set_ylim(-1, 9.4)
    ax.set_yticks(centers, ['Seq. read', 'Seq. write', 'Rand. read', 'Rand. write'])
    ax.tick_params(axis='y', length=0, pad=5)
    ax.set_xscale('log')
    ax.set_xlim(.1, 1000)
    ax.xaxis.set_major_locator(FixedLocator([.1, 1, 10, 100, 1000]))
    ax.xaxis.set_major_formatter(FixedFormatter(['0.1', '1', '10', '100', '1k']))
    ax.xaxis.set_minor_locator(NullLocator())
    ax.get_xticklabels()[-1].set_horizontalalignment('right')
    ax.set_xlabel('Throughput (MiB/s)', labelpad=2)
    io = {}
    for center, mode, phase in zip(centers, ['read', 'write', 'randread', 'randwrite'], ['read', 'write', 'read', 'write']):
        for system, offset, color, marker in [('brewfs', .58, ACCENT, 'o'), ('juicefs', -.58, INK, 's')]:
            v = value(f'c1-{mode}-{system}', phase, 'bw_bytes', 'B/s') / 2**20
            assert v > 0
            io[mode, system] = v
            ax.plot(v, center+offset, marker=marker, ms=3.7, color=color, ls='none')
            label = f'{v:.1f}' if v >= 1 else f'{v:.3f}'
            ax.annotate(label, (v, center+offset), xytext=(5, 0), textcoords='offset points',
                        fontsize=10, color=color, ha='left', va='center')
    fig.legend(handles=[Line2D([], [], color=ACCENT, marker='o', ls='none', ms=3.7, label='BrewFS Flat'),
                        Line2D([], [], color=INK, marker='s', ls='none', ms=3.7, label='JuiceFS')],
               loc='upper center', bbox_to_anchor=(.55, 1.015), ncol=2,
               frameon=False, handlelength=.7, handletextpad=.4, columnspacing=1)
    save(fig, 'io-throughput')

    replay = [value(c, 'replay', 'replay_seconds', 's') for c in ['c2-brewfs-flat', 'c2-brewfs-workspace', 'c2-juicefs']]
    small = [value(c, 'run', 'wall_seconds', 's') for c in ['c3-brewfs-workspace', 'c3-juicefs']]
    fork = value('c4-fork-brewfs-workspace', 'fork', 'elapsed_seconds', 's')
    clone = value('c4-clone-juicefs', 'clone', 'elapsed_seconds', 's')
    publication = value('c4-fork-brewfs-workspace', 'preparation', 'publication_seconds', 's')
    registry = value('c4-fork-brewfs-workspace', 'publication', 'registry_preparation_seconds', 's')
    move = [value(c, 'move-target', 'elapsed_seconds', 's') for c in ['c4-handoff-brewfs-workspace-move','c4-handoff-juicefs-move']]

    def pair_canvas():
        fig, axes = plt.subplots(1, 2, figsize=(240/72, 147/72))
        fig.subplots_adjust(left=30/240, right=236/240, bottom=35/147, top=121/147,
                            wspace=34/86)
        for ax in axes: style(ax)
        return fig, axes

    def bars(ax, values, colors, labels, ymax, ticks, formatter):
        ax.bar(range(len(values)), values, color=colors, width=.58, edgecolor=INK, linewidth=.65)
        ax.set_xlim(-.65, len(values)-.35)
        ax.set_ylim(0, ymax)
        ax.set_yticks(ticks)
        ax.set_xticks(range(len(values)), labels)
        ax.set_title('s', loc='left', pad=3)
        for i, v in enumerate(values):
            ax.annotate(formatter(v), (i,v), xytext=(0,4), textcoords='offset points', ha='center', va='bottom', fontsize=10)

    fig, axes = pair_canvas()
    bars(axes[0], replay, [GRAY,ACCENT,INK], ['F','W','J'], 5100, [0,2000,4000], lambda v:f'{v:.0f}')
    bars(axes[1], small, [ACCENT,INK], ['W','J'], 115, [0,50,100], lambda v:f'{v:.1f}')
    fig.legend(handles=[Patch(facecolor=c, edgecolor=INK, lw=.65, label=l)
                        for c,l in [(GRAY,'Flat'),(ACCENT,'Workspace'),(INK,'JuiceFS')]],
               loc='upper center', bbox_to_anchor=(.52,1.02), ncol=3, frameon=False,
               handlelength=.8, handletextpad=.35, columnspacing=.7)
    for ax,title in zip(axes,['(a) Replay','(b) Small files']):
        box=ax.get_position(); fig.text((box.x0+box.x1)/2, 5/147, title, ha='center', va='bottom', fontsize=10)
    save(fig,'workload-times')

    fig, axes = pair_canvas()
    bars(axes[0], [fork,clone], [ACCENT,INK], ['B','J'], 3.9, [0,1,2,3], lambda v:f'{v:.3f}')
    bars(axes[1], move, [ACCENT,INK], ['B','J'], 9, [0,4,8], lambda v:f'{v:.2f}')
    fig.legend(handles=[Patch(facecolor=c, edgecolor=INK, lw=.65, label=l)
                        for c,l in [(ACCENT,'BrewFS'),(INK,'JuiceFS')]],
               loc='upper center', bbox_to_anchor=(.53,1.02), ncol=2, frameon=False,
               handlelength=.8, handletextpad=.35, columnspacing=1)
    for ax,title in zip(axes,['(a) Fork/clone','(b) Remount']):
        box=ax.get_position(); fig.text((box.x0+box.x1)/2, 5/147, title, ha='center', va='bottom', fontsize=10)
    save(fig,'lifecycle-times')

    phases={}
    for system in ['brewfs-workspace','juicefs']:
        phases[system]={ph:value(f'c3-{system}',ph,'wall_seconds','s') for ph in ['cold-read','lower-prewarm','hot-read','cow','upper-prewarm','hot-write','upper-rewarm','mixed']}
    gap = small[0]-small[1]
    lower_gap = sum(phases['brewfs-workspace'][ph]-phases['juicefs'][ph] for ph in ['cold-read','hot-read'])
    macros = {
        'campReplayFlat':f'{replay[0]:.2f}', 'campReplayWorkspace':f'{replay[1]:.2f}',
        'campReplayJuice':f'{replay[2]:.2f}', 'campReplayRatio':f'{replay[2]/replay[1]:.2f}',
        'campSmallWorkspace':f'{small[0]:.2f}', 'campSmallJuice':f'{small[1]:.2f}',
        'campSmallRatio':f'{small[0]/small[1]:.2f}', 'campLowerGapShare':f'{100*lower_gap/gap:.1f}',
        'campFirstReadWorkspace':f"{phases['brewfs-workspace']['cold-read']:.2f}",
        'campFirstReadJuice':f"{phases['juicefs']['cold-read']:.2f}",
        'campRepeatReadWorkspace':f"{phases['brewfs-workspace']['hot-read']:.2f}",
        'campRepeatReadJuice':f"{phases['juicefs']['hot-read']:.2f}",
        'campFork':f'{fork:.3f}', 'campClone':f'{clone:.3f}', 'campForkRatio':f'{clone/fork:.2f}',
        'campPublicationMinutes':f'{publication/60:.2f}', 'campRegistryMinutes':f'{registry/60:.2f}',
        'campMoveWorkspace':f'{move[0]:.3f}', 'campMoveJuice':f'{move[1]:.3f}',
        'campRandomReadRatio':f"{io['randread','brewfs']/io['randread','juicefs']:.2f}",
    }
    for name,system in [('Flat','c2-brewfs-flat'),('Workspace','c2-brewfs-workspace'),('Juice','c2-juicefs')]:
        for metric in ['operations','actual_read_bytes','actual_write_bytes']:
            unit = 'operations' if metric == 'operations' else 'B'
            hits=[r for r in rows if r['case']==system and r['metric']==metric]
            assert len(hits)==1
            if name=='Flat':
                key={'operations':'campTraceOps','actual_read_bytes':'campTraceReadBytes','actual_write_bytes':'campTraceWriteBytes'}[metric]
                macros[key]=f"{int(float(hits[0]['value'])):,}"
    (out/'values.tex').write_text('% Generated from verified single-run C1--C4 observations.\n'+
        '\n'.join('\\newcommand{\\'+k+'}{'+v+'}' for k,v in macros.items())+'\n')
    # Review tables use the same verified values as the figures. Never edit
    # generated cells to incorporate a new campaign; select its input ledger.
    tables = ROOT/'tables'
    tables.mkdir(exist_ok=True)
    report = ['# 现有实验图表', '',
        '六节点 C1–C4 本轮记录；每项为单次测量。新版本和后续重复实验另列批次。', '',
        'BrewFS Flat 为基础数据路径；Workspace 使用 Packed 只读基线和可写上层。二者不是同引擎 Overlay 开关消融。', '']
    def table(title, name, headers, records, note):
        with (tables/f'{name}.csv').open('w', encoding='utf-8-sig', newline='') as f:
            writer = csv.writer(f); writer.writerow(headers); writer.writerows(records)
        report.extend([f'## {title}', '', '| '+' | '.join(headers)+' |',
                       '| '+' | '.join('---' for _ in headers)+' |'])
        report.extend('| '+' | '.join(str(x) for x in row)+' |' for row in records)
        report.extend(['', note, ''])
    table('I/O 吞吐', 'io-throughput', ['操作', 'BrewFS Flat (MiB/s)', 'JuiceFS (MiB/s)', '吞吐变化'],
        [[label, f"{io[mode,'brewfs']:.3f}", f"{io[mode,'juicefs']:.3f}",
          f"{100*(io[mode,'brewfs']/io[mode,'juicefs']-1):+.1f}%"]
         for mode,label in [('read','顺序读'),('write','顺序写'),('randread','随机读'),('randwrite','随机写')]],
        '64 MiB 输入，单同步任务；顺序 1 MiB、随机 4 KiB，写入包含 fsync。变化 = BrewFS / JuiceFS − 1，正值表示吞吐更高。混合读写缺少 JuiceFS 对照，不纳入本表。')
    tex_rows = []
    for mode,label in [('read','Seq. read'),('write','Seq. write'),('randread','Rand. read'),('randwrite','Rand. write')]:
        b,j=io[mode,'brewfs'],io[mode,'juicefs']
        digits=3 if max(b,j)<1 else 2
        tex_rows.append(f'{label} & {b:.{digits}f} & {j:.{digits}f} & {100*(b/j-1):+.1f}'+r'\% \\')
    (out/'io-throughput-table.tex').write_text(r'''% Generated from the verified metric ledger.
\begin{table}[t]
\centering
\setlength{\tabcolsep}{3pt}
\begin{tabularx}{\columnwidth}{@{}Yrrr@{}}
\toprule
I/O & BrewFS & JuiceFS & Change \\
\midrule
'''+ '\n'.join(tex_rows)+r'''
\bottomrule
\end{tabularx}
\caption{Single-run throughput (MiB/s), with BrewFS Flat's change
relative to JuiceFS. Sequential I/O uses 1\,MiB requests; random I/O
uses 4\,KiB. Writes include \code{fsync}.}
\label{tab:campaign-io}
\end{table}
''',encoding='utf-8')
    table('完整 trace 回放', 'trace-replay', ['系统', '回放时间 (s)', '相对 JuiceFS 耗时降低'],
        [[name, f'{v:.3f}', f'{100*(1-v/replay[2]):.1f}%' if i<2 else '基线']
         for i,(name,v) in enumerate(zip(['BrewFS Flat','BrewFS Workspace','JuiceFS'],replay))],
        '相同的 1,747,109 次文件操作；只计回放，不含输入准备、最终校验和冷重挂。不是 Agent 思考或任务完成时间。')
    phase_labels = [('cold-read','首次下层读取'),('lower-prewarm','下层预热'),('hot-read','重复下层读取'),
                    ('cow','首次私有写入'),('upper-prewarm','上层预热'),('hot-write','重复上层写入'),
                    ('upper-rewarm','上层再次预热'),('mixed','混合读写')]
    phase_rows=[]
    for ph,label in phase_labels:
        b,j=phases['brewfs-workspace'][ph],phases['juicefs'][ph]
        phase_rows.append([label,f'{b:.3f}',f'{j:.3f}',f'{b-j:+.3f}'])
    phase_rows.append(['操作总时间',f'{small[0]:.3f}',f'{small[1]:.3f}',f'{gap:+.3f}'])
    table('十万小文件：阶段耗时', 'smallfile-phases',
        ['阶段','Workspace (s)','JuiceFS (s)','耗时差 (s)'], phase_rows,
        '100,000 × 4 KiB、8 workers。操作前已全树校验，因此首次读取不表示严格冷缓存。总时间来自独立外层计时，不与阶段时间重复相加；正耗时差表示 BrewFS 更慢。')
    table('工作区派生与跨机重挂', 'lifecycle', ['计时范围','BrewFS (s)','JuiceFS (s)'],
        [['十万文件原生 fork / clone',f'{fork:.3f}',f'{clone:.3f}'],
         ['Packed 发布准备',f'{publication:.3f}','—'],
         ['其中：登记审计',f'{registry:.3f}','—'],
         ['目标挂载 + 全树校验 + 写入探测',f'{move[0]:.3f}',f'{move[1]:.3f}']],
        f'fork 使用已发布基线，另需 {publication/60:.2f} min 发布准备；登记审计包含在准备总时间内。横线表示没有同口径测量，不表示零。跨机项为另一份 809 文件输入；不包含 Agent 进程恢复。')
    report.extend(['## 可替换的数据', '',
        '表格和三组 PDF/SVG/PNG 由同一脚本读取指标账本生成；单位和比较公式固定，更新数据后重新运行即可。',
        '布局 B/q 的三组检查用于说明布局持久化和读回正确性，不作为吞吐收益；旧试验和 Aliyun Range 结果保留各自批次。', ''])
    (tables/'RESULTS.zh.md').write_text('\n'.join(report), encoding='utf-8')
    (qa/'data-checks.json').write_text(json.dumps({'verified_metrics':472,'source_files':verification['sources'],
        'case_count':23,'formal_sample':False,'selected_metrics':selected,'macros':macros,
        'figure_roles':{'io-throughput':'I/O costs, eight matched-mode successful C1 cases.',
                        'workload-times':'Replay benefit and small-file bottleneck, both absolute elapsed time.',
                        'lifecycle-times':'Post-publication creation and cross-host remount; distinct timing scopes.'},
        'not_plotted':'Unpaired mixed-I/O, layout correctness, validation stages and detailed percentiles stay in the source ledger; material setup costs are reported in manuscript prose and figure captions.',
        'legacy_results':'Old pilots and Aliyun Range data retain separate figures, source files and configurations.'},indent=2)+'\n')
    print(json.dumps(macros,indent=2))


if __name__=='__main__':
    main()
