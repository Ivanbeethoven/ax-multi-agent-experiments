# Evaluation protocol accompanying Section 8

Revised 2026-10-26. **Planned; no system-performance results.** The current
protocol is [the five-RQ design](ax-multi-agent/paper-design/plan.zh.md), with
parameters and configuration names in [design.json](ax-multi-agent/paper-design/design.json).
Identical files are saved in the execution repository's `paper-design/`.

1. **RQ1:** Isolate overlay overhead and edited-parent fork/retention without packing.
2. **RQ2:** Compare native and packed overlay: database relief, mutable-workspace interference, first-fork construction, reuse and update cost.
3. **RQ3:** Separate client object size, actual backend layout and fetching policy.
4. **RQ4:** Measure partial migration including prior publication, dirty state, cold targets, neighbors and failure correctness.
5. **RQ5:** Measure correct task completion at fixed resources; separate replay from real sessions.

`native`, `native-overlay` and `native-overlay-pack` name controlled configurations,
not sequential operations or CLI flags. Fix object geometry, payloads, caches,
upper path and durability when isolating metadata representation. A plain volume
without fork needs a measured adapter to produce an equivalent isolated child.

JuiceFS is the sole external comparator. Allow native cloning for branches and
reopen the existing directory for migration. Freeze official BrewFS at execution
start and requalify capabilities; historical source audits describe their own revision.

Packed may reduce queries while source records remain. Measure lower, upper and
control traffic separately; include object/client work, capture, packing, upload,
validation, publication, source retention, temporary peaks and actual reclamation.
Compare first native fork, first packed fork and published-version reuse. Report
break-even separately by metric and condition, including no break-even. Quantities
retain their units; monetary sums need explicit prices and time basis. Count shared
objects/builds once; time concurrent batches directly.

Seven archives and F1/F2/M definitions are frozen in the design folder. The two
Pytest captures are the same task; the paper's eight older runs are separate data.
Three initial paired batches support descriptive comparisons; inferential sample
sizes require a separate variance pilot. Preserve failures, deadlines, unsupported
cells and missing measurements. Requests are not independent batch replicates.

The matrix, workload spec and observation schema use current IDs. Prior versions
are retained in `archive/2026-10-24/`. The implementation backlog remains a historical
source checklist. Existing AX/container tools need an explicit adapter for the new
seven-case schedule; this protocol does not enable execution.
