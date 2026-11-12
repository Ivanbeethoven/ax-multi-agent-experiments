# 本轮指标采集

> 历史协议，仅用于原冻结计划；新实验见[当前方案](../current/README.zh.md)。

每个实际角色运行一份 `bench.runtime.observe`，放在Agent的cgroup外。C1–C3和fork有client、metadata、objects三份；handoff新增target。采样和原始文件写各角色数据盘，不写被测挂载。所有角色归档后由协调者校验。

| 角色 | 服务指标入口 | 同时保存 |
|---|---|---|
| client / target | BrewFS `127.0.0.1:8910/metrics`，或JuiceFS `127.0.0.1:9567/metrics` | 挂载及任务PID身份、cgroup CPU/内存/I/O/压力、宿主CPU/磁盘/网络、空间与inode |
| metadata | 原生 `redis-cli INFO ALL` | Redis PID及上述宿主/数据盘指标；保留命令数、网络字节、CPU、内存和持久化状态 |
| objects | RustFS → 本机OTLP采集器 → `127.0.0.1:18889/metrics`；采集器自身为`127.0.0.1:8888/metrics` | RustFS与采集器PID/cgroup、四块实际盘、上述宿主指标；保留请求计数、延迟桶、传输字节、底层盘读写 |

RustFS当前分支通过OTLP导出。采集器用本轮已下载并记录digest的OpenTelemetry Collector镜像，部署在objects节点，无需另开服务器或部署Grafana。使用 `scripts/metric-collector.py`，云上选 `--network host`，两个入口均只监听loopback；镜像和配置摘要进入资产清单。

RustFS配置：

```text
RUSTFS_OBS_METRIC_ENDPOINT=http://127.0.0.1:4318/v1/metrics
RUSTFS_OBS_METRICS_EXPORT_ENABLED=true
RUSTFS_OBS_METER_INTERVAL=1
RUSTFS_OBS_TRACES_EXPORT_ENABLED=false
RUSTFS_OBS_LOGS_EXPORT_ENABLED=false
RUSTFS_OBS_PROFILING_EXPORT_ENABLED=false
RUSTFS_OBS_USE_STDOUT=false
OTEL_RESOURCE_ATTRIBUTES=rustfs.cluster.id=<本轮独立集群ID>
```

采集器按1秒批次接收，导出保留原始时间戳、集群和observer标签，旧点30秒过期。`observe`仍按协议每5秒保存完整Prometheus原文及`/proc`原文，采样间隙上限30秒。采样没有断档不代表指标新鲜：另核原始点时间戳、RustFS存储快照时间，以及采集器的`otelcol_receiver_accepted_metric_points`、`otelcol_receiver_failed_metric_points`、`otelcol_receiver_refused_metric_points`和日志。缺失、过期和重启均保留失败，不能补零。上述采集器指标已从本机实际端点读到；冻结时以部署版本实测名称为准。

阶段边界按[收尾协议](../10-evidence.zh.md)打标，C3逐workspace打标。后端指标异步到达：停止业务后，在计时外等待最后一批导出并保存稳定计数，再完成排空及原生零债务双采样；前台wall单独保留，不能把等待隐去或归入负载操作延迟。对象GET/PUT与物理盘字节分开记录，不能用逻辑文件字节代替物理读取。

先用已知请求数验证计数链，再冻结实际存在的指标名。当前本机检查发出42次S3请求，`rustfs_s3_http_requests_total`增量为42；四个shard的持久化布局另行解码验证。此检查仅证明本机接入，云上还须重做端点、四盘身份、时间戳及至少三对观测开/关检查，共同wall和任务p99偏差均须≤5%。采集器与observer的CPU、内存、磁盘写入计入开销，不能声称有配额就已通过隔离检查。

配置依据：[Collector配置](https://opentelemetry.io/docs/collector/configuration/)；RustFS本批源码的`.docker/observability/otel-collector-config.yaml`及`docs/operations/storage-metrics.md`。历史批次的指标和布局不改写。

本轮开/关校准保留两种客户端内置计数与指标入口，并保留RustFS的OTLP发送逻辑；切换的是三角色外部采样、抓取和OTLP接收器。JuiceFS 1.4.2省略`--metrics`仍会开启默认入口，不能据此写成“内置指标已关闭”，见[该版本实现](https://github.com/juicedata/juicefs/blob/v1.4.2/cmd/mount.go)。两侧均使用新挂载和空客户端缓存，接收器按实际容器ID正常停止，新启动保存新名字、配置和PID。关闭接收器期间不报告后端计数覆盖；开启试验重新核时间戳和接收状态。内置计数的成本属于各系统当前二进制，本轮不声称测出了移除这部分代码的开销。

部署时用systemd的`IOAccounting=yes`保持I/O控制器开启：执行节点的system.slice和Redis服务均设置，并实测各自cgroup的`io.stat`。只向root的`cgroup.subtree_control`写`+io`会被systemd收回，本轮已实际遇到并保留失败。客户端正式采集绑定准备、测量和冷读回三个原始监督目录；同一时刻只挂载一个，其他入口明确记为尚未开始或正常停止。cgroup来源ID加`-process`，避免与同名HTTP/Redis指标入口冲突。

## 历史正式样本的分阶段计数

[summarize-flat-observations.py](https://github.com/Ivanbeethoven/ax-multi-agent-experiments/blob/2443946b40b5644b8cf4eabc6728c96f6c6e6c5e/experiments/unified-suite/pilot-aliyun/scripts/summarize-flat-observations.py) 已退役；下列命令仅在链接对应的历史提交中使用。它读取已验收样本的三角色采集，计算应用运行、整个测量和排空阶段的计数差值：

```bash
python3 experiments/unified-suite/pilot-aliyun/scripts/summarize-flat-observations.py \
  --cell "$CELL_DIR" --close "$CLOSE_JSON" --out "$NEW_JSON"
```

[C2 BrewFS](evidence/progress/observation-deltas-01/c2-native.json)与[C3 JuiceFS](evidence/progress/observation-deltas-01/c3-juicefs.json)已从云端原档生成。每个区间用该节点自己的单调时钟；缺失的计数项保留为空。Redis计数包含采集请求，RustFS有导出延迟，宿主磁盘统计含其他进程。文件系统逻辑字节、对象请求体字节和磁盘扇区字节分别保留。
