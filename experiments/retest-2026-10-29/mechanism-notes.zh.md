# RustFS 与客户端机制备查

这里只保存源码与已验证机制；执行范围以 [唯一主方案](../../paper-design/FINAL-PLAN.zh.md) 为准。资格通过不等于性能收益。

## Dynamic block 必须实际生效

完整启用组的服务端四项必须为 true：`RUSTFS_EC_BLOCK_SIZE_HINT_ENABLE`、`RUSTFS_EC_BLOCK_SIZE_HINT_FLEET_CONFIRMED`、`RUSTFS_EC_READ_QUANTUM_ENABLE`、`RUSTFS_EC_READ_QUANTUM_FLEET_CONFIRMED`。另有四项全关的负对照，以及前两项 true、后两项 false 的 `block-only` 组；后者用于固定 B 比较小读帧的作用。BrewFS 构建含 `rustfs-ec-block-size-hint`，请求使用 `--s3-rustfs-ec-block-size-hint`。直接 S3 探针仅证明后端，不能代替 BrewFS 上传链路。

支持 B 为 64 KiB、256 KiB、1 MiB、4 MiB。此提交的 **512 KiB 不支持**，必须记录 ignored，不能画成已启用的一组。客户端 4 MiB block 和后端 EC block B 分开记录。shard 大小按实际 data_shards 计算；EC 2+2 的 1 MiB / 2 =512 KiB。

2026-10-29 用户确认暂不新增 4 KiB EC block 或强制 4 KiB read-quantum 策略。现有 read-quantum 候选包括 4 KiB，但 64 KiB block / EC 2+2 自动选 16 KiB。4 KiB 随机读取请求仍是负载参数，不代表 4 KiB 磁盘布局已启用。

对新对象保存 PUT/HEAD 的 `x-rustfs-layout-hint-status`、`x-rustfs-layout-hint-reason`、`x-rustfs-effective-ec-block-size`；读取实际磁盘元数据的 erasure block/data/parity 与内部 read-quantum marker；服务端重启后读回并比对完整内容与跨边界 range。布局未变或 marker 缺失只能记 no-op/未验证。目录模拟的 EC 槽位不代表独立物理故障域。

### RustFS 小对象内联必须作为分层变量

固定版本的 `storageclass::should_inline` 按编码后单 shard 大小判定，默认对象预算按 data shards 缩放；EC 2+2 未显式覆盖时每 shard 128 KiB，开启 bucket versioning 后门槛再除 8。PUT 路径还要求对象长度不超过当前 EC block。因此普通、未压缩/加密单 PUT 的预期边界为：非版本桶 `min(B, 256 KiB)`，版本桶 `min(B, 32 KiB)`；实际边界由磁盘证据验收，不能套用于 multipart 或转换流。

满足条件时，编码后的 shard 数据进入该对象的 `xl.meta` inline 区，不另建 `part.1`。这里是 RustFS 对象元数据，与 BrewFS inode/目录元数据、Pack 自身 `inline-data` 开关分开记录。该版本的小读帧选择明确要求 `!is_inline_buffer`：内联对象没有 read-quantum marker 不代表四开关失效。

边界探针包含 1/4/32/64/128/256 KiB、关键边界+1 B、1 MiB；无 hint、64 KiB、256 KiB；versioning 开/关；disabled / block-only / enabled 三组。每项保存四盘 `xl.meta` CRC、inline marker/数据长度、独立 part 文件数/长度、重启前后完整及 range 内容。D 的性能结果按实际 inline/external 分类，尤其关注 64–256 KiB 对象从默认内联变为 B=64 KiB 下独立分片的路径变化。

最新矩阵见 [layout-matrix.json](layout-matrix.json)：保留 64 KiB / 256 KiB / 1 MiB / 4 MiB 的 EC block 候选，重设对象大小与请求口径。小对象增加 128 KiB 及 +1 B，因为该提交的 **完整 GET 直接内存路径默认门槛为 128 KiB**，不同于 256 KiB 内联门槛，且 Range GET 不走这个直接内存判定。实际命中还需满足其他资格条件，不能仅从大小推定。

