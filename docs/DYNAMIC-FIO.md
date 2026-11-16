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

## Run-002 的读取差异

BrewFS Dynamic 开启时，顺序冷读为 635 MiB/s，JuiceFS 为 817 MiB/s。两者均下载约 2 GiB，BrewFS 发起 901 次 GET，JuiceFS 发起 512 次。BrewFS 的顺序热读降至零 GET，吞吐为 1486 MiB/s，JuiceFS 为 2486 MiB/s。对象存储无法解释这轮 BrewFS 热读差距；应检查客户端缓存、FUSE 和数据复制成本。随机冷读的总吞吐均约 8.66 MiB/s，BrewFS 的 P99 为 63.70 ms，JuiceFS 为 12.12 ms。

在 A366 环回对照中，512 MiB 读内存、整对象读取、读量子开启的条件下，客户端 hint 开关对应的随机冷读为 14.31 和 14.25 MiB/s，P99 为 33.82 和 36.44 ms。使用 4 GiB 读内存时，顺序热读由 1249 提升至 1856 MiB/s，随机冷读由 14.31 降至 9.85 MiB/s。使用稀疏读取时，随机冷读 P99 为 6.06 ms，GET 下载量由约 2048 MiB 降至 810 MiB，请求数由 1476 增至 14369。四节点早期试跑中，放大请求数的代价抵消了节省的字节；正式配置继续使用自动读取策略。以上配置各测一轮，适合筛选下一轮复测条件。
