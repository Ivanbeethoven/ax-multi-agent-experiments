# 六台周租服务器：安装与构建计划

2026-11-13；根据实验仓库 main `86f704be` 整理。此页是新增硬件方案，不改历史结果或正式四节点十六盘规格；尚未连接、安装或启动租赁服务器。当前实验与规模以 [BLOCKS](BLOCKS.zh.md)、[FEATURES](FEATURES.zh.md) 为准。

## 机器分工

以下名称为待分配的 SSH 别名，不是真实主机名。全部采用 Ubuntu 24.04 x86_64，尽量统一内核和系统包版本。

| 别名 | CPU / 内存 / 磁盘 | 职责 | 安装内容 |
|---|---|---|---|
| ax-jump | 单路 EPYC 7H12，64核128线程，256GB，960GB SSD | 唯一跳板、构建、协调；测量时客户端1/迁移源 | 通用包、客户端包、构建包；实验仓库、BrewFS、producer、JuiceFS、回放和小文件工具；构建镜像用 Docker |
| ax-client2 | 同上 | 客户端2/迁移目标 | 通用包、客户端包；与 ax-jump 完全相同 SHA 的客户端产物和实验脚本 |
| ax-obj1 | 双路8171M，52核104线程，128GB，960GB SSD | RustFS节点1 | 通用包、Docker Engine/Compose、RustFS冻结镜像、本机OTel采集器 |
| ax-obj2 | 双路6258R，56核112线程，256GB，960GB SSD | RustFS节点2 | 同 ax-obj1 |
| ax-obj3 | 双路8180，56核112线程，256GB，960GB SSD | RustFS节点3 | 同 ax-obj1；交付时核对是否被替换为8269CY |
| ax-obj4 | i9-14900K，24核32线程，128GB，960GB SSD | RustFS节点4、Redis | 同 ax-obj1，另加 redis-server/redis-tools |

选一台7H12作构建机，是根据64核和256GB内存为并行Rust编译选型，并非已实测它在所有任务上最快。i9单线程更强，但核数、内存更少，还承载Redis。初始 `CARGO_BUILD_JOBS=24`，内存/磁盘有余量再增至32；BrewFS和RustFS顺序编译，不同时堆满两套构建。

首轮按现行小试1槽、C3/C4各自独立一万文件开展；通过后再准备1/4/8/16并发，每客户端最多8槽，每槽2 vCPU、8GiB、swap=0。32槽另做容量和调度资格。这里的槽位执行固定文件操作，不启动真实Agent或模型。测量期间 ax-jump 只协调和运行被测客户端，停止编译、下载、镜像分发和压缩；C4目标也独占锁。

## 安装清单

通用包（六台）：`openssh-server openssh-client ca-certificates curl rsync python3 python3-venv util-linux procps iproute2 iperf3 sysstat fio jq zstd unzip tar chrony xfsprogs e2fsprogs smartmontools`。使用系统 `/usr/bin/fio`，保存包版本。全部节点部署本仓库 `bench/runtime/observe.py` 及其依赖代码；记录主机ID、磁盘序列号/文件系统、内核、CPU拓扑和时钟偏差。

客户端另装：`fuse3 libfuse3-3 libsqlite3-0 libssl3t64`，核 `/dev/fuse`、`fusermount3`、cgroup v2 与实际挂载权限；对收到的二进制执行 `ldd`，补齐实测缺失的运行库。按实际运行用户配置 `user_allow_other`，不默认给所有用户免密sudo。

仅 ax-jump 装构建包：`git build-essential pkg-config clang libclang-dev cmake ninja-build protobuf-compiler libssl-dev libfuse3-dev libsqlite3-dev libudev-dev`，另装 rustup/Rust。RustFS分支目前声明stable；构建当天解析具体版本，保存 `rustc -Vv`、`cargo -V`，整个campaign固定。仓库工具要求Rust至少1.88；`cargo --locked`不通过则解决工具链/依赖，不删除lockfile。

Python虚拟环境安装 `scripts/requirements.txt`（当前jsonschema 4.23.0）；S3探测另需boto3，部署时保存实际 `pip freeze`。实验核心不需要AX控制平面、Kubernetes、OpenCode、模型API、TiKV、Ceph或GPU软件。

Docker用于四台对象节点及跳板构建/功能检查。两台客户端若执行现有会启动私有Redis/RustFS的smoke脚本，也安装Docker及Redis；这些临时服务只用于隔离的功能检查，不能冒充四节点性能后端。使用同一发行渠道和精确版本，记录Compose版本、RustFS和OTel镜像digest。Redis使用Ubuntu 24.04仓库的7.x包并固定实际包版本；JuiceFS选择官方v1.4.2 Linux amd64发布产物并核校验值，先核原生clone和所需挂载参数，失败不偷偷换版本。

