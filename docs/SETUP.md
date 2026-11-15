# 六台服务器的准备与执行

直连轮次按 [campaign-direct.json](../configs/campaign-direct.json) 手动执行 C1～C4，入口为 [直连交接](HANDOFF-DIRECT-NETWORK.md)。本仓库的 `bench.cluster.plan` 导出任务清单，实际负载由执行者按清单逐组启动。分组操作见 [C1～C3](HANDOFF-C1-C3.md) 和 [C4](HANDOFF-C4.md)。

## 主机分工

| 主机 / SSH | 实际型号记录 | 标称内存 | 活动工作区上限 / 挂载位置 | 常驻角色及内存 |
|---|---|---:|---:|---|
| A366 / `idcai-60001` | EPYC 7H12，128 逻辑 CPU | 256 GiB | 48 / 52 | 编译、协调；协调 8 GiB，系统采集 16 GiB |
| A355 / `idcai-60525` | EPYC 7H12，128 逻辑 CPU | 256 GiB | 32 / 40 | Redis 64 GiB，系统采集 16 GiB |
| A672 / `idcai-60103` | 双路 8171M，104 逻辑 CPU | 128 GiB | 16 / 16 | RustFS 32 GiB，系统采集 16 GiB |
| A601 / `idcai-60439` | 双路 6258R，112 逻辑 CPU | 256 GiB | 40 / 48 | RustFS 32 GiB，系统采集 16 GiB |
| A635 / `idcai-60533` | 双路 8272CL，104 逻辑 CPU | 256 GiB | 40 / 48 | RustFS 32 GiB，系统采集 16 GiB |
| A368 / `idcai-60555` | i9-14900K，32 逻辑 CPU | 128 GiB | 16 / 20 | RustFS 24 GiB，系统采集 16 GiB |

总容量为 192 个活动工作区、224 个挂载位置。本轮最高 128 个活动工作区；每个 FUSE 与回放进程组共用 4 GiB 内存、2 GiB 磁盘缓存，swap=0。CPU 总配额和实际内存最低值见 [cluster.json](../configs/cluster.json)。A368 记录大小核分配，其它主机按 NUMA/SMT 拓扑分配 CPU。C1 只有 A366 运行负载。

先保存 `lscpu`、内存、磁盘、接口和路由。新轮次使用 [network.json](../configs/network.json) 指定的 eth0 直连地址，部署与测量步骤见 [NETWORK](NETWORK.md)。保存 MTU、RTT、客户端到 Redis 和四台 RustFS 的双向吞吐。SSH 使用既有别名并复用少量连接，正式存储数据采用直连。

## 目录、依赖与版本

服务器统一工作根目录 `/srv/ax-cluster`：`sources/` 源码、`releases/` 二进制、`input/` 公共只读输入、`cache/` 挂载缓存、`mounts/` 挂载点、`private/` 凭据、`runs/` 结果。部署前保存已有进程、挂载、端口及其所属任务。

`infra/idcai/prepare_host.py` 提供 Linux 依赖检查及安装。六台都是 `client`；A366 增加 `build`，A355 增加 `metadata`，四台对象主机增加 `objects`。从本机 PowerShell 传入检查脚本：

```powershell
Get-Content -Raw infra/idcai/prepare_host.py | ssh.exe -T idcai-60001 python3 - --role client --role build
Get-Content -Raw infra/idcai/prepare_host.py | ssh.exe -T idcai-60525 python3 - --role client --role metadata
Get-Content -Raw infra/idcai/prepare_host.py | ssh.exe -T idcai-60103 python3 - --role client --role objects
```

对象主机的同一条命令分别用于 `idcai-60439/60533/60555`。缺少依赖时加 `--apply`，保留 stdout 为主机记录。构建机另检查 `libssl-dev`、`libfuse3-dev` 与 `pkg-config`；Docker 主机检查 `docker compose version`；网络测试需要 `iperf3`，同步附加对照需要支持关闭校验和的 `rsync`。这些组件按缺项安装。

| 对象 | 源码 / 分支 | 构建与部署 |
|---|---|---|
| BrewFS | `https://github.com/brewfs/brewfs` 的最新 `main` | A366 release 构建，features=`workspace,observability,rustfs-ec-block-size-hint`；六台分发同一产物 |
| RustFS | `https://github.com/Ivanbeethoven/rustfs` 的最新 `feat/dynamic-block-size-rebased` | A366 构建一次带源码 revision 标签的镜像，四台部署同一镜像 |
| JuiceFS | `https://github.com/juicedata/juicefs`，固定社区正式版本，最低 1.4.2 | 六台同一产物，记录版本与源码提交 |
| JuiceFS Overlay | `https://github.com/Ivanbeethoven/juicefs` 的 `codex/lazy-workspace-fork-20261113` | 六台同一产物，独立 `forkredis` 卷 |
| 回放器 | 本仓库 `tools/full-fuse-replay` | A366 编译 release，六台分发 |

目前已检查的 BrewFS 提交为 `efd9c902d88b3d17e9fecbd4ac7ff4a6cd15626b`。A366 可能存在 `ax-cluster-release-efd9c902.service` 构建任务，结果位于 `/srv/ax-cluster/releases/efd9c902/build.json`。先检查该记录与进程。随后解析最新分支并固定本轮实际提交；全部重复使用同一产物。新功能分支单独登记，不将其写为上游 main。

