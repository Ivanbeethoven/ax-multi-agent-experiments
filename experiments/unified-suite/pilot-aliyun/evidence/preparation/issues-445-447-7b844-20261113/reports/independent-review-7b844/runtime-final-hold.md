Issue445-447 final independent evidence review: HOLD ONE integrated PR at7b844504.

Independently rehashed883+982+6 archive members and4 timestamp files; factual checks2581/2581.

Qualified EC4 cached writes now have complete16MiB known-pattern cold readback, same binary/args with new PID and empty cache, clean service gates and CPU measurements through strict drain. Three paired BrewFS CPU totals .54/.51/.50 -> .43/.43/.41s (20.37%,15.69%,18% lower). This is process CPU, not end-to-end wall throughput: fully drained wall is fractionally higher in all3pairs. Validation/remount costs are outside measured boundaries and separately recorded.

Long hot-row guards:24arms correct, zero warm/timed misses,64MiB byte budget and600sTTL fixed.65536ops/thread:1thread median3.699990->3.643019s;8threads13.315117->12.843490s. No pair slower by more than5%;1thread has one marginal slower pair. Keep prior short-run regressions, do not claim every shape/round faster.

Correctness remains blocking. Baseline formal third scene randwrite/direct0 fails warm/cold SHA; exact same-shape candidate402 and baseline403 also fail. Each has four512MiB files at20s/4MiB/4jobs/iodepth1/same seed. The original formal failure differs in files0 and1; both exact isolated diagnostics differ in files1 and3 after ordinary unmount with old process gone and fresh empty local cache, although service errors and dirty counters are zero.

The five archived4MiB sealed files exactly cover every differing1MiB segment:12MiB candidate,8MiB baseline; all occur at60MiB offsets inside64MiB chunks. Their actual bytes match warm hashes/prefixes, not cold. This establishes overlay involvement, not warm correctness. Prefix48/56 decoded pairs match reported fio timestamps and cold is later in all20differences; this supports an older overlay hypothesis but does not prove the final application oracle or exact cleanup race.

Do not publish/merge the integrated candidate until the inconsistency is resolved and required standard guards pass. Valid445/446/cached target evidence remains scoped and preserved; Packed7093 acceptance is separate.
