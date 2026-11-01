# 性能记录与可观测性验收

**先验收观测，再开正式性能测试。** 本轮C1为8 Agent、两执行节点各4个；先用小负载核准确性，再按实际8客户端密度核采集开销、存储和丢样。其他组按自身目标密度验收；不借用8 Agent资格覆盖64/128。保留全部失败、慢值和缺测。

## 1. 必须记录什么

| 层次 | 记录 | 口径 / 来源 |
|---|---|---|
| 工作量与时间 | 全任务操作数、读写字节、实际任务数、每任务耗时、cohort makespan、p50/p95/p99、失败/errno/handles、活跃曲线 | Rust回放原始JSON＋协调器事件；任务级与FUSE级分布分开，不平均各客户端p99 |
| FUSE / metadata | 分操作计数/错误/延迟直方图、后端调用与延迟、缓存hit/miss | BrewFS observability；JuiceFS原生metrics；两者回调数不必等于回放POSIX调用数，kernel cache会改变路径 |
| 对象请求 | GET/Range/PUT/HEAD计数、请求延迟、上传下载字节、失败/重试、在途 | 客户端/服务端分别记录；成功payload、HTTP流量与设备字节分别命名 |
| 写入完成 | application-finished、dirty/pending/inflight/staging、drain完成、正常卸载、独立客户端读回完成 | dirty=0、fsync返回、mount退出均不能单独替代远端内容验证；重叠队列计数不相加 |
| 每 Agent / 服务 cgroup | cpu.stat（usage/throttling）、memory.current/peak/events（OOM）、swap、io.stat、PSI、进程PID/start ticks | 从真实cgroup读取；挂载＋replayer共享配额；采集器放宿主预留预算 |
| 每台主机 | CPU/steal、内存/回收、vmstat、diskstats、net/dev、TCP重传、PSI、每数据盘容量/inode | 原始/proc按时间保存；物理盘和分区不重复相加；diskstats sector单位单独注明 |
| Redis | INFO ALL：commandstats、CPU/内存、连接、keyspace、AOF状态/大小/重写、命中/未命中、错误 | 整服务计数，独占池；INFO自身开销保留并注明，不reset全服务计数 |
| TiKV / PD | RPC延迟/错误、CPU、RAFT/WAL、RocksDB读写/缓存/compaction、leader/region、pending | 三节点逐个采集，/metrics实际存在的name与label在资格期固定；不能把六个进程当六台机器 |
| RustFS | HTTP请求、EC布局/B/q、各disk读写/延迟、内部读取字节、错误/重试、容量、CPU/内存 | 从本批冻结分支源码和实际endpoint核指标语义；缺内部字节计数时不以HTTP字节冒充读放大 |
| C4特有 | fork/clone请求到接受/全部完成/首用、source-stop/lease释放/target挂载/首新增正确工具、邻居影响 | 同一协调器时钟；正常停止期间明确记录not-started/cleanly-stopped，绝不填0或丢掉该阶段 |

C2访问延迟用ml-corpus-scan的固定64桶 `access_latency_ns_log2`，桶k范围为 `[2^k,2^(k+1)) ns`，全部访问含错误；汇总桶后报告分位数所在区间，不伪造桶内精确p99。C3保存每个请求index、offset、状态、字节、延迟及本地峰值在途；所有shard的index必须恰好覆盖冻结请求表且无重复，全局配额相加等于目标。

## 2. 固定指标合同

先在资格机读取原生endpoint，用实际输出固定 `experiment-metric-contract-v1`。字段：`reviewed=true`、`source_pins`（BrewFS/JuiceFS/RustFS/Redis/TiKV/PD源码与二进制或镜像）、`bindings=[{observer_id,target_id,required_names,layer,unit,meaning}]`。meaning写明成功/尝试、包含失败与否、字节层、分母、标签聚合和版本差异。不能只写“有监控”。

BrewFS本次源码审阅SHA `918deb90e046889d94eb8ff633822e275792ce28`，需启用 `observability` feature及 `--metrics --metrics-addr`。原生name包括 `brewfs_fuse_op_total`、`brewfs_fuse_op_latency_seconds_{bucket,count,sum}`、`brewfs_object_get_total`、`brewfs_object_get_bytes_total`、`brewfs_object_put_bytes_total`、`brewfs_cache_hits_total` / misses，以及 `brewfs_writeback_dirty_bytes` / pending_upload / inflight。这只是源码能力检查，实际build/help/endpoint仍需验证。

