# 2026-10-29 重测准备与证据

**最新范围：64 Agent 主目标 / 128 扩展，一 Agent 一 VM 或 Docker；先看 [规模方案](SCALE.zh.md) 与 [默认关闭的固定总量随机等待](../../tools/full-fuse-replay/THINK-TIME.zh.md)。全部仍为准备状态。**

**先看 [四集群实验总览](PLAN.zh.md)：每组场景、baseline、机器数、指标，以及阿里云/自备服务器差异。** 详细参数在 [主方案](../../paper-design/FINAL-PLAN.zh.md)，本目录同时保存资格证据和辅助脚本。当前只准备供用户审阅，尚未启动本轮正式性能测量。

| 入口 | 内容与边界 |
|---|---|
| [四集群总览](PLAN.zh.md) | 64/128 Agent；四组对照、指标、角色机器数和两类环境差异 |
| [Manager 接入步骤](runbook/MANAGER.zh.md) / [后续设计](CONTROL-PLANE.zh.md) | #390 的 SQLite 基础 API、fork 重试/安全删除、C1/C4 完成判据；共享后端与节点交接尚未就绪 |
| [分步执行手册](runbook/RUN.zh.md) | 操作脚本、命令、输出与 AI 检查点 |
| [四集群与 prompt](runbook/CLUSTERS.zh.md) | 四位负责人、资源边界与逐步执行合同 |
| [trace 来源与完整性](../../paper-design/TRACE-WORKLOADS.zh.md) | 7 份已有输入、SWE-bench/定制任务来源、OSS 对账与回放边界 |
| [本次代码准备验证](evidence/portable-preparation-review.json) | 两种环境的拓扑、单步工具、Windows/Linux 检查；不代表正式实验通过 |
| [主方案](../../paper-design/FINAL-PLAN.zh.md) | 实验设置、配置、方法、指标与注意事项 |
| [外部 opt-381 证据](evidence/external-opt-381/assessment.zh.md) | 原 v3 报告、摘要冲突说明、Packed 异常与本轮取舍 |
| [状态](qualification-status.json) | 已验证项和未完成项，不能当作正式成绩 |
| [机制备查](mechanism-notes.zh.md) · [布局参数](layout-matrix.json) | inline、EC block、读帧与客户端实际上传路径 |
| [构建回执](evidence/brewfs-build.json) | 已构建并校验的源码、features 和二进制摘要 |
| [布局与真实客户端资格](evidence/layout-v2-qualification/summary.json) | 198 个对象边界、18 个布局、4 个真实 BrewFS 上传对象 |
| [P1/P2 小规模资格](evidence/ml-1024-qualification/summary.json) | 两条 1,024 文件路径已通过；默认 8 MiB 解码工作区失败记录仍保留 |
| [脚本](scripts/README.md) | 批次工具集中放置；单步执行工具在仓库 bench/ |
| [历史工具](history/README.md) | 已完成构建的恢复工具，只留追溯，不再次执行 |

已准备 1,048,576 条独立 TinyStories 记录。当前扫描器只完成数据访问代理负载资格，真实 ML-agent 任务及其固定读集仍需冻结；不能把代理扫描分数称为完整 Agent 成绩。

已有原始证据保持原样；新增解读放在单独文件。旧 2026-10-28 方案见 [归档](../../archive/plans/2026-10-28/README.md)，历史性能数据见 [结果索引](../README.md)。
