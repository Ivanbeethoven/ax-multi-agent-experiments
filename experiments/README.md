# 实验索引

每个 C 独立维护 README.md 和 experiment.json，共用准备、缓存和指标说明在 docs/。

直连轮次执行 C1～C4，合计 137 个条件、735 次系统运行，见 [直连交接](../docs/HANDOFF-DIRECT-NETWORK.md)。

| 实验 | 主条件 | 技术消融 | 参数影响 | 默认系统运行次数，含三次重复 | 本轮执行 |
|---|---:|---:|---:|---:|---|
| [C1 文件 I/O 与动态块](c1-io/README.md) | 17 | 24 | 40 | 321 | 是 |
| [C2 多 Agent 完整任务](c2-agents/README.md) | 4 | 4 | 3 | 81 | 是 |
| [C3 只读小文件 Packed](c3-ml/README.md) | 9 | 6 | 0 | 105 | 是 |
| [C4 fork 与合并](c4-fork/README.md) | 22 | 7 | 0，另有 1 个方法比较 | 228 | 是 |

一个条件可以包含多个系统，每个系统重复三次。C1～C3 合计 107 个条件、507 次系统运行。C2 192 并发、C3 百万文件高并发与 C4 百万文件合并均为独立扩展项，不包含在表内。

[RQ 与技术消融](../docs/ABLATIONS.md) · [执行交接](../docs/HANDOFF-C1-C3.md) · [环境](../docs/SETUP.md) · [结果目录](../docs/METRICS.md)。历史材料见 [archive](../archive/README.md)。

C4 的实际接口、对照、计时与 64 个子工作区安排见 [C4 执行交接](../docs/HANDOFF-C4.md)，结果单独放在 `results/cluster-c4/run-NNN/`。
