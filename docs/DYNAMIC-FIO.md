# Dynamic 与 fio 的隔离诊断

run-002 的 Dynamic 开启条件同时使用 BrewFS 的客户端 hint 和修改版 RustFS；关闭条件使用官方 RustFS 1.0.5。用这两个条件可以比较完整配置的表现。单独估计客户端 hint、RustFS 读量子或每盘同步并发时，要在同一修改版 RustFS 上固定其余设置。

`bench/cluster/diagnostics/dynamic_fio/run.sh` 在 A366 使用环回地址启动独立 Redis 和 RustFS，分别使用 37379 和 39000 端口。BrewFS 指标端口为 39567。每个条件都创建 S3 bucket、Redis 工作区和缓存目录。脚本从正式结果读取 fio 参数和挂载审计，在独立挂载中重新生成输入。正式实验的输入、结果、配置、采集器和对象服务均不参与运行。独立后端的四个卷位于同一台机器，适合辨别机制；四节点直连网络下的吞吐和尾延迟需要另外测量。

每个条件仍采用 8 个 fio 作业、2 GiB 文件集、1 MiB 顺序请求及 4 KiB 随机请求。随机读写共 64 MiB。脚本保存 fio 进程总耗时、fio 内部 I/O 耗时、P99 完成延迟、客户端对象指标和挂载配置。写入结束后等待上传完成，再重新挂载并直接逐字节比较测试工具选定的内容。随机写覆盖每个文件的全部 256 MiB；顺序写和只读条件检查所有文件长度及按测试工具选取的区段。

## 复测

以下命令在 A366 的 Linux 环境中执行，需要具备创建临时 systemd 单元、FUSE 挂载和 Docker 容器的权限。等 A366 的共享磁盘空闲后启动，各条件顺序执行。

```bash
scripts=/srv/ax-cluster/direct/repo/bench/cluster/diagnostics/dynamic_fio
export DIAG_ROOT=/srv/ax-cluster/diagnostics/dynamic-fio-repeat
# 使用修改版 RustFS 的现有镜像，固定客户端读取策略，再切换 hint。
bash "$scripts/run.sh" image 1 auto true 512 whole-object seqread randread
bash "$scripts/run.sh" image 1 off  true 512 whole-object seqread randread
# 保持客户端配置不变，只关闭 RustFS 读量子。
bash "$scripts/run.sh" image 1 auto false 512 whole-object seqread randread
python3 "$scripts/report.py" "$DIAG_ROOT"
```

使用同一候选 RustFS 二进制时，将第一个参数 `image` 替换为其绝对路径，再分别传入同步并发 1 和 8。若同时启动独立 OpenTelemetry Collector，可使用本目录的 `otel.yaml`，并给脚本设置 `DIAG_OTEL=1`，保存每轮前后的服务端指标；该采集器只监听本机 34318 和 39464 端口。每个服务端条件使用一个新采集器实例，避免同名时间序列残留。使用 BrewFS 候选时，设置 `DIAG_BREWFS_BIN` 为候选的绝对路径。默认使用原 8 线程 BrewFS。传入 `DIAG_FORMAL_RUN` 可选择其他已经完成 fio 的正式运行目录，作为只读配置来源。

`repeat_when_idle.py --candidate <RustFS二进制> --out <新诊断目录> --execute` 等待正式套件全部完成、相关诊断服务退出和本机磁盘空闲，然后顺序执行每盘 1／8 个 fsync 线程的四种 fio 负载。固定现用 BrewFS、512 MiB 读内存、客户端 hint 和读量子开启，使用整对象读取策略。状态保存到 `queue.json`，每项仍保存冷／热读、P99、对象指标与重挂载内容检查结果。不传 `--execute` 时只显示待执行命令。

## Run-002 的读取差异

同一组 2 GiB、8 个作业的顺序读取结果如下。冷缓存指新建客户端缓存目录；服务端缓存不在此条件中清空。

| 指标 | BrewFS Dynamic 开启 | JuiceFS Redis |
| --- | ---: | ---: |
| 冷读吞吐，含 fio 启动（MiB/s） | 635.08 | 816.71 |
| fio 内部冷读用时（秒） | 2.739 | 2.025 |
| 冷读进程总用时（秒） | 3.225 | 2.508 |
| 冷读 GET 次数 | 901 | 512 |
| 冷读 GET 字节 | 2,147,487,252 | 2,147,483,648 |
| 每次 GET 平均返回（MiB） | 2.273 | 4.000 |
| 后端物理读取字节 | 2,255,527,936 | 2,276,655,104 |
| 热读吞吐，含 fio 启动（MiB/s） | 1486.05 | 2486.31 |
| 热读 GET 次数／返回量 | 0／0 | 39／156 MiB |

原始结果位于 A366 的 `/srv/ax-cluster/runs/direct/run-002/01-io/01/r1/attempt-05/result.json` 和 `01-io/05/r1/attempt-02/result.json`；各目录另存 `fio-cold.json`、`fio-hot.json`、挂载参数与内容检查结果。

冷读差距为 22.2%，对应约 0.714 秒的实际 I/O 时间差。两者 fio 启动开销都约 0.48 秒。BrewFS 多发出 76% 的 GET，总下载量和后端物理读取量接近。优先检查对象切分、请求调度和预取衔接。GET 次数不能直接当作不同对象的数量，需要另行记录对象键和长度。

输入工具以 4 MiB 缓冲区写入，文件末尾执行同步。BrewFS 的 `dirty_slice_target_size=4 MiB` 是切片目标，源代码还允许空闲、存活时间和缓存压力触发提交，因此一次应用写入不一定对应一个 4 MiB 对象。复测应同时记录输入生成耗时、对象长度分布和切片提交原因，固定文件内容后观察输入切分对冷读的影响。对象长度、RustFS 纠删码块 B、读取量子 q 分别记录。

