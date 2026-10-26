# Evaluation protocol accompanying Section 8

Status: **planned; preregistration draft**, revised 2026-10-24. This file expands
`sections/08-evaluation.tex`, `matrix.csv`, and `agent-workload-spec.yaml`.
It is not an execution record. The eight-task FUSE capture establishes request
structure only; it does not qualify any system comparison below.

## Evidence and version contract

The planned BrewFS source baseline is
`242af5f7e9bba7afc0c61778f1084fcb20e083a0`, matching the manuscript's source audit.
The backend evidence is the separate RustFS research fork
`cc4926fac1107aee76540f28bf22dbd47fcbca24`. The former workload-spec value
`f80e7148c82e9f1ef235ab19318b89cb41a093a2` belongs to the historical upstream
audit; `518e7cc39b2ff0b421ecf0f656de6d468cacb9b8` is the historical research
checkpoint. Neither historical identifier is silently promoted to the executable
treatment, nor does the source-baseline update claim that integrations have passed.

Before execution, generate a manifest containing source and patch hashes, build
features, exact configuration, image digests, hardware/OS/kernel/FUSE settings,
commands, seeds, and instruments. Re-audit the existing integration gate IDs at
that executable revision. E00 must demonstrate the intended mount and subprocess
path, upload producer, conditional/durable PUT semantics, and persisted `(B,q)`.
A header or configuration flag alone does not establish the treatment.

Values below are candidate ranges or initial budgets. The capability pilot,
baseline tuning, and variance pilot use separate records. Freeze the task list,
legal cells, precision target, sample sizes, deadlines, concurrency, arrival
schedule, cache state, retention policy, analysis, and exclusion rules **before**
confirmation runs. Any later change receives a dated amendment and separate
exploratory label. There are no confirmation runs recorded by this document.

## Baselines and operating points

The proposed distributed testbed has two clients, one metadata server, and four
RustFS storage servers on 25 GbE; requested clients have 24–32 physical cores,
128 GiB memory, independent NVMe, and fixed cache allocations. The initial
storage geometry is one drive per server in a verified 2+2 set. Single-host,
four-drive tests support early functional and mechanism work, not machine-failure
claims. Record the actual hardware rather than reporting these requests as obtained.

Screen full copying, supported reflinks, OverlayFS, BranchFS, JuiceFS, and mature
immutable lowers with compatible writable uppers. For Nydus/CernVM-FS candidates,
record the upper, copy-up behavior, metadata path, cache allocation, and publication
or checkpoint needed for the selected durability contract. Use separately
prespecified tuning workloads and equal tuning effort to select the strongest
compatible lower-plus-upper combination for the primary live comparison; freeze
its version and settings before evaluating the task panel. Retain the screening
results and selection criterion. If candidates trade off metrics, report those
operating points instead of declaring one universally strongest.

The nominal three-system live condition includes the full system, the selected
mature lower-plus-upper combination, and a further compatible external baseline
selected before comparison. Internal ablations and additional baseline systems
require separate execution budgets. If a required baseline cannot meet the
contract, retain its separate local-service result and defer the equivalent
remote-service comparison rather than substituting an easier baseline silently.

Match namespace semantics, reliability, persistence boundary, resource placement,
cache memory/SSD, and completion accounting. Local fsync and remote coded-object
completion are different operating points. Include checkpoint/commit work required
for parity; record BranchFS's branch/commit distinction. Immutable sorted-KV with
equal records and cache budget is a representation control, not a reproduction of
DeltaFS. A real DeltaFS system comparison requires operation/workflow compatibility.

## Shared baseline, reuse, and lifetime

A group pins one immutable identity `S`, with repository, base commit, dependency
image digest, selected-subtree manifest digest, and namespace contract recorded.
A common repository name does not imply the same `S`. Different task commits stay
separate unless a declared identical subtree, such as a dependency tree, is the
actual shared baseline. Record private overlays that establish each task's
required initial state and charge their construction. Never replace a benchmark's
base commit to manufacture sharing.