主性能组固定 8 MiB+123 B，32 MiB+123 B 是后置扩展；均为单 PUT 对象，所有候选必须经落盘检查证实为 external。需要归因时才追加固定 B 的 `block-only` 与完整启用组，隔离小读帧影响；在 `block-only` 内比较 B 时，默认 shard frame 也随 B 改变，不能称为纯 EC block 单因素。完整顺序 GET 与 4 KiB 随机 Range 分报，冷读和同对象收敛预热分报。应用层另查文件→4 MiB 客户端 block→实际 S3 对象映射，不能用用户文件大小代替后端对象大小。

客户端链路还存在两个控制点。此版本即使 codec=none，每个持久化 block 仍有 **4 B 格式头**，例如 4 KiB 文件上传成 4,100 B 对象；对象内联阈值 T 对应单 block 文件边界 T−4/T−3。开启任意 EC hint 又会让 `backend-default` 读取策略偏好整对象获取，不能把这个策略变化算成 RustFS 小读帧收益。D 的应用层因果组统一显式 `read_fetch_policy: sparse` 并观察实际 HTTP Range；产品默认策略另报。`auto` hint 对不超过 1 MiB 的 wire object 不发送 hint，超过时请求 1 MiB，所以不能仅凭 `auto` 声称 64/256 KiB 动态布局已启用。

`aliyun_suite.py` 接受 spec 顶层 `read_fetch_policy: sparse` 并传入 BrewFS `--read-fetch-policy sparse`，在每次 mount 回执中记录请求参数。该传参已做离线回归；实际 HTTP Range 与后端读取量仍是正式 D 应用组的验收条件，配置值不代替生效观测。

空间方面，已测 4 KiB S3 对象在默认 B=1 MiB、B=64 KiB、B=256 KiB 下均内联，每个 EC shard 的数据加校验均为 2,080 B，并未按整个 B 填满。小文件尾部空间浪费必须通过实际分片长度和文件系统分配量测量，不能由名义 EC block 直接计算。Pack 的主要假设仍是减少海量对象、元数据和请求开销。

P1 两臂共享同一组 packed payload，因此同时控制 RustFS inline 行为。P2 需要保存用户文件→BrewFS slice/block→S3 对象→RustFS inline/external 的实际映射或可审计统计；用户文件小不等于上传对象小。新百万 TinyStories 语料是独立记录，不是复制放大；原始文件约 805 MiB，但 Pack 聚合后不能假定还走 RustFS 小对象内联。

最新已通过 **198 个实际对象边界用例**（三组各 66 个）、18 个基础布局用例，以及真实 BrewFS FUSE 上传产生的 4 个数据对象。客户端证据包含持久化 hint、实际 B/read-quantum、完整读回、后端重启与重新挂载后的内容一致性。证据及 SHA 清单见 [layout-v2-qualification/summary.json](evidence/layout-v2-qualification/summary.json)；早期 108 项证据仍独立保存在 [inline-qualification/summary.json](evidence/inline-qualification/summary.json)。例如非版本桶的 65,537 B 对象在默认 B=1 MiB 下内联，B=64 KiB 下转为独立分片并使用 16 KiB 读帧。这证明路径转换和实际启用，不证明任何吞吐收益；尚不能据此归因历史 BrewFS/JuiceFS 差异。此资格验证使用同一文件系统的四个目录，不代表四个物理故障域。

### 块大小阶梯的决定

保留当前实现支持的 **64 KiB / 256 KiB / 1 MiB / 4 MiB** 四档，暂不添加 4 KiB EC block。重设的是对象分档与选档依据：首先看实际对象是否内联，再看读取是全量还是局部。小对象保留默认布局作为基线，不预设缩小 B 有空间收益；内联边界附近先测转为外部分片的代价。已确定 external 的对象再比较小 B 的局部读与大 B 的顺序读权衡。应用层按实际 S3 wire object 大小选择，不能直接套用户文件大小；新的自动选档阈值须等配对性能数据后确定。EC 2+2 的 B=1 MiB 组保留为小读帧特性的 no-op 对照。

源码依据固定在同一提交：[inline 预算](https://github.com/Ivanbeethoven/rustfs/blob/a87e8f94a50b9fb6e3da74fed45fe37d4cb969ed/crates/ecstore/src/config/storageclass.rs#L243)、[PUT 内联条件与读帧选择](https://github.com/Ivanbeethoven/rustfs/blob/a87e8f94a50b9fb6e3da74fed45fe37d4cb969ed/crates/ecstore/src/set_disk/ops/object.rs#L4399)。
