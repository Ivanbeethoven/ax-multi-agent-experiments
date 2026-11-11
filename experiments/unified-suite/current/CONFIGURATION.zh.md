# 软件、服务与资源配置

[下一批小规模服务器配置](PILOT-HARDWARE.zh.md)：单节点 pilot 使用 4×100 GiB RustFS 数据盘；下文正式四节点规格保持独立。

[design.json](design.json) 保存规划数值。预检后补齐实际节点/盘、提交 SHA、镜像 digest、工作区 ID 和输入 SHA，再冻结运行清单。

## 版本和卷身份

| 项目 | 固定规则 |
|---|---|
| BrewFS | `https://github.com/brewfs/brewfs` 的 main；campaign开始取最新提交，整个扩展曲线和重复固定 |
| RustFS | `https://github.com/Ivanbeethoven/rustfs` 的 feat/dynamic-block-size-rebased；整个campaign固定镜像digest |
| JuiceFS | ≥1.4.2；记录精确版本与 SHA；所需参数通过实际 `--help` 和挂载核对 |
| 构建 | BrewFS 同一 release 构建保留 default features，额外包含 workspace-overlay、rustfs-ec-block-size-hint；若工具链要求更多能力，在资格阶段确定并统一构建 |
| 分发 | 远端构建一次，产物经内网分发并逐机验 SHA；禁止按消融臂重编不同 feature |
| 基础版 | volume_format=flat-v1；独立 metadata namespace 和 S3 前缀 |
| 完整方案 | volume_format=workspace-v1；schema 1 catalog；Packed-v3 wire005 基线绑定、真实 workspace UUID、workspace_namespace |
| 排除字段 | 新完整方案不使用 native_base、native_control_version、entity_volume_id、experimental-entity-mount |

完整方案先用 `workspace init-packed-volume` 安装 lower，再挂载该 workspace。保存 manifest SHA、catalog绑定、volume/workspace/head/revision、upper bucket和租约代际；派生引用不可变revision。

## 客户端与缓存

| 配置 | BrewFS 基础版 / 完整方案 | JuiceFS |
|---|---|---|
| 数据块/逻辑 chunk | block_size=4 MiB、chunk_size=4 MiB | format 后记录实际 block-size；本批统一 4 MiB（原生参数单位 KiB 时填 4096） |
| 数据读缓存 | VFS read_memory_bytes=512 MiB；read_ssd_bytes=0 | cache-dir=memory，cache-size=512 MiB |
| 缓冲 | write_memory_bytes=512 MiB，memory_budget_bytes=512 MiB | buffer-size=512 MiB |
| 写暂存 | 私有缓存目录，write_ssd_bytes=1 GiB | writeback=false |
| 持久化 | writeback_mode=upload_before_commit；负载要求的 fsync；关闭前远端排空 | 同步上传语义，负载要求的 fsync，关闭前排空 |
| FUSE | cache_ttl_ms=0、keep_cache=false | attr/entry/dir-entry/open cache=0 |
| 预取 | 开，max=128 MiB、concurrency=64、background=true、fetch=backend-default | prefetch=1、max-readahead=128 MiB |
| 写后缓存 | populate=true，persist=false | cache-large-write=true |
| 压缩/校验 | compression=none、verify_cache_checksum=full | format 压缩 none、cache checksum full |
| 后台任务 | 记录实际 GC、租约续期及其开销 | backup-meta=0、no-usage-report=true |

预算外的页缓存、元数据、解码与FUSE RSS另记。系统比较保留算法差异，单变量消融对齐其他行为。“客户端冷”固定为新进程/挂载/私有缓存，保留后端缓存，不执行宿主全局drop_caches。

Packed另有以下内存预算（分别统计used/peak/rejection）：

| `BREWFS_PACKED_V3_*_BUDGET_BYTES` 后缀 | MiB |
|---|---:|
| ROOTS | 16 |
| METADATA_OWNED | 256 |
| PLANS | 32 |
| CONTROL | 1 |
| STORED | 32 |
| RAW | 32 |
| DECODE_WORKSPACE | 8 |
| OUTPUT | 32 |

显式设置上述预算，合计409 MiB，与VFS缓存分别报告。工作区行缓存TTL固定60秒（`BREWFS_WORKSPACE_ROW_CACHE_TTL_MS=60000`），有效性绑定租约。所核对源码租约TTL=30秒、续期=10秒；新提交若变化，更新回执并重核迁移时序。

producer：chunk/block=4 MiB、metadata/data codec=raw、inline=off、size-only、random-small-file、max random frame raw=256 KiB，逐项核CLI和manifest。所有上传者使用本臂hint；排序临时文件及SQLite（若使用）计入导入成本。