构建脚本为 `infra/idcai/build_release.py`，运行 `--help` 查看参数。现有工具链 `/srv/ax-idcai-20261113/toolchain-1.99.0/bin`，Cargo 缓存 `/srv/ax-idcai-20261113/cargo-build`；复用 `build.lock` 与 `measurement.lock`。每次最多 8 个构建 job、64 GiB 内存；性能测量期间停止编译。不能访问 GitHub 时，在本机取源码后通过已有 SSH 入口传入。产物登记源码提交、构建参数、工具链、字节长度及 CLI 版本。

## Redis 与四节点 RustFS

Redis 放 A355，进程预算 64 GiB，`maxmemory=48GiB`、`maxmemory-policy=noeviction`、AOF everysec。数据、日志和凭据置于本轮目录；使用专属 Redis 实例或已有明确属于本轮的实例。逻辑库号只能使用 0～15，本轮采用 DB 0 配合不同卷名和前缀。复制普通 JuiceFS 与 Overlay 数据时分别创建自己的格式。

RustFS 为 A672/A601/A635/A368 四节点，每台现有一块 SSD、一个独立数据目录，EC 2+2。`infra/rustfs/generate_rustfs.py` 支持 `mode=four-node-single-disk`：spec 写入实际 `image` 标签、`image_source_commit`、四个节点的 `private_ip/data_mounts/log_dir/credentials_file/cpus/memory_bytes`。固定镜像 revision；各节点 `nofile=1048576`，保留 CPU 与内存给本机客户端。

```powershell
python infra/rustfs/generate_rustfs.py runs/services.local.json runs/rustfs-deployment
python infra/rustfs/rustfs_node.py --help
```

生成结果中每节点有 `compose.json/node.json`，在对应主机用 Docker Compose 启动。先核实生成的监听地址与实际空闲端口；实例的四个 endpoint 必须全部一致地指向本轮服务。动态布局四个开关默认开启；C1 q 消融单独调整并重新准备对象。RustFS 指标导出使用当前分支实际支持的变量和端口，保存服务启动配置及一次原始指标。

三个文件系统依次使用相同后端、相同端点分配、独立卷/桶前缀。客户端缓存、输入、对象数据和观测日志分目录；同一 SSD 上的物理竞争如实记录。每盘至少保留 20% 空间与 inode；对象数据预算每台 320 GiB、输入 64 GiB、日志 32 GiB。

## 输入和回放

[trace-inputs.json](../configs/trace-inputs.json) 列明 8 类主 trace、OSS 和 Drive 路径及归档大小。优先使用服务器已有副本，只补缺少的输入。两个公共 HTTPS 归档可用 `curl --fail --location --output` 下载；授权 OSS 对象用既有 `ossutil cp` 配置下载一次，再分发。凭据留在 `private/`，不进入结果。

实际输入清单逐项填写归档路径、初始树、`events.jsonl`、`blobs/`、终态和原工具日志。检查序号连续、操作数、写入载荷长度与路径范围，文件内容直接比较。已有 blob 文件名作为不透明标识使用。

Rust 回放命令结构为 `full-fuse-replay EVENTS BLOBS WORKSPACE OUTPUT --performance`，等待条件增加 `--simulate-llm PLAN`。当前 `src/pacing.rs` 与部分 xattr 路径仍包含校验和计算，运行前按用户要求改为序号、计数、长度及直接内容比较；旧下载包装脚本同样先检查。该修改列入执行准备，不能直接运行旧严格模式。

等待仅放在真实工具/轮次边界，保存 `before_seq` 与原日志行位置。相同逻辑任务使用相同等待表。短等待每段 1～7 秒，长等待 3～15 秒；固定总时间分别为边界数乘 4 秒/9 秒，在范围内随机分配并保存所有毫秒值。缺少原工具日志的输入补齐边界后进入带等待组；其无等待结果单独保留。

## 挂载配置与准备项目

[mount-profiles.json](../configs/mount-profiles.json) 和 [MOUNTS](MOUNTS.md) 列出所有缓存、上传、预读与挂载设置。写入组分别记录应用结束与远端可读，保留原始 fsync 语义。开关名称相近时仍核实际行为，不以单个参数名称代替持久完成测量。

| 项目 | 使用位置 | 完成方式 |
|---|---|---|
| 实际配置记录 | 所有挂载 | `bench.cluster.mounts` 生成记录，`audit_mount.py` 核对进程、cgroup、配置与原生容量指标 |
| 完整 trace 与等待 | C2 | 修改上述回放路径后，先单任务内容检查，再 8 个任务验证等待与计数 |
| 普通版本封存与 fork | C2 准备、C3 私有输出 | 使用当前 Native 版本接口，非空树验证继承与隔离 |
| 同机共享只读缓存 | C2/C3 消融 | 独立实现 metadata/pack/block 共享，按 [cache-sharing.json](../configs/cache-sharing.json) 记录总预算和命中 |
| 相同数据对象的 KV 元数据 | C3 表示消融 | 实现元数据适配，保持数据对象和其它缓存不变 |
| 复制名字空间的适配 | C2 技术消融 | 数据引用相同，改变目录记录共享方式 |
| 批量观测 | C2/C3 | 每机单采集进程，阶段快照与原始分段日志 |

上述适配需要实际功能检查。已具备的主条件先执行；缺少接口的消融记录具体 `pending-feature` 并继续完成实现。执行前先用 1,000 个文件及单任务检查格式、计时、上传与直接读回，再按配置运行正式规模。

收尾方法、指标和目录结构见 [METRICS](METRICS.md)。只清理本轮登记的进程、挂载和数据；旧服务或其它会话的测试继续保留。全部原始结果取回后再删除本轮可重建输入副本和缓存。