BrewFS 热读已经没有 S3 GET，仍存在吞吐差距，应检查客户端缓存读取、FUSE 返回数据和内存复制的耗时。当前 64 GiB 磁盘缓存已超过 2 GiB 输入；仅增加容量不能减少冷读首次下载，也没有直接解决热读处理开销。预取保持已选的 16 MiB 窗口、8 个任务；新的配置逐项比较冷读、热读、CPU 时间和 S3 请求。随机冷读总吞吐均约 8.66 MiB/s，BrewFS 的 P99 为 63.70 ms，JuiceFS 为 12.12 ms，仍需比较整对象与范围读取的请求数量和尾延迟。

在 A366 环回对照中，512 MiB 读内存、整对象读取、读量子开启的条件下，客户端 hint 开关对应的随机冷读为 14.31 和 14.25 MiB/s，P99 为 33.82 和 36.44 ms。使用 4 GiB 读内存时，顺序热读由 1249 提升至 1856 MiB/s，随机冷读由 14.31 降至 9.85 MiB/s。使用稀疏读取时，随机冷读 P99 为 6.06 ms，GET 下载量由约 2048 MiB 降至 810 MiB，请求数由 1476 增至 14369。四节点早期试跑中，放大请求数的代价抵消了节省的字节；正式配置继续使用自动读取策略。以上配置各测一轮，适合筛选下一轮复测条件。

固定稀疏随机读后，只切换 RustFS 读量子。关闭与开启分别为 9.85 和 14.52 MiB/s，P99 分别为 7.96 和 5.54 ms；两轮分别发起 14348 和 14347 次 GET，各下载约 809 MiB。固定相同读取策略与对象数据量，便于观察读量子对后端服务时间的影响。

## 同步任务等待

现有修改版 RustFS 的随机写复测为 1.067 MiB/s，P99 为 114.82 ms。BrewFS 发起 16360 次 PUT，上传 64.06 MiB，没有发起 GET。RustFS 记录了 66248 次同步任务，平均排队 45.58 ms，实际执行平均 3.55 ms。四节点正式运行中，修改版 RustFS 的随机写为 0.411 MiB/s，P99 为 308.28 ms；官方 1.0.5 为 1.515 MiB/s，P99 为 82.31 ms。两个版本的每盘同步任务并发分别为 1 和最多 8。候选保留原来的文件和目录同步操作，只改变同步队列每盘可同时运行的任务数。在 A366 使用同一候选二进制，固定读量子、客户端配置和后端拓扑，按 1、8、8、1 的顺序测试。以下为两轮的范围，吞吐包含 fio 启动时间。

| 负载 | 1 线程吞吐（MiB/s） | 8 线程吞吐（MiB/s） | 1 线程 P99（ms） | 8 线程 P99（ms） |
| --- | ---: | ---: | ---: | ---: |
| 64 MiB 随机写 | 1.00–1.04 | 2.32–2.45 | 114.82–126.35 | 40.11–43.25 |
| 2 GiB 顺序写 | 146.90–151.93 | 182.79–183.32 | 10.94–11.86 | 11.47 |

随机写平均同步排队从 49.52–49.73 ms 降至 10.77–11.29 ms，单次同步任务执行时间从 3.64–3.79 ms 升至 11.99–12.67 ms；排队与执行的合计仍下降。每轮随机写均发起 16360 次 PUT、上传 64.06 MiB；顺序写均发起 512 次 PUT、上传约 2048 MiB。所有写入条件均无 GET，均在上传完成后重新挂载并通过内容检查。

候选提交为 `b525408eec9fc748a800f31d3d7ac153c8015025`。`RUSTFS_DISK_FSYNC_WORKERS` 控制每盘同步队列的并发数，支持 1–8，默认 8；前台与隔离队列各保持 1 个工作线程。该参数在进程内首次使用时读取，改变它后需重启候选服务。进程级 `RUSTFS_RUNTIME_FSYNC_BLOCKING_THREADS` 不替代此设置。此次改动保留文件与目录同步调用，直接改善写入等待；独立冷读不执行这一同步队列的写入流程。四节点复测需同时检查随机写吞吐、P99、同步执行时间和磁盘繁忙程度，确定适合实际磁盘的并发数。

A366 上可直接复测候选，测试环境与正式服务分开：

```bash
d=/srv/ax-cluster/diagnostics/dynamic-followup
export DIAG_ROOT=/srv/ax-cluster/diagnostics/dynamic-fsync-repeat
for workers in 1 8; do
  bash "$d/package/run.sh" "$d/rustfs-candidate" "$workers" auto true 512 whole-object randwrite seqwrite
done
python3 "$d/package/report.py" "$DIAG_ROOT"
```

## BrewFS 磁盘缓存范围读取

在 A366 将无额外缓存校验的磁盘范围读合并为一次阻塞任务后，交替测试两轮。现用版本的顺序冷读为 590–660 MiB/s、热读为 1193–1407 MiB/s，候选为 487–500 和 961–1125 MiB/s。两轮顺序热读均无 S3 GET。随机冷读分别为 13.53–13.98 和 12.10–15.60 MiB/s；候选随机热读为 57.11–57.95 MiB/s，现用版本为 48.55–52.18 MiB/s。在交替对照中，候选减少了顺序热读时 BrewFS 线程的调度切换，但增加了累计 CPU 时间。保留现用版本和 512 MiB 默认读缓存。
