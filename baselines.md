# 竞争方案与共同完成规则

核验日期：2026-10-23。本文确定实验方案，不表示任何基线已经部署或跑通。B0 为 BrewFS；可控集群的主对照为 B1、B2、B3，真实云迁移的必测对照为 B4。按用户最新要求，先准备阿里云上的 B1/B2/B3/B4，BrewFS 接口暂缓；正式主对照仍保留 B0。托管 Redis 记 B2-M、NAS 记补充 B5，详见 execution-plan.zh.md。实际二进制版本、源码提交、镜像摘要、挂载参数与资源限制必须写入运行清单，不能把本文引用的在线文档版本当作已部署版本。

| 方案 | 实际组成 | 要回答的比较问题 | 原生可用能力 |
|---|---|---|---|
| B0 | BrewFS + 当前 AX 集成 + 元数据服务 + RustFS | 面向 Agent 的状态组织能否改善整个任务批次 | 共享基线、私有修改、Packed；发布和跨节点恢复分别验收 |
| B1 | 本地 XFS reflink 工作区 + restic + RustFS 上的同租户共享备份仓库 | 本地执行很快，再增量保存到云上，是否已经足够 | 本地 COW、跨快照/工作区内容去重、已有目标文件复用 |
| B2 | JuiceFS Community Edition + Redis + 与 B0 相同的 RustFS 集群 | 成熟对象存储文件系统加原生 clone 能否达到同样效果 | 元数据 clone、对象块共享、随机写新增块、跨节点挂载 |
| B3 | CephFS + RADOS + 多个活跃 MDS + subvolume snapshot/clone | 成熟分布式文件系统在多节点并发和恢复时表现如何 | 分布式元数据、共享只读快照、可写克隆、远端持久化和客户端驱逐 |
| B4 | 阿里云 ESSD PL1 数据盘 + XFS；每个 Agent 独立卷 | 已在云上保存的磁盘直接换挂载，是否已经能快速恢复 Agent | 原卷卸载、分离、挂到同可用区另一实例；不复制整盘数据 |

完整复制只保留为成本解释，不作为主要竞争方案。B1/B2/B3 使用各自原生共享或增量能力，B4 使用原卷换挂载。所有能力都需在固定部署版本上验收。

## 1. 所有方案执行同一个任务合同

- **同一租户内的合作 Agent。**允许同租户复用内容和备份仓库；每个 Agent 有独立可写工作区。实验检验内容/目录修改互不影响，不把它扩展成恶意租户之间的安全隔离结论。restic 密钥由受信任的协调器持有，不发给各 Agent。
- **输入内容相同。**同一任务的代码版本、资料摘要、依赖与权限一致。只读资料可以直接挂载给多个消费者，输出另写私有目录。不得强迫 B2/B3 为每个只读消费者复制整份资料；B1 可在每个节点只保留一份资料，由本节点消费者共享。
- **写后结果相同。**保存代码、私有文件、AX 会话、已完成工具清单和预算用量。所有方案运行同一个文件内容/目录检查与任务判题器。
- **持续写与保存点分开。**各系统可以按自己的实现缓存和后台保存，但统一在规定工具边界发出“保存完成”确认。后台流量和 CPU 不能从任务成本中扣除。
- **固定故障范围。**首轮恢复实验只杀 Agent 工作节点，远端存储服务保持工作；已确认状态必须能在另一工作节点恢复。存储节点、Redis 或 MDS 故障另列条件，不能从工作节点恢复成功推断后端也有同样保证。

共同的保存流程为：停止新工具操作 → 等待现有工具结束 → 保存会话与工具清单 → 完成该方案的远端保存 → 生成含文件摘要、会话摘要、状态标识的完成记录。只有整个流程成功才确认 checkpoint；部分上传、部分目录和仅保存在工作节点本地磁盘的状态均不确认。B4 的 XFS 在 ESSD 远端卷上，不能误归为 B1 尚未备份的本地磁盘。完成记录的协调器、持久化位置和写入策略对所有方案相同。第一次资格检查从另一节点完整读回并校验；正式计时只做预先规定的必要校验，不能在某一方案上额外做全量扫描。

