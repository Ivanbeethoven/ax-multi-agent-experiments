# Figure 1 typography and group-label review

Date: 2026-10-24. Before-state plotted-data JSON and observations source are
retained in this directory. Superseded PNG/PDF, generator and README snapshots
were removed during cleanup; their original copies remain in Git commit `5994769`.

## What the original labels mean

Core (5) identifies the five original task captures, cross-checked against
`tasks.json`. Exploratory (3) identifies the three run records with
`workload=exploratory`. See `analyze.py:100–108` and the README's provenance
discussion. All eight use the same agent harness and configured model;
the latter three have a broader investigation/testing prompt and a 30-minute
budget. The parentheses count tasks, not requests, models, or methods.
Each task has one run. Repository, prompt, and budget differ together, so
the categories do not provide an isolated experimental treatment comparison.

The distinction remains useful in a/b because the figure and text explicitly
describe separate group-normalized distributions. Core contributes 217,129
requests and Exploratory 921,755. Pooling changes the displayed quantities
and is dominated by the larger capture. Conversely, the per-task burst panel
does not need group colors or shapes: the named tasks already identify each
measurement. Removing that redundant encoding preserves all eight observations.

## Applied changes

- Replace DejaVu Sans with Arial and Arial Bold. All visible PDF text is now
  9 pt, including task labels, interval keys, and the temporal subtitle.
- Display the group aliases Initial runs and Extended runs. Remove the
  opaque parenthesized 5/3 from the legend; the short figure caption maps the
  aliases to the five Core and three broader-prompt Exploratory captures.
- Use square color swatches for a/b bar groups. All c markers are neutral
  circles; their positions and task order are unchanged.
- Retain the single-column 3.3 by 2.9 inch layout, four request classes,
  error subsegments, three size intervals, both group/method distributions,
  and all eight temporal observations.
- Change only Figure 1's caption in the manuscript. No Figure 2 file,
  statistics, derived input, or other paragraph is edited.

## Final checks

- Complete before/after plotted-data JSON equality: PASS. All 1,138,884 events
  and all eight tasks remain; no exclusions, regrouping, or new normalization.
- Actual final PDF spans: ArialMT and Arial-BoldMT only; all sizes exactly
  9.0 pt. Content-stream minimum-size audit also passes with a 9 pt floor.
- Page size: 237.600006 by 208.800003 pt (rounding of 3.3 by 2.9 inches).
- Panel alignment: PASS, three comparisons, zero exceptions.
- Rendered collision audit: PASS, zero failures and zero warnings across
  36 text boxes and 79 stroke paths.
- Closest task label is Requests, with 4.773 pt to its marker edge; the
  generator requires at least 3 pt. Other task labels have larger clearances.
- Source preflight: zero failures. The three generic warnings concern the
  vector-first/no-TIFF workflow, 300-dpi preview, and systems-paper width
  rather than the validator's Nature-oriented default.
- Inspected both the full 300-dpi PNG and a 100-dpi PDF-rendered size proof.
  Labels and markers remain distinguishable and the shortened group key
  no longer looks like an unexplained sample-code legend.
- `verify.py` proves that the observations source is unchanged outside the
  Figure 1 caption and records the data/font checks in `verification.json`.

The main manuscript is not compiled in this subtask; its coordinator must
perform final page-placement inspection. No causal group effect or new
performance claim is introduced by the friendlier labels.
