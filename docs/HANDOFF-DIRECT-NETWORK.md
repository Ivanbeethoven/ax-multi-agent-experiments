# C1～C4 直连实验交接 prompt

从仓库当前检出目录开始，执行新一轮 C1～C4 系统对照和消融。仓库为 `Ivanbeethoven/ax-multi-agent-experiments`，执行方案使用 `main`。本机工作副本位于 `C:/Users/HXY/.cache/ax-agent-cluster-plan-20261114`；其它机器使用自己的检出路径。保留已有修改。

## 阅读与输出目录

先读 `AGENTS.md`、`configs/campaign-direct.json`、`configs/network.json`、`docs/NETWORK.md`、`docs/SETUP.md`、`docs/MOUNTS.md`、`docs/METRICS.md`、`docs/ABLATIONS.md`。再读 `experiments/c1-io`、`c2-agents`、`c3-ml`、`c4-fork` 各自的 `README.md` 和 `experiment.json`，以及 C4 的 `adapters.json`。

本轮范围与结果位置采用 `campaign-direct.json`；四个 C 的负载、消融配对、缓存及主机资源沿用当前配置。已有两份分组交接文档可查阅操作细节。本轮统一保存到：

- 本地：`results/cluster-direct/run-NNN/`。
- 六台服务器：`/srv/ax-cluster/runs/direct/run-NNN/`，A366 汇总。
- 每次运行：`C编号/condition/system/r重复/`；重试另建 `attempt-02`。

先分配第一个未使用的编号，再导出四个 C 的计划：

```powershell
$Py = 'C:/Users/HXY/AppData/Roaming/uv/python/cpython-3.14-windows-x86_64-none/python.exe'
$number = 1
do {
    $Run = Join-Path (Get-Location).Path ('results/cluster-direct/run-{0:D3}' -f $number)
    $number++
} while (Test-Path -LiteralPath $Run)
New-Item -ItemType Directory -Path $Run | Out-Null
foreach ($group in @('C1','C2','C3','C4')) {
    & $Py -X utf8 -m bench.cluster.plan --group $group --out (Join-Path $Run "plan/$group.json")
    if ($LASTEXITCODE -ne 0) { throw "Plan generation failed: $group" }
}
Copy-Item -LiteralPath configs/network.json -Destination (Join-Path $Run 'network.json')
Copy-Item -LiteralPath configs/campaign-direct.json -Destination (Join-Path $Run 'campaign.json')
```

## 先完成直连部署

| 主机 | SSH | 直连 IP | 分工 |
|---|---|---|---|
| A366 | idcai-60001 | 175.168.3.66 | 编译、协调、客户端 |
| A355 | idcai-60525 | 175.168.3.55 | Redis、客户端 |
| A672 | idcai-60103 | 175.168.6.72 | RustFS、客户端 |
| A601 | idcai-60439 | 175.168.6.1 | RustFS、客户端 |
| A635 | idcai-60533 | 175.168.6.35 | RustFS、客户端 |
| A368 | idcai-60555 | 175.168.3.68 | RustFS、客户端 |

SSH 使用上述别名并复用少量连接。服务端点与服务器之间的数据传输使用直连 IP。先保存进程、端口、挂载、磁盘余量和正在运行的任务，安排独立的性能测量时段。

按 `docs/NETWORK.md` 检查路由，运行以下命令，输出放在本轮网络目录：

```powershell
& $Py -X utf8 infra/idcai/compare_network_paths.py --out (Join-Path $Run 'network/direct') --routes direct --seconds 5
& $Py -X utf8 infra/idcai/compare_network_paths.py --out (Join-Path $Run 'network/receive-comparison') --receivers A366 A355 --repetitions 2 --seconds 5
```

网络程序输出每次 iperf3 原始 JSON。SSH 暂时断开后恢复连接，使用原命令增加 `--resume`，只补未成功的方向。完成后检查六机临时监听端口已退出。正式存储测试统一使用 `eth0` 直连，三类存储连接都要检查：Redis、S3、RustFS 节点之间。

