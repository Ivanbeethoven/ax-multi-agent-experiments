# BrewFS 实验仓库

| 入口 | 内容 |
|---|---|
| [本轮四集群总览](experiments/retest-2026-10-29/PLAN.zh.md) | 各组场景、baseline、机器数、指标和阿里云/自备环境差异；当前仅准备 |
| [实验方案](paper-design/FINAL-PLAN.zh.md) | 实验设置、配置、方法、测量指标与注意事项 |
| [四集群与 Agent prompt](infra/experiment/CLUSTERS.zh.md) | C1 通用/消融、C2 Pack、C3 动态块、C4 fork/clone 与跨节点流转；推荐 9 台，最低 3 台 |
| [trace 来源与规模](paper-design/TRACE-WORKLOADS.zh.md) | 7 份输入的 benchmark、OSS 对账、回放方法与适用范围 |
| [分步执行手册](infra/experiment/RUN.zh.md) | 具体操作脚本、命令、输出与 AI 检查点 |
| [阿里云准备](infra/alicloud/PROVIDER.zh.md) / [自备服务器准备](infra/servers/README.zh.md) | 共用同一实验脚本，分别管理资源、连接和收尾 |
| [本轮证据](experiments/retest-2026-10-29/README.zh.md) | 构建、布局、小规模验证与外部报告 |
| [历史结果](experiments/README.md) | 已有实验原始记录 |

方案包括产品比较、BrewFS 内部消融、Pack 小文件、动态块、fork/clone 与 Agent 跨节点流转。各组独立并行；baseline 在组内配对，AI 逐步监督执行。

`paper-design/` 保存方案；`harness/` 保存单步工具与参考运行器；`experiments/retest-2026-10-29/scripts/` 保存本轮辅助工具；`evidence/` 保存验证记录。部署约定见 [AGENTS.md](AGENTS.md)。
