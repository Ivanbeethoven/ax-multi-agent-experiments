# 执行 C1～C3 的交接 prompt

这份文档提供 C1～C3 的分组执行方法，使用已有六台服务器完成 BrewFS、原生 JuiceFS 和 JuiceFS Overlay 对照及消融。直连 C1～C4 轮次先读 [直连交接](HANDOFF-DIRECT-NETWORK.md)，采用其中的网络配置、执行范围与结果目录；C4 操作见其独立交接。

## 1. 进入目录并读取文件

当前本机仓库：

```powershell
Set-Location -LiteralPath 'C:/Users/HXY/.cache/ax-agent-cluster-plan-20261114'
git status --short
git branch --show-current
```

该目录属于 `Ivanbeethoven/ax-multi-agent-experiments`，执行方案使用 `main`。保留现有目录整理和未提交修改。先按顺序读：

1. `AGENTS.md`、`README.md`、`experiments/README.md`。
2. `docs/SETUP.md`、`docs/MOUNTS.md`、`docs/METRICS.md`。
3. `experiments/c1-io/README.md` 及 `experiment.json`。
4. `experiments/c2-agents/README.md` 及 `experiment.json`。
5. `experiments/c3-ml/README.md` 及 `experiment.json`。
6. `docs/ABLATIONS.md`、`configs/comparisons.json`。
7. `configs/campaign.json`、`cluster.json`、`fio-profiles.json`、`mount-profiles.json`、`cache-sharing.json`、`trace-inputs.json`。

当前任务参数以这些文件为准。`archive/` 保存历史结果与当时的脚本，可查阅实现，不按旧计划启动整批测试。

## 2. 建立本轮结果目录

首轮编号为 `run-001`。已有该目录时顺延，禁止覆盖旧轮次。

```powershell
$Repo = (Get-Location).Path
$Run = Join-Path $Repo 'results/cluster-c1-c3/run-001'
if (Test-Path -LiteralPath $Run) { throw 'Choose the next unused run number' }
New-Item -ItemType Directory -Path $Run | Out-Null
$Py = 'C:/Users/HXY/AppData/Roaming/uv/python/cpython-3.14-windows-x86_64-none/python.exe'
& $Py -X utf8 -m unittest discover -s tests/cluster -v
& $Py -X utf8 -m bench.cluster.plan --group C1 --out (Join-Path $Run 'plan/C1.json')
& $Py -X utf8 -m bench.cluster.plan --group C2 --out (Join-Path $Run 'plan/C2.json')
& $Py -X utf8 -m bench.cluster.plan --group C3 --out (Join-Path $Run 'plan/C3.json')
```

实际运行输出：六台的 `/srv/ax-cluster/runs/c1-c3/run-001/`。A366 汇总，其它节点写 `hosts/<主机名>/`。最后把配置、原始指标、日志、负载输出、直接内容比较结果和汇总全部取回上面的本地 `$Run`。结构严格使用 `docs/METRICS.md`，不要把结果分散到新的日期目录。本轮结果暂不推送 GitHub。

## 3. 先检查已有环境

使用以下别名，控制连接数量并复用连接：

- A366：`ssh idcai-60001`，客户端、协调、编译。
- A355：`ssh idcai-60525`，客户端、Redis。
- A672：`ssh idcai-60103`，客户端、RustFS。
- A601：`ssh idcai-60439`，客户端、RustFS。
- A635：`ssh idcai-60533`，客户端、RustFS。
- A368：`ssh idcai-60555`，客户端、RustFS。

读取 CPU/NUMA/SMT、内存、磁盘空间、现有进程与挂载、服务端口、网络接口和路由，写入 `inventory.json`。确认本任务上次的临时服务是否仍在运行，按所属目录和 PID 处理；其它任务的进程、挂载和数据保留。不要重新租服务器、格式化磁盘或全机清空缓存。

按 SETUP 的命令检查依赖。RustFS 固定四节点各一盘、EC 2+2，Redis 放 A355。客户端和后端在同机时按配置限制 CPU、内存及缓存。按 NETWORK 使用 eth0 直连，保存吞吐、RTT、MTU、路由和实际服务连接。

BrewFS 同步最新 main；RustFS 同步 `Ivanbeethoven/rustfs:feat/dynamic-block-size-rebased`；JuiceFS Overlay 使用 `Ivanbeethoven/juicefs:codex/lazy-workspace-fork-20261113`；官方 JuiceFS 固定正式版本且至少 1.4.2。构建在 A366 完成一次后分发，记录源码提交、编译参数、工具链、二进制位置和大小。先检查 A366 上已有构建任务，避免重复构建。单个对照与重复组期间保持软件版本不变。

## 4. 准备输入、挂载与观测