## Redis、TiKV、SQLite

每池独占一个Redis，各臂分时使用独立namespace/DB；保存版本digest、无口令配置与起止数据量：

~~~conf
appendonly yes
appendfsync always
save ""
maxmemory 51539607552
maxmemory-policy noeviction
auto-aof-rewrite-percentage 100
auto-aof-rewrite-min-size 64mb
no-appendfsync-on-rewrite no
io-threads 1
~~~

64 GiB内存中maxmemory=48 GiB，余量须覆盖进程和AOF rewrite；峰值不足时重新定容并冻结新运行。各臂持久化条件一致，保留rewrite期间延迟。

TiKV另开补充运行，全部臂一起更换：3个真实存储节点，登记PD/TiKV版本、Raft副本、TLS、端点、盘、写入耐久和store状态。rbm同机三个store用于功能验证。

`state.sqlite`位于协调者本地盘，只存调度与证据索引；producer临时SQLite只用于准备。二者都不是被测元数据服务。

## RustFS 四节点

按[部署生成器](../../../infra/rustfs/README.md)配置4个独立宿主×4块独立盘，保存完整volume列表、实际erasure set与data/parity数；不从16盘推定纠删码比例。

开启动态块时，全节点设置：

~~~conf
RUSTFS_EC_BLOCK_SIZE_HINT_ENABLE=true
RUSTFS_EC_BLOCK_SIZE_HINT_FLEET_CONFIRMED=true
RUSTFS_EC_READ_QUANTUM_ENABLE=true
RUSTFS_EC_READ_QUANTUM_FLEET_CONFIRMED=true
~~~

动态块关闭时四项均false、全部上传者hint=off。独占空闲池，使用同一镜像切换后创建新对象前缀。

主线关闭服务压缩，保留checksum、耐久及纠删码设置。本轮auto-v1在最终对象>1,048,576 B时发1 MiB hint，否则无hint；tiered/range-heavy另立条件。分别记录客户端对象g、Packed frame、实际B/q、inline状态，按落盘证据判定是否生效。

## 单池资源与云盘

| 角色 | 数量（32 Agent 档） | 每台 CPU / RAM | 每台数据盘 | 生命周期 |
|---|---:|---|---|---|
| 执行客户端 | 4 | 32 vCPU / 128 GiB | 100 GiB PL1 | 8/16/32 档分别需要 1/2/4 台 |
| Redis | 1 | 16 vCPU / 64 GiB | 60 GiB PL1 | 本池期间 |
| RustFS | 4 | 16 vCPU / 64 GiB | 4×120 GiB PL1 | 本池期间 |
| 迁移目标 | 0 或 1 | 同执行客户端 | 100 GiB PL1 | 仅 C4 跨机阶段 |
| 协调者/跳板 | 复用 1 | 按归档峰值核 | 状态与归档单独记账 | 全局共用唯一公网入口 |

每台业务节点另配 40 GiB PL0 系统盘。每客户端 8 个 Agent，各 2 vCPU/8 GiB/swap=0，共用 16 CPU/64 GiB；剩余留给 FUSE、内核、日志和采集。Agent cgroup 必须有实际独立 CPU/memory 限额，FUSE 和 observer 的用量单独记录，不把“剩余预算”自动说成物理 CPU 隔离。

原生命令的其余默认值也要在资格阶段展开并保存，尤其 FUSE workers/max_background、metadata/open/read-plan cache、S3 并发/分片、Redis/JuiceFS 版本与格式参数。完整解析结果纳入计划摘要；新分支改变默认值时不得沿用旧资格。

32档为9台/208 vCPU/832 GiB RAM，迁移时10台；C1为6台。两池总并发、资源锁与按需启动见[并行流程](../MAIN-SCALE.zh.md)。

2,380 GiB 数据盘只是起始询价规格。每盘按 `峰值占用 / 0.8` 核容量，包含最坏完整拷贝、纠删码、AOF rewrite、输入解压、日志、失败现场和归档副本；按最满单盘及 inode 检查。C4 百万×4 KiB 的父目录+32 副本为 135.168 GB 逻辑数据，不能直接等同后端物理占用。

[地域与云盘账单](../MAIN-C2-COST-DISKS.zh.md)保留 2026-11-11 的历史询价；它不是当前报价承诺。开机前按同规格重新询价，先准备二进制和输入，再开所需节点。费用按开机到真正释放计算，包含准备/校验/失败/归档。停止实例不等于云盘停止计费。
