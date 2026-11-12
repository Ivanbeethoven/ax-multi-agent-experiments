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

新方案参数已明确，suite 适配与新路线资格待完成；`design.json` 是规划文件，不能直接执行。当前三个统一 flag 尚未实现，旧 suite 模板需显式选择历史模式。先完成 1 Agent、100k 文件资格，再开展最高 32 Agent、百万文件正式实验。

默认一池依次做系统配对，第二池按已就绪任务申请。代码在远端构建一次，资源按阶段申请并在归档核验后释放；部署规则见 [AGENTS.md](AGENTS.md)。

`bench/` 是调度与核验，`tools/` 是 Rust 测量工具，`infra/` 是部署脚本。本地运行状态放 `runs/`，不入 Git；历史回执不改写。
