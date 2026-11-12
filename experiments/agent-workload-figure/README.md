# Opening workload figure

One compact figure combines the eight-task capture and the five toolchain
profiles. It supports one observation: agent workflows generate substantial
metadata demand, mixed request lengths, and concentrated activity.

## Inputs and panels

- **a — Call mix:** all eight tasks, pooled separately within the initial and
  extended capture groups. Denominator: every call in that group, all statuses.
  Hatching identifies errors within each category.
- **b — Request lengths:** the same two groups; each read/write distribution
  has its own denominator. Intervals are (0,4], (4,64], and (64,infinity) KiB.
- **c — Burstiness:** each of the eight tasks separately. Select ceil(10% ×
  number of one-second windows), retaining idle and final partial windows.
- **d — Toolchain calls:** all five archived agent/model profiles, with the
  same six-method metadata definition. Absolute counts; labels are each
  profile's metadata percentage.
- **e — Recorded bytes:** read/write byte totals for the same five profiles.

Inputs are `../agent-fuse-observations/derived/` and the pinned snapshot
`../agent-toolchain-observations/data.json`. The two captures remain distinct;
they are neither pooled together nor a controlled comparison of agent speed.
Each task/profile has one observation; no replicate-based uncertainty is drawn.
The original data and standalone renderers remain available.

## Render

```powershell
python experiments/agent-workload-figure/render.py --paper paper
```

The output is `paper/figures/background/agent-workloads.{pdf,svg,png}`;
the numeric ledger and checks go to `paper/validation/agent-workloads/`.
Use `--qa-scripts` to locate the figure-audit helpers when needed.

## Figure contract

- Quantitative grid, structurally adapted from the two existing Python plots.
- Final canvas: **504 × 150 pt** (177.8 × 52.9 mm), full USENIX text width.
  The independent 240 × 168 pt scenario is placed at 96% of a column.
- Arial, 7.8 pt minimum, 8.2 pt ticks/labels, 8.6 pt panel titles. SVG text
  remains editable; PDF is vector; PNG is a 600 dpi preview.
- Scoped legends separate the capture groups from operation categories.
  Panels d/e share dimensions and rows; a/b share plot width and left edge.
- Learn layout proportions, not content, from Oxbow Fig. 2 and CoPilotIO
  Fig. 2 (OSDI 2026), and Okapi Figs. 1–2 (OSDI 2025): use a column for a
  focused schematic, and a shallow multi-panel strip for related measurements.
  No published artwork is copied.
- Reconcile every count before rendering, audit alignment before export, then
  audit PDF text/collisions and inspect the compiled manuscript at final size.