Group the natural task panel by this identity before measuring overlap. Retain
singleton groups, `K=1` groups, and zero overlap. Measure path- and byte-weighted
read overlap, mutation footprint, name/directory locality, reuse distance, and
page reuse within groups; report their distribution and each group's contribution.
The existing eight-run pilot cannot supply these quantities. Controlled sibling
workspaces and repeated trace sessions can test larger reuse counts, but must be
labelled controlled sharing experiments, separate from natural live-agent sharing.

`K` is the number of assigned uses of one `S`, with the reuse unit fixed per
experiment:

- Workspace experiments: one workspace lifecycle from creation through release,
  including failed and timed-out assignments. Tool calls and in-workspace retries
  are not extra uses. A retry creating another workspace is a new use only under
  the frozen retry policy.
- Standalone Packed experiments: one prespecified query session with the same
  operation count, paths, and cache initialization. Include its mount/setup/access
  work. Report this `K` separately from workspace `K`.

Reset baseline/cache state between independent experimental batches. Sharing
within a batch is part of its treatment; it does not make the tasks independent
replicates. Do not pool amortization across reset batches as if one physical
baseline survived all of them. Record actual `K` and simultaneous users per `S`.

`T` is the actual version lifecycle from construction through final reclamation.
The retention policy specifies a post-last-release interval `tau_ret`, keep/discard
rules, ownership/lease expiry, and reclamation trigger. This interval is contained
in `T`; it is not charged a second time. Its numeric value and the maximum
observation horizon remain to be frozen. Use the same arrival, deadline, retry,
and retention policies across comparators. Actual completion and reclamation times
may differ and are outcomes, so report the resulting `T` for each representation.
For a controlled equal-`T` sensitivity study, declare that intervention separately.

Account for `C_c(K,T)` per resource unit: construction + publication + per-use work
+ retention + reclamation. Record CPU-seconds, physical/HTTP bytes, requests,
byte-seconds, peak memory/storage, and elapsed completion time separately. Do not
add concurrent task durations to estimate batch makespan or add quantities with
different units. Charge shared construction and objects once; shared-object
retention uses the union of residence intervals across overlapping versions.
Rebuilding creates a new version, with shared work explicitly allocated once.
Prepositioned and cold-staged cases remain separate; include the earlier creation
cost in lifecycle accounting even when it is excluded from readiness latency.

Estimate break-even only for a declared reuse unit and retention policy with
positive measured net savings, and within the tested `K,T` range unless an
extrapolation is explicitly labelled. If reclamation is unfinished at the cutoff,
report the cutoff, retained bytes/owners, and pending work as censored lifecycle
cost. Do not claim a fully amortized lifecycle from that observation.

## Experiment and operation matrix

`matrix.csv` maps E00–E16 to the seven research questions. The detailed controls
below accompany the compact manuscript; they are not additional measured results.

| Phase | Representative operations | Controls | Outputs |
|---|---|---|---|
| A: provision/discover | create/fork/mount, statfs, dependency discovery | cold staging versus prepositioning; same initial tree | control return, mount readiness, first navigation, first durable edit |
| B: navigate | git status, grep/find, stat/getattr, readdir, open, readlink | shared lower versus materialized state; cache state | lower-KV calls, GETs/bytes, metadata P50/P99, phase time |
| C: edit/sync | patch, overwrite, append, truncate, sparse write, fsync | same logical writes, cache, persistence and drain boundary | private bytes, upload/write amplification, commit/drain tail |
| D: build/test | compiler/test reads, link, generated files | dependency image and build cache; sequential/random mix | physical/HTTP bytes, CPU, output checks, critical path |
| E: churn | dependency install, create/rename/unlink/link storms, retry | same lifecycle, retry and retention policies | whiteouts/tombstones, retained objects, cleanup lag |
| F: review/release | diff, discard/keep, supported reopen/lease expiry | same ownership and retention contract | owner count, retained byte-seconds, reclamation, failure class |

These phases are labels attached to actual tool spans, not a presumed sequence
that every task follows. Preserve nested/overlapping spans and request dependencies.
Classify failed path/attribute probes separately from data-I/O failures only when
the trace contains the required status/errno evidence. Measure instrumentation
overhead and loss with an on/off control.

E03–E04 candidate ranges are `10^4–10^6` baseline files, 1–128 workspaces, and
0–50% modified files. Include delete, rename, truncate/extend, repeated lifecycles,
and poor locality. Entity directory candidates are 100, 1,000, and 4,096 entries;
standalone snapshot-reader large-directory tests are a separate runtime path.

