# BrewFS / JuiceFS：阿里云单机第一轮可比冒烟

日期：2026-10-25。执行起点 11:06:19 UTC（北京时间 19:06:19），最终测量在 11:30 UTC 前完成；本地归档首次完整核验于 11:36:57 UTC 通过，耗时 30 分 38 秒，在 75 分钟时间盒内。

本次打通了同一 Redis、同一 RustFS、同一合成负载在两个挂载点上的采集链路。最终主测试均执行 **115,718 个逻辑操作，I/O 错误 0，预期不匹配 0**。初始树、最终树、输入 SHA256 和实际读写字节均一致。仅报告观测数字，不据此作文件系统优劣判断。

负载是 **“按 AI trace 统计特征合成的等价负载”**，**不是事件重放**；“等价”仅指下述操作投影和受限统计特征，不代表保留全部原始语义。原 trace 缺少 inode→path 映射和 payload，不能恢复原始路径、内容、FD 生命周期、依赖和错误 oracle。

## 环境、部署和版本

沿用既有 `ssh osdi-smoke` 主机和跳板；没有创建实例、重分区、重新 mkfs、双机、迁移或故障注入。现有 6379 Redis 保留；安装器额外创建的 BrewFS Redis 监听 6380。

| 项目 | 观测值 |
|---|---|
| 系统 | Ubuntu 24.04；Linux `6.8.0-139-generic`，x86_64 |
| CPU | 8 vCPU；Intel Xeon Platinum，KVM；4 core × 2 thread |
| 内存 | `/proc/meminfo`：31,667,340 KiB，约 30.20 GiB |
| 磁盘 | 系统盘 40 GiB；`/data` 100 GiB XFS，`reflink=1` |
| BrewFS | `0.1.3`；commit `0a731dbea3a84d0c6fa27a0a6b962c8b4b576fab`，dirty=false |
| JuiceFS | `1.4.1+2026-07-30.0b90c7d` |
| Redis | `7.0.15`；安装器复用已安装的 `/usr/bin/redis-server` 二进制启动独立服务 |
| RustFS | `1.0.3`；commit `331fb896285a7686f927baf8d9d4dc2d3d3a10a9`；musl release |
| Rust / Cargo | 已安装 Rust/Cargo 1.99 系列，准确 verbose 输出见 environment.json |
| 回放器 | 仓库 `e9755b040bda2732de8afbab6b1663b3017ac6e3` 中 `tools/fuse-replay`；`cargo build --release --locked` |
| FUSE / Docker | `/dev/fuse` 可用；Docker 已安装，本次后端由安装器生成的 systemd 服务运行 |

