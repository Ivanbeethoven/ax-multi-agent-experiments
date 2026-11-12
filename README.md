# BrewFS 实验仓库

验证云上多 Agent 的工作区共享、独立修改与跨机继续任务。**[从当前 C1–C4 方案开始](experiments/unified-suite/current/README.zh.md)**。

主候选：BrewFS `workspace-v1 + Packed-v3`。内部系统对照：`flat-v1`。唯一外部对照：JuiceFS。新实验排除 entity-v1 和 native-v2。

| 内容 | 入口 |
|---|---|
| RQ、对照、三个运行开关与消融 | [当前方案](experiments/unified-suite/current/README.zh.md) |
| 输入、操作量、性能指标、正确性 | [负载与指标](experiments/unified-suite/current/WORKLOADS.zh.md) |
| Redis、四节点 RustFS、缓存、机器与磁盘 | [详细配置](experiments/unified-suite/current/CONFIGURATION.zh.md) |
| 下载、Rust 回放、指标收集与交接 | [执行步骤](experiments/unified-suite/current/EXECUTION.zh.md) |
| 子项所需格式、feature 与开跑条件 | [C1–C4 对照表](experiments/unified-suite/current/FEATURES.zh.md) |
| 完整参数 | [design.json](experiments/unified-suite/current/design.json) |
| 并行与按需资源 | [实验 pipeline](experiments/unified-suite/MAIN-SCALE.zh.md) |
| 全部 trace 下载地址与 SHA | [数据目录](scenarios/k8s-containers/data-catalog.md) · [archives.json](scenarios/k8s-containers/archives.json) |
| 采集与回放工具 | [Rust full-fuse-replay](tools/full-fuse-replay/README.md) |
| 旧计划和实验记录 | [统一套件历史入口](experiments/unified-suite/README.md) · [实验目录](experiments/README.md) |

**实验已暂停。** 当前只归档和清理。[C2–C4 未完成清单](experiments/unified-suite/current/PENDING.zh.md) · [归档、清理与运行脚本入口](experiments/unified-suite/pilot-aliyun/scripts/README.zh.md)。

恢复后的小试用 1 Agent，C3 和 C4 派生各用独立的 10,000 个 4 KiB 文件。百万文件和最高 32 Agent 属于正式规模。`design.json` 记录规划参数；统一运行开关和套件适配尚未完成。

默认一池依次做系统配对，第二池按已就绪任务申请。代码在远端构建一次，资源按阶段申请并在归档核验后释放；部署规则见 [AGENTS.md](AGENTS.md)。

`bench/` 是调度与核验，`tools/` 是 Rust 测量工具，`infra/` 是部署脚本。本地运行状态放 `runs/`，不入 Git；历史回执不改写。
