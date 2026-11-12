# C1–C4 实验方案

目标：测量云上多 Agent 共享初始数据、独立修改及换机器继续任务的性能与正确性。主体使用固定文件访问 trace，C4 用少量真实 OpenCode resume 验证状态恢复。

| 文档 | 内容 |
|---|---|
| [负载与指标](WORKLOADS.zh.md) | 输入、操作量、计时、输出和正确性 |
| [详细配置](CONFIGURATION.zh.md) | 版本、缓存、Redis、四节点 RustFS、机器和盘 |
| [并行流程](../MAIN-SCALE.zh.md) | 完整配对分池、总并发32槽、资源生命周期 |
| [执行与观测](EXECUTION.zh.md) | 工具命令、指标来源、验证和交接 |
| [design.json](design.json) | 机器可读参数；design-only，不可直接执行 |

## 问题与对照

| RQ | 问题 | 实验与设计 |
|---|---|---|
| RQ1 | 能否少读、少传并缩短 I/O 时间？ | C1/C2：动态块开关，测实际 B/q、字节、请求和耗时 |
| RQ2 | 任务运行增加多少开销，并发后怎样？ | C2：系统比较；另做同引擎 Overlay 开关，lower 预热后计时，目标开销≤10% |
| RQ3 | 大量共享小文件的查找、读取和修改是否更快、更省？ | C3：Pack 测元数据请求/阶段时间；Overlay 测私有增量与首次修改 |
| RQ4 | 派生与迁移能否更快且状态正确？ | C4：BrewFS 原生 fork 对比 JuiceFS 原生 clone；另测共享工作区迁移 |

| 图表名 / ID | 实际路径 | Overlay / Pack / 动态块 |
|---|---|---|
| BrewFS 基础版 / `brewfs-basic` | flat-v1，仅用于 C1 I/O 和 C2 回放 | 关 / 关 / 开 |
| BrewFS 完整方案 / `brewfs-full` | workspace-v1 + Packed-v3 wire005 lower | 开 / 开 / 开 |
| JuiceFS / `juicefs` | 原生挂载与 clone；唯一外部对照 | 原生实现；共用 enabled RustFS，不发 BrewFS hint |

新实验排除 workspace-entity-v1 和 workspace-native-v2。旧记录中的 native、native-workspace、packed 分别指 flat、native-v2、entity-packed，按原记录解释。

## 三个开关与消融

计划接口为 `--overlay on|off`、`--packed-metadata on|off`、`--dynamic-block on|off`，统一适配器待实现。同一冻结运行使用相同 BrewFS 二进制和 RustFS 镜像。

| 条件 | Overlay / Pack / 动态块 | 实际动作 |
|---|---|---|
| 完整方案 | 开 / 开 / 开 | Packed lower、私有 upper、所有上传者按策略传 hint |
| 关闭 Overlay | 关 / 开 / 开 | 同引擎绕过 Overlay，保持元数据及 payload 布局；只测 lower 预热后的运行 |
| 关闭 Pack | 开 / 关 / 开 | 同引擎 KV 元数据，保持 payload 对象、帧与缓存预算 |
| 关闭动态块 | 开 / 开 / 关 | 服务四开关关闭、全部上传者无 hint、新对象前缀 |
| 全关 | 关 / 关 / 关 | 三项关闭动作同时实现，回答整体收益 |

Flat 只作为 C1、C2 的系统对照。C4 派生只比较原生 fork 和原生 clone。Overlay 开销单独测：同一 BrewFS 引擎、相同数据布局和缓存预算，两臂各自预热并验证 lower 后再计时。开关尚未实现时，不拿 Flat 或两种创建方式的耗时替代。

## 实现状态

已有输入下载、Rust 回放、原始采集和旧套件工具。新路线仍需 suite 适配、同引擎消融、多机/全局槽位调度、四节点观测，以及完整输入的导入、冷重开、隔离和干净迁移验证。C4 原生 fork 的[非空 lower 本机验证](../pilot-aliyun/evidence/preparation/c4-workspace-v1-packed-fork-01/README.zh.md)未通过。Overlay 同引擎开关仍缺；有开关后还要核实 lower 数据确实热驻留。指标缺口见[执行文档](EXECUTION.zh.md#验收与交接)。

先做1 Agent/100k功能验证，再做百万文件/最高32并发正式条件。旧 `bench.suite template` 只有显式 `--legacy-catalog` 才生成旧协议；它不生成本方案。

源码核对版本为 BrewFS main `ca07159f`、RustFS 指定分支 `f4ed2025`，不是后续运行的固定版本。依据：[卷配置](https://github.com/brewfs/brewfs/blob/ca07159f3da4b670e4aeb878faef928e69bc1634/src/config.rs)、[挂载与 lower 绑定](https://github.com/brewfs/brewfs/blob/ca07159f3da4b670e4aeb878faef928e69bc1634/src/cli.rs)、[Packed 预算](https://github.com/brewfs/brewfs/blob/ca07159f3da4b670e4aeb878faef928e69bc1634/src/workspace_overlay/packed_v3/wire005/budget.rs)、[hint 策略](https://github.com/brewfs/brewfs/blob/ca07159f3da4b670e4aeb878faef928e69bc1634/src/cadapter/s3/ec_hint.rs)。
