# CephFS 与 s3fs 基线实验套件

本目录为 `cephfs`（Ceph Squid `19.2.3`）与 `s3fs`（`v1.93` + RustFS S3）提供独立、完整、与主线 AX 四组实验对齐的测试配置、运行时驱动、编排脚本与单元测试。

所有代码、配置和远端运行路径均与主线 `/srv/ax-cluster/direct/` 完全隔离（远端代码位于 `/srv/ax-cluster/baselines/`，运行产物位于 `/srv/ax-cluster/runs/baselines/run-baselines-001/`），不修改主线跟踪文件，也不干扰正在执行的主线任务。

---

## 1. 四组实验设计（共 28 次运行）

主配置文件见 [baseline-suite.json](configs/baseline-suite.json) 与 [baseline-mount-profiles.json](configs/baseline-mount-profiles.json)，固定种子 `2026111501`、每条件运行 `1` 次、单挂载资源上限严格对齐主线（`ax-clients.slice` 下第 2~4 组 `MemoryMax=4 GiB` / 本地磁盘预算 `8 GiB`，第 1 组 `fio` 资源档 `MemoryMax=16 GiB` / 本地磁盘预算 `64 GiB`、`MemorySwapMax=0`、最大并发 `64` Agent），全程采用**直接字节比较**，零哈希/零校验和：

| 实验组 | 工作负载与主线对齐参数 | `cephfs` 实现说明 | `s3fs` 实现说明 | 运行次数 |
|---|---|---|---|---:|
| `01-io` | 8 个 `fio` job，`direct=1`，2 GiB 工作集（`8 × 256 MiB`）；1 MiB 顺序读/写（2 GiB）、4 KiB 随机读/写（64 MiB）；每累计 64 MiB 执行 `fsync`（顺序写 `fsync=8`、随机写 `fsync=2048` + `end_fsync=1`）；冷读 + 同挂载第二遍热读；跨机（A355）冷挂载 `c1-io-helper verify-files` 逐字节检查 | `ceph-fuse` 独立 `fio` cgroup 挂载（`MemoryMax=16 GiB`、`client_oc_size=2 GiB`、`noatime`），原生支持 `O_DIRECT` | `s3fs` 独立 `fio` cgroup 挂载（`MemoryMax=16 GiB`、64 GiB 本地缓存预算含 `max_dirty_data=8192 MiB`），启用 `enable_unsigned_payload`、`direct_io`（满足 `fio direct=1`）并将 `tmpdir`/`use_cache` 置于 NVMe `/srv/ax-cluster` | 8（4+4） |
| `02-fork` | 100,000 个 4 KiB 父目录文件；创建 `1 / 16 / 64` 个子工作区 + `direct-parent` 直接父目录对照；冷读 10,000 文件 + 覆盖写 1,000 文件；跨机读回与父工作区隔离验证 | **无原生 CoW fork**：通过独立 `ceph-fuse` 读者/写者挂载执行全量目录树复制 + `wait-upload-zero`，完整统计 `fork_copy_cost` | **无原生 CoW fork**：不使用服务端 `CopyObject` 捷径，通过独立 `s3fs` 读者/写者挂载执行端到端 FUSE 全量数据复制 + 后台上传排空，完整统计 `fork_copy_cost` | 8（4+4） |
| `03-packed` | 100,000 个 4 KiB 小文件（每目录 1,000 文件；8 worker 准备上传并计时至跨机可读验证）；32 个并发读者挂载跨 6 台服务器读取 2 遍（`c3-loader --verify-bytes 1`） | 独立 `ceph-fuse` 读者挂载 + OSD/MDS 计数采集 | 独立 `s3fs` 读者挂载（开启本地元数据/数据缓存预算与 `listobjectsv2`）+ RustFS 指标采集 | 4（2+2） |
| `04-agent-replay` | 8 类真实 Agent trace，`1 / 8 / 16 / 64` 个并发 Agent（1 Agent 顺序跑 8 任务），`full-fuse-replay --performance` + 相同种子 `1~7s`（均值 `4s`）工具调用边界等待；100% 任务跨机 `trace-verify-and-index verify-tree` 逐字节验证 | 基于父目录经 `ceph-fuse` 全量拷贝生成子工作区并统计 `fork_copy_cost`，随后按 6 机槽位并发挂载回放 | 基于父前缀经 `s3fs` 全量拷贝生成子工作区并统计 `fork_copy_cost`；回放中对 `link()` 返回 `EOPNOTSUPP` 自动回退为 FUSE 文件拷贝并统计 `hardlink_copy_cost` | 8（4+4） |

