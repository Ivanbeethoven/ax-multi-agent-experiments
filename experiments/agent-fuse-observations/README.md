# Agent FUSE observations used in the manuscript

These analysis scripts now live in the experiment repository. The manuscript is
the `paper/` Git submodule; default figure output is `paper/figures/agent-fuse/`.
Initialize it with `git submodule update --init paper` before regenerating paper
figures. Historical paths below describe the original analysis record.

Reanalysis date: 2026-10-23. This is a measured **workload characterization** of
the supplied capture, not a BrewFS/RustFS performance experiment. The new
Section 2.3 replaces the earlier unverified-pilot paragraph. E00–E16 remain
planned; the pilot supplies only part of E01's request characterization.

## Current single-column main figure

The paper includes `figures/agent-fuse/fuse-single-column.pdf` through
`sections/02-agent-observations.tex`. `plot_single_column.py` regenerates
its editable SVG, vector PDF, and PNG from the validated derived data.
The source canvas is 3.3 × 2.9 inches and is included at column width;
all figure text uses Arial at 9 pt at source size. Panel (a) shows the all-call method mix
with errors hatched, (b) the within-method/group requested-length distribution,
and (c) every task's busiest-tenth statistic. The displayed **Initial runs**
and **Extended runs** are aliases for the original Core and Exploratory capture
groups. Initial contains five original task runs; Extended contains three runs
with a broader investigation/testing prompt and a 30-minute budget. They use
the same agent harness and configured model. They are not different methods,
models, or independently replicated treatments.

The two groups remain separate in panels (a,b), preserving the published
group-normalized statistics and avoiding a pooled result dominated by the
larger Extended capture. The legend's parenthesized counts were removed;
the five/three sample counts and name correspondence are now in the caption.
Panel (c) uses one neutral marker style for every named task, because its
per-task burst statistic does not need group coloring.

All events and all eight tasks are retained. The four method classes and
three size bins are explicit aggregations; the per-task temporal panel
uses one-second bins including zeros and the final partial bin. See
`figure-contract-single-column.md` for definitions. Current generator QA
outputs, plotted data, PDF font-family checks, and label-clearance checks are
under `qa/typography-groups/`. Before-state plotted data and the observations
section are retained there for the data/caption verification. Superseded
layouts and generated before-state previews were removed during cleanup;
their historical copies remain in Git commit `5994769`.

```powershell
python experiments/agent-fuse-observations/plot_single_column.py --qa-scripts 'C:/path/to/nature-figure/scripts'
```

Detailed method breakdowns, size ECDFs, and cumulative time curves remain
companion diagnostics through `plot.py`. The superseded 7-inch combined main
figure and its separate generator were removed.

## Inputs and provenance

Original directory (read-only):
`C:/Users/HXY/Documents/Codex/2026-10-03/wsl-localhost-ubuntu-24-04-home/outputs/agent-fuse-swebench/`.
`events.jsonl` contains 557,135,223 bytes and 1,138,884 parsed records. No raw
capture is copied into the paper repository. `derived/summary.json` records
SHA-256 hashes and byte lengths for the seven used source files. HTML dashboards
were not used as data sources.

The eight runs used DSH 0.1.5-rc.1 and the CCSwitch DeepSeek provider's configured
`deepseek-v4-flash`; neither the Codex smoke test nor unavailable Claude Code
is included. Exploratory run records label the same DSH configuration
`dsh-exploratory`. Their broader investigation/testing prompt and 30-minute
budget differ from Core; repository, prompt, duration and grouping are
confounded. Each task is observed once. No inferential uncertainty or causal
group effect is estimated from individual, correlated requests.

`tasks.json` has five Core instances, checked against the nested task metadata
in `runs.json`. The three Exploratory instances are identified using their
`runs.json` task/base-commit/workload fields and matching event labels.
`agent-availability.json` still says five measured DSH runs; its inventory
covers Core, whereas the later README/report and run/event records include
Exploratory. The full task IDs and base commits are retained in the derived
summary. All original run counts, method counts, ok/error counts and byte sums
match recomputation. Operation IDs are distinct and contiguous within each
task; no malformed event was skipped. This checks the supplied JSONL, not
capture loss or logging overhead, for which there is no control run. These
operation-ID, hash, parser and reconciliation details are kept here rather
than repeated in the main text.

## Per-task counts (moved from the main-text table)

These are all-method, all-status counts for each complete mounted capture,
including setup/canary and teardown. Each task was run once. The task column
uses repository name plus instance suffix; full IDs, base commits and timestamps
remain in `derived/summary.json`. Every recorded agent and daemon exit code
is zero; benchmark grading success was not established.

