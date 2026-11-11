# C2/C4：原生 workspace 和 Packed 的阻塞

核对的是本批 BrewFS `ca07159f3da4b670e4aeb878faef928e69bc1634`，现有二进制 SHA256 为 `814499855a949cad7a8752c04f272fa1b02524b36b7ea77b0e4b3bcc6031c5c0`。读了冻结源码，并查看该二进制的 `workspace --help`；未连云端或启动实验。

| 路线 | 当前阻塞 |
|---|---|
| C2 `native-workspace` | 需把 `pytest-full-02` 的原始非空树发布成可派生的 `workspace-native-v2` 版本，然后从它建私有工作区跑完整 trace。当前没有可用的端到端原生发布入口。 |
| C4 `native-workspace` fork | 同一个发布缺口；这里要用独立的 `fork-source`（100,000 × 4 KiB），不能用 C3 数据或空版本。已有[空版本父子隔离实测](evidence/preparation/c4-native-empty-fork/README.zh.md)，不能当作非空派生。 |
| C4 Packed fork | 可复用 C3 的 Packed 发布方式，须单独发布同一份 C4 `fork-source`。但固定测试要求把 lower 文件 `d000000/f00000004` 移到根目录。当前 Entity FUSE 没接 `rename`/`rename2`，默认返回 `ENOSYS`；底层已有事务。省去重命名会改变测试。 |

原生生产入口查证：

- [CLI 命令定义](https://github.com/brewfs/brewfs/blob/ca07159f3da4b670e4aeb878faef928e69bc1634/src/config.rs#L496)中，`init-volume` 初始化空 catalog，[`init-native`](https://github.com/brewfs/brewfs/blob/ca07159f3da4b670e4aeb878faef928e69bc1634/src/cli.rs#L2814)只加 native 控制头，`create/fork` 消费已存在的不可变 `BaseRevision`。`init-packed-volume` 使用 Packed lower，`--entity-native-source` 属于另一条 `workspace-entity-v1` 路线。
- [Manager](https://github.com/brewfs/brewfs/blob/ca07159f3da4b670e4aeb878faef928e69bc1634/doc/operations/manager.md#L293) 的 native P1 catalog revision 不是可写 head 的冻结版本，API 不做 checkpoint。[Operator 的 snapshot](https://github.com/brewfs/brewfs/blob/ca07159f3da4b670e4aeb878faef928e69bc1634/operator/brewfs-operator/src/workspace/admin.rs#L294)先验证已有 revision，再为它登记名字；[CSI](https://github.com/brewfs/brewfs/blob/ca07159f3da4b670e4aeb878faef928e69bc1634/operator/brewfs-csi/src/main.rs#L154)直接返回不支持 snapshot。
- 原生[目录/TAR 导入库](https://github.com/brewfs/brewfs/blob/ca07159f3da4b670e4aeb878faef928e69bc1634/src/native_base/ingest/mod.rs#L26)停在 `BUILT_UNPUBLISHED`。原生[发布事务](https://github.com/brewfs/brewfs/blob/ca07159f3da4b670e4aeb878faef928e69bc1634/src/native_base/lifecycle/publish.rs#L207)和[从 PublishedRevision 派生事务](https://github.com/brewfs/brewfs/blob/ca07159f3da4b670e4aeb878faef928e69bc1634/src/native_base/lifecycle/fork.rs#L60)存在，但该提交的调用都在测试中。它们还未连成当前挂载和 CLI 可用的发布入口。
- C4 重命名见 [`fs_step.py`](../../../bench/fs_step.py#L64)、[Entity FUSE 实现](https://github.com/brewfs/brewfs/blob/ca07159f3da4b670e4aeb878faef928e69bc1634/src/fuse/entity.rs#L468)、[FUSE 默认行为](https://github.com/brewfs/brewfs/blob/ca07159f3da4b670e4aeb878faef928e69bc1634/vendor/asyncfuse/src/raw/filesystem.rs#L180)与[原生 rename 事务](https://github.com/brewfs/brewfs/blob/ca07159f3da4b670e4aeb878faef928e69bc1634/src/workspace_overlay/entity/namespace.rs#L1458)。C2 Packed 的额外操作缺口见[完整 trace 核对](C2-ENTITY-NEXT.zh.md)。

下一步各用几个文件验证：

1. **原生 workspace：** 提供生产入口，把包含子目录和真实字节的源树发布为可挂载、可派生的原生 revision，记实际 ID、内容摘要和写者退出。用该 revision 派生两个子工作区，核首次读、单子修改、父与兄弟不变、正常退出及同一子工作区冷读回。通过后分别发布原 C2、C4 输入并资格验证。跨机恢复后继续写入还要另过[已有 EIO 反例](evidence/preparation/native-ca07159-guards/workspace-guarded-failure.json)。
2. **C4 Packed：** 将普通 FUSE `rename` 接到底层事务。用同一个小 manifest 试父与两个私有子工作区，确认修改的子工作区旧名消失、新名数据不变，父和兄弟仍有旧名。核文件/目录 fsync、两次零上传债务、原挂载退出，再从只读 Redis/S3 验实际子工作区和父树。通过后才发布 100k 并接专用 C4 计时入口；计时从创建新工作区开始，包含首次挂载，不能借用 C3 性能样本。
