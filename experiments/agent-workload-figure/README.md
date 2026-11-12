# Background workload figure

Figure 2 asks which filesystem demands the design must address. It uses two
panels in one column, rather than one panel for every available statistic.

- **a — Metadata requests:** percentage of all calls in each of the five
  archived agent/model profiles. All five profiles remain present; no pooled
  cross-profile estimate or speed ranking is drawn.
- **b — Request sizes:** eight task runs, with initial and extended groups
  kept separate. Each read/write row sums to 100%. Bins are (0,4], (4,64],
  and (64,infinity) KiB.

Inputs are `../agent-fuse-observations/derived/` and the pinned
`../agent-toolchain-observations/data.json`. Both use the same six-method
metadata definition. Each task/profile has one observation, so no replicate
intervals are drawn. The captures retain their own denominators.

The old task call-mix chart repeated panel a's argument. Its metadata share
remains in the paper's prose. Burstiness is also summarized in prose. Absolute
profile call counts and bytes remain in the numeric ledger. The full source
records and standalone renderers are preserved.

```powershell
python experiments/agent-workload-figure/render.py --paper paper
```

Exports: `paper/figures/background/agent-workloads.{pdf,svg,png}`.
Checks and numeric ledger: `paper/validation/agent-workloads/`.
`--qa-scripts` can specify the figure-audit helper directory.

The 240 × 172 pt canvas uses Arial at 7.8–8.6 pt and is placed at column width.
The two plot areas share their left edge and width. SVG retains editable text;
PDF is vector, the exported PNG is 600 dpi, and the QA preview is 300 dpi.
The renderer reconciles every source count and runs the panel-alignment check
before export. Run PDF text/collision audits and inspect the compiled page
following layout changes.

Palette: metadata profiles use neutral gray; request-size bins use vermilion
(≤4 KiB), medium gray (4–64 KiB), and white (>64 KiB), with dark outlines.
The grayscale rendering retains three distinct lightness levels. Dimensions,
fonts, plotted values and normalization are unchanged.