JuiceFS的 `--metrics` 和name以[官方监控说明](https://juicefs.com/docs/community/administration/monitoring/)及[指标表](https://juicefs.com/docs/community/reference/p8s_metrics/)为候选，必须再核冻结版本的实际输出。优先原生FUSE、metadata、对象请求histogram和bytes、blockcache、buffer/staging/uploading；不要要求新版本才出现的name后用空值填老版本。TiKV/PD入口参考[官方监控API](https://tikv.org/docs/6.5/deploy/monitor/api/)。

RustFS使用用户指定 `feat/dynamic-block-size-rebased` 分支，本批fresh pin；不能沿用旧官方image或凭猜测填写metrics URL。EC布局另做实际xl.meta/B/q证据，metrics开关不等于dynamic block已启用。

## 3. 采集器设置和命令

每物理/虚拟主机一采集器，放在宿主预留CPU内，不与Agent争用2CPU/8GiB预算。默认1秒一次，资格期冻结interval；快于采样间隔的短操作靠累计计数/histogram的前后差，不声称每秒采样捕获全部瞬时峰值。使用[observe.py](../../../bench/runtime/observe.py)，保留每次原始文本、SHA、单调/UTC时间、采集器CPU消耗和错误。

observer spec `experiment-observer-v1` 字段：

- `reviewed`、`interval_seconds`、`metric_contract_sha256`。
- `data_paths`：本轮实际数据盘；`cgroups=[{id,path}]`：真实cgroupfs路径；`processes`：实际 `pid,boot_id,start_ticks`；只采stat/status/io，不采环境和命令行。
- `targets=[{id,kind:http,url,format:prometheus,required_names,counter_names,timeout_seconds}]`。TLS保持校验；可用固定ca_file/bearer_file引用，禁止URL里带凭据、重定向或自动代理。loopback由节点本地采集，不开放公网端口。
- Redis使用 `kind=command,format=redis-info,step=引用`，只接受原生 `redis-cli ... INFO ALL`；密码走受控环境。
- 客户端target可填 `mount_directory`，只有实际ready前/正常stopped回执后才允许记录计划内生命周期状态；必须live的阶段由cohort `required_live_targets`强制检查。其他失联计为失败。
- 纯协调主机可用 `role=host-only`，空targets/cgroups/processes只表示该主机没有相应服务，不能用于代替客户端或后端指标。

```bash
python3 -m bench.runtime.observe start --spec "$OBSERVER" --sha256 "$OBSERVER_SHA" --output "$OBS_STATE"
python3 -m bench.runtime.observe status --output "$OBS_STATE"
```

cohort在全部worker准备后自动写 `measurement-start`，结束后写 `application-finished`；一台也缺失就不接受live check。各节点标记与协调器ACK都保留，不相减不同机器的monotonic时钟。

后续分步记录排空与独立读回，再正常停止采集并校验：

```bash
python3 -m bench.runtime.observe mark --output "$OBS_STATE" --label drain-complete
python3 -m bench.runtime.observe mark --output "$OBS_STATE" --label remote-verified
python3 -m bench.runtime.observe stop --output "$OBS_STATE"
python3 -m bench.runtime.observe audit --output "$OBS_STATE" --max-gap-seconds 3 \
  --marks measurement-start application-finished drain-complete remote-verified
```

标记动作不代替drain/读回证据：保存原生完成回执、内容校验和明确关联的结果SHA。关键name缺失、非有限数、counter回退/进程重启、采样失败、超过3秒断档、文件摘要不符，均失败。3秒是1秒间隔的默认门；修改采样间隔须同步冻结门限并重新资格。原始日志、stderr、OOM、部分结果都保全，不能只有一张图或平均耗时。

## 4. 采集开销与正式接受

同一build至少3个交替on/off配对，输入、节点、目标密度、缓存和任务量一致；C1本轮8客户端。文件系统每次fresh挂载，C3每次同等请求/缓存状态。off为observer-calibration-off、无collector，挂载生成用同名purpose关闭metrics；on的worker/cohort为observer-calibration-on，挂载使用正常带metrics配置。BrewFS进程级OnceLock使其不是同挂载严格因果试验。正式条件始终开监控。

`experiment-observer-overhead-plan-v1`固定pairs=[{on:cohort结果引用,off:引用}]、max_relative_change<=0.05、metric_contract_sha256、workload_contract_sha256。两侧实际结果必须绑定同一指标/工作量合同；trace核操作和读写字节/handles，ML核访问/字节/errors，S3核请求/字节/全局并发/shard。运行：

```bash
python3 -m bench.runtime.observe overhead --spec "$OVERHEAD_PLAN" --sha256 "$OVERHEAD_SHA" --output "$OVERHEAD_RESULT"
```

核对每对Agent数、操作/读写字节/handles一致，比较makespan与任务p99的配对组中位数；默认相对变化均≤5%。超出则先降低采集负担并重做完整配对，不只重跑不利一臂。任何binary、interval、目标数量或采集路径改变都使旧开销资格失效。校验通过只表明该规模下满足预设容差，不声称零开销。

正式接受需要同时具备：等工作量回放、正确性资格、完整采样及阶段覆盖、排空/远端内容确认、资源/版本/输入身份、所有结果已下载并SHA核验。当前cohort返回的 `passed` 只是应用与live check状态，`performance_accepted=false`；最后由负责Agent逐项关联证据后形成验收记录。缺项只列为未完成或诊断，不能开始正式对比后再补指标。
