# JuiceFS 写放大：受控微实验

实验日期：2026-10-25；云端微实验采样 11:48–11:54 UTC（19:48–19:54 北京时间），11:55 UTC 卸载实验挂载点。版本为 `1.4.1+2026-07-30.0b90c7d`。只运行下面的微实验，未重跑 115,718 操作主测试。

**主要机制是碎片 slice 的 compaction，混合读写会提早触发合并。不能将其描述为“每次小写先 GET 原 4 MiB block，再原地补丁 PUT”。** 小写可以直接上传 4 KiB 新 slice；随后读取碎片化文件触发合并，合并才读取旧数据并生成覆盖可见数据范围的新 slice。本次文件范围恰为 4 MiB，因此出现大量 4 MiB 上传。缩小对象 block 到 64 KiB 仍合并整个 4 MiB 范围，只是拆成更多对象。

旧 stable 快照直接记录 **990 次 compaction、输出大小计数 4,152,360,960 B = 990 × 4 MiB**，相当于其 4,213,462,213 B PUT 的 **98.55%**。受控实验进一步把“小写上传”和“读触发合并上传”分阶段复现；这是机制证据。旧窗口缺少逐对象请求审计，98.55% 是两个客户端计数器的比值，不是逐请求审计后的归因百分比。

## 环境、边界与计量

- 经 `ssh osdi-smoke` 连接，保留原 `/mnt/jfs`（端口 9567）和原进程。没有读写 `/mnt/brewfs` 中的文件；收尾仅用 `findmnt` 检查它仍挂载。
- 默认配置另挂 `/data/amp/default`（9571）；同卷 `--writeback` 另挂 `/data/amp/writeback`（9572），均使用 Redis 6380 db1。64 KiB 卷另挂 `/data/amp/small`（9573），使用预先确认空的 db2、独立 bucket `jfs-amp-64k-20261025`。格式中的 `BlockSize` 分别为 4096、64，单位 KiB，见 [finish.json](results/amp/finish.json)。
- 三个实验挂载各用独立新缓存目录，其余参数沿用原挂载：cache 8192 MiB、buffer 2048 MiB、compression none、`--no-bgjob`、metadata backup 0。额外启用 debug 日志辅助定位合并；不作延迟比较。
- 每个 case 用新目录 `/data/amp/<mount>/amp-20261025-<case>/`。单个 Python 进程串行发起系统调用，一次只运行一个 case。JuiceFS 自身后台 goroutine 仍可并发上传/合并。
- 除稀疏 case 外，先顺序填满新文件并 fsync；等计数器稳定后才取 `before.json`，因此排除建树、初始上传。主写阶段不显式读取文件。最终 fsync、close 后等待至少连续 3 秒计数器稳定，再取 `after.json`。还保存 `immediate.json`，没有只取 writeback 尚未上传完的瞬间。
- 所有写均用 `os.pwrite`，无 Python 用户态写缓冲；“final”指循环末 fsync，“each”指每次 pwrite 后 fsync，末尾再 fsync。`flush` 的用户态缓冲问题在这里不适用。最终文件内容逐字节比对，校验读取在 `after.json` 之后，另存校验后快照，排除校验触发的 GET/compaction。
- 每次快照抓全部四个客户端，原始 Prometheus 文本在 JSON 的 `clients.<name>.raw`。只对当前实验客户端算主结果；其他三个客户端在各测量窗口的 PUT/GET/compaction 增量均为零。各 case 的对象请求错误增量为 0；结束时 staging blocks、staging block bytes、staging writing blocks 均为 0。

字节与次数分别取 `juicefs_object_request_data_bytes{method="PUT"/"GET"}`、`juicefs_object_request_durations_histogram_seconds_count{method="PUT"/"GET"}`，跨 storage-class 标签求和后做差。合并大小/次数取 `juicefs_compact_size_histogram_bytes_sum/count`。**PUT× = PUT 字节 / 本 case 成功 pwrite 字节总和；GET× 也以这些写字节为分母**，不是按读取请求大小计算的读放大。单位 B；1 MiB = 1,048,576 B。指标是客户端请求 payload，包含合并流量，不是网络 wire bytes。

## A–E 结果

A/D/E 使用完全相同的 1000 个 4 KiB 对齐随机 offset（固定种子 20261025，在 1024 个槽中有放回抽样，638 个不同槽），以及相同的每次写入内容。完整 offset 保存在各 `result.json`。B 在已填满的 4 MiB 文件 offset 0 再顺序写满一个对齐 4 MiB 块；应用一次 pwrite 在 FUSE 层分成 32 次写。

