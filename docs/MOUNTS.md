# 每个挂载点的配置

参数源为 [mount-profiles.json](../configs/mount-profiles.json)，由 [mounts.py](../bench/cluster/mounts.py) 生成记录。BrewFS 配置对应已合并的 `5750af24`；每个进程显式指定容量，包含父子共存阶段。

## 容量与预取

fio 使用 `resource_profile=fio`：BrewFS 和 JuiceFS 每挂载均为 16 GiB 内存、15 GiB 读 SSD 缓存和 1 GiB 写暂存预算。下面列出其余三组的默认配置；应用内存缓存、预读、上传并发和写入策略在两种资源配置间保持相同。

| 项目 | 每个挂载的配置 |
|---|---|
| 进程组总内存 | 4 GiB，含 FUSE、回放器和计入 cgroup 的页缓存；swap=0 |
| BrewFS 读 / 写内存 | 512 / 256 MiB，通用写内存预算 256 MiB |
| Packed metadata / retained payload | 128 / 8 MiB |
| 工作区 row cache | 64 MiB，lease 策略，TTL 字段显式 60 秒 |
| 八个 Packed 内存池 | roots 16、metadata_owned 256、plans 32、control 16、stored 64、raw 64、decode_workspace 16、output 64 MiB |
| Packed 磁盘缓存 | 4 GiB，默认 on；元数据和数据共用整包异步缓存 |
| Packed 挂载读 / 写 SSD | 读 3 GiB、写 1 GiB，加整包缓存共 8 GiB |
| Native 挂载读 / 写 SSD | 7 / 1 GiB，合计 8 GiB |
| BrewFS 普通数据预读 | 初始窗口 4 MiB、最大窗口 4 MiB、4 个 worker；range 后台预取开启，working-set factor=2 |
| Packed 整包并发 | 每实例最多 4 个 HEAD、4 个整包下载；以当前实现为准 |
| JuiceFS 缓冲 / 预读 | `buffer-size=768M`，`max-readahead=4M`，`prefetch=1`，`max-downloads=8`，`max-uploads=4` |
| JuiceFS 本地空间 | 7 GiB 读缓存、1 GiB 写暂存预留，合计 8 GiB |
| JuiceFS Overlay 元数据缓存 | `fork_cache_bytes=268435456`，连同在途事务的临时结果计入进程预算 |

Packed 磁盘容量不计入内存。metadata cache 和内存池有重叠的计费对象，配置检查保守地保留容量，实际内存由 cgroup 和进程统计报告。为解析、堆、下载缓冲和内核预留至少 2 GiB。各实验关闭某一缓存时，其余容量不变。

Native 的元数据访问没有独立预取开关。Packed 元数据和数据共同受整包缓存控制，解码后 payload retention 单独配置。初始化和发布使用独立进程环境，不继承挂载的缓存环境。

## 覆盖每次挂载

| 阶段 `phase` | 缓存状态 | 必须保存 |
|---|---|---|
| `initial` | 新目录 | 导入挂载及其实际参数 |
| `measure` | 新目录，或明确的同进程热读 | 计时前容量与计数，实际进程信息 |
| `verify` | 独立目录 | 计时后的内容比较及其耗时 |
| `remount` | 新目录；磁盘复用条件另选 | 上一进程退出、新 PID、缓存目录 |
| `fork-child` | 子工作区新目录 | 父/子 ID、子进程全部配置 |

同一持久 Packed 目录有独占锁。并发挂载分别使用 `cache_root`；只有先退出旧所有者，才可复用其目录。记录持久缓存或临时缓存实际使用情况。复用同一进程的热读复用该进程记录，不另创建挂载。

## 启动后记录

从 `/proc/<pid>/exe`、脱敏的 argv 和配置环境确认二进制、配置文件与变量。读取原生容量：`brewfs_packed_index_cache_configured_bytes`、`brewfs_packed_v3_payload_retention_capacity_bytes`、八个 `brewfs_packed_budget_<pool>_capacity_bytes`、`brewfs_workspace_row_cache_capacity_bytes`。整包缓存记录 configured bytes、实际 reserved bytes、命中、未命中、下载量和驱逐。

每次计时分为前台读取、后台下载和正常退出前的剩余时间。保存退出时未完成下载数量、后续下载字节和退出耗时。启动配置缺失、进程参数不一致或主机预算超额时，停止该项并显示具体字段。
