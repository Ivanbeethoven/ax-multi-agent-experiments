# Figure and evidence contract

All plots use recomputed JSONL counts, with Core and Exploratory separate. They
are descriptive observations of eight single runs, not treatment comparisons.
The observation unit is a FUSE request; group distributions are event-weighted.
No confidence intervals across correlated requests or invented replicate errors.

| Figure | Question and panel roles | Main-text use |
|---|---|---|
| fuse-profile (a,b) | Which calls must evaluation retain? Core and Exploratory method bars stratify the request mix, with all statuses in each denominator and white error segments. Stars identify all six metadata methods; open/flush/release are explicitly excluded. | Namespace/attribute/directory traffic and failed probes motivate positive and negative metadata-path evaluation. |
| fuse-profile (c,d) | Which I/O lengths accompany that mix? Separate read and write ECDFs compare both groups at equal physical scale, with method/group sample counts. | Small and intermediate requests motivate measuring client-fetch and finalized-object transformations before selecting storage quanta. |
| fuse-time | Does a group average obscure workload intensity? One cumulative curve per task, grouped panels with explicit differing time/count ranges. | Retain task identity and bursts in replay; no semantic phase labels or concurrency inference. |

Export: 7-inch width at final two-column scale, editable vector PDF and SVG,
300 dpi PNG preview; Arial, 8 pt minimum intended text. Blue/orange identify
groups in the four-panel profile; outlines/dashes provide redundant encoding.
Temporal curves use colors and distinct line styles. No 3-D effects or dual axes.
The four-panel profile answers one question: what request structure should the
evaluation retain? It keeps the reference draft's panel roles and byte
ticks, while using the existing target analysis and 8 pt typography. Core and
Exploratory method panels are necessary strata, and read/write ECDFs distinguish
operation-size distributions. Temporal heterogeneity remains a separate figure.
No input rows or requests are removed. Request counts, error counts, positive
log-axis values and ECDF sample totals are asserted during plotting.

Compact dimensions: the profile is 7 x 2.45 inches and the time figure is
7 x 2 inches. All four profile panels share top/bottom bounds and title baselines.
Comparable groups (a,b) and (c,d) have equal plot widths and heights. Method
panels share the left method labels and use narrower axes than the ECDFs, which
need space for logarithmic size ticks. These are intentionally different plot
types with separate explicit audit groups; the default 1.5 pt alignment
tolerance is not relaxed. Legends sit in data-free regions, and all sample
counts, errors and metadata stars remain. No observations are excluded.
PDF font and collision audits plus visual checks are required after changes.

Allocation: request mix, sizes and temporal/task heterogeneity are necessary
support in the motivation; status semantics and WSL/handler boundaries qualify
the main claims. Source hashes, exact quantile/histogram tables, parser audit and
timestamp discrepancies stay in this companion directory. The detailed eight-task
table also moves here, while task ranges, exceptions and all time curves remain
in the main manuscript. These observations do
not replace E01's cross-task locality/sharing study or any performance treatment.