---

## 2. 与主线 AX 四组实验的对齐核查与无法 1:1 对齐项说明

### 2.1 已 1:1 严格对齐的部分

1. **全局运行参数与随机性**：与主线 [experiment-suite.json](../../configs/experiment-suite.json) 一致，`repetitions=1`、随机种子 `2026111501`、`measurement_concurrency=1`（同一性能比较组串行互斥执行）。
2. **机器拓扑、槽位分配与跨机校验双射**：完全复用 6 台机器（`eth0` 直连 IP）、`placements()` 权重 `[16, 11, 6, 13, 13, 5]` 槽位分配算法，以及 `VERIFY_HOST_MAP` 跨机冷挂载双射（`A366->A601->A635->A355->A366`、`A672<->A368`）。
3. **单工作区 cgroup 与本地资源上限**：所有 FUSE 客户端及负载进程均运行于 `ax-clients.slice` 独立 `systemd-run` 单元内，严格对齐主线 [mount-profiles.json](../../configs/mount-profiles.json) 施加 `MemorySwapMax=0`、`MemoryAccounting=yes`、`CPUAccounting=yes`、`IOAccounting=yes`、`TasksMax=16384`、`LimitNOFILE=1048576`，第 2~4 组使用 `MemoryMax=4 GiB` / `8 GiB` 本地磁盘预算，第 1 组使用 `fio` 资源档 `MemoryMax=16 GiB` / `64 GiB` 本地磁盘预算（含 `8 GiB` 写暂存上限），且进程命令行零密钥暴露。
4. **测试工具、输入数据与校验方式**：100% 复用同一套输入集（`/srv/ax-cluster/input/traces`、`smallfiles-v2`）与编译产物（`fio`、`c1-io-helper`、`bench.cluster.c4_workload`、`c3-loader`、`full-fuse-replay`、`trace-verify-and-index`）；`04-agent-replay` 任务洗牌、`start_delay_ms`（`0..30000ms`）与 `trace-think-plan-v2`（`1000..7000ms` MT19937 可行分配洗牌）算法与主线逐比特一致；全程采用**跨机冷挂载直接字节比较**，禁用任何哈希与校验和。
5. **计时窗口与指标沉淀约定**：对齐主线阶段标记前后 `2.2s` 指标导出沉淀等待，以及 `wait-upload-zero` 连续两次零待传观测间隔 `>= 1.0s` 的排空判定（`upload_drain_seconds` 取首次归零时刻）。`--check` 小数据验证参数（如 `01-io` 的 `8 × 4 MiB` 文件 / `16 MiB` IO、`02-fork` 与 `03-packed` 的 `32` 文件、`04-agent-replay` 的单 trace `1` Agent）也与主线 `run_suite.py` / `suite_groups.py` 对齐。

### 2.2 因系统架构与语义限制无法 1:1 对齐的部分（共 7 项）

以下部分源于 `CephFS` 与 `s3fs` 自身的架构与协议边界，客观上无法与 `BrewFS` / `JuiceFS` 做机械等同，套件已做显式等价处理并单列开销统计：