| Case | 写入 B | PUT B | PUT 次 | GET B | GET 次 | PUT× | GET× | 合并次 / 合并 B |
|---|---:|---:|---:|---:|---:|---:|---:|---|
| A_final | 4,096,000 | 12,460,032 | 992 | 8,392,704 | 3 | 3.042 | 2.049 | 2 / 8,388,608 |
| A_each | 4,096,000 | 46,039,040 | 1,010 | 41,988,096 | 11 | 11.240 | 10.251 | 10 / 41,943,040 |
| B_full | 4,194,304 | 4,194,304 | 1 | 0 | 0 | 1.000 | 0 | 0 / 0 |
| C_sparse_final | 409,600 | 4,096 | 1 | 0 | 0 | 0.010 | 0 | 0 / 0 |
| C_sparse_each | 409,600 | 413,696 | 101 | 0 | 0 | 1.010 | 0 | 1 / 4,096 |
| C_sparse_distinct | 409,600 | 409,600 | 1 | 0 | 0 | 1.000 | 0 | 0 / 0 |
| D_final | 4,096,000 | 20,848,640 | 994 | 12,644,352 | 5 | 5.090 | 3.087 | 4 / 16,777,216 |
| D_each | 4,096,000 | 46,039,040 | 1,010 | 37,793,792 | 10 | 11.240 | 9.227 | 10 / 41,943,040 |
| E_final | 4,096,000 | 8,265,728 | 1,054 | 4,194,304 | 64 | 2.018 | 1.024 | 1 / 4,194,304 |
| E_each | 4,096,000 | 46,039,040 | 1,640 | 42,233,856 | 663 | 11.240 | 10.311 | 10 / 41,943,040 |

各 case 的可比性及解释：

| Case | 对照口径与限制 |
|---|---|
| A_final | 请求中的 A 基线：4 MiB 已有文件，随机 4 KiB × 1000，末尾 fsync。2 次整文件范围合并，未出现 1000 次整块 RMW。 |
| A_each | 与 A_final 同 offset、内容、文件大小，只改 fsync 频率；实测合并增加到 10 次。不能把最终统一 fsync 的结果代表逐次持久化。 |
| B_full | 顺序全块负对照；请求数、请求大小和总写量均与 A 不同（B 总量多 2.4%），用归一化比值比较，不是等请求数对照。 |
| C_sparse_final | 空文件反复覆盖同一 offset 3 MiB 的 4 KiB，共 100 次；末尾统一 fsync，只有最终 4 KiB 上传。0.01× 来自重复覆盖合并，不能与 A 的随机不同槽直接比较。 |
| C_sparse_each | 与 C_sparse_final 同一写序列，各次 fsync；100 个小上传加一次 4 KiB 合并，GET 仍为 0。用于排除“洞必须先 GET”的假设。 |
| C_sparse_distinct | 空文件从 3 MiB 起连续写 100 个不同 4 KiB 槽，末尾 fsync；前方洞保留，409,600 B PUT、0 GET，补足 C 同位置覆盖的局限。 |
| D_final | 与 A_final 同序列，改为同卷 writeback。异步调度使合并次数变成 4，对比 A 的 2 次，不能把 5.09× 与 3.042× 当成纯 writeback 效应。 |
| D_each | 与 A_each 同序列，均实测 10 次合并；PUT 字节、次数完全一致，GET 少 4,194,304 B。写回后的持久化语义不同，按 staging 排空后的后端计数比较。 |
| E_final | 与 A_final 同逻辑序列，新卷 block=64 KiB；仅发生 1 次合并，不能由其字节较少推导 64 KiB block 按比例降低放大。 |
| E_each | 与 A_each 同序列，均为 10 次 4 MiB 合并；PUT 字节相同，合并的 10 个大 PUT 变成 640 个小 PUT，总次数从 1010 增到 1640。新卷命名空间不同。 |

## 把触发顺序拆开

为解释 A–E 纯写实验为何没有出现原主负载那样约 1000 次合并，增加单文件分阶段探针 F。先填满 4 MiB；在 offset 4096、1 MiB、2 MiB、3 MiB 各写 4 KiB，各自 fsync；再单独从 offset 0 pread 4 KiB。没有调用 `juicefs compact`。

`F_read_trigger` 的原始阶段快照给出：

| 阶段 | PUT B / 次 | GET B / 次 | FUSE 写 B | 合并次 / B |
|---|---:|---:|---:|---|
| 4 次 pwrite + fsync，尚未读 | 16,384 / 4 | 0 / 0 | 16,384 | 0 / 0 |
| 随后一次 4 KiB pread，等待合并完成 | 4,194,304 / 1 | 7,323,648 / 5 | 0 | 1 / 4,194,304 |

写后 Redis chunk slice 列表长度为 5，读及合并后为 1。debug 日志同时记录 inode 19470 的 `5 slices (4194304 bytes)` 合并，见 [default-mount.log](results/amp/default-mount.log)。[写后快照](results/amp/F_read_trigger/cycle-first-after-writes.json)、[读后快照](results/amp/F_read_trigger/cycle-first-after-read.json)可独立复算。读阶段没有新 FUSE 写，却出现 4 MiB PUT，排除了“这个 PUT 就是前面 4 KiB 写的固定块上传”的解释。

