# 605/606 诊断：前驱文件的 compaction 确实进入目标 CPU 窗口

本报告日期为 2026-11-13（Asia/Shanghai），表内运行时间均为 UTC。只读对象为 RBM `corrected-runtime-review-09` 静止归档；不修改原始日志、二进制或生产代码。

结论：目标 `fio-randwrite-direct1` 的 active 和 strict-drain CPU 窗口包含上一场 `fio-randwrite-direct0` 文件的真实 compaction。它不再只是根据全局 PUT 差额提出的假设。现有证据仍不能把 601/603 的全部 CPU 回退归给 compaction，也不能从异步任务持续时间扣减 CPU；原完整矩阵的数值及 HOLD 状态保留。

## 身份及具体证据

两个实际运行头分别为 corrected baseline `4f982f45c153a0c31e3e9cd8ff642ec7659c98fd` 与 candidate `c8f5f16aefe811540782b952481982179a1bfd6f`。本次涉及的 worker、compactor、S3 adapter 和 Redis chunk 编码源码逐字相同。`events.json` 记录实际 HEAD、源码 SHA 和所读 80 个原始输入与归档 manifest 的逐项 SHA 对照。

`start-request.json` 通过真实 `stat` 保存了前三个场景的六个文件。两臂 inode 映射相同：seqwrite/direct0 为 6，seqwrite/direct1 为 8，randwrite/direct0 的四个文件为 10、13、12、11。每个文件为 512 MiB，路径及长度与前驱冷 SHA 验证的 `expected.json` 一一匹配。Redis `chunk_id` 编码为 `inode * 1_000_000_000 + chunk_index`（`src/meta/stores/redis/mod.rs:2157–2170`）。

当前挂载的实际 compaction 日志恰好覆盖 inode 10–13 各 8 个 chunk，即 32 个旧 randwrite/direct0 chunk；所有都具备获取锁、开始及完成日志。两臂各为 31 个 Heavy、1 个 Light。它们不是当前 direct1 场景的新文件，也不是根据 inode 连号猜测的文件。

| 诊断事实 | baseline 605 | candidate 606 |
|---|---:|---:|
| 前驱 32 个任务开始 | 01:19:58.755162 | 01:36:13.905887 |
| 目标 active 内完成 | 4 Heavy + 1 Light | 3 Heavy |
| 目标 drain 内完成 | 20 Heavy | 21 Heavy + 1 Light |
| 九项写回债全零后完成 | 14 Heavy | 15 Heavy + 1 Light |
| 前驱最后完成 | 01:21:26.907902 | 01:37:40.529570 |
| 最后完成至 strict CPU-end | 5.184 s | 5.110 s |
| 31 个 Heavy 的锁后 slice 条目合计 | 677 | 714 |

所有阶段按原 `client-cpu-raw.tsv` 的三个实际 CPU 采样时刻划分。`events.json` 保存每个任务的日志行号、UTC、精确整数纳秒、inode/path、chunk_index、锁后 slice_count，以及与 active/drain 的时间重叠。重叠时间不是 CPU 时间。两臂前驱输入的 slice 条目数不同，也不能单独换算成字节数或 CPU 比率。

目标启动前 `.stats` 均显示 `fuse_write_bytes_total=0`，已有 96 次 PUT。perf 控制 ACK 后、目标 CPU-start 前仍未写新文件，PUT 已分别达到 112/113。当前进程身份、旧场景重挂得到的 PID、配置 SHA 与目标使用的 PID/配置对应。结合上述旧 inode 任务日志，可直接确认上一场冷重挂启动的后台任务延续到了目标阶段。

## PUT 及源码解释的边界

`worker.rs:287–352` 枚举并逐个处理 eligible chunk；`compactor.rs:249–258` 读取、合并旧 slices 并写入新 slice；`compactor.rs:319–363` 按 metadata 顺序遍历各 entry；`compactor.rs:368–381` 按 block 调用 `write_fresh_range`，并共享上传 semaphore。后者不通过 FileWriter 的九项写回债账本，所以债务全零仍可有这些 PUT。现有 strict-drain 同时等待全局 PUT quiet，因而其 CPU 窗口包含这段后台活动。

九债归零后的 native PUT 增量为 baseline 221 次、926,942,068 B，candidate 240 次、1,006,633,920 B；同一时间范围内的 S3 span-close 日志分别为 220/240 次，全部 `total_size=4,194,308`。native 统计异步镜像和快照取样边界与日志完成时刻不同，这里保留一项差值，不声称它们严格逐请求对账。候选侧的 240 个完成与 15 个完整 Heavy 任务的形状相容，但这种算术相容不能代替逐对象身份。

实际 S3 日志只有 `total_size`，没有对象 key。`s3.rs:760` 的 instrument 声明了空 `key` 字段，而函数仅在 765 记录 `total_size`；因此不能把匿名 PUT span 一一映射到具体 chunk、新 slice 或对象。任务与 PUT 的时间重叠可以核实，单个 PUT 的对象归属不能从此日志恢复。

本报告不根据 CRC/SHA 等共享叶子函数分配 CPU。调用栈采样、单调时钟对齐、丢样及可解栈质量由独立审查补充。即使识别到 compaction 栈，也必须区分该机制确实消耗 CPU、以及它是否解释两臂全部差额。

## 可支持的结论

- 证明前驱 compaction 与当前 active/drain 同时发生，原六场景累计历史协议不是隔离的单一前台 writer CPU 测试。
- 原 601/603 仍是其完整累计历史流程的有效观测；605/606 是加日志及采样的诊断，不与原结果跨批平均，也不取代性能准入。
- 两臂本诊断挂载中 32 个旧 randwrite chunk 都有完成日志；这不表示全 namespace 的后续生命周期已结束。目标后的冷重挂还可能启动当前文件的新任务。
- 新 fixed-work 610/611/612 只回答 fresh namespace、固定相同工作量下的前台及 strict-writeback-drain 是否回退；默认 compaction 保留。它不自动豁免原历史协议的回退。

复算：`python3 /home/dev/ax-randwrite-drain-diagnosis-01/profile09_events.py`。输出 `profile09/events.json` SHA256 为 `fbde2a9ae148569693019a84a7489f7ab12444e4421a3ccfaddfc8085b58f644`。原包 SHA256 为 `b5ee0a2fd7ac7b400642fc587a02bd6cf495003dbc4991a10b52bcec708e705c`。
