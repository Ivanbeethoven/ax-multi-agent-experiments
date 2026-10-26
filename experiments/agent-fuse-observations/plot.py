#!/usr/bin/env python3
"""Render manuscript figures from analyze.py outputs; no raw-data resampling.

Dependencies: matplotlib, numpy; nature-figure's audit_panel_alignment.py is
loaded from --qa-scripts (the installed skill directory by default).
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.patches import Patch

HERE = Path(__file__).resolve().parent
BLUE, ORANGE = '#0072B2', '#D55E00'
GROUPS = ('Core', 'Exploratory')
META = {'lookup', 'getattr', 'readdirplus', 'opendir', 'releasedir', 'getxattr'}
TASKS = {
    'Core': [('django__django-10097', 'Django 10097'),
             ('matplotlib__matplotlib-13989', 'Matplotlib 13989'),
             ('pallets__flask-5014', 'Flask 5014'),
             ('psf__requests-1142', 'Requests 1142'),
             ('sphinx-doc__sphinx-10323', 'Sphinx 10323')],
    'Exploratory': [('astropy__astropy-13398', 'Astropy 13398'),
                    ('pytest-dev__pytest-5787', 'Pytest 5787'),
                    ('sympy__sympy-12489', 'SymPy 12489')],
}


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--data', type=Path, default=HERE/'derived')
    ap.add_argument('--output', type=Path, default=HERE.parents[1]/'paper/figures/agent-fuse')
    ap.add_argument('--qa-output', type=Path, default=HERE/'qa')
    ap.add_argument('--qa-scripts', type=Path,
                    default=Path.home()/'.codex/skills/nature-figure/scripts')
    ap.add_argument('--profile-only', action='store_true',
                    help='Redraw only the combined profile; preserve the time figure')
    args = ap.parse_args()
    if args.output == HERE.parents[1] / 'paper/figures/agent-fuse' and not (HERE.parents[1] / 'paper/main.tex').is_file():
        ap.error('Initialize the paper submodule first: git submodule update --init paper')
    sys.path.insert(0, str(args.qa_scripts))
    from audit_panel_alignment import require_matplotlib_panel_alignment
    args.output.mkdir(parents=True, exist_ok=True)
    args.qa_output.mkdir(parents=True, exist_ok=True)
    summary = json.loads((args.data/'summary.json').read_text())
    with (args.data/'request-sizes.csv').open(newline='') as f:
        sizes = list(csv.DictReader(f))
    with (args.data/'timeseries-1s.csv').open(newline='') as f:
        times = list(csv.DictReader(f))
    plt.rcParams.update({'font.family':'sans-serif', 'font.sans-serif':['Arial','DejaVu Sans'],
                         'font.size':8, 'axes.titlesize':9, 'axes.labelsize':8,
                         'xtick.labelsize':8, 'ytick.labelsize':8,
                         'legend.fontsize':8, 'legend.frameon':False,
                         'axes.spines.top':False, 'axes.spines.right':False,
                         'axes.linewidth':.6, 'pdf.fonttype':42, 'ps.fonttype':42,
                         'svg.fonttype':'none', 'savefig.dpi':300})

    def save(fig, name, **alignment):
        fig.canvas.draw()
        require_matplotlib_panel_alignment(fig, json_out=args.qa_output/(name+'.alignment.json'),
                                            tolerance_pt=1.5, gutter_tolerance_pt=1.5,
                                            strict=True, **alignment)
        fig.savefig(args.output/f'{name}.pdf')
        fig.savefig(args.output/f'{name}.svg')
        fig.savefig(args.output/f'{name}.png', dpi=300)
        plt.close(fig)

    # One figure answers which request structure the evaluation must retain.
    # Panels a/b stratify operation mix; c/d resolve read versus write sizes.
    # Width is the manuscript's actual 7 inches: no placement downscaling.
    # A single row saves height without shrinking typography or removing data.
    # Method panels share row labels; ECDF panels receive wider axes for log ticks.
    fig = plt.figure(figsize=(7, 2.45))
    axes = np.array([fig.add_axes([x/504, 30/176.4, w/504, 117/176.4])
                     for x,w in [(52,62),(124,62),(235,104),(392,104)]]).reshape(2,2)
    pooled = summary['groups']['All']['methods']
    shown = [m for m, _ in sorted(pooled.items(), key=lambda kv: kv[1], reverse=True)[:12]]
    assert META.issubset(shown), 'Every metadata-subset method must be explicit'
    labels = [m + ('*' if m in META else '') for m in shown] + ['Other']
    for i, (ax, group, color) in enumerate(zip(axes[0], GROUPS, (BLUE, ORANGE))):
        r = summary['groups'][group]
        vals = [r['methods'].get(m, 0) for m in shown]
        vals += [r['events'] - sum(vals)]
        errors = [r['method_status'].get(m, {}).get('error', 0) for m in shown]
        errors += [r['statuses']['error'] - sum(errors)]
        assert sum(vals) == r['events'] and sum(errors) == r['statuses']['error']
        assert all(0 <= error <= total for error, total in zip(errors, vals))
        total_pct = 100 * np.asarray(vals, dtype=float) / r['events']
        error_pct = 100 * np.asarray(errors, dtype=float) / r['events']
        other_pct = total_pct - error_pct  # includes ok AND incomplete
        y = np.arange(len(labels))
        ax.barh(y, other_pct, color=color, height=.73, linewidth=0)
        ax.barh(y, error_pct, left=other_pct, color='white',
                edgecolor='#555555', height=.73, linewidth=.6)
        ax.set_yticks(y, labels if i==0 else ['']*len(labels))
        ax.invert_yaxis()
        ax.set_xlim(0, 40)
        ax.set_xticks([0, 20, 40])
        ax.set_xlabel('Calls (%)', labelpad=1)
        ax.set_title(f'({chr(97+i)}) {group}\nn={r["events"]:,}',
                     loc='left', pad=4, fontsize=8, linespacing=1.2)
        ax.tick_params(axis='both', pad=2, length=3)
        ax.grid(axis='x', alpha=.16, linewidth=.5)
        ax.set_axisbelow(True)
    axes[0,0].legend(handles=[Patch(facecolor='white', edgecolor='#555555',
                                   label='error')], loc='lower right',
                     fontsize=8, handlelength=.8, handletextpad=.3, borderaxespad=.1)

    # Exact histogram ECDFs: all requests, no smoothing, no downsampling.
    for i, (ax, method) in enumerate(zip(axes[1], ('read', 'write'))):
        for group, color, style in zip(GROUPS, (BLUE, ORANGE), ('-', '--')):
            rows = sorted((int(r['request_bytes']), int(r['count'])) for r in sizes
                          if r['scope'] == group and r['method'] == method)
            x = np.asarray([r[0] for r in rows], dtype=float) / 1024
            counts = np.asarray([r[1] for r in rows])
            assert np.all(x > 0), 'Log axis requires positive request sizes'
            assert counts.sum() == summary['groups'][group]['size'][method]['n']
            cdf = 100 * np.cumsum(counts) / counts.sum()
            ax.step(np.r_[x[0], x], np.r_[0, cdf], where='post', color=color,
                    linestyle=style, linewidth=1.4, label=f'{group} (n={counts.sum():,})')
        ax.set_xscale('log', base=2)
        ax.set_xlim(1/1024 if method == 'write' else 2, 160)
        ticks = [1/1024, .0625, 4, 128] if method == 'write' else [4, 16, 64, 128]
        tick_labels = ['1 B', '64 B', '4', '128'] if method == 'write' else ['4', '16', '64', '128']
        ax.set_xticks(ticks, tick_labels)
        ax.minorticks_off()
        ax.set_ylim(0, 104)
        ax.set_yticks([0, 50, 100])
        ax.set_xlabel('Size (KiB; log)', labelpad=1)
        ax.set_ylabel('ECDF (%)', labelpad=2)
        ns = [summary['groups'][g]['size'][method]['n'] for g in GROUPS]
        ax.set_title(f'({chr(99+i)}) {method}\nn={ns[0]:,} / {ns[1]:,}',
                     loc='left', pad=4, fontsize=8, linespacing=1.2)
        ax.tick_params(axis='both', pad=2, length=3)
        ax.grid(False)  # keep the in-panel legend clear of grid strokes
    handles, _ = axes[1,0].get_legend_handles_labels()
    axes[1,0].legend(handles, GROUPS, loc='upper left', borderaxespad=.15,
                     fontsize=8, handlelength=1.25, handletextpad=.4, labelspacing=.2)
    save(fig, 'fuse-profile', row_groups=[['a','b'],['c','d']])
    if args.profile_only:
        print('Rendered four-panel profile; per-task time figure preserved.')
        return

    fig, axes = plt.subplots(1, 2, figsize=(7, 2.0))
    fig.subplots_adjust(left=.10, right=.99, bottom=.23, top=.70, wspace=.28)
    colors = [BLUE,ORANGE,'#009E73','#CC79A7','#333333']
    styles = ['-','--','-.',':',(0,(5,1,1,1))]
    for i,(ax,group) in enumerate(zip(axes,GROUPS)):
        for (task,label),color,style in zip(TASKS[group],colors,styles):
            rows = [r for r in times if r['task']==task]
            x = [int(r['bin_start_s'])+1 for r in rows]
            y = np.cumsum([int(r['calls']) for r in rows])/1000
            ax.step([0]+x, np.r_[0,y], where='post', label=label.rsplit(' ',1)[0],
                    color=color, linestyle=style, linewidth=1.25)
        ax.set_xlabel('Elapsed capture time (s)', labelpad=1)
        ax.set_ylabel('Calls (thousands)', labelpad=2)
        ax.set_title(f'({chr(97+i)}) {group}', loc='left', pad=28)
        ax.tick_params(axis='both', pad=2, length=3)
        ax.set_xlim(0,75 if group=='Core' else 570)
        ax.set_ylim(0,140 if group=='Core' else 700)
        ax.set_xticks([0,15,30,45,60,75] if group=='Core' else [0,100,200,300,400,500])
        ax.set_yticks([0,35,70,105,140] if group=='Core' else [0,175,350,525,700])
        ax.legend(loc='upper left', bbox_to_anchor=(0,1.39), ncol=3 if group=='Core' else 2,
                  fontsize=8, labelspacing=.25, handlelength=1.7,
                  columnspacing=.6, borderaxespad=0)
        ax.grid(alpha=.16, linewidth=.5)
    save(fig, 'fuse-time')
    print('Rendered combined profile and independent time figure (PDF/SVG/PNG); alignment gates passed.')


if __name__ == '__main__':
    main()
