# 3. Packed 只读小文件

状态：已确认。统一参数与 Dynamic 比较方法见 [完整方案](../docs/PLAN-REVIEW.md)。

固定 100,000 个 4 KiB 文件，每目录 1,000 个文件，所有系统使用相同内容。Packed 数据集保持只读。

**准备与上传：** Native、Packed、JuiceFS Redis、JuiceFS TiKV，从同一个本地目录开始，使用 8 个准备 worker，计时至其他机器可读。Packed 的扫描、打包、上传和注册均计入。

**读取：** 固定 32 个读者，每个读者读取全部文件两遍，分别记录首次冷读与第二遍读取。

Native 先一次创建全部子工作区，再启动各读者挂载。所有挂载准备完成后统一开始读取；工作区创建和挂载时间单独记录。每次运行使用独立的挂载标识和日志文件，读取开始时立即保存指标快照。

| 读取组 | 设置 |
|---|---|
| BrewFS Native | 逐文件元数据，TiKV，正常缓存 |
| BrewFS Packed | 整包缓存开启，Dynamic 开启 |
| BrewFS Packed | 整包缓存关闭，其他参数相同 |
| JuiceFS Redis | 正常缓存 |
| JuiceFS TiKV | 正常缓存 |
| BrewFS Packed | 整包缓存开启，Dynamic 关闭 |

Native 和 Packed 使用同一套 TiKV 服务保存各自需要的可变元数据。JuiceFS 分别采用 Redis 与 TiKV。

Dynamic 关闭组重新生成其对象，同时记录准备和读取时间。主要指标为准备总时间、首次 64 文件读取时间、每遍读取时间、元数据请求数、S3 请求数与下载字节。

共 5 组准备、6 组读取，各运行一次，合计 **11 次运行**。复用现有导入工具、`c3-loader` 和原始指标采集代码。

验证 Packed 候选程序时，将可执行文件分发到六台节点的同一绝对路径，并在实验执行服务中设置 `AX_PACKED_BREWFS_BINARY`。该路径同时用于 Packed 工作区注册和挂载，实际程序路径及版本保存在挂载审计中。未设置时使用 `direct/bin/brewfs`。每次重测建立新的 `attempt-*` 目录，保留此前输出。

候选程序的并发正确性检查使用 `bench/cluster/runtime/check_packed_candidate.py --name <本次名称>`，同样设置 `AX_PACKED_BREWFS_BINARY`。脚本复用现有 10 万文件，在 A366 的 45、46 号槽位和 A355 的 35、36 号槽位分别挂载，每个挂载用 8 个线程读取两遍并逐字节检查。输出保存到 `run-002/diagnostics/<本次名称>/`，包含挂载配置、读者结果、标准输出、错误输出和退出记录。该检查单独标记为并发正确性验证；正式性能数据仍由上述 32 读者条件产生。

候选检查会保存各挂载的完整日志。需要读取请求的具体错误时，增加 `--rust-log 'info,brewfs::vfs::fs=debug'`；日志过滤条件同时写入挂载审计和 `run.json`。

读取结束或发生错误时，保存 `read-end.json` 和 `reader-stops.json`；各节点保留每个读者的 JSON、标准输出和错误输出。只有全部 32 个读者完成两遍读取并通过内容检查，整项才计为通过。