Redis 在 A355；RustFS 四节点一盘，EC 2+2。按 `network.json` 生成监听地址、容器主机名映射与客户端 endpoint。使用本轮独立卷、对象前缀和缓存。保存 `endpoints.json` 和六机实际配置；凭据单独放 `private/`。先完成四节点 S3 写入/读回、各文件系统挂载/写入/冷重挂检查。

## 版本、输入与资源

**优先复用现有脚本、二进制、输入和运行记录。** 开始前查找本地与服务器已有入口，运行 `--help` 并检查参数。已有功能直接调用；需要适配当前配置时修改原模块或增加简短包装，继续使用统一的结果目录和字段。准备阶段输出 `tools.json`，列出实际脚本、二进制、参数与用途。

| 工作 | 优先复用的入口 |
|---|---|
| 依赖检查、构建、直连测速 | `infra/idcai/prepare_host.py`、`build_release.py`、`compare_network_paths.py` |
| 四节点 RustFS 部署 | `infra/rustfs/generate_rustfs.py`、`rustfs_node.py` |
| 任务清单、挂载配置与检查 | `bench/cluster/plan.py`、`mounts.py`、`audit_mount.py` |
| C1 文件 I/O | `configs/fio-profiles.json` 生成 fio 参数；复用 `bench/fio_workload.py` 的适用结果解析函数，以及 `tools/fs-probe/src/bin/c1-io-helper.rs`、`c1-namespace.rs` |
| C2 完整任务 | `tools/full-fuse-replay`、`bench/trace_think_plan.py` |
| C3 数据加载与成本统计 | `tools/fs-probe/src/bin/c3-loader.rs`、`tools/ml-corpus-scan`、`bench/packed_cost.py` |
| C4 合并 | `bench/cluster/workspace_merge.py` 和 BrewFS 中的 `merge_posix.py` |
| 观测与汇总 | 复用 `bench/runtime/observe.py` 的指标读取实现和现有汇总函数，输出本轮规定的原始字段 |

调用前按本轮配置确认工作量、同步方式、缓存、结果字段及直接内容检查路径。C1 使用 `direct=1`、`verify=none` 和各条件指定的 fsync 频率；模块内部的固定默认值按本轮参数适配。所有实际执行路径遵守不计算哈希及校验和的要求。

BrewFS 使用最新 main，RustFS 使用 `Ivanbeethoven/rustfs:feat/dynamic-block-size-rebased`，JuiceFS Overlay 使用 `Ivanbeethoven/juicefs:codex/lazy-workspace-fork-20261113`，原生 JuiceFS 固定同一个正式版本且至少 1.4.2。A366 编译一次后分发，保存实际提交和编译参数。C4 检查 main 是否包含 `aacfc6db` 的 merge 功能；需要单独集成功能时记录实际构建分支，配对重复内固定版本。

复用 `infra/idcai/prepare_host.py` 和 `build_release.py`。优先用服务器已有输入，缺失部分按 `configs/trace-inputs.json` 下载一次并直连分发。完整 trace 仍用 Rust `full-fuse-replay`，无需调用模型。输入清单保留 events、blobs、初始树、终态与工具边界文件的实际路径。

每工作区 4 GiB 内存、2 GiB 磁盘缓存；同机缓存总量、共享方式及上传并发按 `mount-profiles.json` 与 `cache-sharing.json`。六机合计主实验最高 128 个活动工作区；C4 最高 64 个 child 加 base/target，共 66 个挂载。服务器、CPU、内存和缓存设置在系统间保持一致。编译、下载与计时分开进行。

## 实验顺序与指标

| 实验 | 必做内容 | 完成数 |
|---|---|---|
| C1 | B=64/256/1024/4096 KiB；应用随机读写=4/64/256/1024 KiB；1 MiB 顺序读写；B/q 与混合布局、预读消融 | 81 条件，321 次系统运行 |
| C2 | 完整八类 trace；8/32/64/128 工作区；固定工具边界随机等待；名字空间、行缓存、同机缓存消融 | 11 条件，81 次 |
| C3 | Packed 用于只读大量小文件；准备/上传与 JuiceFS 导入对比；1/32/128 读者、重复读取；Packed/KV × 元数据缓存四组 | 15 条件，105 次 |
| C4 | Native fork、局部修改、合并；父目录/双方修改量曲线；4/16/64 child 归并；差异扫描 × 引用复用消融 | 30 条件，228 次 |