优先使用服务器已有的八类完整 trace，缺少的按 `trace-inputs.json` 从 OSS/Drive 下载，写入本轮 `inputs.json`。记录实际初始树、events、blobs、终态和工具日志路径。输入放在被测挂载外，repo 与 HOME/XDG 放在工作区内。

用户禁止计算、生成、比较和验证任何 hash/checksum。先检查旧下载器、回放器 pacing/xattr 分支与结果包装器，改为直接内容比较、事件数量、序号与字节长度检查，再运行。不要执行旧严格校验命令。

挂载按 `mount-profiles.json` 生成，保存每挂载配置和实际进程配置。默认每个工作区 4 GiB 内存、2 GiB 磁盘缓存，所有同机挂载按总预算计入。共享缓存条件需要实际跨挂载复用 metadata/pack/block，保持私有修改与上传暂存隔离。不要把有独占锁的缓存目录配置成多个进程共用。

每机一个采集进程，每 5 秒记录主机、进程/cgroup、挂载、Redis/RustFS 指标，阶段边界保存计数快照。先做 1,000 文件及单任务功能检查，再验证 8 个挂载和采集开销。确认负载开始前已经产生原始指标，随后进入正式条件。

## 5. 手动执行顺序

按 JSON 导出的任务逐组执行，使用现有工具和简短脚本；遇到脚本或配置错误直接修复，并保存最终可复用命令。不要先投入时间重写整个调度框架。

先完成 C1 基本 fio 和文件/目录测试，再跑 C1 B/q、读写尺寸与混合对象布局；随后 C2 的 8、32、64、128 个工作区；再跑 C3 准备、读取、派生、复用；各组对应的消融紧接其参照条件执行。同一后端一次只执行一个计时条件，各系统按重复编号轮换顺序。编译、下载、清理和其它性能测试不与计时段同时运行。

**C1 必须覆盖多尺寸读写。** 后端 B=64/256/1024/4096 KiB；应用随机请求=4/64/256/1024 KiB，分别纯读/纯写；另测 1 MiB 顺序读/写。文件系统 chunk 固定 4 MiB。所有工作量、job 数、同步方式见 C1 配置。动态布局实验每档重新准备对象，保存请求 B、实际 B/q 和 EC 布局；不同尺寸的 PUT 成本、随机覆盖写成本和远端可读时间一起记录。JuiceFS 两组执行同样的随机/顺序负载，一份相同对照可供不同 B 的曲线复用。输出读取和写入两类图。

**C2 使用完整 trace。** 三系统复用同一任务、主机安排、输入、写入内容和等待表。等待只插入真实工具边界，保留计划/实际等待；8 类任务等量。报告各类任务时间、任务吞吐、实际读写字节、操作数、重复读取比例、S3 与磁盘字节。任务数随并发增长时，不直接用不同任务总量的批次耗时计算加速比。

**C3 只读数据使用 Packed。** 1 万/10 万/100 万个 4 KiB 文件的准备，从完整本地目录到另一机器可读，扫描、属性、打包、上传、注册全部计时。10 万文件使用 1/32/128 个读者，固定种子 shuffle，读三遍；再测 16 轮复用。JuiceFS 的只读读者直接共享目录，私有修改实验才 fork/clone。所有输出写入私有层。准备速度与 JuiceFS 导入上传速度并列，不能只展示打包后的读取时间。

C1～C3 默认共 107 个条件、507 次系统运行，包含三次重复：C1 为 81 个条件/321 次，C2 为 11 个/81 次，C3 为 15 个/105 次。C2 的 192 并发、C3 的百万文件高并发属于独立扩展，本轮先不加入。

## 6. 消融、进度和收尾

按 `comparisons.json` 执行单项变化比较，记录对应机制指标。C3 的 Packed/KV × 元数据缓存开/关四组都要保留。等待与写入策略单列为参数实验。缺少接口的条件写 `pending-feature` 和具体接口位置，继续完成可执行条件并修补适配；不要填入推测数值。

功能检查直接比较内容、属性、父子隔离和冷重挂结果。应用结束、上传完成、新挂载读回各自记时。每次重复保留配置、命令、退出码、stdout/stderr、原始采样、计数和检查输出。性能统计保留全部重复，重试另建 attempt 目录。

更新 `status.json`，汇报按 C 分列，例如 `C1：完成 12/81 个条件，完成 30/321 次系统运行`，并列主实验、消融、参数实验的已完成数及当前任务。一个条件只有预定系统与三次重复都完成后才计为完成。

结束后整理 `$Run/README.md`、`summary.csv`、`comparisons.csv` 和图。正文说明软件/硬件配置、C1 各尺寸读写、C2 并发任务、C3 准备与复用、各消融结果及未完成条件。原始数据全部取回并确认可读后，按本轮清单清理临时挂载、进程、缓存和对象前缀，保留公共输入与其它任务数据。