| Group / task | All calls | Read | Write |
|---|---:|---:|---:|
| Core — Django 10097 | 129,476 | 10,291 | 342 |
| Core — Matplotlib 13989 | 43,235 | 6,490 | 152 |
| Core — Flask 5014 | 8,542 | 878 | 54 |
| Core — Requests 1142 | 8,892 | 1,189 | 67 |
| Core — Sphinx 10323 | 26,984 | 3,294 | 497 |
| **Core total** | **217,129** | **22,142** | **1,112** |
| Exploratory — Astropy 13398 | 634,726 | 60,335 | 18,210 |
| Exploratory — Pytest 5787 | 54,893 | 4,899 | 1,568 |
| Exploratory — SymPy 12489 | 232,136 | 33,058 | 3,822 |
| **Exploratory total** | **921,755** | **98,292** | **23,600** |

The main text retains task-level metadata shares (39.6–65.4%), read/write
ratios (3.12–42.7), call-count range (8,542–634,726), Astropy/Pytest group
contributions, capture spans and burst statistics. The companion time figure
continues to show every task, and main-figure panel c shows each task's burst
fraction. Moving this table does not reduce the discussion
to pooled group averages.

## Definitions

- Streaming analysis uses one JSONL record at a time, counters, integer-byte
  histograms, per-second count bins, and a per-task operation-ID bitmap. It
  never materializes the event file as a list. Small JSON metadata files are
  loaded normally. The source digest is computed during the same stream pass.
- All counts include ok, error and incomplete records. Group distributions
  are event-weighted, separately for Core and Exploratory. `All` is retained
  for reconciliation, not substituted for the two groups in figures.
- The explicit six-method metadata subset is lookup, getattr, readdirplus,
  opendir, releasedir, and getxattr. It covers namespace/attribute/directory
  requests, excludes file open/release/flush, and is not claimed to be an
  exhaustive classification. The paper also reports its share among ok calls.
- Request sizes are `request_bytes` of read/write, validated against their
  parsed `args.size`/`args.data_len`. They are requested FUSE lengths, not
  returned lengths, application syscall sizes, unique bytes or object sizes.
  Read/write are all ok. Quantiles use linear interpolation at `(n-1)*p` over
  exact histograms. KiB means 1024 bytes; CSV values remain integer bytes.
  Write medians, checked against the exact CSV histograms and both workspaces'
  summaries, are 4,084 bytes (3.98828125 KiB) for Core and 4,117 bytes
  (4.0205078125 KiB) for Exploratory. They must not both be rounded to 4 KiB
  when discussing the at-most-4-KiB boundary.
- Method response-byte sums omit nulls; a zero for a non-read/write method
  does not establish a zero-byte protocol response. Only read/write payload
  counts were extracted by the supplied parser. Read requests total
  4,897,783,808 bytes; recorded read responses total 4,686,183,590 bytes.
  Write requests/responses both total 363,330,465 bytes.
- Time origin is each task's first request start, checked against
  `relative_ms`. Bins are `[i,i+1)` seconds; include zero bins and the final
  partial bin. Cumulative curves place each bin's count at its upper boundary.
  The busiest-window statistic uses the largest `ceil(0.1 * number_of_bins)`
  bins divided by all calls, without identifying semantic tool phases.
- Capture duration is **last request start minus first request start**. Sums
  across independent captures are 273.821040 s (Core), 927.922423 s
  (Exploratory), 1,201.743463 s (all). These replace the report's approximate
  274.1/898.1/1,172.2 s for this explicit definition. They are neither summed
  handler latency nor a multi-task wall-clock measurement. Astropy's capture
  extends about 30 s beyond the recorded `finished_at`; its final teardown
  interval is included, not silently removed. Table/plots use actual spans.
- There are 64 events before `agent_started_at` (eight setup/canary operations
  per task), and 24 after the run record's `finished_at`. These are retained
  to reconcile the capture, and are not all attributed to agent-authored work.
  No unrecorded agent end timestamp is inferred.

## Claim-to-evidence map

| Manuscript claim | Recomputed evidence | Artifact |
|---|---|---|
| Metadata-related calls constitute a group majority | Six-method share: Core 61.5399%, Exploratory 56.3502%; SymPy exception 39.6035% | `methods.csv`, `summary.json`; main panel a and task ranges in text |
| Reads outnumber writes, with task differences | Core 22,142/1,112 = 19.9119; Exploratory 98,292/23,600 = 4.16492; per-task ratios 3.12436–42.6974 | `tasks.csv`, per-task table above; main panel a; exact request counts in text |
| Small and intermediate requests both occur | Read median Core 8 KiB / Exploratory 16 KiB; write median 4084 / 4117 bytes. All per-group threshold counts and p50/p75/p90/p95/p99 are retained | `request-sizes.csv`, `summary.json`; main panel b; companion ECDFs retain full detail |
| Intensity differs by task and time | 8,542–634,726 calls/task; busiest tenth of 1-s windows contains 47.4134–83.6706% of calls; Astropy contributes 68.8606% of Exploratory events | `tasks.csv`, `timeseries-1s.csv`; main panel c; companion cumulative curves |
| Negative probes matter to the request mix | 253,669 error records; six-method share among ok calls is Core 51.9944%, Exploratory 44.2249% | `methods.csv`, `summary.json`; main panel a hatched error segments and metadata paragraph |