| 方案 | 可以发出共同确认的条件 | 另一节点恢复 |
|---|---|---|
| B1 | 工作区与会话停止变化；restic backup 返回 0，记录确切 snapshot ID；完成记录保存成功 | 按确切 snapshot ID 恢复，校验内容与会话后恢复工具；不能用 latest 选取并发备份 |
| B2 | 不启用客户端 writeback；相关文件同步成功、元数据更新完成；完成记录保存成功；要求历史回滚时还需冻结 checkpoint clone 完成 | 挂载同一远端卷；计划迁移可直接重新挂载原工作区，历史回滚从冻结 checkpoint 建立新的可写工作区 |
| B3 | 相关文件 fsync 成功，完成记录保存成功；要求历史回滚时还需停止修改后创建 snapshot 成功 | 计划迁移可重新挂载原 subvolume；历史回滚从该 snapshot clone，等待 complete 后使用 |
| B4 | 工作区、AX 会话及工具清单已写入同一 ESSD 卷并同步成功；完成记录保存成功；源停止修改 | 正常卸载、分离原卷，挂到同可用区目标实例；检查状态后继续；计划迁移不要求先创建 ESSD snapshot |
| B0 | 当前实现规定的远端数据和元数据确认均成功；版本与会话完成记录可恢复 | 依实现恢复指定版本；若需另建私有修改分支，成本也计入 |

### 保存与打开要分开测

现有 `harness/baseline_cli.py` 仅用于本地操作检查：B1 保存为 restic 快照，打开时恢复到新目录；B2 保存时遍历文件树同步并创建 clone，打开时再创建 clone。它没有实现 B1 暖目标复用，也没有实现 B2 当前工作区直接重挂。不能把这两条额外复制或克隆的路径当作正式迁移结果。

| 测什么 | 怎么做 | 记录什么、怎么判断 |
|---|---|---|
| 保存当前状态 | 工具停稳后，把文件、会话和进度保存到远端；不额外要求历史版本 | 远端完成时间、状态标识和共同完成记录；缺任一项不算保存完成 |
| 保存历史版本 | 保留一个后续修改不能改变、能按确切标识恢复的版本 | 快照或 clone 完成、版本标识；再修改工作区，旧版本内容必须不变 |
| 重新打开当前状态 | 目标直接使用已确认的当前状态；B2/B3 重挂原目录，B4 换挂原盘 | 实际目录/子卷/盘标识、撤销旧访问与目标首个正确结果；不能悄悄改走 clone 或完整恢复 |
| 恢复历史版本 | 指定版本，建立独立可写工作区；允许该方案正常需要的复制或 clone | 确切版本、恢复成本、内容与会话检查；修改恢复结果不能改变保留版本 |
| 利用目标已有文件恢复 | B1 允许先由已有基线建立 reflink 目录，再由 restic 复用匹配文件并删除版本中已不存在的文件 | 原有文件摘要、实际下载/扫描/删除量；与冷目标分组，不能把目录存在当作复用已生效 |

各条路径的准备状态见 [操作准备清单](baseline-operation-contracts.json)，当前均未在实际部署上验收。现有保存适配器返回的只是存储步骤完成，仍需协调器完成记录；现有打开适配器不代表已完成 Agent 会话恢复。B1 跨节点通常走恢复路径；B4 历史快照另测，不强加给正常换挂。

计划迁移中允许 B2/B3 直接重挂原远端工作区，B4 直接换挂原卷，不强迫它们重新复制、克隆或创建历史快照。故障接管中若必须回到上一个已确认版本，各系统都使用该版本恢复；若允许继续故障前已落盘但尚未确认的新状态，则必须作为另一种完成规则，不能混入同一比较。

## 2. B1：本地 XFS reflink + restic