| 编号 | 涉及实验组 | 主线（`BrewFS` / `JuiceFS`）行为 | `CephFS` / `s3fs` 无法对齐的根因 | 本套件的处理方式与单列统计字段 |
|---|---|---|---|---|
| **1** | `02-fork`、`04-agent-replay`（及 `01-io` 子层准备） | 通过元数据级写时复制（`BrewFS fork`、`JuiceFS clone`、`JuiceFS Overlay`）在毫秒级创建子工作区，初始零数据拷贝；`01-io` 也在父卷 fork 出的子可写层上测试 | 标准 POSIX 目录模式的 `CephFS` 与对象前缀挂载的 `s3fs` **均无原生 CoW 工作区 `fork`/Overlay 能力** | `02-fork` 与 `04-agent-replay` 禁用服务端 `CopyObject` 捷径，统一通过受控冷态 FUSE 读挂载 + FUSE 写挂载执行**端到端全量目录树复制**并等待上传排空，在 `fork-copy-cost.json` 及 `result.json` 的 `fork_copy_cost` / `fork_copy_summary` 中完整单列耗时、CPU/峰值内存、新增存储字节/对象数与后端物理 IO；`01-io` 直接在独立目录/独立桶前缀上准备并清理 `2 GiB`（`8 × 256 MiB`）文件 |
| **2** | `01-io`、`03-packed`、`04-agent-replay` | 主线对 `BrewFS` 扫描块大小 `B=auto/64KiB/4MiB`，并在第 1、3、4 组对比 `Dynamic` 开启（修改版 RustFS `:9000` 接收 EC 块大小提示）与关闭（原版 RustFS `1.0.5` `:19000`）；主线中的 `JuiceFS` 基线亦仅运行固定默认块与 `Dynamic=on` | `CephFS` 使用自有 RADOS + BlueStore 后端（默认固定 `4 MiB` 对象条带，不经过 S3/RustFS，`dynamic=n/a`）；`s3fs` 采用 **1 个文件 = 1 个完整 S3 对象**映射，无客户端分块层，也不支持 `BrewFS` 的 EC block-size hint 协议 | 与主线中 `JuiceFS` 基线的矩阵裁剪一致：`CephFS` 与 `s3fs` 均不展开 `B=64KiB/4MiB` 扫描，也不重复运行无协议差异的 `Dynamic=off` 对照（`s3fs` 默认访问 `:9000` 标准 S3 接口，套件保留 `dynamic=off` 切至 `:19000` 的底层开关供按需排查） |
| **3** | `03-packed` | 主线对比 `BrewFS Packed`（整包归档格式 + `packed_cache on/off` + 8 类内存池 + TiKV 可变元数据）以及 `JuiceFS` 的 `Redis` vs `TiKV` 元数据引擎对比 | `CephFS` 为 Ceph MDS + RADOS 逐文件存储，`s3fs` 为逐文件独立 `4 KiB` S3 对象（每文件独立 `PUT`/`GET`/`HEAD`），**均无 Packed 归档格式、整包缓存开关或可插拔 Redis/TiKV 元数据引擎** | 按各自原生形态运行 `prepare` + `32` 读者并发读 `2` 遍（共 4 次运行），直接体现传统逐文件 POSIX 分布式文件系统与逐对象 S3 FUSE 在海量小文件场景下的元数据与小请求开销 |
| **4** | `04-agent-replay` | 8 类真实 Agent 轨迹包含 POSIX 硬链接 `link()`（如 `bun install` 产生 `2,013` 次、`git` 产生 `10~48` 次），`BrewFS`、`JuiceFS` 与 `CephFS` 均作为原生元数据硬链接执行 | `s3fs` 无独立 inode 间接层，`linkat()` 固定返回 `EOPNOTSUPP`（`errno=95`）；经核查全部 8 条轨迹中被硬链接文件后续写入次数均为 `0` | 保持轨迹事件与 `full-fuse-replay` 二进制不变，在 `s3fs` 回放中通过 `LD_PRELOAD` 加载 [s3fs_hardlink_copy_shim.c](runtime/s3fs_hardlink_copy_shim.c)：先调真实 `linkat()`，仅当返回 `EOPNOTSUPP` 时回退为端到端 FUSE 文件读写拷贝（与真实 `bun`/`git` 在无硬链接文件系统上的回退行为一致），并在 `hardlink_copy_cost` / `hardlink_copy_summary` 中单列 `fallback_count`、`copied_bytes` 与 `copy_seconds` |
| **5** | `01-io` | `fio` 使用 `direct=1`，写负载每累计写入 `64 MiB`（单 job 每 `8 MiB`）执行一次 `fsync`（顺序写 `fsync=8`、随机写 `fsync=2048` + `end_fsync=1`），分块/日志客户端仅刷出脏块 | `s3fs` 本地通过 `tmpdir` 完整暂存对象，需显式挂载 `-o direct_io` 才能接受 `O_DIRECT`；且因 S3 对象不可原地部分修改，**每次对 `256 MiB` 脏文件调用 `fsync` 都会触发整对象分段重传** | 对 `s3fs` 的 `01-io` 测量挂载启用 `direct_io` 并将 `tmpdir`/`use_cache` 置于 NVMe，保留与主线完全相同的 `fsync` 频率不作人为弱化，由 `write_amplification_s3` / `write_amplification_physical` 如实记录整对象刷新写放大 |
| **6** | 全部 4 组（后端存储介质形态） | RustFS 直接使用 4 台存储节点已有 NVMe 分区上的目录，采用 `2+2` 纠删码（EC） | 受限于集群规则**“不开新机器，不格式化已有磁盘”**，无法清空或重分区已有 NVMe 磁盘建立裸盘 BlueStore OSD | `CephFS` 在相同 4 台存储节点（`A672`/`A601`/`A635`/`A368`）的同一块 NVMe SSD 上通过 `losetup --direct-io=on` 挂载稀疏后备镜像文件（`/var/lib/ceph/osd-backing/osd-0..3.img`，每节点 `250 GiB`）构建 4 个 BlueStore OSD（数据池/元数据池均为 `size=2` 双副本，2 个 MDS 位于 `A355`/`A672`，3 个 MON 位于 `A366`/`A355`/`A672`） |
| **7** | 全部 4 组（客户端内部遥测接口） | `BrewFS` 与 `JuiceFS` 在 `127.0.0.1:(20000+slot)` 暴露客户端 Prometheus HTTP `/metrics`（含客户端 S3 字节与后台待传队列 gauge） | `ceph-fuse` 通过本地 UNIX Admin Socket（`.asok`）导出 JSON 性能计数器；上游 `s3fs` 进程**无内置 Prometheus/HTTP 指标端点** | 统一对齐主机级（CPU/内存/NVMe/`eth0`）与 cgroup v2（`cpu.stat`/`memory.peak`）指标；此外 `CephFS` 采集 `.asok` `perf dump`（`objectcacher` 脏字节与客户端缓存命中）及 OSD/MDS 计数器，`s3fs` 采集本地 `tmp`/`cache` 目录状态与服务端 RustFS `:9464` Prometheus S3 指标 |

