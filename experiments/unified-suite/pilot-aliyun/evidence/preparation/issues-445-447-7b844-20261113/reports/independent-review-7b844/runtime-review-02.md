Issue #445-447 independent workspace review: actual correctness verified; integrated PR remains NOT READY.

Independently rehashed 2148 archived files and passed 13937/13937 factual checks.

114 mounts clean,56 fill waves/448 SHA-correct reads,56 hot xattr cases,2 memory cases. Same frozen source/binaries and explicitly equal mount budgets. All hot warm/timed row misses are0.

- fill keys=1 threads=1: median 77.113 -> 78.818 ms (+2.211%), all six pairs faster=False.
- fill keys=1 threads=8: median 77.973 -> 77.306 ms (-0.856%), all six pairs faster=False.
- fill keys=8 threads=1: median 450.949 -> 451.478 ms (+0.118%), all six pairs faster=False.
- fill keys=8 threads=8: median 418.046 -> 68.154 ms (-83.697%), all six pairs faster=True.
- row keys=1 threads=1: median 56.150 -> 57.865 ms (+3.055%), all six pairs faster=False.
- row keys=1 threads=8: median 228.493 -> 225.920 ms (-1.126%), all six pairs faster=False.
- row keys=128 threads=1: median 57.410 -> 64.106 ms (+11.663%), all six pairs faster=False.
- row keys=128 threads=8: median 219.182 -> 235.541 ms (+7.464%), all six pairs faster=False.

The8-group/8-thread fill result is a real-path controlled-delay concurrency improvement, with independently recomputed peak1->8 and one fetch per exact GroupMeta. It includes cold leaf lookup/open+pread+close; parent setup is excluded. No general undelayed speed claim.

Row hot/fill uses64MiB; memory pressure separately uses512KiB. Candidate maximum sampled retained bytes524046<=524288 across65 samples. Baseline capacity/retained metrics are unsupported, not0; this is not RSS or a continuous peak.

The128-key short hot measurements regress at the median. Keep these records; fixed-op longer paired measurements are pending. Earlier ordinary single-path cached timings remain unqualified, and fresh EC4 durability plus formal512MiB/20s standard guards remain pending.

EC4 means four logical directories on the same physical disk with an explicit check bypass, not four independent disks. Packed7093 acceptance is separate.
