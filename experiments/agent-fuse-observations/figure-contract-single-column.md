# Single-column workload figure

Question: which measured request properties should the evaluation preserve?
The conclusion remains substantial metadata traffic, mixed request lengths,
and per-task temporal concentration in these eight captures. This is workload
characterization, not a BrewFS performance result or a general causal claim.

The new Python/Matplotlib rendering structurally adapts the prior compact
figure without changing its data, transformations, or task order.
The former wide row is replaced with a 3.3 by 2.9 inch grid: request classes
above size mixtures in the left column, all eight task observations spanning
both rows at right. PDF/SVG are vector exports; PNG is a 300-dpi preview.
All labels and local keys use Arial at 9 pt. No manuscript downscaling
is intended: the image is included at the single-column width.

- Panel a partitions every group call into Metadata, Read, Write and Other.
  The six-method metadata definition and error-within-total semantics stay
  unchanged. Group bars retain all statuses and weight calls equally.
- Panel b preserves both read/write distributions in both groups, using all
  integer-byte observations and the same three closed/open interval boundaries.
- Panel c retains all eight unpooled tasks and the same fraction in each
  task's busiest `ceil(0.1 * number_of_windows)` one-second windows.

The latest typography revision labels Core/Exploratory as Initial/Extended
runs in panels a/b, with their original identities and five/three counts
mapped in the caption. This is a display alias, not a change of membership
or normalization. Panel c now uses one neutral circle style for every task;
group coloring is not needed to interpret an individual task's burst share.

All 1,138,884 events, setup/teardown, zero-call windows, and final partial
windows remain included. `qa/single-column/plotted-data.json` is exactly
equal to the former `qa/combined/plotted-data.json`; exclusions remain empty.
Caption definitions and method-specific denominators remain explicit.

Alignment is checked using the actual Matplotlib grid, including the shared
top edge of a/c and bottom edge of b/c. Rendered PDF font and collision
audits, source preflight, and panel-by-panel visual inspection are recorded
in `qa/typography-groups/` for the current rendering, with earlier versions
in `qa/single-column/` and `qa/coordinated/`. Old figures, source scripts, and derived data are
preserved. Only the observations section's figure and caption are updated.
