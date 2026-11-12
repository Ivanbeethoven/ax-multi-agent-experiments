# 工作入口与当前范围

先读 [当前 C1–C4 方案](experiments/unified-suite/current/README.zh.md)、[feature 对照](experiments/unified-suite/current/FEATURES.zh.md)和[未完成项](experiments/unified-suite/current/PENDING.zh.md)。

2026-11-13 已按 [8 个实验块](experiments/unified-suite/current/BLOCKS.zh.md)整理分工。当前授权是检查流程和修改规划，未恢复云上正式实验。各块独立准备；配对条件仍同池顺序执行。运行器的 `--block` / `--dry-run` 仅用于最小功能检查，不能作为性能准入或跨池资源锁。

- 正式实验暂停。2026-11-12 的 `ssh rbm` 最小检查已收尾：首轮 21 项通过，剩余 5 项由[后续检查](experiments/unified-suite/pilot-aliyun/evidence/preparation/rbm-five-checks-20261112/README.zh.md)补齐。原失败不改写，功能通过不算性能验收。
- 用户已取消实际 Agent 运行和 OpenCode resume；C4 只检查工作区文件树、跨机重挂和后续文件读写，状态文件按普通文件核验。
- JuiceFS 混合读写已跳过。C4 删除 BrewFS 完整复制，只比较原生 fork 与原生 clone；Overlay 用同引擎开关，并在计时前验证 lower 热态。
- 主候选为 `workspace-v1 + Packed-v3`；`flat-v1` 只用于 C1/C2；外部对照只有 JuiceFS。entity-v1、native-v2 不进入新实验。缺少的统一开关/适配如实记录，不用旧引擎补数。
- 小试为 1 个负载槽，C3/C4 派生各 10,000 × 4 KiB 独立输入；`--tiny` 仅用于明确选择的脚本检查。正式百万文件、最高 32 槽参数仍以 current 为准，不把历史规模改写成新规模。
- 结果、失败、冻结配置和原始回执保持原义。仓库整理先查引用；保留最新修复、配置和测试，退役文件记录来源 commit 与 SHA256，见 [archive](archive/README.md)。其他会话的修改在独立 worktree 中处理。

# 资源与运行

- 原 A/B 两池各 3 台实例、9 块盘已释放。jump 及 40 GiB 系统盘、100 GiB 协调盘保留；最近核验为 StopCharging。原 ID 不可再派发。恢复实验需用户新指令及新资源清单。
- 只能释放有本任务创建证明的资源，不按名称前缀删除。原网络、OSS 和其他任务资源保留；清理方法见 [RELEASE](experiments/unified-suite/pilot-aliyun/RELEASE.zh.md)。
- 用户只要本地结果，不再自动下载原 trace、完整监控或运行产物包。删除磁盘前，按实际保留范围在盘外核验不可再生证据；先取回执再停机。StopCharging 不代表磁盘免费。
- 默认一池，最多两池；总并发≤32负载槽（C1计1槽，迁移目标替换源）。同一配对批次固定在一池，各臂顺序执行，owner 持有准备至归档的锁。共享主机、盘、服务、瓶颈链路的导入/归档/测量串行；见 [MAIN-SCALE](experiments/unified-suite/MAIN-SCALE.zh.md)。
- 运行前读实际 plan、inventory、state 和资源账本。started 不自动重放；共享资源使用同一 resource ID。协调者本地 `state.sqlite` 仅记录调度；元数据主线 Redis，TiKV另立补充运行。
- 每项清理按 [CLEANUP](experiments/unified-suite/current/CLEANUP.zh.md)执行。池锁覆盖收尾；小试可用 `--scratch-parent` 隔离临时目录，成功且退出证据完整才删除。失败现场和共享输入保留，不按名称前缀或时长清理。派发前核新增峰值后仍余 20% 容量及 inode。
- 不为提高通过率修改冻结校验器或阈值；`reviewed/passed` 只能来自真实审查。旧冻结协议使用既有校验器，`bench.suite template --legacy-catalog` 仅供明确选择的历史复现。

# 构建、配置与版本

- 每个新 campaign 同步 BrewFS 最新分支和用户指定 RustFS 分支：`https://github.com/Ivanbeethoven/rustfs` 的 `feat/dynamic-block-size-rebased`。冻结后固定全部配对重复和扩展曲线使用的产物，不能中途热更新；升级另开 campaign。
- 同一源码、工具链、feature/profile、目标平台只编译一次，再按 SHA256/镜像 digest 通过内网分发。不同构建参数单独标识；不逐节点重复编译。
- 默认仅跳板有公网 IP，其余节点使用私网和 ProxyJump。输入先下载至跳板再内网分发，或使用同地域 OSS internal endpoint；不为下载给每台 worker 配公网。已有任务不为调整拓扑而贸然中断。
- 动态布局默认开启：RustFS 四个 layout 开关为 true，BrewFS 编译 hint feature 并使用 auto。auto 不是完整多档自适应，行为见 [RustFS 部署](infra/rustfs/README.md)。只有明确消融可以关闭 dynamic；C3 无 hint 臂服务端仍开启。
- 同一批使用同一 BrewFS 二进制和 RustFS 镜像。冻结前分支推进须重建验证；分支不可读、构建失败或来源对不上则记录 blocked_reason，不自动换分支或官方 latest。
- 先用 `infra/rustfs/resolve_backend.py source` 同步专用 checkout，再构建；image 步骤重核远端分支、源码与 OCI revision，保存 `backend-version.json`。
- 回执记录 repository、branch、revision_policy=branch-latest、source_commit、image_reference、image_digest、built_from_commit、resolved_at、brewfs_client_sha、resolved_by。`org.opencontainers.image.revision` 必须等于 source_commit；仅固定 digest 不足以证明来源。
- 配置包含 `REPLACE_WITH` 或 `REGISTRY/` 占位时直接拒绝部署。记录实际镜像、hint 状态、对象大小、inline/external、持久化 B/q、重挂读回；开关开启、hint 接受、布局变化分别验证。
- 历史提交号解释历史版本，不回填新 SHA。诊断候选与性能构建明确区分。

# 代码与文档

`bench/` 是调度与核验，`bench/runtime/` 是节点执行，`tools/` 是测量工具，`infra/` 是部署。[脚本入口](experiments/unified-suite/pilot-aliyun/scripts/README.zh.md)列当前使用路径。

写清场景、配置、输入输出、指标和命令；共用要求只写一次。旧报价和历史过程留在证据页。修改后运行 [仓库检查与测试](tests/README.md)，推送前核对远端新提交，不能覆盖其他会话的工作。