合计 137 个条件、735 次系统运行，已含三个重复。按 C1→C2→C3→C4 分组执行，每组先主实验、再对应消融。同一后端一次一个计时条件，系统顺序按重复轮换；192 并发等扩展项留在独立计划。

### C1 的并行安排

1. **准备阶段最多两个任务并行**：在 A366/A355 生成配置、请求偏移表、输入目录及运行命令；批量准备相同布局使用的不可变输入。服务上传和大文件生成在计时前完成，保留各阶段耗时。B/q 改变时按实际布局重新准备对象。
2. **条件内部保持 8 个 fio job 并行**：job 使用独立文件或区间，合计工作量保持配置值。8 个 job 的完成时间计为同一条件，`numjobs` 和总字节数在系统间相同。
3. **测量与离线解析重叠**：A366 运行当前条件，A355 最多一个后台任务解析上一条件已保存的 JSON、生成汇总行或准备下一条件参数。后台任务限 2 个逻辑 CPU、2 GiB 内存，计入 A355 既有预算；使用 Redis 之外的 CPU。它只读取已保存的小结果文件。
4. **共享后端的计时条件顺序执行**：四节点 RustFS、Redis 与对象盘被各条件共用。测量期间暂停对象上传、后台读回校验、GC、压缩大日志和下一条件的真实 I/O。不同桶或不同客户端仍属于同一后端。
5. **减少重复准备**：复用已构建产物、已下载 trace 和内容/布局相同的只读父数据；写入条件从相同初态派生。JuiceFS 的 8 个随机条件与 2 个顺序条件按计划各做三次，结果在不同 B 的图中复用。每次保留规定的冷/热状态和系统轮换顺序。

保存 `schedule.jsonl`：任务、主机、阶段、起止时间、CPU/内存限制和同时运行的任务。分别统计准备时间、测量时间、离线处理时间与整轮墙钟时间，用实际重叠量说明节省的时间。配置见 `configs/c1-execution.json`。

C1 记录吞吐、IOPS、延迟分布、应用/S3/物理字节、实际 B/q。C2 记录每类任务耗时、完成吞吐、操作数、重复读取比例及计划/实际等待。C3 把扫描、属性捕获、打包、上传、注册计入准备时间，并记录首次数据时间和后续读取时间。C4 区分子任务写入、上传、合并、重新可读时间，记录双方扫描记录、复制字节、冲突和冷读结果。

每机一个采集进程，5 秒一次，保存 FUSE、cgroup、主机、Redis、RustFS、对象盘和 `eth0/axwg0` 指标；阶段边界追加快照。应用结束、后台上传完成、新挂载读回分别计时。每档并发同时报告网络、CPU、元数据与磁盘负载，说明实际瓶颈。全部系统使用相同负载、随机种子、端点分配和持久完成条件。

C4 按 `docs/HANDOFF-C4.md` 完成共享 Native base、共享对象桶、离线 merge 的准备，先在四节点 RustFS 上验证 1,000 文件小任务。JuiceFS 两组使用相同 POSIX 三方合并工具。缺少的策略开关先补齐并测试，对应条件记录 `pending-feature`；已具备的条件继续执行。

## 保存与交付

保存 `inventory.json`、`versions.json`、`inputs.json`、`network.json`、`endpoints.json`、`plan/`、`mounts/`、`commands.jsonl`、`status.json`、`hosts/`、各 C 原始输出、`summary.csv`、`comparisons.csv` 和图。按 `C1 x/81、C2 x/11、C3 x/15、C4 x/30` 汇报，并附系统运行次数与未完成原因。

内容检查采用直接字节和属性比较；不生成或核验哈希及校验和。新轮次结果独立保存，网络设置作为比较分组字段。原始数据取回本地并确认可读后，按清单清理本轮临时进程、挂载、缓存和对象，保留公共输入及其它任务资源。实验中修好的命令、适配和配置整理为可复用脚本，结果暂不推送。
