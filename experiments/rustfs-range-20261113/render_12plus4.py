"""Render the seven EC 12+4 conditions in two side-by-side, single-column panels.

Question: how do smaller coding blocks affect Range GET throughput and latency?
Panel a shows absolute request throughput across all request sizes; panel b
checks median and tail latency. All 49 non-redundant observations are plotted.
One run per condition: no invented uncertainty intervals. Percentages use the
displayed absolute values. The 4 KiB baseline startup errors stay in the caption.
Contract: quantitative grid, Python, 240 x 164 pt, editable text, 10 pt throughout.
"""
import argparse
import json
import sys
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.lines import Line2D
from matplotlib.ticker import FixedLocator, FixedFormatter, NullLocator

from export_12plus4 import change

ROOT = Path(__file__).resolve().parent
INK, GRAY, ACCENT = '#222222', '#737373', '#D55E00'


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--paper', type=Path, required=True)
    ap.add_argument('--qa-tools', type=Path, required=True)
    args = ap.parse_args()
    rows = json.loads((ROOT / 'source/12plus4-author-table.json').read_text())['rows']
    archive = {(r['ec'], r['series'], r['range_size']): r
               for r in json.loads((ROOT / 'data.json').read_text())['observations']}
    assert len(rows) == 7
    checked = []
    for r in rows:
        for prefix, series in [('base', '105p1'), ('hint256', 'dyn256k'), ('hint64', 'dyn64k')]:
            # Preserve the bandwidth source check; draw its equivalent request
            # rate so growing request sizes do not dominate the vertical scale.
            assert r[f'{prefix}_mib_s'] == archive['12+4', series, r['size']]['mib_per_s']
            pairs = [('req_s', 'req_per_s')]
            if prefix != 'hint256':
                pairs += [('p50_ms', 'p50_ms'), ('p99_ms', 'p99_ms')]
            for field, source in pairs:
                value = r[f'{prefix}_{field}']
                source_value = archive['12+4', series, r['size']][source]
                if field == 'req_s':
                    # The replacement table reports rates to one decimal place.
                    assert Decimal(str(value)) == Decimal(str(source_value)).quantize(Decimal('0.1'), rounding=ROUND_HALF_UP)
                else:
                    assert value == source_value
                assert value > 0
                checked.append({'range': r['size'], 'field': f'{prefix}_{field}', 'value': value})
    x = [4, 16, 32, 64, 128, 256, 1024]
    assert all(a < b for a, b in zip(x, x[1:]))
    out = args.paper / 'figures/pilot'
    out.mkdir(parents=True, exist_ok=True)
    qa = ROOT / 'qa'
    qa.mkdir(exist_ok=True)
    matplotlib.rcParams.update({
        'font.family': 'Arial', 'font.size': 10, 'axes.labelsize': 10,
        'xtick.labelsize': 10, 'ytick.labelsize': 10, 'legend.fontsize': 10,
        'axes.linewidth': .8, 'pdf.fonttype': 42, 'svg.fonttype': 'none',
        'figure.facecolor': 'white', 'axes.facecolor': 'white',
        'text.color': INK, 'axes.labelcolor': INK,
        'xtick.color': INK, 'ytick.color': INK,
    })
    fig, axes = plt.subplots(1, 2, figsize=(240 / 72, 164 / 72))
    fig.subplots_adjust(left=26/240, right=236/240, bottom=43/164,
                        top=133/164, wspace=30/90)
    styles = {
        'base': dict(color=INK, marker='^', mfc='white', ls=(0, (3, 2))),
        'hint256': dict(color=GRAY, marker='s', mfc='white', ls='-'),
        'hint64': dict(color=ACCENT, marker='o', mfc=ACCENT, ls='-'),
    }
    for ax in axes:
        ax.set_xscale('log', base=2)
        ax.set_xlim(2.8, 1450)
        ax.xaxis.set_major_locator(FixedLocator([4, 16, 64, 1024]))
        ax.xaxis.set_major_formatter(FixedFormatter(['4', '16', '64', '1024']))
        ax.xaxis.set_minor_locator(NullLocator())
        ax.get_xticklabels()[-1].set_horizontalalignment('right')
        ax.tick_params(length=2.5, pad=2)
        ax.spines[['top', 'right']].set_visible(False)
        ax.set_axisbelow(True)
        ax.grid(axis='y', color='#dddddd', linewidth=.45)

    a, b = axes
    for prefix, style in styles.items():
        a.plot(x, [r[prefix + '_req_s'] / 1000 for r in rows],
               lw=1.0, ms=5.2 if prefix == 'hint256' else 3.2, mew=.7, **style)
    a.set_ylim(2, 4.1)
    a.yaxis.set_major_locator(FixedLocator([2, 3, 4]))
    a.yaxis.set_major_formatter(FixedFormatter(['2', '3', '4']))
    a.yaxis.set_minor_locator(NullLocator())
    a.grid(False)
    a.set_title('kreq/s', loc='left', fontsize=10, pad=3)

    # A marker and leader identify the actual comparison; no global-best label.
    a.annotate(f"+{change(rows[1]['hint64_req_s'], rows[1]['base_req_s']):.1f}%",
               xy=(16, rows[1]['hint64_req_s'] / 1000), xytext=(12, 3.91),
               color=ACCENT, fontsize=10, ha='left', va='center',
               arrowprops=dict(arrowstyle='-', color=ACCENT, lw=.7,
                               shrinkA=2, shrinkB=3))

    for metric in ['p50', 'p99']:
        for prefix in ['base', 'hint64']:
            b.plot(x, [r[f'{prefix}_{metric}_ms'] for r in rows],
                   lw=1.0, ms=3.8, mew=.7, **styles[prefix])
    b.set_ylim(8, 48)
    b.set_yticks([10, 20, 30, 40])
    b.grid(False)
    b.set_title('ms', loc='left', fontsize=10, pad=3)
    # Direct group labels distinguish median and tail curves. Their changes are
    # at the same 16 KiB request size as panel a, explicitly stated in the caption.
    for metric, y in [('p99', 40.7), ('p50', 13.2)]:
        b.text(4, y, metric.upper(), fontsize=10, va='center')
        b.annotate(f"{change(rows[1][f'hint64_{metric}_ms'], rows[1][f'base_{metric}_ms']):.1f}%",
                   xy=(4, y), xytext=(21, 0), textcoords='offset points',
                   color=ACCENT, fontsize=10, ha='left', va='center')

    handles = [Line2D([], [], lw=1, ms=3.8, mew=.7, **styles[key], label=label)
               for key, label in [('base', 'RustFS'), ('hint256', 'Hint 256K'), ('hint64', 'Hint 64K')]]
    fig.legend(handles=handles, loc='upper center', bbox_to_anchor=(.5, 1.02),
               ncol=3, frameon=False, handlelength=1.1, handletextpad=.35,
               columnspacing=.6, borderpad=0)
    for ax, title in zip(axes, ['(a) Throughput', '(b) Latency']):
        cx = (ax.get_position().x0 + ax.get_position().x1) / 2
        fig.text(cx, 19/164, title, ha='center', va='center', fontsize=10)
    fig.text(.55, 4/164, 'Range size (KiB)', ha='center', va='bottom', fontsize=10)

    sys.path.insert(0, str(args.qa_tools))
    from audit_panel_alignment import require_matplotlib_panel_alignment
    fig.canvas.draw()
    require_matplotlib_panel_alignment(fig, json_out=qa/'12plus4-alignment.json',
        overlay_svg=qa/'12plus4-alignment.svg', tolerance_pt=1.5,
        gutter_tolerance_pt=1.5, strict=True)
    stem = out / 'rustfs-range-12plus4'
    fig.savefig(stem.with_suffix('.pdf'), metadata={'CreationDate': None, 'ModDate': None})
    fig.savefig(stem.with_suffix('.svg'))
    fig.savefig(stem.with_suffix('.png'), dpi=600)
    plt.close(fig)
    svg = stem.with_suffix('.svg')
    svg.write_text('\n'.join(line.rstrip() for line in svg.read_text(encoding='utf-8').splitlines()) + '\n', encoding='utf-8')
    (qa/'12plus4-plot-data.json').write_text(json.dumps({
        'scope': 'EC 12+4 only', 'observations': checked,
        'absolute_values_plotted': len(checked), 'excluded_observations': 0,
        'redundant_metric': 'Bandwidth retained in source; request rate shown. For each request size, throughput = rate times request size.',
        'panel_a': {'metric': 'Request throughput', 'unit': '1000 requests/s', 'scale': 'linear', 'ylim': [2, 4.1]},
        'annotations': 'All three percentage labels compare Hint 64 KiB to RustFS at 16 KiB.',
        'uncertainty': 'One run per condition; P50/P99 are within-run request percentiles.',
        'font_pt': 10, 'canvas_pt': [240, 164],
        'percentages': [{ 'size': r['size'],
            'throughput_256': str(change(r['hint256_req_s'], r['base_req_s'])),
            'throughput_64': str(change(r['hint64_req_s'], r['base_req_s'])),
            'p50_64': str(change(r['hint64_p50_ms'], r['base_p50_ms'])),
            'p99_64': str(change(r['hint64_p99_ms'], r['base_p99_ms']))} for r in rows]
    }, indent=2) + '\n')
    print('Rendered 49 absolute values in two single-column panels; 10 pt text.')


if __name__ == '__main__':
    main()
