# BrewFS 实验仓库

比较 BrewFS 与 JuiceFS 在工作区共享、文件读写、派生和跨机重挂上的表现。使用已经采集的文件系统 trace；当前不再运行真实 Agent，也不验证 OpenCode 对话恢复。

**正式实验暂停。** 最小脚本检查已完成：首轮 21 个入口通过，剩余 5 个由后续检查补齐。功能通过不等于正式性能验收，实际版本和规模见结果记录。

| 要找什么 | 入口 |
|---|---|
| 已有性能结果和图 | [C1–C4 结果](experiments/unified-suite/current/results-20261112/RESULT.zh.md) |
| 最新五项功能检查 | [结果、修复、版本与清理](experiments/unified-suite/pilot-aliyun/evidence/preparation/rbm-five-checks-20261112/README.zh.md) |
| 实验怎么设计、还缺什么 | [当前 C1–C4 方案](experiments/unified-suite/current/README.zh.md) · [未完成项](experiments/unified-suite/current/PENDING.zh.md) |
| 脚本怎么运行 | [脚本入口](experiments/unified-suite/pilot-aliyun/scripts/README.zh.md) · [最小检查](experiments/unified-suite/pilot-aliyun/FIVE-CHECKS.zh.md) |
| 配置、输入与指标 | [配置](experiments/unified-suite/current/CONFIGURATION.zh.md) · [负载与指标](experiments/unified-suite/current/WORKLOADS.zh.md) · [参数](experiments/unified-suite/current/design.json) |
| trace 从哪下载、怎么回放 | [下载清单](scenarios/k8s-containers/data-catalog.md) · [地址与 SHA256](scenarios/k8s-containers/archives.json) · [Rust 回放器](tools/full-fuse-replay/README.md) |
| 历史实验和退役脚本 | [实验索引](experiments/README.md) · [历史文件清单](archive/README.md) |

当前主候选是 `workspace-v1 + Packed-v3`，外部对照只有 JuiceFS。C4 比较原生 fork 与原生 clone；Overlay 开销使用同引擎开关，并在计时前验证 lower 已热。Flat 只用于 C1/C2，完整复制不作为 C4 对照。统一开关和正式规模适配的缺项见当前方案。

`bench/` 放调度、负载和核验，`tools/` 放 Rust 工具，`infra/` 放部署，`experiments/` 放计划与结果。本地运行输出写到 `runs/`，不提交 Git。验证命令见 [tests/README.md](tests/README.md)。

原 A/B 测试机已释放，jump 和两块盘保留，最近一次核验为 StopCharging。恢复实验要使用新的资源清单；不能照抄历史机器 ID。版本、资源归属和并行规则见 [AGENTS.md](AGENTS.md)。
