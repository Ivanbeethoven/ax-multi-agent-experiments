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