---

## 3. 目录结构

- **配置 (`configs/`)**
  - [baseline-suite.json](configs/baseline-suite.json)：28 项实验矩阵定义。
  - [baseline-mount-profiles.json](configs/baseline-mount-profiles.json)：6 台主机槽位预算、`ceph-fuse` 与 `s3fs` 挂载参数模板。
  - [baseline-runtime.json](configs/baseline-runtime.json)：远端隔离目录、CephFS 前缀 `/ax-baselines/run-baselines-001`、S3 桶前缀 `axbase1`、主线运行冲突守卫配置。
- **核心运行时 (`runtime/`)**
  - [baseline_suite.py](runtime/baseline_suite.py)：配置校验与 `plan.json` 导出。
  - [baseline_mounts.py](runtime/baseline_mounts.py) / [baseline_audit_mount.py](runtime/baseline_audit_mount.py)：挂载命令渲染、窗口内存/磁盘预算检查、`/proc/<pid>` 与 cgroup 审计及密钥脱敏。
  - [baseline_mount_driver.py](runtime/baseline_mount_driver.py)：单机 `systemd-run` 挂载启停、`exec-in-cgroup`、`wait-upload-zero`（连续两次间隔 `>=1s` 归零校验）与 Ceph admin socket / `s3fs` 缓存指标读取。
  - [baseline_host_batch.py](runtime/baseline_host_batch.py)：单机多线程批量挂载、批量 `full-fuse-replay`、批量跨机验证、`c3-loader` 与按深度并行目录树复制。
  - [s3fs_hardlink_copy_shim.c](runtime/s3fs_hardlink_copy_shim.c)：`s3fs` 下拦截 `linkat()` 返回 `EOPNOTSUPP` 时执行端到端 FUSE 全量文件拷贝并统计回退成本。
  - [baseline_collector.py](runtime/baseline_collector.py)：无校验（`zstd --no-check`）单机采集守护进程，采集主机 CPU/内存/NVMe/eth0、挂载 cgroup、RustFS Prometheus 与本地 Ceph OSD/MDS 计数器。
  - [baseline_cluster_lib.py](runtime/baseline_cluster_lib.py) / [baseline_groups.py](runtime/baseline_groups.py) / [run_baseline_suite.py](runtime/run_baseline_suite.py)：A366 协调器库、四组实验完整流程与套件统一入口。