安装路径遵循 [官方 Quick Start](https://github.com/brewfs/brewfs#quick-start)，保存了[官方安装器原文快照](install_brewfs_single_node.sh)。云端 GitHub raw 连接失败后，经 Windows 下载再 scp。云端 BrewFS 二进制下载超时后，同样传输官方发布文件和发布方 SHA256，由未修改的官方安装器通过 `file://` 安装。RustFS 日志冲突做了一处 unit 配置修复，详见失败表。**未使用 `242af5f` 源码回退，也未从源码构建 BrewFS。**

| 挂载 | 元数据 | 对象存储 | 数据隔离 |
|---|---|---|---|
| `/mnt/brewfs` | `redis://127.0.0.1:6380/0` | `http://127.0.0.1:9000` | bucket `brewfs-data` |
| `/mnt/jfs` | `redis://127.0.0.1:6380/1` | 同一个 RustFS 进程、同一个 9000 endpoint | bucket `jfs-smoke-20261025`，对象前缀 `jfs-smoke-20261025/` |

RustFS console 为 9001。两个 `mountpoint` 命令均退出 0，且两边均完成 21 字节写入、fsync、重新读取和 SHA256 校验。原始证据见 [JuiceFS 初始化记录](results/juicefs-setup.json)、`results/*-read-write-check.json` 和 [最终环境记录](results/cleandiag/environment.json)。

`brewfs info` 默认调用曾因 SSH 的 `XDG_RUNTIME_DIR` 与 systemd 服务的 registry 根目录不同而失败。实际成功命令：

```bash
env -u XDG_RUNTIME_DIR /data/bin/brewfs info /mnt/brewfs
```

成功输出包含 `mount_point: /mnt/brewfs`、`version: 0.1.3`、`meta_backend: redis-meta-store` 及 capabilities；[成功原文](results/brewfs-info-shared-runtime.log)和[默认环境失败原文](results/brewfs-info-no-argument.log)均保留。registry 选择规则也核对了对应 release 的 [runtime.rs](https://github.com/brewfs/brewfs/blob/0a731dbea3a84d0c6fa27a0a6b962c8b4b576fab/src/control/runtime.rs)。

参数采用安装器 balanced 配置并记录差异：BrewFS read/write memory 各 1 GiB、总预算 2 GiB，read/write SSD 各 8 GiB，FUSE workers=4，`commit_before_upload`；JuiceFS buffer=2048 MiB、cache=8192 MiB，默认关闭 `--writeback`，禁用后台 metadata backup。两边 compression=none。两者缓存结构、TTL、预取和写回语义并未完全对齐，不能称为等缓存/等持久化策略基准。

工作负载、后端数据、Redis AOF、缓存、构建 target、输入和结果均在 `/data`。最初 Cargo registry 约 20 MiB 曾写入 `/root/.cargo/registry`，随后移至 `/data/cargo/registry`，原路径改为软链接；后续显式指定 `CARGO_HOME=/data/cargo`。systemd unit、OS journal、临时控制 registry 等系统管理文件仍在系统路径；后台自动更新也独立修改了 OS 文件。这些例外如实保留，不宣称系统盘零写入。Windows 源目录只读，所有本次本地输出均在 `experiments/cloud-smoke/`。

可复核运行文件 SHA256：

| 文件 | SHA256 |
|---|---|
| 官方安装器快照 | `5ba6d1006ccb3d82667282ac46a1a0bb74479ba998750a0194bbc15219acfd7c` |
| BrewFS 发布二进制 | `b2717201b69ec071d0ca0a202e31029ac48f235d2743cbd55b8e822e1e806162` |
| RustFS 实际二进制 | `c14fd1216f99efba9cac4bb469a5fa31ea9e461b82b79a28b69280e057613c93` |
| 原版 fuse-replay | `414275189746222cf55bbe311ca3f7208051e0f23775ed7792eb99fbd685cb50` |
| 独立延迟诊断副本 | `dd3d68748e3e68504f477ea95fbf2762e745487d468285da77beea7d5d0becc7` |

RustFS 下载 URL 含 `latest`，安装器未找到其发布 checksum；不能保证未来该 URL 返回相同文件。本次版本、二进制哈希和云端 `/data/bin/rustfs` 副本才标识实际运行对象。BrewFS 则核对了[发布方 checksum](results/brewfs-publisher.sha256)。

## 源 trace 统计与转换边界

源：用户指定的 Windows `.../agent-fuse-swebench/events.jsonl`，557,135,223 字节（531.33 MiB），**1,138,884 条，8 个 run**；Windows 与云端扫描 SHA256 均为：

`4a9e1275439c36540202a980240e70751b23e95330628aed47dcbc1f26461544`

按 `(task, agent, workload)` 分组，每组恰好一个 init。[trace-statistics.json](results/trace-statistics.json)保存每个 run 的完整 method 混合、状态计数、读写长度/offset 直方图与分位数、引用 inode/name/fh 去重数及请求区间重叠峰值。

| run | task（省略仓库前缀） | 事件数 | 引用 inode ID 数 | 同时在途请求峰值 | 读长度 p50/p99 B | 写长度 p50/p99 B |
|---|---|---:|---:|---:|---:|---:|
| 0 | astropy-13398 / exploratory | 634,726 | 10,176 | 17 | 16,384 / 131,072 | 4,096 / 131,072 |
| 1 | django-10097 / core | 129,476 | 9,274 | 18 | 4,096 / 131,072 | 6,109 / 131,072 |
| 2 | matplotlib-13989 / core | 43,235 | 4,518 | 10 | 12,288 / 131,072 | 4,084 / 131,072 |
| 3 | pytest-5787 / exploratory | 54,893 | 2,209 | 15 | 8,192 / 131,072 | 5,636 / 121,869 |
| 4 | flask-5014 / core | 8,542 | 367 | 18 | 4,096 / 131,072 | 4,084 / 85,765 |
| 5 | sympy-12489 / exploratory | 232,136 | 5,260 | 13 | 16,384 / 131,072 | 4,307 / 124,126 |
| 6 | requests-1142 / core | 8,892 | 225 | 6 | 8,192 / 131,072 | 2,325 / 24,015 |
| 7 | sphinx-10323 / core | 26,984 | 2,095 | 8 | 12,288 / 131,072 | 1,108 / 131,072 |

所有 run 都缺少显式 stream/thread ID。区间重叠峰值是并发请求代理，**不是已恢复的线程或逻辑流数**；fh 去重数也不是并发数。inode 引用数含目录和 parent，只是工作集代理，不能给出真实文件树大小和活跃数据字节。

最终选择 run 0，固定 seed=20261025。转换器将受支持 method 的比例用最大余数法分配到 100,000 个操作单元，确定性打乱；按经验联合分布采样 `(length, offset)`。构造 4 个独立 stream、每流 16 文件，每文件 4 MiB，共 64 文件 / 256 MiB 初始数据。初始内容由 SHAKE256 生成，两边逐文件相同。写 payload 使用 `fill=65+stream`，不来自原始 Agent。

| 源 method | run 0 原计数 | 合成操作单元 | 投影 |
|---|---:|---:|---|
| lookup | 205,538 | 44,719 | 对已存在合成路径 stat |
| getattr | 56,946 | 12,390 | stat |
| read | 60,335 | 13,127 | pread |
| write | 18,210 | 3,962 | pwrite，合成 fill |
| open | 38,341 | 8,342 | open + close |
| create | 7,221 | 1,571 | 新文件 open/create + close + unlink |
| readdirplus | 61,219 | 13,320 | readdir，校验 16 个目录项；不还原批量属性 |
| rename | 3,069 | 668 | rename 后恢复原名 |
| fsync | 234 | 51 | fsync |
| unlink | 7,006 | 1,524 | 辅助 create/close 后 unlink |
| mkdir / rmdir | 764 / 735 | 166 / 160 | mkdir + rmdir |

这些 method 覆盖 459,618/634,726 个源事件；其余 175,108 条仍在统计中，但未逐条执行。flush/release/opendir/releasedir、xattr、forget、setattr、link、symlink 等没有无损映射。源 run 0 的状态为 ok=463,947、error=163,213、incomplete=7,566；合成操作全部预期成功，**不保留源错误率及 negative lookup 局部性**。

实际 JSONL 包含 **115,718** 个逻辑操作，即 100,000 个投影单元加 15,718 个辅助操作：open 11,501、close 11,501、stat 57,109、readdir 13,320、pread 13,127、pwrite 3,962、unlink 3,095、rename 1,336、mkdir/rmdir 各 326、fsync 115。辅助 open/close/fsync 和命名恢复均包含在计时及 ops/s 内。

源 run 0 读长度 p50/p99 为 16,384/131,072 B，写长度为 4,096/131,072 B；合成值相同。长度没有发生 clamp。源读 offset p99=33,247,232 B，写 offset p99=28,704,768 B；受每文件 4 MiB 上限影响，2,209 次读、1,042 次写的 offset 被截到合法文件范围，合成 offset p99 分别为 4,169,728/4,193,280 B。工作集缩小和 offset 截断是明确的冒烟限制。

最终 [workload.jsonl](results/workload.jsonl) **8,002,409 字节**，小于 50 MB；SHA256：

`7a1b00f394734d7eacb92660ef263083baf630bbcc56c293cae3aa65f9f85825`

[synthesis.json](results/synthesis.json)记录映射、遗漏、直方图、clamp 和辅助操作。完整重新生成已核对操作字节逐字节一致；曾修正 unlink-cycle 辅助 open 的统计标签，测量 JSONL 未改动，旧统计另存。[复验记录](results/conversion-reproducibility.json)中 100,000 + 15,718 = 115,718。

## 最终主测试：workers=4、asap、timing off

先在各自新目录建立初始树并完整 fingerprint；该步骤读取全部初始内容，会暖缓存。两边使用同一 trace 头部指纹，执行前自动验证。每个文件最后显式 fsync/close。一次只运行一个 replay 进程；主测试使用原版回放器。

| 文件系统 | 尝试/成功 | 预期错误 / I/O 错误 / 不匹配 | execution s | logical ops/s | p50 / p99 |
|---|---:|---:|---:|---:|---|
| BrewFS | 115,718 / 115,718 | 0 / 0 / 0 | 19.569439 | 5,913.20 | 不采集：timing off |
| JuiceFS | 115,718 / 115,718 | 0 / 0 / 0 | 55.080663 | 2,100.88 | 不采集：timing off |

两边实际读取 **598,482,944 B**、写入 **61,899,097 B**；short read/write 均为 0。`logical ops/s = attempted/execution_seconds`，不是底层 FUSE IOPS；内核缓存与回放器的安全路径解析会改变底层请求数。

| 主测试回放进程 | user CPU s | system CPU s | CPU %（单核=100%） | 最大 RSS KiB |
|---|---:|---:|---:|---:|
| BrewFS workload | 2.425134 | 3.878386 | 28.90 | 47,040 |
| JuiceFS workload | 2.376080 | 5.110219 | 13.14 | 48,192 |

CPU/RSS 来自 `wait4`，含 timeout 包装器、子进程解析、准备和结果输出，分母分别为完整进程 wall time 21.815073/56.994963 s；不是仅 execution 阶段 CPU。

服务进程也留有 100 ms `/proc` 原始采样。主测试进程生命期内：

| 窗口 | 进程 | CPU 增量 s | 采样 RSS 峰值 KiB |
|---|---|---:|---:|
| BrewFS/off | BrewFS | 33.27 | 1,232,112 |
| BrewFS/off | RustFS | 32.49 | 1,000,100 |
| BrewFS/off | 6380 Redis | 4.00 | 24,660 |
| JuiceFS/off | RustFS | 183.43 | 1,150,800 |
| JuiceFS/off | 6380 Redis | 10.22 | 24,768 |

`/proc` collector 未匹配到 JuiceFS daemon 的 argv 形式，因此该进程没有对应采样峰值，不能填成 0。替代证据来自原始 Prometheus 前后快照：JuiceFS/off 的 `juicefs_process_cpu_seconds_total` 增量 **79.37 s**；RSS 起点/终点 **1,120,002,048 / 1,165,877,248 B**，仅为端点、不是峰值。此窗口延伸至 replay 退出后 5 秒，与上表 `/proc` 窗口不同。collector 后续版本已归一化 argv 分隔形式；没有回填或伪造本次原始样本。

## 后端 PUT 字节观察

快照位于 replay 启动前和进程退出后 5 秒，排除初始树生成/预热。保存完整 `.stats` 和 JuiceFS `/metrics`，只在汇总时做差。

| 主测试 | 计数器 | before B | after B | 增量 B |
|---|---|---:|---:|---:|
| BrewFS | `brewfs_s3_put_bytes_total` | 611,566,074 | 673,481,007 | **61,914,933** |
| JuiceFS | `juicefs_object_request_data_bytes{method="PUT"}` | 10,536,788,016 | 14,750,250,229 | **4,213,462,213** |

这是**客户端暴露的后端 PUT payload 计数器**，包含各自后端表示/元数据等 PUT，未抓取 RustFS 服务端逐请求审计或网络 wire 字节。两个指标的定义和实现范围不完全相同；不把该差值直接解释为上传放大或性能结论。BrewFS 后快照的 dirty/live-dirty/recent-pending-upload/remote-upload-inflight 四个字节 gauge 均为 0；JuiceFS 未启用 writeback，trace 也包含末尾 fsync。

## 独立延迟诊断：timing op

原版回放器在 timing off 没有逐操作样本；原版 timing op 也只有总和/最大值。因此保留原版主测试，另对源码副本用 [instrument_latency.py](instrument_latency.py)增加样本保存，再以相同负载、独立初始树采集 `cleandiag`。没有修改仓库 `tools/fuse-replay`。

| 文件系统 | 样本数 / 不匹配 | p50 ms | p99 ms | 诊断 execution s | 诊断 logical ops/s |
|---|---:|---:|---:|---:|---:|
| BrewFS | 115,718 / 0 | 0.099359 | 4.032742 | 15.652634 | 7,392.88 |
| JuiceFS | 115,718 / 0 | 0.054486 | 42.352905 | 67.137454 | 1,723.60 |

p50/p99 是全部逻辑操作（含辅助操作）的 nearest-rank，计时区间含安全路径解析；不是单个 FUSE opcode 或应用任务延迟。诊断新增样本分配/保存开销，数字不归入 timing off 主测试，也不从单次 on/off 数字推算计时开销。每个纳秒样本都在 `results/cleandiag/*-op.result.json` 中。

四个最终汇总格的初始指纹均为：

`79132702660373e9f2a47f36850237cbc27c22553e0a88779def169b9ef9db54`

最终树指纹均为：

`e830540dc17645743f19dc72cfc3fc9ae795e72845682ccd39e2e91d13bdc8c0`

最终核验包括同一 trace、初始/最终树、操作数、读写字节、workers/pace、主测试 timing off、服务进程存活、JuiceFS client start-time 不变、无错误和 50 MB 限额；[summary.json](results/summary.json)中全部检查通过。

## 失败、受干扰数据和处理

| 项目 | 原始错误 / 事实 | 处理和留存 |
|---|---|---|
| 云端拉 installer | `curl: (35) Recv failure: Connection reset by peer` | Windows 下载官方原文并 scp；错误在 preflight-errors.json |
| 云端拉 BrewFS | `curl: (28) Operation timed out after 300000 milliseconds with 13238182 out of 68740096 bytes received` | 同一官方文件经 Windows 传输，发布 checksum 一致；保留 install.log 和 install-staged.log |
| 操作者更新运行中 wrapper | `install-stack.sh: line 15: ewfs_single_node.sh: command not found`，并触发重复下载 | 终止旧 wrapper/download；旧 wrapper 最终退出码未知，不伪填；新调用 exit=0 |
| RustFS 1.0.3 启动 | `Log sink conflict: stdout and rolling log resolve to the same file /data/brewfs-logs/rustfs.log` | 仅将 unit stdout 改到 `/data/brewfs-logs/rustfs-stdout.log`；保留修复前后 unit 与 fatal 日志 |
| BrewFS info | `no brewfs instance for mount point: /mnt/brewfs` / `no brewfs instances found` | 取消 SSH XDG_RUNTIME_DIR 后成功；两种输出均保留 |
| 宿主机 unattended-upgrades | 11:15:47 UTC 开始；日志显示 11:20:27 全部升级完成；期间 Redis/BrewFS 被重启，BrewFS 一次启动报 `BusyLoadingError: Redis is loading the dataset in memory` | 初轮四格全部归档 `initial/`，其逻辑操作 oracle 全通过，但进程存活检查失败，未作为最终主表；等待更新完成、临时停 timer 后新目录补跑，结束恢复 timer |
| 合成统计计数 | unlink-cycle 辅助 open 漏计为 scaffold；操作本身已存在 | 修正元数据统计；重新完整转换并核对 workload 操作字节及带指纹 SHA 完全不变；保存旧 synthesis |
| 第一次补跑诊断 | 完整转换复验与 stable/op 诊断重叠，有一个 CPU 核的额外工作 | stable/op 原样保留；最终延迟使用后来单独的 cleandiag/op |
| 汇总启动过早 | cleandiag measurement 文件尚未完成，首次 audit 退出 1 | 原始 traceback 保存在 stable/audit-early.log；等待数据完成后 audit 退出 0 |
| JuiceFS `/proc` 采样 | argv 匹配未命中，缺少 daemon 的 `/proc` 样本 | 用已保存的 client Prometheus 前后快照汇报 CPU 与端点 RSS；明确不是峰值 |

Windows 无 rg/Python、辅助 SSH 引号、首次未压缩传输取消等辅助失败均见 [preflight-errors.json](results/preflight-errors.json)。首轮与中间诊断没有删除或并入最终稳定样本。

最终计数依据：主测试取 `results/stable/*-off`，延迟取 `results/cleandiag/*-op`；`summary.json` 每行的 `raw_results_directory` 指明来源。`initial/summary.json` 的失败检查仍明确为 false，不把 `valid=true` 的操作 oracle 误当成环境未受干扰。

## 限制与交付状态

- 单台抢占式机器、每个最终格仅一次进程样本，没有独立重复、置信区间或推广性结论。后台更新影响过初轮；最终窗口服务身份检查通过，但不是专用物理机隔离实验。
- 一个 run 的缩小合成工作集、4 条人为独立流、打乱顺序、成功路径 oracle，不恢复 Agent 任务、原始依赖、原 payload、negative lookup、原错误比例或原 FUSE 成本。
- 完整初始树校验会暖缓存；没有严格冷缓存屏障。两套客户端及同一 Redis/RustFS 共用 CPU、内存和 `/data`；缓存、TTL、预取、写回与后端表示仍不同。
- ops/s 是逻辑 POSIX 工作量；stat、readdir 等含安全解析/额外 syscall。源 trace 的 latency_ms 未作为 pacing 或输出测量值使用。
- CPU/RSS 和 backend 字节各有上述采集窗口；PUT 指标不是 wire bytes。fsync 和最终 hash 验证不等同于断电持久性验证；本次未做故障注入。

复现入口：[commands.md](commands.md)、[convert_trace.py](convert_trace.py)、[run_smoke.py](run_smoke.py)、[audit_results.py](audit_results.py)。原始 JSON、stdout/stderr、后端快照、进程采样、环境和 incident 日志均在 [results/](results/)。云端结果实际位于 `/data/results/2026-10-25`，实验路径下的 results 为其软链接。

[cloud-manifest.json](results/cloud-manifest.json)与[本地归档校验](results/local-verification.json)核对了 129 份云端结果/输入文件和 10 份自动化脚本的 SHA256；所有 JSON 均可解析，报告本地链接存在，交付 trace SHA 与测量值一致。Python 编译检查和最终 audit 均退出 0；初轮 audit 退出 1 的记录保留。

主机和挂载保持运行，未删除任何云端初始树/结果，未关闭实例。两个 apt timer 恢复 active，相关前/中/后状态已存档。除 `experiments/cloud-smoke/` 外未修改仓库文件；已有两份 planning 未跟踪文件保持原状。

后续受控微实验见 [JuiceFS 写放大机制验证](jfs-write-amplification.md)。
