# 服务器与环境准备

四组实验方案见 [PLAN-REVIEW](PLAN-REVIEW.md)，运行组件见 [RUNNING](RUNNING.md)。

| 主机 / SSH | 实际型号记录 | 标称内存 | 活动工作区上限 / 挂载位置 | 常驻角色及内存 |
|---|---|---:|---:|---|
| A366 / `idcai-60001` | EPYC 7H12，128 逻辑 CPU | 256 GiB | 48 / 52 | 编译、协调；协调 8 GiB，系统采集 16 GiB |
| A355 / `idcai-60525` | EPYC 7H12，128 逻辑 CPU | 256 GiB | 32 / 40 | Redis 64 GiB、TiKV 24 GiB、PD 4 GiB，系统采集 16 GiB |
| A672 / `idcai-60103` | 双路 8171M，104 逻辑 CPU | 128 GiB | 16 / 16 | RustFS 32 GiB，系统采集 16 GiB |
| A601 / `idcai-60439` | 双路 6258R，112 逻辑 CPU | 256 GiB | 40 / 48 | RustFS 32 GiB，系统采集 16 GiB |
| A635 / `idcai-60533` | 双路 8272CL，104 逻辑 CPU | 256 GiB | 40 / 48 | RustFS 32 GiB，系统采集 16 GiB |
| A368 / `idcai-60555` | i9-14900K，32 逻辑 CPU | 128 GiB | 16 / 20 | RustFS 24 GiB，系统采集 16 GiB |

总容量为 192 个活动工作区、224 个挂载位置。本轮最高 64 个活动工作区；每个 FUSE 与回放进程组共用 4 GiB 内存、8 GiB 磁盘缓存，swap=0。CPU 总配额和实际内存最低值见 [cluster.json](../configs/cluster.json)。A368 记录大小核分配，其它主机按 NUMA/SMT 拓扑分配 CPU。C1 只有 A366 运行负载。

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

当前使用 BrewFS main 的 `5750af24eba7ddc4bbea53f2abfbc7a6951d3331`，A366 构建产物与记录位于 `/srv/ax-cluster/releases/5750af24/`。六台机器使用同一份 BrewFS、Packed 准备工具和工作区管理工具。同一轮的对照使用同一产物，源码提交与构建记录随结果保存。

构建脚本为 `infra/idcai/build_release.py`，运行 `--help` 查看参数。现有工具链 `/srv/ax-idcai-20261113/toolchain-1.99.0/bin`，Cargo 缓存 `/srv/ax-idcai-20261113/cargo-build`；复用 `build.lock` 与 `measurement.lock`。每次最多 8 个构建 job、64 GiB 内存；性能测量期间停止编译。不能访问 GitHub 时，在本机取源码后通过已有 SSH 入口传入。产物登记源码提交、构建参数、工具链、字节长度及 CLI 版本。

## Redis 与四节点 RustFS

Redis 放 A355，进程预算 64 GiB，`maxmemory=48GiB`、`maxmemory-policy=noeviction`、AOF everysec。数据、日志和凭据置于本轮目录；使用专属 Redis 实例或已有明确属于本轮的实例。逻辑库号只能使用 0～15，本轮使用独立端口 17379，按组使用不同逻辑库和卷名前缀。复制普通 JuiceFS 与 Overlay 数据时分别创建自己的格式。

RustFS 为 A672/A601/A635/A368 四节点，每台现有一块 SSD、一个独立数据目录，EC 2+2。`infra/rustfs/generate_rustfs.py` 支持 `mode=four-node-single-disk`：spec 写入实际 `image` 标签、`image_source_commit`、四个节点的 `private_ip/data_mounts/log_dir/credentials_file/cpus/memory_bytes`。固定镜像 revision；各节点 `nofile=1048576`，保留 CPU 与内存给本机客户端。

```powershell
python infra/rustfs/generate_rustfs.py runs/services.local.json runs/rustfs-deployment
python infra/rustfs/rustfs_node.py --help
```

