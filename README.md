# BrewFS 实验仓库

执行 Agent 从 **[SSH 操作手册](experiments/retest-2026-10-29/runbook/SSH-RUN.zh.md)** 开始。环境可以是已配置 SSH 的容器、虚拟机或服务器；当前只准备脚本，运行时按实际清单分配资源。

| 任务 | 入口 |
|---|---|
| 看实验设置、baseline 和指标 | [C1–C4 总览](experiments/retest-2026-10-29/PLAN.zh.md) |
| 交给实验 Agent | [分工与调度单](experiments/retest-2026-10-29/runbook/CLUSTERS.zh.md) |
| 连接节点并逐步执行 | [SSH-RUN](experiments/retest-2026-10-29/runbook/SSH-RUN.zh.md) |
| 准备输入与核对来源 | [数据交接](experiments/retest-2026-10-29/runbook/DATA.zh.md) · [Trace 来源](paper-design/TRACE-WORKLOADS.zh.md) |
| 查看性能记录要求 | [可观测性](experiments/retest-2026-10-29/runbook/OBSERVABILITY.zh.md) |
| 找代码或运行测试 | [bench](bench/README.md) · [runtime](bench/runtime/README.md) · [tests](tests/README.md) |
| 查看既有结果 | [本轮证据](experiments/retest-2026-10-29/README.zh.md) · [历史结果](experiments/README.md) |

## 目录

```text
experiments/retest-2026-10-29/   实验方法、Agent 交接、运行手册与证据
bench/                         方法矩阵、输入/结果核验、单步工具
  runtime/                     SSH 接入、挂载、并发、流转与观测
  reference/                   仍用于既有资格/结果校验的实现
infra/                         可选环境准备与 RustFS 部署
scripts/                       Trace 采集、打包、回放及仓库检查
tools/                         Rust 回放器与测量程序
tests/                         回归与接入测试
paper-design/                  方法依据、参数及 Trace 来源
archive/                       退役实现和旧方案
```

`runs/` 保存本机配置、日志和结果，不入 Git；`inputs/`、`results/` 和历史证据保留原始记录。环境接入与实验测量分开，资源版本约定见 [AGENTS.md](AGENTS.md)。