这里 GET 不等于恰好 4 MiB：应用读取、内核读提前、JuiceFS range GET/整对象预取及合并读取可重叠。应用 4096 B pread 在本探针产生了 16,384 B 的 FUSE 读；不能仅凭 GET/PUT 相等逐请求识别 RMW。

进一步重复 100 轮，每轮相同四个位置但更新内容，比较同一 400 次写序列。读版本每轮末额外 pread 并等待 Redis slice 列表合并至 1；纯写版本省略这一步。二者均每次写后 fsync，最终内容 SHA256 相同。

| Case | 写入 B | 应用读 B | PUT B | PUT 次 | GET B | GET 次 | PUT× | GET× | 合并次 / B |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|
| F_write_400 | 1,638,400 | 0 | 18,415,616 | 404 | 16,777,216 | 4 | 11.240 | 10.240 | 4 / 16,777,216 |
| F_read_100 | 1,638,400 | 409,600 | 421,068,800 | 500 | 424,374,272 | 199 | 257.000 | 259.0175 | 100 / 419,430,400 |
| D_read_100 | 1,638,400 | 409,600 | 421,068,800 | 500 | 416,022,528 | 195 | 257.000 | 253.920 | 100 / 419,430,400 |
| E_read_100 | 1,638,400 | 409,600 | 421,068,800 | 6,800 | 422,764,544 | 6,534 | 257.000 | 258.035 | 100 / 419,430,400 |
| F_read_trigger | 16,384 | 4,096 | 4,210,688 | 5 | 7,323,648 | 5 | 257.000 | 447.000 | 1 / 4,194,304 |
| F_span_64K | 16,384 | 4,096 | 81,920 | 5 | 0 | 0 | 5.000 | 0 | 1 / 65,536 |

- **F_write_400 vs F_read_100**：逻辑写序列和最终数据相同；增加读及合并完成屏障后多出 96 次合并，PUT 精确多出 **402,653,184 B = 96 × 4 MiB**，总 PUT 从 11.24× 到 257×。循环节奏也改变，不能把这个倍率作为任意读写节奏的普遍系数；上面的单轮分阶段快照直接确认读的触发作用。
- **D_read_100 vs F_read_100**：只改为同卷 writeback，仍为 100 次合并、相同 421,068,800 B PUT。GET 少 8,351,744 B（1.97%），首轮合并 GET 为 0，符合新写大块已在 writeback 本地缓存的观测；后续仍大量 GET。不能将这点 GET 差额完全分配给单一缓存动作。
- **E_read_100 vs F_read_100**：逻辑文件和序列相同，block 从 4 MiB 变 64 KiB，PUT 字节不变。400 个 4 KiB 上传之外，合并上传由 **100 个 4 MiB 对象变成 6400 个 64 KiB 对象**。这直接否定“本负载合并字节量随 block size 缩小 64 倍”。
- **F_span_64K vs F_read_trigger**：保留默认 4 MiB block，只把初始文件范围缩成 64 KiB，四个 patch 改在该文件的相应四个位置。合并输出随之为 65,536 B，PUT 为 16,384 + 65,536 = 81,920 B。这改变了文件范围/位置，只用于识别合并范围；小对象初始上传会缓存，不能直接比较 GET。

## 与实际版本源码相互核对

源码锁定二进制中的 commit `0b90c7db5a929ae6adc5faad948d108efd2c99f9`，副本及哈希在 [source-final-manifest.json](results/amp/source-final-manifest.json)。源码用于解释已观测的阶段变化，不代替实验。

