# 指标与结果保存

## 采集架构

node-coord 保存任务表和阶段事件；六台各一个采集进程，访问本机所有挂载的 metrics 端口，读取主机与任务 cgroup 数据。四个 RustFS 节点按当前条件采集修改版 9464 或原版 19464 的服务指标及相应进程 CPU/内存，并记录物理盘指标，node-meta 采集 Redis，TiKV 节点采集其服务指标。每 5 秒采样一次，准备/挂载/开始/应用结束/上传完成/内容检查/退出另写阶段事件。

每个挂载端口为 `20000 + 本机槽位`。采集按主机追加 JSONL，原始 Prometheus 文本分段压缩。不要按每次采样、每个挂载生成几十个零碎文件。采集实现为 `bench/cluster/runtime/host_collector.py`，计数提取位于 `metric_totals.py`。

缓存累计达到 16 MiB 或 60 次采样即写入压缩分段，减少采集器内存占用。控制请求默认等待 30 秒；客户端断开后采集器继续运行。挂载准备、读者执行或采集控制出现错误时，执行器仍卸载已经创建的挂载，并保存相关输出。

| 来源 | 必存指标 |
|---|---|
| fio / 回放 / 数据加载器 | 完成状态、操作类型与次数、成功字节、errno、每段时间、可合并延迟直方图、等待计划与实际等待 |
| GraftFS / JuiceFS | FUSE 请求与延迟、缓存容量/使用/命中、元数据请求、上传队列、未完成上传字节与操作、后台错误 |
| cgroup 与进程 | CPU、RSS、memory.current/peak/stat、io.stat、memory.events、FD、线程与 PSI |
| Redis | 内存、命令/Lua 调用、连接、延迟、AOF 状态、重启与淘汰数 |
| TiKV | 请求数、请求延迟、CPU、内存、磁盘读写与服务状态 |
| 四台 RustFS | GET/Range/HEAD/PUT/LIST 次数与字节、请求延迟、队列、EC CPU、实际 B/q、读写错误 |
| 主机 | CPU/内存、diskstats、网卡收发、磁盘剩余空间与 inode、UTC 与单调时钟 |

每组都保存 `app_done` 和 `remote_ready` 时间。前者是应用操作完成；后者包含后台上传排空，以及新挂载实际读回。读回检查耗时独立记录，同时报告不含检查的上传完成时间。两次间隔至少 1 秒的零上传计数与进程正常退出一并保存。

fio 的 `fio_read_throughput_mib_s`、`fio_write_throughput_mib_s` 直接取 fio 报告的带宽；`fio_io_seconds` 为其 I/O 运行时间。`throughput_mib_s` 使用应用总时间计算，包含 fio 启动与退出。展示时分别列名，不能混用。MiB/s 表示每秒 1,048,576 字节。

## 字节与比例

应用字节取成功 read/write 返回值；S3 字节取实际请求/响应；物理字节取四台对象节点的磁盘计数差值。物理设备每次只求和一次。同盘上的客户端缓存和对象服务分别记录能归属的 I/O 与整盘总量。

RustFS 客户端 GET 字节采用 `rustfs_s3_get_object_size_bytes_sum`，PUT 字节采用 `rustfs_s3_put_object_size_bytes_sum`，请求数采用 `rustfs_s3_http_requests_total`。`rustfs_http_server_*` 包含节点间通信，单列为 HTTP 总流量。分片读取、节点间传输与物理盘读取分别保留；客户端自身提供的上传下载计数同时保存。


读取放大 = 后端实际读取字节 / 应用成功读取字节。上传放大 = 实际 PUT 数据字节 / 应用成功写入字节。零分母记为 `null`。缓存使物理读取低于应用字节时，按实际值呈现。

重复读取比例 = `1 - 本任务每个文件内容版本的已读区间并集字节 / 累计成功读取字节`。同一文件被覆盖、截断或替换后按新内容版本统计；重命名沿用文件身份。跨工作区缓存复用另统计唯一对象区间和实际下载字节。输入 trace 的操作次数与真实 FUSE 请求次数分列。

短于 5 秒的阶段同时保存开始/结束计数快照；阶段总量直接取计数差值。延迟来自操作记录或服务直方图。多个 P99 不做算术平均。服务器重启或计数回退时分段计算，原始数据保留。

RustFS 每秒导出指标，OpenTelemetry 最多再等待一秒发送。阶段快照前等待 2.2 秒并记录实际等待时间；应用计时从开始快照后启动，在结束快照前停止。这段等待单独计入观测开销。

## 保存结构

```text
results/<run-id>/
  inventory.json  inputs.json
  versions/
  config/  mounts/  commands.jsonl  status.json
  hosts/<host>/
  01-io/
  02-fork/
  03-packed/
  04-agent-replay/
```

每个实验目录按配置编号、重复编号和执行次数保存原始结果；系统名称记录在 job.json 与 result.json 中。服务器以 node-coord 为汇总节点，其余节点写各自的 `hosts/<host>/`。本轮完成后一次性取回本地。

历史结果位于 `archive/results/`，保留原始内容与对应配置。

挂载审计另存实际 `TOKIO_WORKER_THREADS`、`RAYON_NUM_THREADS`、`GOMAXPROCS`、CPU 亲和性、当前及上级 cgroup 的 CPU 配额、进程线程名称与数量、二进制路径和文件大小。线程对比同时使用相同源码的 runtime 查询程序，在挂载 cgroup 中记录 Tokio 选择的 worker 数量。GraftFS 的 EC hint 参数随审计保存。

S3 应用流量优先使用客户端 GET/PUT 字节计数，零值按零记录。RustFS 服务总计保存在 `rustfs_*` 字段中；只有缺少客户端流量计数时才采用服务总计，并通过 `s3_byte_source` 标明来源。