E05–E07 compare mutable per-file KV, immutable sorted-KV, and Packed with equal
records and cache capacity. Count upper/control/lower KV separately, and index,
GroupMeta, descriptor, and payload object requests/bytes separately. Define
cold-client, index-warm, metadata-warm, and data-warm states; control or report
backend page cache. A runtime that rejects payload-cache controls is not admitted
to that sweep without a qualified adapter. Include build, publish, rebuild, and
reclaim costs, not just lookup latency.

E08 fixes the S3 stream and data/parity geometry before varying backend settings.
Object lengths cross inline, 1 MiB, encoding-block, and multipart boundaries.
Compare default, automatic, and four supported fixed `B` choices; compare legacy
and implemented `q` policy by effective persisted geometry. Same `(B,q)` means
no treatment. Larger coding geometries are separate cells because they affect
whether `q` is below logical shard size. New layout variants use newly written,
content-equivalent objects.

The file-system object-formation sweep varies `g` with the logical write stream
and backend geometry fixed, recording final `L_o`, object counts, compression,
headers, tails, and uploads. Hold the ordinary client's absolute range threshold
constant where supported or expose its dependence on `g` as a coupled factor.
E09 then separates range/whole-object fetch, admission, and coalescing. Read length,
alignment, encoding-block/`q` boundary crossings, and degraded state are factors.
Separate upper/lower integrity and upload paths. Measure physical data/parity
reads, HTTP bytes, request counts, checksum work, EC CPU, and latency.

E10 includes append/overwrite, short tails, multipart retries, frequent durability
requests, and repair interference. Instrument accepted writes, staging, remote
completion, metadata commit, and drained debt. Lower read amplification alone
cannot establish lower write amplification or cheaper repair.

## Composition and its falsifiers

E11 separates two claims: combined value is incremental improvement over the best
supported proper subset under one runtime; system advantage compares the result
with the strongest compatible external baseline at matched visible semantics,
persistence, and resources. External baselines need not share an implementation
runtime. Freeze the primary endpoint, resource and
quality constraints, practical effect threshold, comparator set, and uncertainty
analysis before confirmation. Compare all prespecified subset contrasts, or select
the comparator on separate pilot data; do not pick the weakest subset after seeing
confirmation outcomes. Report complementary benefits and trade-offs per metric.

Additive improvements can establish useful combined value. A factorial interaction
is a separate question about departure from an additive model on a declared scale
(for example, additive log latency, hence multiplicative latency ratios). A zero
interaction does not negate component gains; a statistically detectable interaction
does not itself establish practical value. No incremental benefit over simpler
subsets, unacceptable maintenance/resource cost, or quality regressions weakens
the combined-value claim. A weak granularity contribution bounds that mechanism's
claim without erasing independently supported workspace or Packed effects.

Cross only combinations constructible under one runtime contract. Keep materialized
versus shared state, metadata representation, `g`, backend layout, and client read
policy as explicit factors. If a treatment exists only within one path, use nested
comparisons. Buffered ordinary volume and direct-I/O Entity differ in caching and
writeback; switching paths is not a one-factor test. Standalone Packed remains an
independent experiment and does not require workspace composition.

## Replay, live batches, and statistics

Replay needs initial state and path/inode/handle mappings, payload references with
retrievable bytes, lengths/offsets, dependencies, synchronization, and tool-phase
boundaries. Inode IDs and length histograms alone are insufficient. Freeze tool
inputs and verify file-tree/content/metadata outputs. Report closed-loop and
arrival-driven replay separately. Live runs also pin model/provider, harness,
prompt/tool hashes, token/time budgets, retries, and any supported random seed.
Record nondeterminism; pairing does not promise identical model outputs.

The proposed extension selects 20 compatible tasks from each of the three families
named in Section 8. Freeze exact public versions, task IDs, base commits, exclusions,
and Core/Exploratory prompt strata after capability screening. Screening is not
allowed to remove a task merely because storage performance is poor.

