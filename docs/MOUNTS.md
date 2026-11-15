# 每个挂载点的配置

参数源为 [mount-profiles.json](../configs/mount-profiles.json)，由 [mounts.py](../bench/cluster/mounts.py) 生成记录。BrewFS 配置对应已合并的 `efd9c902`；每个进程显式指定容量，包含父子共存阶段。

## 容量与预取

| 项目 | 每个挂载的配置 |
|---|---|
| 进程组总内存 | 4 GiB，含 FUSE、回放器和计入 cgroup 的页缓存；swap=0 |
| BrewFS 读 / 写内存 | 512 / 256 MiB，通用写内存预算 256 MiB |
| Packed metadata / retained payload | 128 / 8 MiB |
| 工作区 row cache | 64 MiB，lease 策略，TTL 字段显式 60 秒 |
| 八个 Packed 内存池 | roots 16、metadata_owned 256、plans 32、control 16、stored 64、raw 64、decode_workspace 16、output 64 MiB |
| Packed 磁盘缓存 | 1 GiB，默认 on；元数据和数据共用整包异步缓存 |
| Packed 挂载读 / 写 SSD | 各 512 MiB，加整包缓存共 2 GiB |
| Native 挂载读 / 写 SSD | 1,536 / 512 MiB，合计 2 GiB |
| BrewFS 普通数据预读 | 初始窗口 4 MiB、最大窗口 4 MiB、4 个 worker；range 后台预取开启，working-set factor=2 |
| Packed 整包并发 | 每实例最多 4 个 HEAD、4 个整包下载；以当前实现为准 |
| JuiceFS 缓冲 / 预读 | `buffer-size=768M`，`max-readahead=4M`，`prefetch=1`，`max-downloads=8`，`max-uploads=4` |
| JuiceFS 本地空间 | 1,536 MiB 读缓存，512 MiB 写暂存预留，合计 2 GiB |
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

## 同一主机的多个挂载共享缓存

共享范围由 [cache-sharing.json](../configs/cache-sharing.json) 定义。相同不可变版本的元数据、Pack 和数据块归入一个主机缓存组，由一个所有者管理填充和驱逐。缓存键包含存储命名空间、卷、不可变版本、对象/页及范围。私有 row cache、修改和上传暂存留在各工作区。

C2、C3 和 C4 分别设置共享开关条件。共享组的总容量等于参与挂载在私有缓存条件下的读缓存容量之和，只计一次；父子共存按实际加入的缓存组计费。共享组的 HEAD、下载和上传总并发上限，与独立缓存参照组在该主机的上限之和相同，读者线程数保持不变。共享所有者的 CPU、内存从参与客户端预算中划出。统计进程内存、命中、合并请求和实际传输字节。跨主机子工作区使用另一组。

当前 BrewFS Packed 持久目录只支持单所有者，独立进程共用目录会发生临时缓存回退。共享条件通过专用适配接入，并验证多个进程读取同一对象只填充一次、私有写入相互隔离；将相同目录直接传给多个进程不算完成共享。各系统的实际共享能力登记在环境记录中。

每个请求写明 `id, phase, engine, host, slot, workspace, parent_workspace, purpose, binary, cache_state, windows, root`。`windows` 列出该进程存在的资源阶段，如 `measure`、`fork-overlap`。配置器按阶段逐主机累计内存、磁盘、HEAD、下载并发和连接观察上限，检查挂载位置及缓存目录冲突。

```powershell
python -m bench.cluster.mounts --requests runs/mount-requests.json --out runs/mount-configs.json
```

所有阶段调用同一生成函数。子挂载显式记录自己的参数。缓存复用请求同时提供 `previous_mount` 和 `reuse_cache_root`；跨机子挂载使用新的目录。

## 启动后记录

从 `/proc/<pid>/exe`、脱敏的 argv 和配置环境确认二进制、配置文件与变量。读取原生容量：`brewfs_packed_index_cache_configured_bytes`、`brewfs_packed_v3_payload_retention_capacity_bytes`、八个 `brewfs_packed_budget_<pool>_capacity_bytes`、`brewfs_workspace_row_cache_capacity_bytes`。整包缓存记录 configured bytes、实际 reserved bytes、命中、未命中、下载量和驱逐。

每次计时分为前台读取、后台下载和正常退出前的剩余时间。保存退出时未完成下载数量、后续下载字节和退出耗时。启动配置缺失、进程参数不一致或主机预算超额时，停止该项并显示具体字段。
