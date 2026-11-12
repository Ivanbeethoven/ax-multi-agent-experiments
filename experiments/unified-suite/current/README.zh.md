# C1–C4 实验方案

**实验已暂停。** 先保全结果并清理本任务创建的云资源，保留原跳板机；不启动新实验。[脚本入口与当前卡点](../pilot-aliyun/scripts/README.zh.md)

本轮按用户要求跳过 JuiceFS 混合读写，不再补跑或补验收，原有结果保留。[C2–C4 未完成清单](PENDING.zh.md)按当前方案列项；旧 14/28 只用于解释历史账本，不能代表已删除完整复制实验后的当前进度。

各子项的工作区格式、构建 feature、运行开关与开跑条件见[统一对照表](FEATURES.zh.md)。

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

已有输入下载、Rust 回放、原始采集和旧套件工具。新路线仍需 suite 适配、可写 Overlay 消融、多机调度、四节点观测和跨机恢复验证。最近的小试：

| 项目 | 已验证 | 还缺 |
|---|---|---|
| C1 | [64 MiB 同步混合读写](../pilot-aliyun/evidence/preparation/c1-b-randrw-scratch-reverse-02/README.zh.md)完成正序、反序各一组配对，四臂均通过全量冷挂载 CRC；[固定 B 的读帧开关](../pilot-aliyun/evidence/preparation/c1-rbm-rustfs-quantum-01/README.zh.md)通过落盘布局与读回核验 | 增加重复；读帧变化对物理 I/O 和任务时间的影响 |
| C2 | [两文件热态对照](../pilot-aliyun/evidence/preparation/c2-rbm-namespace-seqfix-01/README.zh.md)两组都跑通，计时期间零对象存储读取；[云端初态发布](../pilot-aliyun/evidence/preparation/c2-current-trace-a-producer-timeout-03/README.zh.md)在 300 秒超时，回放未启动；[同一发布程序的本地检查](../pilot-aliyun/evidence/preparation/c2-local-publisher-01/README.zh.md)用 5.53 秒生成并校验全部 182 个对象；[云端短时定位](../pilot-aliyun/evidence/preparation/c2-publish-service-timeout-01/README.zh.md)在启动生产器前的桶操作发生读取超时 | 先核实对象服务就绪，再区分桶查询与创建。新检查使用 3 秒读取超时，不能解释旧 300 秒超时。完整 trace 回放与冷验尚未完成 |
| C3 | [云上抽样四阶段完成](../pilot-aliyun/evidence/preparation/cloud-c3-script-fixes-20261112/README.zh.md)：64 个固定文件、128 次内容校验，正常卸载；十万文件全量仍未完成 | 完整读回；同样设置下的性能对照 |
| C4 | [rbm 同输入原生 fork / clone](../pilot-aliyun/evidence/preparation/rbm-c4-published-20261112/README.zh.md)均通过子读父、覆盖后父不变、空缓存重挂；[阿里云 A→B](../pilot-aliyun/evidence/preparation/c4-state-reopen-a-b-result-01/README.zh.md)读回同一工作区和完全相同的 OpenCode 会话导出；[本地退出检查](../pilot-aliyun/evidence/preparation/c4-local-export-closure-diagnosis-01/README.zh.md)实测到主进程退出后 Git 仍在写快照，运行器已改为等待原进程组自然结束 | 跨机整项未通过：目标子进程退出超时，未采目标零债务；新运行器尚未云端复验。无模型续跑，云端超过 5 秒的原因尚未复现。一万文件派生性能配对本轮跳过 |

小试用于排除实现和部署问题，独立保存，不回填旧套件的正式验收。

本轮为 1 Agent、一万文件；C3、C4 派生的[独立输入及本机全量核验](TENK-INPUTS.zh.md)已就绪，尚未运行一万文件挂载资格。耗时过大的项保存证据后跳过。[本轮收尾范围](../pilot-aliyun/evidence/progress/closing-small-pilot-20261112/README.zh.md)列明已完成与未完成。百万文件/最高32并发属于后续正式条件。旧 `bench.suite template` 只有显式 `--legacy-catalog` 才生成旧协议；它不生成本方案。

[云资源清理已完成](../pilot-aliyun/evidence/preparation/b-pool-retirement-01/README.zh.md)：原 B 池三台、九盘（520 GiB）已释放；保留 A 池及跳板四台，均已停机。原始 trace、实验证据和部署信息已保留。后续使用第二个池须重新部署，不能再派发旧 B 实例 ID。

[数据与账单核查](../pilot-aliyun/evidence/progress/pilot-evidence-audit-20261112/README.zh.md)：七份历史 trace 的原 OSS 路径、清单和下载入口均已核对；本次未重新完整下载七个大归档计算 SHA。11 月 11–12 日专属资源已查到税前账单 ¥211.19，不含共享跳板、EIP、OSS，当天账单仍可能补记。

源码核对版本为 BrewFS main `ca07159f`、RustFS 指定分支 `f4ed2025`，不是后续运行的固定版本。依据：[卷配置](https://github.com/brewfs/brewfs/blob/ca07159f3da4b670e4aeb878faef928e69bc1634/src/config.rs)、[挂载与 lower 绑定](https://github.com/brewfs/brewfs/blob/ca07159f3da4b670e4aeb878faef928e69bc1634/src/cli.rs)、[Packed 预算](https://github.com/brewfs/brewfs/blob/ca07159f3da4b670e4aeb878faef928e69bc1634/src/workspace_overlay/packed_v3/wire005/budget.rs)、[hint 策略](https://github.com/brewfs/brewfs/blob/ca07159f3da4b670e4aeb878faef928e69bc1634/src/cadapter/s3/ec_hint.rs)。