## 仓库、分支与产物

| 组件 | 仓库与分支 | 处理 |
|---|---|---|
| 实验代码、回放、小文件工具 | `https://github.com/Ivanbeethoven/ax-multi-agent-experiments.git`，main | 下载最新一次；编译本仓库 tools，不混用早期独立回放仓库产物 |
| BrewFS与producer | `https://github.com/brewfs/brewfs.git`，main | 同一release保留default features，加workspace-overlay、observability、rustfs-ec-block-size-hint |
| RustFS | `https://github.com/Ivanbeethoven/rustfs.git`，feat/dynamic-block-size-rebased | release编译一次，封装带源码revision标签的镜像，四节点同digest |
| JuiceFS | `https://github.com/juicedata/juicefs`，v1.4.2发布版 | 使用官方二进制，不自行编译；保存版本与SHA256 |
| Redis、Docker、OTel | 发行包/官方镜像 | 不编译；冻结实际版本/digest |

本次 `git ls-remote` 核对：BrewFS main为 `ca07159f3da4b670e4aeb878faef928e69bc1634`，RustFS指定分支为 `f4ed202508f2153afb7956bae6f7799b60eccd00`。这两个SHA只是本次查验记录，不是下次构建输入。构建前重新同步分支，冻结后同一扩展曲线、重复与消融保持相同产物；升级另开campaign。

**消融和fork仍有候选补丁，不能写成main已经全部支持。** 当前已通过的小样涉及只读Overlay候选、Pack-off/all-off候选、Packed原生fork候选；其中五项检查构建明确使用 `package.brewfs.opt-level=0`。这些诊断二进制禁止用于性能计时。先将所需补丁整理成可审查的统一候选提交，记录基于main的SHA、完整补丁SHA与能力，再用正常release编译。候选分支名尚未确定，不虚构已发布分支；缺候选只阻塞相应实验块。优先等待上游合入，合入后用最新main复验。

构建路径约定：`/srv/ax-src/{experiments,brewfs,rustfs}`；每campaign独立 `/srv/ax-build/<campaign>` 保存target、bin、日志和回执。在跳板顺序执行以下构建步骤（先设AX、BREWFS_SOURCE、RUSTFS_SOURCE、BUILD为实际绝对路径）：

```bash
export CARGO_BUILD_JOBS=24 CARGO_INCREMENTAL=0
# 不使用 target-cpu=native；AMD构建的产物还要在Intel上运行。
export RUSTFLAGS='-C target-cpu=x86-64'
export CARGO_TARGET_DIR="$BUILD/targets/brewfs"
cargo build --manifest-path "$BREWFS_SOURCE/Cargo.toml" --release --locked \
  --features workspace-overlay,observability,rustfs-ec-block-size-hint \
  --bin brewfs --bin packed_v3_snapshot_fixture

python3 "$AX/infra/rustfs/resolve_backend.py" source \
  --rustfs-source "$RUSTFS_SOURCE" --output "$BUILD/source-version.json"
export CARGO_TARGET_DIR="$BUILD/targets/rustfs"
cargo build --manifest-path "$RUSTFS_SOURCE/Cargo.toml" --release --locked --bin rustfs

export CARGO_TARGET_DIR="$BUILD/targets/tools"
cargo build --manifest-path "$AX/tools/fs-probe/Cargo.toml" --release --locked --bin smallfiles
cargo build --manifest-path "$AX/tools/full-fuse-replay/Cargo.toml" --release --locked
```

构建保留仓库正常release优化，不采用诊断opt-level=0，不用旧 `build-local.sh brewfs` 的 `--no-default-features` 和entity组合。上述是编译核心命令，不是完整构建证明脚本：部署前另保存源码干净状态/候选补丁、命令、退出码、工具链、动态库依赖、产物SHA256；镜像在实际兼容的运行基础镜像上验证版本及S3读写，再运行 `resolve_backend.py image` 核源码与镜像标签。旧镜像打包器要求旧attestation目录且固定Debian基础镜像，不能直接接上新输出目录；尤其不能假定Ubuntu链接的二进制能在旧Debian镜像运行。

各架构型号至少各一台运行 `--version` 和小读写验证，排除指令集、glibc和动态库不兼容。通过私网rsync分发bin、脚本、镜像与SHA256清单，接收端逐项核验；整个campaign不逐台编译。

## 服务、网络与磁盘