生成结果中每节点有 `compose.json/node.json`，在对应主机用 Docker Compose 启动。先核实生成的监听地址与实际空闲端口；实例的四个 endpoint 必须全部一致地指向本轮服务。Dynamic 条件由执行器在四台同时切换，使用同一 RustFS 二进制；为关闭条件重新生成输入对象。RustFS 指标导出使用当前分支实际支持的变量和端口，保存服务启动配置及一次原始指标。

三个文件系统依次使用相同后端、相同端点分配、独立卷/桶前缀。客户端缓存、输入、对象数据和观测日志分目录；同一 SSD 上的物理竞争如实记录。每盘至少保留 20% 空间与 inode；对象数据预算每台 320 GiB、输入 64 GiB、日志 32 GiB。

## 输入和回放

[trace-inputs.json](../configs/trace-inputs.json) 列明 8 类主 trace、OSS 和 Drive 路径及归档大小。优先使用服务器已有副本，只补缺少的输入。两个公共 HTTPS 归档可用 `curl --fail --location --output` 下载；授权 OSS 对象用既有 `ossutil cp` 配置下载一次，再分发。凭据留在 `private/`，不进入结果。

实际输入清单逐项填写归档路径、初始树、`events.jsonl`、`blobs/`、终态和原工具日志。检查序号连续、操作数、写入载荷长度与路径范围，文件内容直接比较。已有 blob 文件名作为不透明标识使用。

Rust 回放命令为 `full-fuse-replay EVENTS BLOBS WORKSPACE OUTPUT --performance`，等待条件增加 `--simulate-llm PLAN`。本轮工具通过序号、计数、载荷长度及直接内容比较检查输入和结果。实际二进制路径和版本保存在结果目录的 `versions.json`。

性能模式的报告以 `completed`、操作数、剩余句柄和等待边界判断是否完成；`valid` 在该模式固定为 `false`。文件内容采用独立的跨机器读回结果判断，保留 `verify.passed` 和实际比较字节数。

等待仅放在真实工具/轮次边界，保存 `before_seq` 与原日志行位置。相同逻辑任务使用相同等待表。每段等待 1～7 秒，固定总等待为边界数乘 4 秒，在范围内随机分配并保存所有毫秒值。缺少原工具日志的输入补齐边界后进入带等待组；其无等待结果单独保留。

## 挂载、采集和结果

挂载配置由 `bench/cluster/mounts.py` 生成，实际进程与 cgroup 由 `audit_mount.py` 检查。六台主机使用 `host_collector.py` 采集，批量任务由 `host_batch_runner.py` 在主机内部执行。

TiKV/PD 使用 v7.5.0，在 A355 部署；二进制为 `/srv/ax-cluster/direct/bin/tikv-server-v7.5.0` 和 `pd-server-v7.5.0`。官方发布包地址为 `https://tiup-mirrors.pingcap.com/tikv-v7.5.0-linux-amd64.tar.gz` 和对应的 `pd-v7.5.0-linux-amd64.tar.gz`。PD 端口 2379，TiKV 端口 20160、指标端口 20180。单节点副本数为 1，数据目录 `/srv/ax-cluster/services/run-002/`。Native、Packed 与 JuiceFS TiKV 使用不同命名空间。

`setup_suite.py` 保存这些服务的配置并创建 systemd 单元。BrewFS 管理工具必须使用与挂载二进制相同的源码提交构建：`infra/idcai/build_workspace_admin.py --source /srv/ax-cluster/sources/brewfs-5750af24 --template <仓库>/tools/workspace-admin --out <产物目录>`。复用该脚本的 Rust 工具链和编译缓存；`tools/full-fuse-replay` 与 `tools/fs-probe` 分别构建回放器和数据加载器。

每次保存实际软件版本、命令和配置。原始数据完整取回后，按运行编号清理临时挂载、缓存和对象。