选择它是因为它代表强的“本地运行、云上保存”方案。每个节点只准备一次不可变输入，再用 `cp --reflink=always` 建立独立可写目录。GNU 官方说明该选项在 COW 不可用时会报错，因此不会悄悄退化为普通复制。XFS 的 reflink 支持及实际块共享需在部署时检查；源、目标放在同一合格 XFS 文件系统内。目录遍历、inode 创建和首次基线下载照常计时。[GNU cp 官方文档](https://www.gnu.org/software/coreutils/manual/html_node/cp-invocation.html)

拟用命令形状如下；占位符必须由实验配置替换，本文件不执行命令：

```bash
cp -a --reflink=always /srv/ax-base/<base-id> /srv/ax-runs/<run-id>
restic -r s3:https://<rustfs-endpoint>/<bucket>/<tenant-repository> init
restic -r <repository> backup --json --host <stable-workspace-id> --tag <checkpoint-id> /workspace /agent-state
restic -r <repository> restore <snapshot-id> --target /srv/ax-restore/<run-id>
```

`init` 只在建立仓库时运行一次。同租户所有工作区使用一个仓库，先把共同基线备份一次；不为每个 Agent 新建空仓库。为同一工作区保持稳定备份路径和逻辑 host 标识，使后续备份能找到正确父快照。restic 既可跳过未变化文件，也能对变化内容去重；上传统计取实际新增字节，不取扫描过的逻辑文件大小。[restic 备份与变化检测](https://restic.readthedocs.io/en/stable/040_backup.html)、[官方跨主机仓库说明](https://github.com/restic/restic/blob/master/doc/faq.rst)

restic 支持指定 S3 兼容服务地址，故 B1/B2/B0 可共用 RustFS 服务但使用隔离前缀。备份协议先保存数据和索引，最后保存快照；只有退出码 0 才满足本实验确认规则，退出码 3 产生的不完整快照不得确认。[S3 仓库配置](https://restic.readthedocs.io/en/stable/030_preparing_a_new_repo.html#s3-compatible-storage)、[仓库写入顺序](https://restic.readthedocs.io/en/stable/100_references.html#read-and-write-ordering)

目标冷、暖两种恢复均保留。暖目标可以先由已有基线建立 reflink 目录，再让 restic 核对并只恢复不匹配内容；不能强迫每次从空目录下载整份数据。恢复时还必须删除 checkpoint 中已不存在的文件；正式驱动程序使用官方 `--delete` 所要求的受限 include/exclude，并先 dry-run 核对专用目标目录。恢复完成后才把目录交给 Agent；中途失败不得暴露半恢复状态。[restic 目标复用和删除规则](https://restic.readthedocs.io/en/stable/050_restore.html#restoring-in-place)

适用：批次运行、独立修改、保存/回滚、共享资料分发与跨节点恢复。**B1 未进行远端备份时的本地工具耗时只作热运行参考**；需要保存/迁移的端到端比较必须包含备份、恢复与验证。若 B1 在目标已有基线而 B0/B2 目标全冷，该结果必须分栏，不作为同条件胜负。

## 3. B2：JuiceFS CE clone + Redis + 同 RustFS

官方社区版提供 `juicefs clone`，命令在 v1.1 加入。clone 复制元数据而共享底层对象块；后续修改写新块，未改内容继续共享。这不是完整复制基线。克隆会增加元数据占用；物理空间从对象存储和 Redis 计量，不能直接把各工作区 `du` 相加。[社区版 clone 文档](https://juicefs.com/docs/community/guide/clone/)、[社区版命令参考](https://juicefs.com/docs/community/command_reference/)

```bash
juicefs format --storage s3 --bucket https://<rustfs-endpoint>/<jfs-bucket> redis://<redis-host>/<db> <volume-name>
juicefs mount redis://<redis-host>/<db> /mnt/jfs
juicefs clone -p /mnt/jfs/bases/<base-id> /mnt/jfs/workspaces/<workspace-id>
juicefs clone -p /mnt/jfs/workspaces/<workspace-id> /mnt/jfs/checkpoints/<checkpoint-id>
juicefs mount --read-only --subdir /published/<version-id> redis://<redis-host>/<db> /mnt/input
```

`format` 仅用于专用新卷，凭据通过运行环境注入。正式参数以已固定版本的命令帮助为准。工作区和 checkpoint 的源在 clone 期间保持静止；官方不保证变化中的目录 clone 原子一致。同一源同时只能一个 clone 成功，驱动程序应排队，或使用预先验证的分层 clone 调度，并把创建、排队、重试和额外元数据全部计入。不能发出大量必失败的同源 clone，再把失败率当作正常任务能力。[clone 一致性约束](https://juicefs.com/docs/community/guide/clone/#consistency)

不开启 `--writeback`：官方默认路径会先把数据上传到对象存储，再提交文件元数据；`fsync`/关闭触发的数据提交也要检查返回结果。发布资料后冻结其目录，并以只读挂载供所有消费者直接访问。[缓存、提交与一致性](https://juicefs.com/docs/community/guide/cache/)

Redis 的持久化、复制和故障转移配置必须与 B0 的同类元数据服务一并公开。若包含 Redis 主机崩溃且要求已确认数据不丢失，不能用未说明的 `appendfsync everysec` 冒充零丢失：官方明确其存在约一秒的数据丢失窗口。可用 `appendfsync always` 对齐单机落盘确认；这仍不自动保证任意异步副本故障转移零丢失，需另验该故障范围。[Redis 官方持久化说明](https://redis.io/docs/latest/operate/oss_and_stack/management/persistence/)

适用：全部主实验。迁移时复用远端数据和既有工作区，目标仅承担正常挂载与读取；不会要求它重新上传整份基线。B2 与 B0 使用相同 RustFS 硬件、网络、对象持久化策略及总缓存预算，但这是整套系统比较，不能把差值单独归因于 Packed。

## 4. B3：CephFS snapshot/clone + RADOS + 多 MDS

它代表可扩展的共享文件系统。CephFS 支持多个活跃 MDS，适合多个客户端在不同目录并发访问。计划固定 2 个活跃 MDS 和至少 1 个备用 MDS 为初始配置；如果 pilot 显示元数据饱和，可在结果采集前冻结 4 个活跃 MDS 的正式配置，但必须给其他系统相同总服务端资源预算。实际 `ceph status` 必须证实活跃 rank 数，不能只修改 `max_mds`。[多 MDS 官方文档](https://docs.ceph.com/en/tentacle/cephfs/multimds/)

```bash
ceph fs set <fs-name> max_mds 2
ceph fs subvolume create <fs-name> <base-subvolume>
ceph fs subvolume snapshot create <fs-name> <base-subvolume> <snapshot-name>
ceph fs subvolume snapshot clone <fs-name> <base-subvolume> <snapshot-name> <workspace-subvolume>
ceph fs clone status <fs-name> <workspace-subvolume>
ceph fs subvolume getpath <fs-name> <workspace-subvolume>
ceph fs subvolume snapshot getpath <fs-name> <base-subvolume> <snapshot-name>
```

需要特别区分：snapshot 提供不可变视图；官方 Tentacle 文档中的 subvolume clone 则是异步批量复制，**不是元数据级零拷贝克隆**。必须等 `clone status` 为 `complete`，再获取路径并启动 Agent。默认并发 clone 数为 4；正式配置需公开 `max_concurrent_clones` 与 `snapshot_clone_no_wait`，允许正常排队，不能把服务端拒绝排队的默认行为伪装为无法扩展。[snapshot 语义](https://docs.ceph.com/en/tentacle/cephfs/snapshots/)、[subvolume clone 与状态](https://docs.ceph.com/en/tentacle/cephfs/fs-volumes/#cloning-snapshots)

只读消费者直接使用同一 snapshot 路径；只有需要独立修改整个目录的工作区才 clone。对需要恢复当前远端状态的迁移，直接重挂原 subvolume；需要历史版本时才付 snapshot clone 成本。文件保存用成功的 fsync 作为数据落盘依据，不把成功 fclose 单独当作保证。[CephFS 同步说明](https://docs.ceph.com/en/tentacle/cephfs/full/)

故障接管不能假设共享文件系统自带“每个 Agent 只能一个写者”的应用规则。所有方案使用同一协调器限制新任务启动；另外记录各自真正拒绝旧写者的手段。CephFS 有原生客户端 eviction 和 OSD blocklist，可阻止旧客户端继续访问；应允许基线使用，所需检测和驱逐时间计入恢复。未刷新的旧客户端缓冲内容不属于已确认状态。[CephFS 客户端驱逐](https://docs.ceph.com/en/tentacle/cephfs/eviction/)

适用：正式多节点批次、共享资料、独立修改、保存/回滚和恢复。B3 使用自己的 RADOS 数据路径，**不是与 B0 共用 S3 的单因素对照**。单列服务器数量、CPU/内存、原始磁盘、网络、数据/元数据冗余和可容忍的故障范围；主比较对齐物理资源预算及预先约定的故障容忍，报告因此得到的有效容量。不能只给 B3 一个 MDS 或更弱的磁盘配置，再把差异全部归因于设计。

## 5. B4：阿里云 ESSD PL1 + XFS 原盘换挂载

主线为同一阿里云可用区内两台 ECS，一个 Agent 一块独立按量数据盘；不使用根盘。源保存文件与会话、同步并正常卸载后，分离原盘、挂到目标，核对真实设备和内容后继续。**不添加整盘复制，也不强制每次先建快照。**实际命令与事件记录入口为 [阿里云交接脚本](scripts/alicloud_disk_handoff.py)，未执行时仅产生步骤计划。[阿里云挂盘说明](https://help.aliyun.com/en/ecs/user-guide/attach-a-data-disk)

| 布局 | 移动一个 Agent 的操作 | 必须计入 |
|---|---|---|
| 每 Agent 独立盘 | 暂停该 Agent、保存、正常卸载、原盘换挂、验证、首个正确工具结果 | 同步、API 等待/限流、设备出现、挂载、会话恢复；盘数、容量、性能级别、实例挂盘配额与费用 |
| 每节点共享盘 | 其他 Agent 继续时，采用经过验证的单目录状态转移 | 目录不能独立 detach；若移整盘，全部邻居中断均计入；reflink 不改变移盘粒度 |

状态核验必须同时检查盘 ID、目标实例 ID、附件状态、盘所有权标签和实际设备，单看 In_use 不够；迁移恢复不会格式化已有盘。正常组不强制分离，不静默回退到快照/复制。失败保留现场和阶段记录；强制交接仅进入另设故障实验，并检验文件系统和已确认状态。

普通 ESSD 使用同 AZ。**Regional ESSD 是已存在的跨 AZ 路线**，单列产品、支持地域、目标、配置、费用与恢复保证；不能用普通盘限制宣称所有云盘都缺迁移灵活性。[Regional ESSD](https://www.alibabacloud.com/help/en/ecs/user-guide/regional-essd-disks) 普通 XFS 不支持由多挂载开关带来的多机同时读写；需要集群文件系统或经过验证的单写者协议。[多挂载边界](https://help.aliyun.com/en/ecs/user-guide/attach-a-data-disk)

主比较在同一阿里云环境完成。登记地域/AZ、ECS 型号、镜像 ID、XFS、盘容量/已用量/性能级别、实测 IOPS/吞吐和挂盘上限。PL1 容量与性能有关，容量变化不能自动解释为单纯容量影响。云服务后台资源不可见，结果作为实际部署和费用比较，不冒充同后端机制消融。记录实际账单而非预填价格。

计时终点是目标产出首个正确工具结果。总暂停包括所有被选中 Agent 在统一暂停后等待迁移槽位的时间。邻居吞吐、延迟、停顿和正确性单独报告。与不迁移的配对组比较总任务额外耗时；如果独立盘在同 AZ 更快，保留该结果并收窄主张。

## 6. 这种对照能否用于顶会论文

可以作为实际部署基线；这是实验设计判断，不是录用保证。它直接检验最简单的替代解释：既然云盘本身已经远端保存，为什么不能直接换挂载？若省略它，仅与复制目录比较，会遗漏这条强路径。论文应把结论落实到同等任务与完成语义下的工作区粒度、目标选择、并发迁移、邻居影响和总成本，不能仅凭云API等待较长宣布存储机制有创新。

相关的顶会证据是 FAST 2026 的 ThinkAhead：它研究云块盘从镜像创建后的数据加载，使用生产轨迹、集群实验和机制对照评价实际存储路径。它研究的是镜像加载，不是原盘detach/attach；这里只借鉴将部署结果与机制解释分开的实验方式，不能引用为“已有论文证明换挂载差”。[论文与公开材料](https://www.usenix.org/conference/fast26/presentation/chen)

多挂载问题按上节阿里云产品和文件系统语义验证，不将多挂载开关等同于目录级独立迁移。

## 7. 开跑前固定、正式结果必须保留

1. 版本：Linux 内核、XFS 工具、GNU coreutils、restic、JuiceFS CE、Redis、Ceph 客户端/服务端、BrewFS、AX、RustFS、阿里云 CLI 的确切版本或提交；本文全部仍待运行登记。
2. 语义：只读共享目录、每工作区私有目录、checkpoint 内容、远端确认条件、恢复后允许的状态、旧写者处理和故障范围。
3. 预算：每工作节点资源、存储服务总资源、缓存总量、网络条件；首次输入导入、clone、保留版本及回收成本。
4. 资格：同样的文件/目录 oracle、会话恢复、实际块共享/去重、跨节点读回、clone 完成与半恢复不可见。某项不支持就明确限制该项，不把另一种语义替代后混画。
5. 结果：统一的任务成功率、批次完成时间、保存与恢复时间、实际网络/物理存储和失败原因；本地热运行、远端完成、冷目标、暖目标分开；可控集群与真实云换挂载分开。

这些基线不需要与 B0 采用相同内部结构，但应获得各自成熟、可实现且符合共同合同的能力。先用 B1/B2 校准无模型文件操作和计量，再接通多节点与 AX，以相同任务与完成规则加入 B0/B3；B4 的真实云换挂载是迁移结论的必测项。不能在看到正式结果后替换输入、故障范围或主要指标。
