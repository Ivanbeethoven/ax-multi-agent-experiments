# 执行与观测

[并行流程](../MAIN-SCALE.zh.md)规定分池、配对、资源锁和释放；[配置](CONFIGURATION.zh.md)规定软件与服务参数。当前适配进度见[方案入口](README.zh.md#实现状态)。

## 准备与下载

在远端 Linux（Python ≥3.11）从仓库根目录操作，构建一次并保存产物 SHA：

~~~bash
cargo build --release --locked --manifest-path tools/fs-probe/Cargo.toml --bin smallfiles
cargo build --release --locked --manifest-path tools/full-fuse-replay/Cargo.toml
python3 -m bench.current_design --config experiments/unified-suite/current/design.json
~~~

最后一条只核规划一致性和并发预算，不创建资源或可执行 plan。BrewFS/RustFS 的版本、镜像 digest 与完整构建参数另入 build-manifest。

[archives.json](../../../scenarios/k8s-containers/archives.json)列7份 trace 的 URI、大小和 SHA；当前 C2 取4份：

~~~bash
python3 scenarios/k8s-containers/scripts/download_archives.py \
  --source oss --cases pytest-full-02 django ripgrep-20261026 archive-io-20261026 \
  --out /srv/ax-data/archives
~~~

杭州 ECS 可加 `--internal`。下载器核整包 SHA，解包见[数据目录](../../../scenarios/k8s-containers/data-catalog.md#解压和回放)，成员/初终态校验见[回放说明](../../../tools/full-fuse-replay/README.md)。TRACE使用catalog.root_dir对应的目录，case ID不一定等于解包目录。完整回调数：pytest 1,747,109；django 57,787,980；ripgrep 3,122,195；archive I/O 3,673,814。rbm 的140回调合成探针不属于这些输入。

每宿主共享一份只读 events/blobs，每 Agent 独立工作区；输入和结果都放被测挂载外。先严格回放，再从新初态测性能：

~~~bash
"$BIN/full-fuse-replay" "$TRACE/capture/events.jsonl" "$TRACE/capture/blobs" \
  "$WORKSPACE" "$OUT/replay.json" --performance
~~~

调用前完成挂载、初态校验和 barrier；调用后核完整操作/字节/剩余句柄及最终树，再做冷重挂。需要 portable terminal probe 时按工具文档单列。

## 挂载适配

| 环节 | 必需操作与回执 |
|---|---|
| 导入 | 构建 Packed manifest；全量读回；保存真实上传 hint、对象布局与源树 SHA |
| 初始化 | `brewfs workspace --meta-backend redis --meta-url … --workspace-namespace … init-packed-volume --manifest-key … --data-config …`；保存返回的 workspace/revision |
| 挂载 | `brewfs mount --config … MOUNTPOINT`；使用 workspace-v1、真实 UUID/namespace，核 catalog lower 绑定 |
| 派生 | BrewFS 对已验证 revision 原生 fork，JuiceFS 对同内容原生 clone；保存 argv、父子 ID、首读、任务时间和冷验 |
| 关闭/重开 | 排空、原生卸载和进程退出；以同一 workspace 冷重挂，验证全部 touched 状态 |
| Overlay 运行开销 | 同一引擎逐臂预热目标 lower；保留工作集、挂载身份、两遍访问及末遍计数，再开始相同操作的计时 |
| 多机 | 独立 cgroup/路径、全员 ready barrier、固定启动偏移和每 Agent 的真实退出 |

命令结构来自已核对源码；省略号由本批私有配置填充。保存原生 `--help` 和完整解析设置后再生成 argv。新 route、全局槽位和采集适配尚待接入 suite，旧 planner 不接受新规划。

Overlay 热态回执至少含 `input_sha256`、两臂共同的 lower 工作集清单 SHA、各臂真实开关状态、实际缓存预算、挂载身份、每遍操作数和末遍 lower 的后端请求及读字节差值。Packed wire005 从同一挂载的 `.stats` 读取 `brewfs_packed_v3_runtime_backend_range_gets_total` 和 `brewfs_packed_v3_runtime_backend_received_bytes_total`；它们覆盖运行时 metadata 和 payload，不能用 HTTP 中缺失的指标当作零。先用独立冷读验证计数会动。当前普通读取路径未证明帧数据可在直读下驻留；达不到热态，就只报告卡点，不用页缓存掩盖 Overlay。协调者保存本臂预热结束、验证通过和计时放行的顺序。末遍缺字段、计数重置、发生 remount 或预热没有覆盖计时段的 lower 工作集时，结果不进入热态对照。

用 `python3 -m bench.warm_lower --mount MOUNT --working-set RANGES.json --working-set-sha256 SHA256 --out OUT` 检查本臂。清单记录每段的 `path`、`offset`、`length`、`sha256`。在[本地两文件诊断](../pilot-aliyun/evidence/preparation/c2-local-packed-warm-01/README.zh.md)中，当前 workspace-v1 的 `.stats` 缺这组 Packed 计数。独立检查对象端请求发现，两遍都经 FUSE 读出同样的 77 B，第二遍仍从 RustFS 取回全部 77 B（2 次 Range）。这个配置不能进入热态计时。该诊断未比较两臂或测量 Overlay 开销。

## 指标收集

~~~text
协调者：计划与SHA、state.sqlite、阶段事件、资源账单、归档索引
  ├─ 每个客户端/目标：Agent + FUSE metrics + cgroup/proc/盘/网
  ├─ Redis：INFO + 原生延迟数据 + AOF + 宿主指标
  └─ 每个RustFS节点：请求/字节/布局/债务 + 每盘/网卡
~~~

各宿主在 Agent cgroup 外运行 `bench.runtime.observe`，5秒采样，保存原始文本和 sample.json。只需采集端，不强制部署 Prometheus/Grafana。当前 command target 支持 `redis-cli INFO ALL`；Redis 延迟直方图需额外原生采集或固定版本 exporter，不能假定 INFO 已包含全部延迟指标。

| 层 | 指标与来源 |
|---|---|
| 应用 | fio JSON、smallfiles CSV、回放 JSON：完成时间、成功字节、操作数、错误序号、可获得的延迟分布 |
| Agent/FUSE | 分开记录 cgroup CPU秒、RSS/峰值、memory.events、io.stat、cpu.stat、PID/boot ID |
| 文件系统 | lower/upper命中、miss、解码、驻留/eviction、元数据请求、上传债务：原生 metrics |
| Redis | 命令数、内存、AOF/rewrite、fsync等待及延迟数据；不持续开 MONITOR |
| RustFS | 每节点 GET/Range/PUT、请求字节与错误、实际 B/q；每盘物理读写用 diskstats |
| 宿主/费用 | CPU/PSI、IOPS/队列、NIC、空间/inode、时钟误差；云 API 的创建/释放及计费记录 |

metric contract 对每项列 `source/field/unit/scope/counter-or-gauge`，同时绑定 observer ID 与实际 endpoint。counter 差值按本层统计；重启、断档和缺字段原样保留。物理读放大=后端物理读字节/应用成功读字节，网络传输放大另算；分母为0时不计算。共享后端同窗存在准备/校验 I/O 时，该窗口不用于性能归因。

阶段标记：measure-start、measure-end、drain-end、verify-end。C3 加工作区后缀，各自保存真实时间；不同工作区阶段可重叠，后端计数默认只归整批，不能拼成纯阶段成本。跨机保存 UTC、本机 monotonic 和时钟误差，不相减不同机器的 monotonic。

采集覆盖挂载、测量、排空、卸载及冷验证；冷验证挂载使用新 target ID，保留其生命周期，验证结束也必须真正退出。挂载前/退出后由原生回执识别 not-started/cleanly-stopped。

## 验收与交接

- 每种实际规模/采样配置先做至少3对 observer on/off 校准，makespan 及可获得的操作 P99 偏差≤5%；正式采样最大间隔30秒。
- 上传债务需两次间隔≥1秒的原生样本同时 bytes=0、ops=0；最终原进程退出、mount消失，包含冷验证产生的进程。
- 当前回放器有总操作、读写字节、总耗时和失败序号；逐操作延迟直方图、版本感知重复读取率待补。缺失项登记 unavailable；5秒样本和原采集耗时不能代替本次 I/O 延迟。
- 归档包括 config/plan/inventory、输入与版本摘要、原始输出、观测与阶段事件、正确性、失败/恢复、关闭回执。文件停止写入后生成成员清单及 SHA，独立下载核验。
- 交接保留 owner/lease/block/cell/stage、真实 PID/boot ID、路径、资源归属和下一操作。按[并行流程](../MAIN-SCALE.zh.md)归还锁与释放资源；原冻结结果保持原版本和输入身份。