1. [writer.go](https://github.com/juicedata/juicefs/blob/0b90c7db5a929ae6adc5faad948d108efd2c99f9/pkg/vfs/writer.go#L272) 为新的写片段创建 writer，并保留片段自身起始 offset；小写不必读取原文件块。四次 4096 B PUT、零 GET 的阶段结果与此一致。
2. [base.go 的 Read](https://github.com/juicedata/juicefs/blob/0b90c7db5a929ae6adc5faad948d108efd2c99f9/pkg/meta/base.go#L2120) 在原始 slice 数或整理后的可见 slice 数达到 5 时异步触发 compaction；[Write](https://github.com/juicedata/juicefs/blob/0b90c7db5a929ae6adc5faad948d108efd2c99f9/pkg/meta/base.go#L2176) 的触发条件则包含 slice 数模 100 等于 99，或超过 350。因而少量碎片后的读取，可以比纯写更早合并。`--no-bgjob` 没有取消这两条请求路径。
3. [compact.go](https://github.com/juicedata/juicefs/blob/0b90c7db5a929ae6adc5faad948d108efd2c99f9/pkg/vfs/compact.go#L57) 读取可见 slices、顺序写新 slice，明确调用 `writer.SetWriteback(false)`；合并计数器在合并开始时记录预计输出大小。当前 4 MiB 初始文件加局部覆盖的可见范围仍为 4 MiB；文件位于一个 64 MiB metadata chunk 内。配置 block 决定新 slice 分成多少对象，不将合并范围缩成单个 block。
4. [cached_store.go](https://github.com/juicedata/juicefs/blob/0b90c7db5a929ae6adc5faad948d108efd2c99f9/pkg/chunk/cached_store.go#L371) 默认会缓存小于 block 的新写对象；完整大块是否保留缓存受 writeback/大写缓存选项等影响。因此“发生合并”不意味着一定等量远端 GET；本地命中及读取预取改变 GET 数值。

## 旧冒烟窗口只读复核

只读取既有 before/after 文件，没有运行主负载。[smoke-recheck.json](results/amp/smoke-recheck.json)保存原路径、SHA256、差值和计算过程。

| 归档窗口 | PUT B | GET B | 合并次数 | 合并输出计数 B | 合并计数 / PUT | PUT 减合并计数 B |
|---|---:|---:|---:|---:|---:|---:|
| initial / off | 4,855,261,446 | 4,822,725,032 | 1,140 | 4,781,506,560 | 98.4809% | 73,754,886 |
| stable / off | 4,213,462,213 | 4,241,663,849 | 990 | 4,152,360,960 | 98.5499% | 61,101,253 |
| cleandiag / op | 4,200,891,313 | 4,231,748,080 | 987 | 4,139,778,048 | 98.5452% | 61,113,265 |

三个窗口的平均合并大小均恰为 4,194,304 B，对象请求错误增量均为 0。initial 的实际 FUSE 写计数为 74,676,310 B，并非 stable/cleandiag 的 61,899,097 B，因此这里只比较机制形态，不把它当成严格相同工作量的独立重复。

stable 的 PUT / FUSE 写为 **68.06985×**。合并输出计数 / FUSE 写为 **67.08274×**；剩余 PUT 为 **0.98711×**。后者包含正常片段上传及可能的覆盖合并影响，不能解释为恰好逐次应用上传。compact histogram 在合并开始时记账，旧窗口没有每个合并完成/浪费的逐请求审计，因此该拆分是客户端计数器层面的核算。受控探针有内容校验、合并完成的 metadata 屏障、零请求错误和排空快照，提供更直接的因果证据。

口径补充：背景的 598,482,944 B 是回放器成功读取的字节总和；stable 原始 `juicefs_fuse_read_size_bytes_sum` 差值为 644,141,056 B。读提前等使二者不必相等，不能把应用读取数直接从 GET 中扣除，得到“精确 compaction GET”。本次不改原 report 的既有文字。

## 贡献与限制

在这台机器、这个版本和被测文件范围下，证据支持：**小写先产生细粒度不可变片段，读写混合使 slice 合并频繁发生，合并反复搬运 4 MiB 可见范围，主导旧窗口的 PUT 计数。** 写回关闭不是该放大成立的必要条件：打开后，固定 100 次合并的 PUT 完全不变。稀疏新文件不强制 GET：三个 C case 的 GET 均为零，其一次合并也仅输出 4 KiB。block 大小主要改变本实验合并后的对象数，不能按 block 大小线性预测总字节。

这些变量相互影响，不存在由五个单次实验唯一确定、可相加的“随机写占 X%、writeback 占 Y%、稀疏占 Z%”。本报告给出受控对照的实测差额，以及旧窗口的合并计数核算。没有旧负载逐对象审计，无法严格重建 990 次合并中多少由 Read、Write 或其他路径触发，也无法逐 GET 分离应用读、预取及合并读。旧主负载有多文件、四条流、暖缓存及不同关闭/读取节奏；本次单文件串行探针不声称逐项复现其时间行为。

final 系列只测一次，其合并次数受写入/上传调度影响；因此用 each 系列和显式合并完成屏障解释机制，不把 final 系列差异当作配置收益。没有测断电持久性、稳态性能或 RustFS wire 流量，也不推广到其他 JuiceFS 版本和任意文件布局。

全部 16 个 case 内容校验通过；本地独立复算的 792 项断言通过，既有 129 个归档文件哈希与旧清单一致，`report.md` 原内容按字节未变，仅追加新报告链接。实验挂载点已卸载，原 `/mnt/jfs`、`/mnt/brewfs` 仍在；实验缓存、卷数据和新 bucket/db2 保留，未清理或修改旧结果。归档原始 JSON、脚本、日志、哈希及复算入口见 [results/amp/README.md](results/amp/README.md)。
