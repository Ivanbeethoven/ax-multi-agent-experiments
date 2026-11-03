# SSH 实验运行代码

操作入口：[统一C1–C4](../../experiments/unified-suite/README.md)，旧[SSH-RUN](../../experiments/retest-2026-10-29/runbook/SSH-RUN.zh.md)保留历史方法。运行环境由执行方提供，本目录按单步组织。

| 层 | 文件 | 工作 |
|---|---|---|
| 接入 | [fleet.py](fleet.py)、[preflight.py](preflight.py) | 生成/检查 SSH 清单，只读探测，生成 peer，调用一个生命周期操作 |
| 配置 | [prepare.py](prepare.py)、[examples](examples) | 挂载、trace worker、已有容器配置草稿 |
| 新工作量 | [workloads.py](workloads.py)、[maintenance.py](maintenance.py) | fio/小文件worker草稿、只读维护与磁盘快照 |
| 执行 | [node.py](node.py)、[cohort.py](cohort.py) | 管理一个挂载/worker；一次全体就绪屏障与结果聚合 |
| 流转 | [handoff.py](handoff.py) | C4 源停止、写者释放、原生会话转移和目标继续 |
| 观测 | [observe.py](observe.py) | 原始指标采集、阶段标记、完整性/断档/开销检查 |
| 共用库 | [transport.py](transport.py)、[common.py](common.py) | SSH/stdin、摘要、进程和实际 cgroup 身份 |

清单结构放在 [ssh_inventory.py](../ssh_inventory.py)。方法矩阵放在 [scale_plan.py](../scale_plan.py)。这些层共用一套实现，provider=ssh 不调用云 API。

结果保全用 [experiment_step.py](../experiment_step.py) 的 seal/verify；输入与最终内容用 [trace_checks.py](../trace_checks.py)。完整指标要求集中在 [OBSERVABILITY](../../experiments/retest-2026-10-29/runbook/OBSERVABILITY.zh.md)。
