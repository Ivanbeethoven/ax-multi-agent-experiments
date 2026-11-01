# PR #394 合并后的 C1/C4 接入核对

核对日期：2026-11-01（Asia/Shanghai）。本次只读核对产品源码、现有验证记录与实验脚本，并更新文档；**未启动实验、服务、挂载、构建或云资源，也未修改执行/资格状态**。

结论：共享 Redis/TiKV 与 native P1 的 Manager 元数据能力已实现；C1/C4 的完整接入仍未通过资格。当前记为 `metadata_api_supported_in_source`、`experiment_integration_unqualified`、`execution_enabled=false`。旧文档的“Manager 仅 SQLite”已过时，但不能因此把真实挂载、非空语料和两节点恢复改为 ready。

## 1. 固定版本与审查依据

- [PR #394](https://github.com/brewfs/brewfs/pull/394) 已合并，时间为 2026-11-01 00:56:32（北京时间）。合并提交：`cba857b567fa6e94d50edd1d2e1c47c25c7b2e50`。
- PR head：`2dde181047ae6a8a00c66c0b3b328c02a3bae5c5`。GitHub merge preview：`b205d100f1a0c3e01b2c17e10512fec602f04982`，组合主线 `2783b1bf19ad4a8599c8be2a7a3da4b312c646bd`。
- preview 与最终合并树相同：`563db2dde9460bf79ee51e1257b2782d8bcf32b9`。本次另核 head→merge 的 Manager、Console、native admin、config、产品 Manager 文档无差异。
- 当前接口以固定版本 [产品文档](https://github.com/brewfs/brewfs/blob/cba857b567fa6e94d50edd1d2e1c47c25c7b2e50/doc/operations/manager.md)、[capabilities/HTTP](https://github.com/brewfs/brewfs/blob/cba857b567fa6e94d50edd1d2e1c47c25c7b2e50/src/console/manager_api.rs)、[资源结构](https://github.com/brewfs/brewfs/blob/cba857b567fa6e94d50edd1d2e1c47c25c7b2e50/src/manager/mod.rs)、[revision 编码](https://github.com/brewfs/brewfs/blob/cba857b567fa6e94d50edd1d2e1c47c25c7b2e50/src/manager/revision.rs) 为准。
- [#390 审查](manager-pr390-review.zh.md)、[2026-10-29 资格记录](../qualification-status.json) 与 [旧构建收据](brewfs-build.json) 保持原版本和结果；不回填 #394 SHA。

## 2. 已关闭的产品能力缺口

| 能力 | 当前事实 | 实验侧约束 |
|---|---|---|
| 共享 catalog | `workspace-v1` 支持 SQLite/Redis/TiKV；native-v2 P1 支持 Redis/TiKV | 按 `profiles` 的 format/backend/revision kind 核交集；顶层 backend 并集不是 native 支持矩阵 |
| 持久生命周期 | 初始化/登记、精确 fork、检查、逻辑删除；原请求/key/预分配 ID 恢复 | 202 仅接收；逐项成功、metadata readiness、mount/远端 readiness 分开 |
| 共享所有权 | 永久 journal owner＋backend-clock session fencing；身份绑定与重绑定拒绝 | 单 Manager＋原持久 journal；session 过期不能让另一 journal 接管；不是 HA |
| 非空 native 语义 | 真实 Redis/TiKV 上独立 child 读写、重开、lower/sibling 保全；已推进 head 不回退 | 对象 sink 是内存，正式来源发布和 S3/FUSE 仍需验证 |
| native 删除 | catalog 逻辑标记＋publication fence/domain quarantine | 两阶段非原子；部分失败保留 recovery_required；不提供 drain 或物理回收 |

P1 这里指 **native catalog-lower profile**，不是实验布局矩阵中的同名 P1/P2。其 carrier catalog 为 v1/schema1，native volume/control 配置为 schema2/control2，两者不能混用。native `PublishedRevision`、frozen metadata、packed catalog binding、entity-v1、每 workspace 独立 bucket 均不在此接口范围。

## 3. 仍需关闭的接入阻塞

以下是待完成项，不是本次执行清单；“出口”用于未来获准资格时验收。

| ID / 影响范围 | 当前阻塞与证据 | 关闭条件 |
|---|---|---|
| B1：C1、C4 F，非空来源 | Manager 无导入/checkpoint；P1 revision 不含 mutable native head。非空后端测试通过 [test helper](https://github.com/brewfs/brewfs/blob/cba857b567fa6e94d50edd1d2e1c47c25c7b2e50/src/native_base/runtime/admin_test_support.rs) 写入 InMemoryBlockStore 并调用测试封存器，不能作为生产准备链 | 正式语料→非空 P1 immutable lower→精确 revision→Manager fork→真实对象读回的可复核步骤与内容摘要；空 root/写后 base_revision/Pack 不得替代 |
| B2：C1、C4 F，HTTP 到节点适配 | `bench/scripts/infra/scenarios` 没有已集成的 Manager API 客户端；[experiment_step.py](../../../bench/experiment_step.py) 只执行单条已审命令；[旧 mount_command](../../../bench/reference/aliyun_suite.py#L422) 硬编码 Redis，Overlay 参数为 workspace-v1 | 按 profile 生成真实 native config，核 API IDs 与 metadata/native/object namespace；保存请求 SHA/key、operation、所有分页/逐项、mount/boot/lease 及 cohort ready 收据；失败不能自动进入负载 |
| B3：全部新资格，部署产物 | [旧构建](brewfs-build.json) 是 80ddaf7；[build-client.sh](../scripts/build-client.sh) 使用 release/io-uring/native/EC-hint。PR 的 debug/test 产物不能替代该实验 build | 冻结 merge SHA 或另审的新 SHA；按各自 platform/toolchain/feature/profile 构建一次、核 binary SHA/digest并内网分发；实际 capability 与生效配置归档。RustFS 仍按 AGENTS 指定分支单独 pin，不用历史 SHA 冒充本批解析 |
| B4：C4 M，跨节点恢复 | Manager 没有 checkpoint/mount/drain/attachment/handoff/remote-ready；[session_check.py](../../../bench/session_check.py) 核会话历史，未执行真实导出/恢复或 lease 交接 | BrewFS/JuiceFS 两节点 stay/move；同工具边界停写、原生远端完成、卸载与释放写者、冷目标恢复、HOME/XDG/内容与首次正确工具、统一协调时钟、邻居持续运行；DELETE/lease 到期不能代替 |
| B5：64/128 扩展，规模适配 | [scale_plan.py](../../../bench/scale_plan.py) 保持 execution/provisioning 关闭；分布式屏障、活跃度、burst、迁移适配仍待实现。Manager 每请求上限100且逐项串行 | 固定 128=64+64、两份原请求/独立 key；完整 128 个 ID/状态、部分重试不增殖、实际挂载和隔离、队列/429/p95/p99/资源观测。提交 burst 不称内部并行 |
| B6：native 异步消融，产品能力 | 合并 [config.rs:1321](https://github.com/brewfs/brewfs/blob/cba857b567fa6e94d50edd1d2e1c47c25c7b2e50/src/config.rs#L1321) 仍拒绝 native-v2 `commit_before_upload` | 同一格式真正实现并通过 pending-writeback、错误/重启、远端完成与配对资格后再开放；同步主组不依赖此项，不能换格式补异步臂 |
| B7：条件性拓扑不匹配 | Redis 仅 redis:// 直连，无 TLS/Cluster/Sentinel discovery；native TiKV TLS、native 独立 bucket、多 Manager 不支持 | 实际拓扑匹配批准的 profile；需要上述能力的条件保持 blocked，不能静默降级 TLS/隔离/部署方式；v1 TiKV TLS 不能推及 native |

上述 B1/B2/B3 是接入 C1/Manager C4 F 的前置项；B4 独立阻塞迁移；B5 阻塞相应规模；B6/B7 只阻塞要求这些能力的条件，不把同步、小规模资格一概说成产品无法支持。方案中的“native lower”须先落实为真实 format/revision，不能仅凭名称默认等于 Manager P1。

## 4. 已有验证与未覆盖边界

以下复核已有产物和 CI，没有在本次重跑测试。完整本地 source 基于 `f6699e5de91a5c6f9472f6b86528e8c48a249536` 加上最终 PR 改动；source manifest SHA256 为 `3fc47eef6a0ca50e8361eeed59072d7aaccea5cd2e4722395f43150d61fdf331`，native libtest SHA256 为 `b43b49256b95b178d5200dcc063bf283628a236ac2cc6beac40ec4b6ac325cca`。

| 既有验证 | 结果 | 出处/边界 |
|---|---|---|
| native 全库 | 2635 passed / 0 failed / 465 ignored | PR 冻结源，不是本次重新运行 |
| 真实 Redis/TiKV | 27 / 0 / 0 | 同一已核 libtest；含原两项 G13 GC、Manager/shared/native/锁/丢回复契约；内存对象 sink |
| 默认 workspace lib/bins | 1281 / 0 / 264 | PR 冻结源 |
| Operator all-target tests | 59 / 0 / 1 | PR 冻结源；不代表 Operator 已接 Manager |
| 默认/native 构建，默认/workspace/native/Operator 严格 Clippy，fmt/check/shell fixtures | 通过 | PR 冻结源，本地 18 项门禁 |
| 合并组合树默认 CI | 1295 / 0 / 264；build 与严格 Clippy 通过 | [CI run](https://github.com/brewfs/brewfs/actions/runs/41221336725)，checkout 为 b205d100；与最终合并树相同 |
| 合并组合树 native/Operator all-target 严格 Clippy | 通过 | 仅组合树编译/静态检查；全量 native/backend 未在此树另重跑 |

真实后端证据已经覆盖：精确 revision、原 ID 幂等、部分完成/丢 ACK、journal owner/session、失效凭据仍可读操作历史、孤立 session 恢复、workspace/native lease、已推进 head 不回退、非空 lower 和删除保护。原 GC 失败已修正 fixture 并在最终 27 项通过，不能继续列为未修复产品阻塞。

仍缺的**执行证据**：

- 合并版本的实验 release 产物与正式 S3/RustFS＋FUSE 全链路；非空 source/child 内容、隔离、冷重开、远端完成及安全卸载。一次 metadata_ready 或 root_hash 检查不替代它们。
- 真 Console OS 进程强杀/重启、节点断网、实际客户端 I/O 共存下的控制服务故障与恢复。现有 crash-session 测试是 orderly close 后构造持久 orphan-session 残留再 reopen，证明恢复语义，不是真进程强杀端到端结果。
- 64/128 子项/挂载、同屏障提交、429/积压、失败项对账、polling 开销、排队/尾延迟与 Manager 资源；现有后端测试主要是 1/2/3 个子项。
- 两节点 BrewFS/JuiceFS stay/move、可靠写入边界回执、会话与文件完整性、无丢写/双写、只 drain 迁移集合、未迁移邻居影响。
- 条件性配置的资格（例如 v1 TiKV TLS、凭据轮换和实际部署路由）；源码支持不自动等于目标部署已经验证。native 不支持的 TLS/异步等先解决产品能力。

## 5. 证据保全与本次文档检查

原始验证目录仍保全在 `C:/Users/HXY/.codex/visualizations/2026/10/31/01a1908c-5884-7bb3-aa20-34d8885e3fdc/manager-shared-native-validation/`。以下摘要用于定位和校验原件，不把机器路径当成可移植部署输入：

| 文件 | SHA256 |
|---|---|
| final-audit.json | `5f4a1f8f82409a2477e1fcab450a59bef2a376e0a70c0e45a6e5fa1957f7b596` |
| final-merge.json | `1e9cb065accb121447dc71bafaafcfeb3b70717aca46c67979bd7d7aa5b19829` |
| backend-validation-final.md | `79863eaccfadaba758748634577c7178c23b0633bcaf577d48d9b28956c0c4b8` |
| 20261031T163412Z-real-backends/result.json | `d694d7b00d226974d28f18961cf22e6f51cd1f5702d4290ff39ae95b79306ae3` |

本次更新 [MANAGER](../runbook/MANAGER.zh.md)、[CONTROL-PLANE](../CONTROL-PLANE.zh.md)、C1/C1-pilot/C4 prompt、RUN/SCALE-RUN/CLUSTERS/MIGRATION，以及 README/PLAN/SCALE 入口。连同本报告共 13 份文档：158 个本地链接全部存在，43 个 shell 代码块只用 `bash -n` 解析、未执行，3 个 JSON 代码块解析通过，Markdown fence 配对与 `git diff --check` 通过。独立源码复核指出 CONTROL-PLANE 的 mounted/boot ACK 等仍为设计目标，已明确标注。旧 #390 记录与 qualification-status、执行脚本及实验结果不改动。后续顺序见 MANAGER 的门禁节，所有运行命令均留待独立授权。