- 一池四对象节点，每台一个真实本地SSD数据路径。四节点均配置四个 `RUSTFS_EC_*` 开关为true，动态块消融另建条件并统一关闭。实际端点列表、erasure set、data/parity数和持久化B/q必须从部署与探针记录，不预先声称2+2已经验收。
- **现有生成器不接受此拓扑**：`infra/rustfs/generate_rustfs.py` 的distributed固定4节点×4盘，`rustfs_node.py` 要求独立挂载。实施前新增显式4节点×1盘模式及测试，保留旧模式；不能建四个目录骗过磁盘检查。若960GB也是系统盘，记录系统/日志共享情况并适配单盘验证，不能自动格式化系统盘。
- Redis仅绑定ax-obj4私网，密码存权限0600的私有配置。初始maxmemory=48GiB，预留64GiB Redis预算，RustFS先限制32GiB；总额及OS余量实际核算。持久化沿用 `appendonly yes`、`appendfsync always`、`save ""`、`noeviction`。i9通过lscpu识别P/E核及SMT关系，Redis固定到独立P核且RustFS排除其同胞线程；不写死CPU编号。共SSD仍有竞争，单列AOF rewrite期间磁盘延迟。
- 只有ax-jump开放来源受限的公网SSH；其他节点经ProxyJump。Redis6379、S3/RustFS节点通信9000及实际额外端口只允许实验私网；控制台9001不开放公网。镜像离线分发或私有registry，不能把Docker API暴露公网。
- 每台RustFS部署本地OTel collector：接收loopback4318并导出本地Prometheus端点，再由本机observer保存。两客户端采BrewFS8910/JuiceFS9567及Packed `.stats`；Redis采INFO、持久化和内存；全六台采/proc、cgroup、磁盘、网卡、空间/inode。启动前验证计数随操作增长，缺指标不记成零。不需要额外搭建Grafana集群。
- observer默认5秒采样、最大间隔30秒，保留逐操作原始延迟和阶段标记，不能用5秒采样推导操作P99；按现行协议重新做on/off开销校准。状态SQLite放ax-jump本地盘，小型结果汇总到这里；原文测量期间留各节点。
- 先做双向iperf3及四节点同时发送测试，区分公网/私网、每台/共享限制；磁盘预检仅用专用可删测试文件，禁止对裸盘跑写测试。未实测前不把商家“50M”写成MB/s。带宽饱和或共盘竞争应在结果标注，不靠增加槽位掩盖。

## 数据与执行顺序

跳板从OSS下载；这些服务器不在阿里云VPC，**不使用 `--internal`**。已有匹配SHA的输入直接复用：

```bash
python3 scenarios/k8s-containers/scripts/download_archives.py \
  --source oss --cases pytest-full-02 --out /srv/ax-input/archives
```

完整来源、归档大小、SHA与解包目录在 [archives.json](../../../scenarios/k8s-containers/archives.json) 和 [数据目录](../../../scenarios/k8s-containers/data-catalog.md)。先只取pytest，后续再取django/ripgrep/archive；输入events/blobs同宿主共享只读，每槽独立可写工作区。C3/C4按 [TENK-INPUTS](TENK-INPUTS.zh.md) 分别生成，不复用一套父树。

1. 收实际IP、SSH指纹、root/sudo权限、SSD与私网信息；冻结inventory，两个7H12只能在交付后以实测确认哪个作跳板。
2. 跳板装依赖、取分支、编译和打包；其他节点只装运行依赖。确认磁盘空间能同时容纳target、输入、工作区和失败现场。
3. 完成四节点单盘适配与四节点观测；私网部署Redis/RustFS，做S3 PUT/GET/Range、B/q及全部型号产物兼容检查。
4. `python3 -m bench.experiment_smoke_matrix --list-blocks`，按 [BLOCKS](BLOCKS.zh.md) 先dry-run再逐块小样；现有smoke启动的loopback临时服务不能作为四节点性能部署，跨机迁移要改为直连私网后端，不照搬公网SSH隧道计时。
5. 最新main已增加owned scratch清理。按 [CLEANUP](CLEANUP.zh.md) 使用 `--scratch-parent`；成功且退出证据完整才回收，失败保留。正式共享RustFS对象通过原生删除/GC回收，不能套用小试私有目录删除逻辑。
6. 通过完整导入、冷重挂、观测校准和空间门槛后，再安排一万文件/完整trace以及扩展曲线。每块保留同组full对照；共享六台只允许一池顺序测量，构建也占用池锁。每盘/inode始终按新增峰值后至少20%余量准入。

交付物：实际inventory、build-manifest及SHA清单、backend-version、私有服务配置（不入Git）、网络/磁盘基线、各块预览与资格结果、分阶段指标及清理回执。当前只完成方案与源码检查，未编译新二进制，也未宣称正式实验可以直接开跑。
