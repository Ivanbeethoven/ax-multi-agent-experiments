# 实验准备与证据

当前操作入口：[SSH-RUN](runbook/SSH-RUN.zh.md)。实验方法见[总览](PLAN.zh.md)与[C1](C1.zh.md)，执行分工见[CLUSTERS](runbook/CLUSTERS.zh.md)。当前准备已有SSH环境的脚本，C1并发上限8；机器数量按实际清单填写。

以下记录保留各自批次的版本、配置与验证范围。

| 入口 | 内容 |
|---|---|
| [四集群总览](PLAN.zh.md) · [分工与 prompt](runbook/CLUSTERS.zh.md) | 实验设置、配置、方法、指标与负责人 |
| [数据交接](runbook/DATA.zh.md) · [trace 来源](../../paper-design/TRACE-WORKLOADS.zh.md) | 27份输入交接、原有trace来源、完整性核验 |
| [Manager 操作](runbook/MANAGER.zh.md) · [接入核对](evidence/manager-pr394-integration-review.zh.md) | 生命周期接口、版本支持及实际资格 |
| [机制备查](mechanism-notes.zh.md) · [布局参数](layout-matrix.json) | inline、EC block、读帧和实际上传路径 |
| [状态](qualification-status.json) | 各批次已验证项及待完成项 |
| [SSH 准备验证](evidence/ssh-preparation-review.json) | 154 项通过、11 项环境相关跳过，含真实 SSH 与观测器启停；附代码及测试日志摘要 |
| [构建回执](evidence/brewfs-build.json) | 历史构建源码、features和二进制摘要 |
| [布局资格](evidence/layout-v2-qualification/summary.json) · [ML资格](evidence/ml-1024-qualification/summary.json) | 198个边界对象、18种布局；两条1024文件路径及原失败记录 |
| [外部 opt-381](evidence/external-opt-381/assessment.zh.md) | 原报告、摘要冲突说明与Packed异常 |
| [早期准备验证](evidence/portable-preparation-review.json) · [ECS准备验证](evidence/ecs-provided-preparation-review.json) | 对应提交的离线验证范围 |
| [基础资格命令](runbook/RUN.zh.md) · [历史工具](history/README.md) | 早期配置与恢复工具，供复现追溯 |

已准备 1,048,576 条独立 TinyStories 记录。当前扫描器只完成数据访问代理负载资格，真实 ML-agent 任务及其固定读集仍需冻结；不能把代理扫描分数称为完整 Agent 成绩。

Manager 接入基线固定为 [PR #394](https://github.com/brewfs/brewfs/pull/394) 的合并提交 `cba857b567fa6e94d50edd1d2e1c47c25c7b2e50`。共享后端的元数据支持已落地，不能再把“只支持 SQLite”列为当前阻塞；实际批次核验部署产物、非空源、原生挂载和两节点交接资格；历史产品测试与本批结果分别记录。

已有原始证据保持原样；新增解读放在单独文件。旧 2026-10-28 方案见 [归档](../../archive/plans/2026-10-28/README.md)，历史性能数据见 [结果索引](../README.md)。
