#!/usr/bin/env python3
"""A single-column workload figure from the complete measured capture.

The old detailed profile and time figures are preserved. This rendering uses
all events; size bins and busiest-window aggregation are explicitly documented.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import sys
from pathlib import Path

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib import font_manager
import numpy as np
from matplotlib.lines import Line2D
from matplotlib.patches import Patch, Rectangle

HERE = Path(__file__).resolve().parent
GROUPS = ('Core', 'Exploratory')
GROUP_LABELS = {'Core': 'Initial runs', 'Exploratory': 'Extended runs'}
COLORS = {'Core': '#236DA0', 'Exploratory': '#C46A2C'}
TASK_COLOR = '#555555'
TASKS = [
    ('django__django-10097', 'Django'),
    ('matplotlib__matplotlib-13989', 'Matplotlib'),
    ('pallets__flask-5014', 'Flask'),
    ('psf__requests-1142', 'Requests'),
    ('sphinx-doc__sphinx-10323', 'Sphinx'),
    ('astropy__astropy-13398', 'Astropy'),
    ('pytest-dev__pytest-5787', 'Pytest'),
    ('sympy__sympy-12489', 'SymPy'),
]


def csv_rows(path):
    with path.open(newline='') as stream:
        return list(csv.DictReader(stream))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data', type=Path, default=HERE / 'derived')
    parser.add_argument('--output', type=Path, default=HERE.parents[1] / 'paper/figures/agent-fuse')
    parser.add_argument('--qa-output', type=Path, default=HERE / 'qa/typography-groups')
    parser.add_argument('--qa-scripts', type=Path, default=Path.home() / '.codex/skills/nature-figure/scripts')
    args = parser.parse_args()
    if args.output == HERE.parents[1] / 'paper/figures/agent-fuse' and not (HERE.parents[1] / 'paper/main.tex').is_file():
        parser.error('Initialize the paper submodule first: git submodule update --init paper')
    sys.path.insert(0, str(args.qa_scripts))
    from audit_panel_alignment import require_matplotlib_panel_alignment

    summary = json.loads((args.data / 'summary.json').read_text())
    size_rows = csv_rows(args.data / 'request-sizes.csv')
    time_rows = csv_rows(args.data / 'timeseries-1s.csv')
    task_rows = {r['task']: r for r in csv_rows(args.data / 'tasks.csv')}
    metadata = set(summary['metadata6_methods'])
    assert set(task_rows) == {task for task, _ in TASKS}
    assert sum(int(row['events']) for row in task_rows.values()) == summary['total_events'] == 1138884
    args.output.mkdir(exist_ok=True, parents=True)
    args.qa_output.mkdir(exist_ok=True, parents=True)

    # Register the installed manuscript figure font; fail visibly if unavailable
    # instead of silently substituting a wider face that changes label geometry.
    for filename in ('arial.ttf', 'arialbd.ttf'):
        font_path = Path('C:/Windows/Fonts') / filename
        if font_path.exists():
            font_manager.fontManager.addfont(str(font_path))
    font_manager.findfont('Arial', fallback_to_default=False)
    plt.rcParams.update({
        'font.family': 'sans-serif', 'font.sans-serif': ['Arial'],
        'font.size': 9, 'axes.labelsize': 9, 'axes.titlesize': 9,
        'xtick.labelsize': 9, 'ytick.labelsize': 9, 'legend.fontsize': 9,
        'legend.frameon': False, 'axes.spines.top': False,
        'axes.spines.right': False, 'axes.linewidth': 0.6,
        'axes.edgecolor': '#777777', 'xtick.color': '#333333', 'ytick.color': '#333333',
        'pdf.fonttype': 42, 'ps.fonttype': 42, 'svg.fonttype': 'none',
        'hatch.linewidth': 0.5, 'savefig.dpi': 300,
    })
    # Two compact group summaries flank an unpooled, eight-task temporal panel.
    # The measured 2-by-2 grid makes the spanning panel share its outer edges.
    fig = plt.figure(figsize=(3.3, 2.9))
    width, height = fig.get_size_inches() * 72
    grid = fig.add_gridspec(2, 2, left=51 / width, right=226 / width,
                            bottom=25 / height, top=160 / height,
                            width_ratios=[60, 101], height_ratios=[46, 30],
                            wspace=14 / 80.5, hspace=59 / 38)
    axes = [fig.add_subplot(grid[0, 0]), fig.add_subplot(grid[1, 0]),
            fig.add_subplot(grid[:, 1])]
    for ax in axes:
        ax.tick_params(axis='both', length=2.2, pad=2)
        ax.set_axisbelow(True)
        ax.grid(axis='x', color='#DEDEDE', linewidth=0.45)
    for x, y, title in [(4, 183, 'a  Call mix'),
                        (4, 78, 'b  Request sizes'),
                        (125, 183, 'c  Burstiness')]:
        fig.text(x / width, y / height, title, weight='bold', fontsize=9)
    fig.legend(
        handles=[Patch(facecolor=COLORS[g], edgecolor='none', label=GROUP_LABELS[g])
                 for g in GROUPS],
        loc='upper center', bbox_to_anchor=(0.51, 1 - 2 / height), ncol=2,
        handlelength=1.0, handletextpad=0.4, columnspacing=1.4, borderaxespad=0,
    )

    # a: A complete, mutually exclusive partition; metadata is the stated subset.
    ax = axes[0]
    # Fix the final transform before sizing finite hatch strokes in points.
    ax.set_xlim(0, 70)
    ax.set_ylim(3.55, -0.55)
    mix_records = []
    for index, group in enumerate(GROUPS):
        record = summary['groups'][group]
        methods = record['methods']
        counts = [sum(methods.get(m, 0) for m in metadata), methods['read'], methods['write']]
        counts.append(record['events'] - sum(counts))
        errors = [sum(record['method_status'].get(m, {}).get('error', 0) for m in metadata), 0, 0]
        errors.append(record['statuses']['error'] - sum(errors))
        assert sum(counts) == record['events'] and sum(errors) == record['statuses']['error']
        assert all(0 <= error <= count for error, count in zip(errors, counts))
        total_pct = np.asarray(counts) / record['events'] * 100
        error_pct = np.asarray(errors) / record['events'] * 100
        ypos = np.arange(4) + (index - 0.5) * 0.31
        ax.barh(ypos, total_pct, height=0.27, color=COLORS[group], linewidth=0)
        ax.barh(ypos, error_pct, left=total_pct - error_pct, height=0.27,
                facecolor='white', edgecolor=COLORS[group], linewidth=0.7)
        # Finite clipped diagonal segments preserve a separately identifiable
        # error encoding without PDF hatch tiles spanning the whole page.
        for y, total, error in zip(ypos, total_pct, error_pct):
            if error <= 0:
                continue
            x0, yy0 = ax.transData.transform((total - error, y - 0.135))
            x1, yy1 = ax.transData.transform((total, y + 0.135))
            y0, y1 = sorted((yy0, yy1))
            rise = y1 - y0
            for base in np.arange(x0 - rise, x1, 5 * fig.dpi / 72):
                start, end = max(x0, base), min(x1, base + rise)
                if end <= start:
                    continue
                points = ax.transData.inverted().transform(
                    [(start, y0 + start - base), (end, y0 + end - base)])
                ax.plot(points[:, 0], points[:, 1], color=COLORS[group],
                        linewidth=0.55, zorder=3)
        for category, count, error in zip(('Metadata', 'Read', 'Write', 'Other'), counts, errors):
            mix_records.append({'group': group, 'category': category, 'count': count,
                                'error': error, 'denominator': record['events']})
    ax.set_yticks(range(4), ['Metadata', 'Read', 'Write', 'Other'])
    ax.set_ylim(3.55, -0.55)
    ax.set_xlim(0, 70)
    ax.set_xticks([0, 30, 60])
    ax.set_xlabel('All calls (%)', labelpad=2)
    fig.add_artist(Rectangle((4 / width, 167.5 / height), 6 / width, 6 / height,
                             transform=fig.transFigure, facecolor='white',
                             edgecolor='#666666', linewidth=0.65))
    fig.add_artist(Line2D([4 / width, 10 / width], [167.5 / height, 173.5 / height],
                          transform=fig.transFigure, color='#666666', linewidth=0.55))
    fig.text(15 / width, 169 / height, 'Error', fontsize=9)

    # b: Full histograms aggregated at the exact boundaries already used in text.
    # Hue remains the group encoding; fill intensity identifies the size interval.
    ax = axes[1]
    size_records = []
    for method_index, method in enumerate(('read', 'write')):
        for group_index, group in enumerate(GROUPS):
            source = [(int(r['request_bytes']), int(r['count'])) for r in size_rows
                      if r['scope'] == group and r['method'] == method]
            bins = [sum(count for size, count in source if size <= 4096),
                    sum(count for size, count in source if 4096 < size <= 65536),
                    sum(count for size, count in source if size > 65536)]
            expected = summary['groups'][group]['size'][method]['n']
            assert sum(bins) == expected == sum(count for size, count in source)
            assert min(size for size, _ in source) > 0
            cumulative = 0
            ypos = method_index * 1.9 + group_index * 0.48
            for bin_index, (count, alpha) in enumerate(zip(bins, (1.0, 0.30, 0.0))):
                pct = 100 * count / expected
                fill = matplotlib.colors.to_rgba(COLORS[group], alpha)
                ax.barh(ypos, pct, left=cumulative, height=0.34, color=fill,
                        edgecolor=COLORS[group], linewidth=0.6)
                cumulative += pct
                size_records.append({'group': group, 'method': method, 'bin': bin_index,
                                     'count': count, 'denominator': expected})
            assert abs(cumulative - 100) < 1e-9
    ax.set_yticks([0.24, 2.14], ['Read', 'Write'])
    ax.set_ylim(2.75, -0.45)
    ax.set_xlim(0, 100)
    ax.set_xticks([0, 50, 100])
    ax.set_xlabel('R/W calls (%)', labelpad=2)
    # This compact interval key is shared by all four bars.
    keys = [Patch(facecolor='#666666', edgecolor='#666666', label='≤4'),
            Patch(facecolor='#DDDDDD', edgecolor='#666666', label='4–64'),
            Patch(facecolor='white', edgecolor='#666666', label='>64 KiB')]
    fig.legend(handles=keys, loc='lower left', bbox_to_anchor=(4 / width, 57.5 / height), ncol=3,
               borderaxespad=0, handlelength=0.55, handletextpad=0.2, columnspacing=0.35,
               fontsize=9)

    # c: Recompute the burst statistic from every one-second window, including zeros.
    # Tasks are not pooled: every measured task receives one visible marker.
    ax = axes[2]
    burst_records = []
    task_labels = []
    task_points = []
    for index, (task, label) in enumerate(TASKS):
        row = task_rows[task]
        counts = [int(r['calls']) for r in time_rows if r['task'] == task]
        assert sum(counts) == int(row['events'])
        selected = math.ceil(0.1 * len(counts))
        burst = 100 * sum(sorted(counts, reverse=True)[:selected]) / sum(counts)
        assert abs(burst - float(row['top_decile_bin_call_pct'])) < 1e-9
        group = row['group']
        # Every observed point is >=47.4%. Place names in the otherwise empty
        # left range without cropping the 0-100% axis or hiding observations.
        task_labels.append(ax.text(1.5, index, label, fontsize=9,
                                   ha='left', va='center', color='#333333', zorder=3))
        point, = ax.plot(burst, index, marker='o', color=TASK_COLOR,
                        markersize=4, linestyle='None', zorder=2)
        task_points.append(point)
        burst_records.append({'task': task, 'group': group, 'windows': len(counts),
                              'selected_windows': selected, 'calls': sum(counts), 'burst_pct': burst})
    ax.set_yticks([])
    ax.set_ylim(7.65, -0.65)
    ax.set_xlim(0, 100)
    ax.set_xticks([0, 50, 100])
    ax.set_xlabel('Task calls (%)', labelpad=2)
    fig.text(125 / width, 169 / height, 'Busiest 10% of 1-s bins', fontsize=9)

    # Add muted leaders only after the label ends. They link names to points,
    # not a zero-origin bar; a physical clearance check prevents overlap.
    fig.canvas.draw()
    renderer = fig.canvas.get_renderer()
    label_clearances = []
    for index, (label, point) in enumerate(zip(task_labels, task_points)):
        label_right_px = label.get_window_extent(renderer).x1
        point_x = float(point.get_xdata()[0])
        point_px = ax.transData.transform((point_x, index))[0]
        clearance_pt = (point_px - label_right_px) * 72 / fig.dpi - 2
        assert clearance_pt >= 3, (label.get_text(), clearance_pt)
        leader_start = ax.transData.inverted().transform(
            (label_right_px + 3 * fig.dpi / 72, 0))[0]
        ax.plot([leader_start, point_x], [index, index], color='#BBBBBB',
                linewidth=0.65, zorder=1)
        label_clearances.append({'task': label.get_text(),
                                 'label_to_marker_edge_pt': clearance_pt})

    fig.canvas.draw()
    require_matplotlib_panel_alignment(
        fig, json_out=args.qa_output / 'fuse-single-column.alignment.json',
        panel_ids=['a', 'b', 'c'], column_groups=[['a', 'b']],
        tolerance_pt=1.5, gutter_tolerance_pt=1.5, strict=True,
    )
    stem = args.output / 'fuse-single-column'
    fig.savefig(stem.with_suffix('.pdf'))
    fig.savefig(stem.with_suffix('.svg'))
    fig.savefig(stem.with_suffix('.png'), dpi=300)
    plt.close(fig)
    (args.qa_output / 'plotted-data.json').write_text(json.dumps({
        'all_events': summary['total_events'], 'mix': mix_records,
        'sizes': size_records, 'bursts': burst_records,
        'bin_intervals_bytes': ['(0,4096]', '(4096,65536]', '(65536,infinity)'],
        'exclusions': [], 'task_observations_per_task': 1,
    }, indent=2) + '\n')
    (args.qa_output / 'task-label-clearances.json').write_text(
        json.dumps(label_clearances, indent=2) + '\n')
    print(f'Rendered {stem}; all 1,138,884 events and all eight tasks preserved.')


if __name__ == '__main__':
    main()
