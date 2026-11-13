Issue #445-447 independent runtime review at 7b844504: NOT READY for one integrated PR.

All 1336 immutable runtime-review-01 members were independently rehashed. 1472/1472 factual audit checks passed; this is not runtime acceptance.

Both frozen release identities, mounted cache configuration, resource limits and common harness match. The single --name fix applies to both arms. Separate operation logs prove exactly 4096 distinct cached FUSE 4KiB writes with nonzero request IDs per arm. Source/CI/component evidence remains valid in preflight.json.

The old three cached AB/BA/AB pairs are unqualified diagnostics. CPU totals reproduce from /proc ticks, but all six finish with only32 uploaded bytes for16MiB work. BOTH logged shape runs show chunks-v2/3/0 upload failures/InternalError while the harness returns0 and dirty becomes0. Local fio verification and dirty/pending0 do not prove remote durability. These runs cannot support publishable CPU improvement. One pair also has12.31% higher fully drained wall time.

The baseline standard smoke fails randrw direct0 and direct1 after four-file prefill, strict drain and remount. Each has four EIO reads. Prefill/measured names, directories, sizes, job counts and seeds match, and each argv has one --name. This failed32MiB/3s smoke does not satisfy512MiB/20s guards and does not identify the causal layer. A baseline failure does not waive candidate correctness.

Actual workspace guards have no completed waves or row cases: old helper init PUT fails. Its common protocol fix has local RED/GREEN evidence but actual fill and row hot/capacity checks remain pending.

Require fresh remotely durable cached pairs, full512MiB/20s six-scene baseline/candidate guards (repeat unresolved cases only), and actual Shared workspace fill/row checks before accepting ONE PR. Retain failures. Packed7093 acceptance is separate and no results are mixed.