These observations motivate testing positive and negative metadata paths for
Packed, mixed sizes for client/object-layout experiments, and task-specific
bursts for replay. They do not show file sharing, sparse mutation, locality,
repeated path reuse, optimal `(B,q)` values or a performance advantage. Raw
method/size/time histograms alone cannot support semantics-preserving replay;
paths, handle dependencies, payloads and synchronization must be recaptured.

## Status and measurement boundaries

872,519 ok + 253,669 error + 12,696 incomplete = 1,138,884. Error counts are
lookup 233,452; getxattr 20,203; rmdir 14. The collection notes describe path
probes and unsupported attributes, but JSONL contains no errno or error text.
We therefore do not relabel all lookup errors ENOENT or all getxattr errors
ENOTSUP, or equate them to task failures. All eight run records say completed
with agent/daemon exit codes 0; this is not evidence of SWE-bench grading success.

The supplied `collect_logfs.py` appends REQ records with initial status
incomplete and changes them only when it recognizes a matching operation-ID
completion. All remaining incomplete events are forget (12,688) and destroy
(8); their completion time and latency are null. The label means a parser
pairing state, not necessarily a failed filesystem operation. In particular,
forget has no normal reply. A completion without a known REQ is skipped by
this parser, contrary to the report's stated aim to preserve unmatched ends.
The original daemon text logs are not supplied in this directory, so neither
unmatched-end counts nor each errno can be reconstructed here.

The system under observation is libfuse-fs PassthroughFs wrapped by rfuse3
LoggingFileSystem on Ubuntu 24.04 WSL. The supplied environment notes describe
an O_PATH/`/proc/self/fd` compatibility path because WSL rejects
`open_by_handle_at`. This is not a qualification of a production file-handle
path. Handler latency excludes model response time and does not measure
RustFS, Redis, network object storage or cloud disks. Elapsed capture time,
by contrast, retains inter-request gaps that may include model waiting; the
cumulative curves do not subtract that time. Handler intervals may
overlap; summing them is not a critical-path or task-time estimate. No timing
speedup is reported. Kernel caching/readahead and instrumentation can affect
the visible mix; this eight-task, one-agent/model panel is not a population
claim about all coding agents or concurrent multi-agent execution.

## Reproduce

From the repository root, use Python 3.12 (the analysis itself is stdlib-only):

```powershell
python experiments/agent-fuse-observations/analyze.py --source 'C:/path/to/agent-fuse-swebench'
python -m pip install -r experiments/agent-fuse-observations/requirements.txt
python experiments/agent-fuse-observations/plot_single_column.py --qa-scripts 'C:/path/to/nature-figure/scripts'
```

The installed nature-figure skill from
`https://github.com/Yuan1z0825/nature-skills` supplies
`audit_panel_alignment.py`. Plotting defaults to
`~/.codex/skills/nature-figure/scripts`. The derived CSVs are sufficient to
redraw all figures without the 557 MB event file; `summary.json` supplies
metadata and validates plotted sample counts. The raw input is needed to
reproduce the statistics, including source hashes.

Companion diagnostic figures are `figures/agent-fuse/fuse-profile.{pdf,svg,png}`
(four-panel method/size profile) and `fuse-time.{pdf,svg,png}` (independent
per-task cumulative calls). The obsolete standalone method/request-size exports
have been removed. The `plot.py` command regenerates both diagnostic
figures; `--profile-only` preserves the time figure when revising the profile.
At the historical profile's 7-inch width, all labels, ticks and legends are
at least 8 pt. Stars mark the six-method metadata subset; white bar segments
preserve error counts. Write ticks below 1 KiB use byte labels.
The compact profile is 7 x 2.45 inches: four panels in one row, with shared
method labels for (a,b), equal widths within each method/ECDF pair, and a
Core/Exploratory sample-count order in (c,d). The time figure is 7 x 2 inches;
its legend uses repository names, with exact task IDs retained in the table
above and derived files. Every request, size bin and per-task time window is
unchanged. Historical compact-figure audits are also saved under
`validation/compact-figures/`.

Figures are provided as PDF, editable SVG and PNG.
`figure-contract-single-column.md` specifies the current main figure;
`figure-contract.md` covers historical diagnostics. `qa/` preserves alignment,
font and collision results. `qa/typography-groups/review.md` covers the current
single-column figure; `qa/review.md` describes historical exports. PDF/SVG
are publication graphics; PNGs
are 300 dpi previews. Historical QA records retain their original inspection
paths. Build the paper with XeLaTeX from the repository root (`main.tex` is the
formal manuscript entry point):

```powershell
latexmk -xelatex '-outdir=.' '-auxdir=.' -interaction=nonstopmode -halt-on-error main.tex
```

No source events are modified; no observations are simulated, dropped for
performance reasons, or inferred from the interactive HTML displays.