A live batch comprises the complete frozen panel: each task is assigned exactly
once, with its required `S`, seed when supported, arrival order, and prompt stratum.
One concurrency cap `m` and scheduling policy apply to all three systems. Freeze
`m` after the pilot, subject to panel size and available resources. Replay explores
the candidate concurrency set `{1,4,16,64}` before a live sweep; these are not four
conditions already funded by the nominal live budget.

| Planned panel | Tasks per batch | Systems | Initial batches per system | Task executions | Total system-batches |
|---|---:|---:|---:|---:|---:|
| Core budget | 20 | 3 | 3 | 180 | 9 |
| Extension budget | 60 | 3 | 3 | 540 | 9 |

These budgets cover one concurrency/cache/durability/retention condition and exclude
pilot, tuning, microbenchmark, and extra ablation executions. The extension is the
60-task panel budget, not 540 additional executions on top of the same 20 tasks.
The initial three paired blocks are not a precision guarantee. Freeze the formal
number of independent batches after a variance pilot and target precision analysis;
if resources cannot supply it, retain an exploratory label rather than inflating
sample size with tasks or requests.

Pair systems within a block using the same panel, baseline identities, arrival
manifest, seed schedule, cache/retention condition, and host/time allocation.
Randomize system execution order within each block. Reset deployment/storage and
cache state between independent batches; recreate any prepositioned baseline to
the declared condition. Retain sharing inside the batch. Record resets and host
failures; dependent back-to-back runs are not independent repetitions by declaration.

The primary completion endpoint is the number and fraction of all assigned tasks
that satisfy the task harness by a common deadline. Record unsuccessful terminations,
timeouts, retries, and causes; a zero process exit code is not resolution. Batch
makespan ends when every assigned task meets its success criterion; if that does
not occur by the deadline it is right-censored there, not shortened by an early
failure. Report completion counts alongside censored makespan. Successful tasks
per hour uses the full frozen observation window, not a failure-shortened duration.
Completed-task latency is secondary and retains its selection caveat.

Independent units for batch endpoints are paired deployment/batch blocks. A planned
95% interval resamples paired blocks while preserving all tasks and system pairings
inside each block; microbenchmarks initially target ten independent blocks, with
formal size frozen after the pilot. Task-level models/resampling must account for
both batch dependence and repeated repository/task identities. Generalization to
new repositories requires adequate repository replication; fixed-panel estimates
do not imply it. If the realized independent sample is too small for the chosen
interval method, show every paired outcome and descriptive effects and retain an
exploratory claim. Millions of requests and 180 task executions are never used as
independent sample counts. All inferential choices and multiplicity handling are
frozen before confirmation; report absolute effects as well as ratios.

## Correctness, failures, and long runs

E14 compares known-answer and stateful concurrent operation sequences with ext4 or
OverlayFS within a declared shared contract: whiteouts, same-name recreation,
lower-file modification, truncate/extend, holes, links, rename-overwrite,
open-unlink-read, and cross-workspace visibility. Save the oracle, unsupported
operations, expected errors, and history needed to inspect each mismatch.

E15 injects failures around object upload, manifest/binding publication, mapping
commit, process restart, missing objects, frame corruption, and interrupted repair.
Distinguish fail-closed detection from successful recovery. Fresh-only Entity does
not qualify reopen/crash takeover; those cells require a completed and tested runtime
extension. Record old/new persisted-layout compatibility and unreclaimed garbage.

E16 begins with a candidate two-hour protocol, then sets duration to cover multiple
cache/cleanup cycles and the selected retention policy. Track windowed P99, memory,
open descriptors, owners, pending uploads, retained objects, and reclamation backlog.
Publish/repair/GC interference tests require actual runtime support. High mutation,
poor locality, frequent rebuilding, and large sequential reads are boundary cases,
not exclusions. Long-run duration does not establish a completed lifecycle when
reclamation is still outstanding.

## Manuscript compression record

Section 8 retains evidence status, semantic parity, baseline strength, sharing and
lifetime definitions, causal controls, composition criteria, batch units, and failure
limits. The two former wide tables and detailed phase/operation/cache/fault lists
are retained here and in the machine-readable specification/matrix. Their labels
`tab:rqs` and `tab:agent-load` had no references outside Section 8 and were removed
with their in-section references. Section 8 was reduced from 2,271 to 1,186
whitespace-delimited words; final page count and typography require the main build.
