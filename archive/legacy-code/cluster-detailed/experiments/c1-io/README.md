# C1：对象布局与文件 I/O

**RQ：每对象 B/q 布局能减少多少读取放大，能否缩短文件 I/O？** 对应论文 Variable-Granularity Object Storage；同时测 Native Overlay 的常规读写成本。

配置：[experiment.json](experiment.json)、[fio-profiles.json](../../configs/fio-profiles.json)。后台为四节点 RustFS 和 A355 Redis。每个工作区为独立挂载、4 GiB 总内存、2 GiB 磁盘缓存。BrewFS 单独在 A366 计时；JuiceFS 与 JuiceFS Overlay 的 fio 主实验与请求尺寸组在 A366/A355 两项并行，结果标记 `shared-backend-concurrent`，记录每次重复的主机安排。其它条件单独计时。

| 类别 | 输入与操作 | 改变的参数 | 输出 |
|---|---|---|---|
| 主实验：fio | 32 GiB 初始数据；顺序读/写各 8 GiB；随机读 65,536 次；同步随机写 4,096 次；70% 读混合访问 16,384 次 | 五种访问模式，固定 8 个 job | 吞吐、IOPS、P50/P95/P99、CPU、S3 和磁盘字节 |
| 消融：文件路径 | 将同一 4 KiB 请求序列经过 Native Overlay | B=64 KiB/1 MiB/4 MiB | 应用请求如何形成 S3 Range，后端收益能否传到任务 |
| 读写尺寸：随机 | 32 GiB 文件集合，8 个 job，每次合计读取或覆盖写入 256 MiB | B=64/256/1024/4096 KiB；应用请求=4/64/256/1024 KiB；纯读/纯写 | IOPS、带宽、延迟、上传完成时间、EC CPU、S3 与磁盘字节 |
| 读写尺寸：顺序 | 32 GiB 文件集合，8 个 job，每次合计读取或写入 8 GiB | B 四档；应用请求固定 1 MiB；纯读/纯写 | 吞吐、写入放大、编码成本、上传后尾部时间 |

顺序请求为 1 MiB；随机读/写为 4 KiB；混合请求为 64 KiB。使用 `direct=1`、`psync`、`iodepth=1`、`verify=none`，内容直接比较另做。顺序写最后 fsync；同步随机写每次 fsync；混合写每 128 次写 fsync。job 使用独立区间，表中工作量为所有 job 合计。


q 由 RustFS 根据 B 和 EC 2+2 布局选择。准备阶段读取实际对象布局并保存；每个 B 条件写入自己的文件。请求偏移表固定，包含四种边界起点及边界前 2 KiB 的位置。客户端块大小保持 4 MiB。

尺寸组使用 `direct=1`、`psync`、`iodepth=1`，关闭客户端数据预读，固定上传/下载并发。读写均使用相同的预分配文件集合；写入每累计 64 MiB 执行一次 fsync，最后 fsync 并等待远端可读。256 MiB 和 8 GiB 都是全部 job 合计，随机请求位置按种子生成。写入组每次从相同父版本重新派生，不把前一条件的修改带入下一条件。

同一个 B 下改变应用请求大小；同一个应用请求大小下改变 B。JuiceFS 和 JuiceFS Overlay 执行相同的 8 个随机条件与 2 个顺序条件，使用其实际对象布局并记录。两组对照结果可复用于四个 B 的图，不重复执行四遍相同对照。每个请求尺寸至少呈现读取与写入两张曲线。


对象已有布局不会因修改进程参数自动改变；每档重新写入对象并读取持久化布局。保存 `requested_B`、`actual_B`、`actual_q`、对象逻辑长度、各分片长度和 EC 配置。

文件读取同时记录客户端冷态和同挂载第二遍的热态。单独计时条件的物理读取放大使用对象后端冷态数据；后端热态另列。内容检查放在计时后：32 GiB 文件组检查全部文件长度和固定取样块，记录实际比较字节数。并发组保存共同后端总指标，通过组记录关联到两项结果，不计算单个任务的后端物理读取放大。

**工作量：15 个主条件、3 个文件读取条件、40 个读写尺寸条件，三次重复共 234 次系统运行。** 同时最多两个 JuiceFS 文件 I/O 条件；Native Overlay 参数组单独计时。32 GiB 输入复用，准备对象与写入副本按条件清理。

执行采用 [c1-execution.json](../../configs/c1-execution.json)：准备阶段最多两个任务并行，fio 条件内部固定 8 个 job；A366 测量时，A355 可用 2 个逻辑 CPU、2 GiB 内存解析上一条件的小结果或生成下一条件参数。后台处理使用 Redis 之外的 CPU，并计入主机现有预算。实际对象准备、读回和清理安排在计时窗口之外。记录 `schedule.jsonl` 和整轮墙钟时间，复用相同不可变输入及匹配的 JuiceFS 对照结果。

批量生成本 C 的顺序任务：

```powershell
python -m bench.cluster.plan --group C1 --out runs/c1-plan.json
```

每项输出统一写入 `runs/<run-id>/C1/<condition>/<system>/r<repeat>/`，保存 fio JSON、配置、原始指标、内容比较结果和分段时间。环境准备见 [SETUP](../../docs/SETUP.md)，挂载配置见 [MOUNTS](../../docs/MOUNTS.md)。
