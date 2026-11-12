# C1–C4：子项、格式和开关

[design.json](design.json) 是参数源；本表用来选路线、核对构建和判断能否开跑。它不改变实验数量或调度状态。

当前名称针对 BrewFS `codex/packed-prefetch` 候选分支：普通卷 `flat`、共享工作区 `workspace`、只读底座 `Packed snapshot`。Cargo feature 为 `workspace`，生产器为 `packed_snapshot_fixture`；内部模块同样使用 `workspace::packed`。旧格式名与旧持久化键不提供兼容，实验必须重新生成数据。上游 `main` 的旧版本回执仅说明历史结果，不证明这次改名后的二进制通过验证。Entity 和 Native 工作区不进入本轮 C3/C4。

已实现的整包缓存接口为 `--packed-cache on|off`（默认 `on`）和 `--packed-cache-bytes BYTES`（默认 256 MiB）；环境变量为 `BREWFS_PACKED_CACHE`、`BREWFS_PACKED_CACHE_BYTES`。此开关只控制整包异步本地缓存，不能替代下面计划中的 Pack/Overlay 消融。两臂使用同一 release 二进制、同一输入及相同缓存预算。正式结果以独立的缓存对照回执为准。

2026-11-13 已复核固定源码 `7093f141` 的[整包缓存对照](../pilot-aliyun/evidence/preparation/packed-cache-7093-20261112/README.zh.md)：128 和 10,000 文件均完成同二进制、同显式预算的正反顺序验证；10,000 文件整轮耗时降低 30.81%，首次 readdir 增加 26.02%。用户明确按完整 E2E 改善验收并接受这一取舍，`strict_metadata_no_regression=false` 事实保留。Control 8 MiB / Output 128 MiB 是本轮显式配置，默认预算未测；独立复现的冷 HEAD 阻塞已缓存对象读取列为后续性能项。本记录不改变 `design-only`、`execution_enabled=false` 或 C3/C4 suite 执行资格。

| 子项 | BrewFS / 对照 | 要启用的机制与资格 |
| --- | --- | --- |
| C1 五种 I/O | flat / JuiceFS | Overlay、Pack 关；动态块按主配置。逐次 fsync、排空、冷重挂 CRC。 |
| C1 动态块 | 同一 flat 二进制 | auto 对 off，另测固定 hint；拆 q 时 B 必须相同。按落盘 B/q、对象大小和内联状态确认。 |
| C2 完整回放（单任务、同质、混合、错峰） | flat、workspace + Packed snapshot、JuiceFS | 完整方案 Overlay/Pack/动态块开；基础版只开动态块。严格回放、最终树与冷重挂；多客户端另验 barrier 和槽位。 |
| C2 Overlay 开销 | 同一 BrewFS 工作区引擎 | 只切 Overlay，Pack、动态块、输入和缓存相同；确认末遍仍经过 BrewFS 且 lower 后端读增量为零。[C2 候选的两文件只读路径已接入并通过](../pilot-aliyun/evidence/preparation/rbm-c2-read-controls-20261112/README.zh.md)；可写路径和更大输入另验。 |
| C3 共享小文件与首次修改 | workspace + Packed snapshot / JuiceFS | 完整方案三项开；先验完整导入、首次私有写、父数据不变及冷重挂。Pack 消融只切 Pack，配对 payload 与对象布局；诊断候选的 8 文件对照已通过，正式性能资格另验。 |
| C4 原生派生 | workspace + Packed snapshot 原生 fork / JuiceFS 原生 clone | 同一非空父数据；首次子读、覆盖后父不变、排空与冷重挂。[rbm 候选复验](../pilot-aliyun/evidence/preparation/rbm-c4-published-20261112/README.zh.md)已通过子读、覆盖后父不变和冷重挂；需传构建回执并先发布 Packed 快照。不派发完整复制；大规模及性能资格另验。 |
| C4 干净迁移 | 同一 BrewFS 工作区 / JuiceFS | stay 与 move 使用同一输入；源停写、排空、释放后目标重开同一工作区。核文件树、后续文件读写及旧写者拒绝；不再执行 Agent 或验证对话恢复。最小跨机检查范围见 [五项检查](../pilot-aliyun/FIVE-CHECKS.zh.md)。 |

Cargo 构建与运行开关分开记录：冻结一次同一 BrewFS release（保留默认 features，加 `workspace` 、`observability` 和 `rustfs-ec-block-size-hint`），所有配对臂复用该产物。 `--overlay`、`--packed-metadata`、`--dynamic-block` 是计划中的运行接口，不能用按臂重编代替。动态块关闭时所有上传者 hint=off、RustFS 四项同时关闭；见[配置](CONFIGURATION.zh.md)。新一轮记录上游提交、实际 feature 列表、二进制 SHA 和镜像 ID；rbm 冒烟产物不作为正式冻结产物。

## 用 flag 选择模式

```bash
python3 -m bench.experiment_modes --experiment C1-layout --mode dynamic-off
python3 -m bench.experiment_modes --experiment C2-runtime --mode overlay-off
python3 -m bench.experiment_modes --experiment C4-handoff --mode brewfs-full --placement move
```

`--experiment` 选子项，`--mode` 选对照或消融，迁移另用 `--placement stay|move`。这里的模式是文件系统配置，不涉及模型调用。选择器输出预期格式、机制、hint 和尚未验收的条件；不会启动文件系统或派发任务。
实际最小运行用 python3 -m bench.experiment_smoke，参数及范围见[rbm 最小验证](../pilot-aliyun/RBM-MINIMAL.zh.md)。底层已经支持的 hint 参数是 `--s3-rustfs-ec-block-size-hint`；C2 只读 Overlay 路径已有候选入口；Pack 和全关已有诊断候选的最小检查，见 [五项结果](../pilot-aliyun/evidence/preparation/rbm-five-checks-20261112/README.zh.md)；可写 Overlay 开关和正式性能资格仍待完成。

统一构建：

```bash
cargo build --release --locked --features workspace,observability,rustfs-ec-block-size-hint \
  --bin brewfs --bin packed_snapshot_fixture
```

保留 Cargo 默认 features。这里打开的是实验需要的三项能力；不额外开启实验未使用的网关替代路径、profiling 或其他挂载格式。rbm 已用现有二进制复验 128 文件的挂载、读写和冷客户端重挂，见 [回执](../pilot-aliyun/evidence/preparation/rbm-experiment-modes-20261112/lifecycle.json)。这份回执不证明统一构建或尚未接通的消融可用。

统一 feature 新产物及真实 flag 路由的[rbm 最小验证结果](../pilot-aliyun/evidence/preparation/rbm-unified-modes-20261112/README.zh.md)已归档；正式实验仍按各子项资格准入。
