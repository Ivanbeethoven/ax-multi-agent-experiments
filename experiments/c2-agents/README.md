# C2：多 Agent 的完整任务负载

**RQ：私有工作区、缓存和写入策略能否改善多 Agent 的任务时间与资源占用？** 对应论文 Delta-Based Workspaces，并检查底层 I/O 改进在真实访问中的收益。

配置：[experiment.json](experiment.json)。比较 BrewFS Native Overlay、原生 JuiceFS、JuiceFS Overlay。六台均运行客户端，共用四节点 RustFS 和 A355 Redis。

输入为八类完整 trace：pytest、sklearn、AppWorld export、BFCL travel、DABench、Office Excel、Terminal access logs、ML vectorization。实际名称和下载地址见 [输入清单](../../configs/trace-inputs.json)。repo 与 HOME/XDG 在同一个工作区，trace 和写入 blobs 放在挂载外。

| 类别 | 设置 | 要测的影响 |
|---|---|---|
| 主实验 | 8、32、64、128 个工作区；八类任务数量相等；每工作区完整执行一次 | 吞吐扩展、每类任务耗时、总 CPU/内存、共享后端压力 |
| 参数：等待 | 固定 64 个，比较不等待、1–7 秒、3–15 秒 | 真实工作节奏下的收益与 I/O 上限 |
| 参数：写入策略 | 固定 64 个，另测本地暂存后返回 | 应用等待与最终上传成本如何变化 |
| 消融：名字空间共享 | 固定 64 个，BrewFS 创建时复制目录记录，文件对象仍共享 | 将私有元数据增量的效果与行缓存的效果分开 |
| 消融：BrewFS 行缓存 | 固定 64 个，关闭工作区行缓存 | 私有元数据缓存对任务时间及 Redis 请求的影响 |
| 消融：JuiceFS Overlay 缓存 | 固定 64 个，`fork_cache_bytes=0` | 分层元数据缓存的作用 |
| 消融：同机共享缓存 | 固定 64 个，相同主机读缓存总预算；共享只读元数据、Pack 和不可变数据块 | 减少跨挂载的重复下载、解析和缓存副本 |
| 独立扩展 | 192 个，使用主配置 | 接近本机群最大并发时的完成率、排队和资源占用 |

任务启动分散在 0–30 秒。等待插入已核对的工具调用边界，三组复用相同任务、主机位置和等待列表；文件操作顺序与内容不变。每次保存计划等待和实际等待。边界表由原工具日志生成，不以文件系统空闲时间猜测模型行为。

规模增长时任务总数随之增长，因此主图报告任务/小时、每类任务 P50/P95、操作吞吐和资源，而非直接用不同总量的批次时间计算加速比。活跃回放时间包含解析与输入读取，另记录累计文件操作时间。

每次记录应用完成、后台上传完成、另一机器新缓存首次成功读回三个时间点。保留原 trace 的 fsync/close。成功读写字节、S3 字节、物理磁盘字节分别统计；任务内重复读取按文件内容版本和区间计算。

**工作量：4 个主条件、4 个消融条件、3 个参数条件，三次重复共 81 次系统运行。** 192 并发单独生成，共 9 次，不扩展其它所有参数。配对参照和机制指标见 [ABLATIONS](../../docs/ABLATIONS.md)。工作区准备单独计时，并与任务执行相加报告完整成本。

```powershell
python -m bench.cluster.plan --group C2 --out runs/c2-plan.json
python -m bench.cluster.plan --group C2 --include-optional --out runs/c2-with-capacity.json
```

环境和指标见 [SETUP](../../docs/SETUP.md)；逐挂载配置见 [MOUNTS](../../docs/MOUNTS.md)。
