# C4 纯写反向复测

**纯写对顺序敏感；直接随机写额外墙钟开销待归因，候选尚未通过性能验收。** 本轮顺序为 C4→baseline，只重复 seqwrite 与 randwrite；[首轮结果](../first-run/README.md)完整保留。

| 场景 | active 吞吐 baseline → C4（MiB/s） | 变化 | tool-wall 吞吐 baseline → C4（MiB/s） | 变化 |
|---|---:|---:|---:|---:|
| fio-randwrite-direct0 | 94.371 → 241.231 | +155.6% | 65.379 → 229.905 | +251.6% |
| fio-randwrite-direct1 | 143.350 → 144.162 | +0.6% | 136.571 → 115.520 | -15.4% |
| fio-seqwrite-direct0 | 118.188 → 253.392 | +114.4% | 81.517 → 241.905 | +196.8% |
| fio-seqwrite-direct1 | 181.355 → 302.019 | +66.5% | 164.909 → 302.200 | +83.3% |

seqwrite 的相对吞吐方向在两轮间翻转，buffered randwrite 的差异幅度变化明显。直接随机写 active 吞吐接近，但包含工具全程的墙钟吞吐仍低 15.4%；C4 的 wall−active 约 4.967 s，baseline 约 0.993 s。墙钟来自 perf-summary.tsv 的整秒记录，含启动／退出等开销；这不是单独测得的写回尾延迟。

两版本各 4 项执行 pass、exit 0，写后 drain／pending／dirty／buffer dirty 全 0，各 1 次挂载自然退出／卸载 0，自有容器已核验为 0。未清宿主页缓存；两轮单次观测无法把差异严格归因于顺序或软件。

[精确吞吐及 clat p99](summary.json) · [原始全部比较](comparison.tsv) · [原始文件 SHA256](source-file-hashes.json) · [状态](status.json) · [清理核验](cleanup-verification.json)