- **本地编排脚本 (`scripts/`)**
  - [sync_baselines.py](scripts/sync_baselines.py)：打包并同步代码与配置至 6 台机器的 `/srv/ax-cluster/baselines`。
  - [setup_baselines.py](scripts/setup_baselines.py)：检查主线空闲状态、按需启动 `cephfs-cluster` 并卸载全局 `/mnt/cephfs`、下发 `0600` `s3fs` 凭证文件、启动 `ax-baselines-collector.service`。
  - [start_baselines.py](scripts/start_baselines.py)：一键同步、初始化并在 `A366` 通过 `systemd-run` 后台启动（支持 `--tiny`、`--check`、`--group`、`--system`、`--job`）。
  - [status_baselines.py](scripts/status_baselines.py)：单次 SSH 查询运行进度、当前任务、Ceph 状态及主线冲突守卫。
  - [collect_baselines.py](scripts/collect_baselines.py)：整轮或小数据检查完成后将远端原始结果取回本地 `results/baselines/run-baselines-001/`。
- **单元测试 (`tests/`)**
  - [test_baseline_suite.py](tests/test_baseline_suite.py)：覆盖计划生成、主线参数对齐、挂载渲染与 64-Agent 槽位校验、挂载审计、fio/think-plan 生成及全目录零哈希/零校验和静态检查。

---

## 4. 使用方法（本机 PowerShell 7）

### 4.1 本地单元测试
```powershell
uv run --python 3.12 python -m unittest discover -s experiments/cephfs-s3fs-baselines/tests -v
```

### 4.2 仅同步脚本到 6 台服务器（不启动守护进程，随时安全执行）
```powershell
uv run --python 3.12 python experiments/cephfs-s3fs-baselines/scripts/start_baselines.py --sync-only
```

### 4.3 极小规模全链路验证（`--tiny`，14 项代表性任务）
`--tiny` 针对 `cephfs` 与 `s3fs` 各抽取 7 项核心代表任务（`01-io` 顺序读 + 随机写、`02-fork` 单子工作区读写隔离 + 多子工作区并发全量拷贝、`03-packed` 准备 + 多机并发读、`04-agent-replay` 跨机多 Agent 回放与全量拷贝），端到端验证挂载审计、全量拷贝成本统计、硬链接拷贝回退与跨机逐字节校验：

```powershell
# 一键启动极小规模验证（14 项：7 cephfs + 7 s3fs）
uv run --python 3.12 python experiments/cephfs-s3fs-baselines/scripts/start_baselines.py --tiny

# 单次查询 --tiny 进度
uv run --python 3.12 python experiments/cephfs-s3fs-baselines/scripts/status_baselines.py --tiny

# 取回 --tiny 验证结果到本地 results/baselines/run-baselines-001/
uv run --python 3.12 python experiments/cephfs-s3fs-baselines/scripts/collect_baselines.py --tiny-only
```

### 4.4 全矩阵小数据验证（`--check`，覆盖全部 28 个配置）
> `start_baselines.py` 与 `setup_baselines.py` 内置冲突保护：若检测到主线任务或 `measurement.lock` 正在运行，默认拒绝启动，避免干扰主线实验。

```powershell
# 验证 cephfs + s3fs 全部 28 个条件的小数据路径
uv run --python 3.12 python experiments/cephfs-s3fs-baselines/scripts/start_baselines.py --check

# 单次查询 --check 进度
uv run --python 3.12 python experiments/cephfs-s3fs-baselines/scripts/status_baselines.py --check
```

### 4.5 启动正式基线实验与取回结果
```powershell
# 启动全部 28 次正式基线运行（或加 --system cephfs / --system s3fs / --group 01-io 分批执行）
uv run --python 3.12 python experiments/cephfs-s3fs-baselines/scripts/start_baselines.py

# 单次查询正式实验状态
uv run --python 3.12 python experiments/cephfs-s3fs-baselines/scripts/status_baselines.py

# 整轮完成后取回全部原始数据到本地 results/baselines/run-baselines-001/
uv run --python 3.12 python experiments/cephfs-s3fs-baselines/scripts/collect_baselines.py
```

### 4.6 实验结束后关闭采集器与 CephFS 服务
```powershell
ssh idcai-60001 "/srv/ax-idcai-20261113/venv/bin/python3 /srv/ax-cluster/baselines/scripts/setup_baselines.py --teardown --stop-cephfs"
```
