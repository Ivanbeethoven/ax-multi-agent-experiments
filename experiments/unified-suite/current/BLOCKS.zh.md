# 8 个实验块与执行顺序

本次只整理流程，没有开机或启动实验。已有验收结果不重跑。最小脚本已通；正式计时还要核本批产物、实际输入、缓存和观测。方案分成 8 块，每块可以独立交接、保存结果、处理失败。

**实验块是分工单位；一次对照组才是测量单位。** 同一个输入、规模和重复的所有对照必须在同一池依次运行，不能把 BrewFS 和 JuiceFS 分到两个池比较。一个实验块有多个条件时，各条件独立准备和验收。

## 每块测什么

| 块 / 运行名称 | 输入与对照 | 主要指标 | 正确性与目前缺项 |
|---|---|---|---|
| 1 基础读写 `c1-io` | pilot 每模式 64 MiB；BrewFS flat 对 JuiceFS。顺序读/写、随机读/写分别成组。 | 固定工作量耗时、吞吐、IOPS、fio 延迟；fsync 和排空分别记。 | 冷重挂 CRC。JuiceFS 混合读写已跳过；单边 BrewFS randrw 只能单列诊断。最新产物仍需同条件校准。 |
| 2 动态块与读取粒度 `c1-layout` | 同一 flat 产物，auto/off；固定 hint 64 KiB、256 KiB、1 MiB、4 MiB；另做固定 B 的 q 开关。 | 实际 B/q、Range 次数、物理读写量、完成时间、空间占用。 | 内容相同、落盘布局确实变化；拆 q 时 B 必须相同。最小探针只验配置和读写，正式要补布局与物理 I/O 证据。 |
| 3 完整 trace `c2-trace` | 先用完整 pytest；BrewFS basic、full、JuiceFS。后续 django、ripgrep、archive 各自成组。 | 回放时间、run+drain、操作数/成功字节、CPU、元数据及对象请求。 | 初态、终态、硬链接、权限、SQLite、冷重挂；完整 Packed 发布曾超时，须先完成导入资格。不能截取前 N 次冒充完整 trace。 |
| 4 热 lower 上的 Overlay `c2-overlay` | 同一 workspace 产物，只切 Overlay。冻结可放入共同缓存的 lower 工作集，两臂分别预热。 | `T_on / T_off - 1`、路径查找/读耗时、lower 请求与字节；可写条件另测首次修改及 fsync。 | 两臂末遍 lower 后端读请求/字节均为 0，操作确实进入 BrewFS。当前只读候选已验证，可写条件尚缺适配；达不到热态则不进入计时。 |
| 5 trace 消融 `c2-ablations` | 与块 3 相同的完整输入；分别做 full/Pack-off、full/dynamic-off、full/all-off 三组。 | 时间变化、KV 与对象请求、物理 I/O、CPU；准备成本单列。 | 每组自己的 full 对照和同一二进制。Pack-off 保持 payload；dynamic-off 使用新对象；all-off 先物化私有初态，不解释为纯 Overlay 开销。现有 Pack/all-off 诊断产物不能作性能产物。 |
| 6 共享小文件 `c3-smallfiles` | 独立的 10,000×4 KiB 输入；full/JuiceFS 为系统对照，full/Pack-off 为另一组。 | 冷读、热读、首次私有写、热写、混合读写的阶段时间；KV/对象请求、每工作区新增空间。 | 逐读内容、父/兄弟隔离和冷重挂。pilot 操作量沿用 WORKLOADS；8/128 文件检查已通，仍需一万文件运行适配及完整校验。Pack-off 未就绪不阻塞系统对照。 |
| 7 原生派生 `c4-fork` | 另一套独立的 10,000×4 KiB 父树；BrewFS 原生 fork 对 JuiceFS 原生 clone。pilot 各派生 1 个。 | 派生命令、首次正确读、全部就绪、修改+同步+排空时间，新增上传和空间。 | 非空父树一致、child 继承 lower、父/兄弟隔离、冷重挂。只用原生派生；两文件已通，一万文件和统一优化产物仍需验证。 |
| 8 工作区迁移 `c4-handoff` | 同一文件树；BrewFS/JuiceFS 各做 stay 与 move，共 4 个条件。正式 pilot 用原 894 项输入；脚本检查用独立的 113 项输入。 | 源停写至目标首读、首个持久写及固定后续文件操作结束；排空/卸载/挂载/校验各记时间。 | 源已停写卸载，目标同一卷、新缓存、全树一致、后续读写正确。目标为另一宿主。113 项已通；正式输入、目标零债务和私网配对待验。无 Agent/模型调用。 |

块 5 的三组消融互不等待；块 6 的系统对照与 Pack 消融分开计数。功能检查允许一起核入口，性能测量必须各带本组的 full 对照，不能拿块 3 的时间来相减。重复、输入与指标定义仍以 [WORKLOADS](WORKLOADS.zh.md) 和 [design.json](design.json) 为准；pilot 一次只报实测值，不从一次结果推 P99 或稳定提升。

块 8 的旧写者拒绝是独立子项。没做时只报告干净迁移，不宣称故障恢复或原生 fencing 已通过；旧冻结 C4 的验收要求不变。

## 只共享准备，不串成一条长队

共同准备只做一次：

1. 在远端构建；按本批最新分支冻结源码、features、补丁、二进制 SHA 和 RustFS digest。统一性能产物与诊断候选分别登记。
2. 跳板下载一次归档并核 SHA，通过私网分发；各 worker 核 SHA 后保留只读副本。每轮恢复初态是新建 workspace/upper，不是重新下载 trace。
3. 分别生成 C3 与 C4 输入、预期树和 SHA；不要用 C3 的目录结构代替 C4。
4. 各块核自己的导入、重挂、指标和容量；合格才进入该块的计时队列。大 trace 发布失败只挡相关块，不挡 C1 或独立的小文件输入。

| 准备产物 | 使用者 | 复用范围 |
|---|---|---|
| build-manifest、工具、RustFS 镜像 | 所有块 | 相同平台和构建参数分发一次；不同候选明确区分 |
| trace 归档、events/blobs、初终态清单 | 3、5 | 宿主上的只读输入；每次测量独立可写工作区 |
| 热工作集与每段预期内容 | 4 | 两臂共用清单；每臂在自己的计时挂载上重新预热 |
| C3 一万文件及独立 oracle | 6 | 经核验的不可变 base；每次新建 child |
| C4 一万文件及独立 oracle | 7 | 两系统使用同一份父树内容；发布/import 在计时外 |
| checkpoint 文件树、固定后续操作 | 8 | 四个条件初态相同；每次独立卷身份，move 内源/目标同一卷 |

Packed 导入应走 producer/发布接口，不通过 FUSE 逐文件上传。将扫描/打包、上传、发布、校验分开记录；不能把全部等待都记成“下载慢”。先看阶段回执，再调整对应步骤。

## 两池的分工

默认一池。只有第二块已具备实际输入、可用产物及运行入口，才启用第二池。下面是优先次序，**不是前后依赖**；有阻塞就取下一项就绪条件。

| 队列 | 优先处理 |
|---|---|
| 池 A：数据读写 | 1 基础读写 → 2 动态块 → 3 完整 trace → 5 trace 消融 |
| 池 B：工作区 | 6 共享小文件 → 7 fork/clone → 4 热 Overlay → 8 文件系统迁移 |

- 两池各自独占客户端、Redis、RustFS 宿主与数据盘。不同 namespace、bucket、端口或容器，不代表性能隔离。
- 源/目标、共享物理盘、服务和瓶颈网络都登记为资源。C4 的目标也要锁定；不能同时拿它给另一块编译或跑负载。
- 同池从导入、校准到各臂测量、排空、冷验和封存串行。切换 RustFS dynamic 配置只影响本池，所有旧使用者退出后再切。
- 共用跳板可传小型控制消息；下载、分发、压缩、归档上传放在两池都不测量的传输窗口。测量时原始数据写各节点本地盘。
- 全部活跃负载最多 32 槽；C1 计 1 槽，迁移目标替换源。任何一池跑 32 槽时，另一池不能再测负载。
- 每条扩展曲线和本组重复尽量留在同池。不能拿 A 池的 BrewFS 时间除以 B 池的 JuiceFS 时间。

例如可同时做“块 2 + 块 7”、“块 3 + 块 6”、“块 5 + 块 4”，前提是两池物理资源独立且槽位总数合规。同一 rbm 上的几个容器只用于顺序功能验证，不能画成两条独立性能曲线。

小规模用 [PILOT-HARDWARE](PILOT-HARDWARE.zh.md)；正式四节点 RustFS 用 [CONFIGURATION](CONFIGURATION.zh.md)。两种拓扑分别记录。不能因小试用一节点，就将结果标成四节点集群。迁移目标只在块 8 前申请；一池没有就绪项就收尾释放，不开机等修代码。当前资源归属和清理规则继续有效。

## 每个条件的固定流程

结果放在 `runs/<campaign>/<block>/<condition>/r01/<arm>/attempt-001/`。失败保留原目录；重试换 attempt，不覆盖、不自动重投。

| 步骤 | 做什么 | 留下什么 |
|---|---|---|
| 预览 | 核参数、输入路径、真实产物、目标机器；不启动服务 | 完整命令和版本/输入清单；未就绪原因 |
| 准备 | 查上一项进程/挂载；取池锁；核容量，再导入独立初态 | 主机/盘/namespace 身份，准备阶段耗时 |
| 校准 | 同一实际规模、采集设置下的 on/off；已有匹配证据可复用 | 配对原文；不能由“已留硬件余量”代替 |
| 测量 | observer 就绪、挂载、必要的预热与 barrier，再执行固定工作量 | 原生负载输出、阶段时间、各层计数 |
| 关闭与验证 | 排空、原进程退出、冷缓存重挂并读回，验证挂载也正常退出 | 内容/隔离、两次零债务、进程与挂载回执 |
| 封存与清理 | 核哈希，保存小型结果；回收本块已归档且无引用的临时内容 | 归档索引、实际剩余空间/inode、资源归属和下一步 |

至少分别记导入/发布、挂载、预热、负载、排空、冷验、归档时间。阶段可以嵌套，不能将父子计时直接相加。现有最小运行器提供 `stages.jsonl`；它没有的细分项写“未采集”，不据总耗时猜测。

每盘和 inode 预留至少 20%，按导入、私有写、AOF rewrite、失败现场及归档峰值核算。准备前和收尾后各查一次。删除对象或元数据须确认所有引用已关闭；不要直接删 RustFS 数据目录。容量不够先清本任务可再生且已确认无用的缓存，不在计时中扩盘或 GC。

具体保留范围、8 块清理边界和命令见 [清理流程](CLEANUP.zh.md)。最小检查可用 `--scratch-parent` 为每项分配独立临时目录，配合 `--scratch-peak-bytes`、`--scratch-peak-inodes` 在派发前核余量；通过且退出核验完整才自动清理。失败保留现场，收尾仍持有本池及目标的锁。

失败或超时只停止本块。先核其服务/挂载确实退出，再接下一块；独立池可以继续。每阶段使用已冻结的超时，保留卡住的阶段和原始输出，不能无限续跑或截短输入凑通过。

## 现有脚本怎样按块用

下面的入口是**最小功能检查**，不生成正式性能结果，也不创建云资源。它不接受一万文件规模参数；正式规模适配未完成时，不要把 `--tiny` 的通过当作一万文件结果。

```bash
python3 -m bench.experiment_smoke_matrix --list-blocks
```

在实际 Linux 节点准备私有 `common.json`：内容是共享 CLI 参数数组，填现有文件的绝对路径和本批真实版本，不要复用旧机器路径。例如：

```json
[
  "--brewfs", "/opt/ax/bin/brewfs",
  "--producer", "/opt/ax/bin/packed_snapshot_fixture",
  "--smallfiles", "/opt/ax/bin/smallfiles",
  "--juicefs", "/opt/ax/bin/juicefs",
  "--brewfs-revision", "<本批完整源码 SHA>",
  "--rustfs-image", "<本批已核来源的镜像 digest>",
  "--rustfs-revision", "<本批完整 RustFS SHA>"
]
```

上述是结构示例，尖括号要替换。按块补下面参数。用 `--case-overrides overrides.json` 可给具体 case 传不同候选的路径、源码 SHA 和构建回执；键名由 `--list-blocks` 列出。涉及性能对照时，本组各臂仍必须用同一构建，不能借 overrides 混用诊断与优化产物。

| 块 | 额外参数及当前输入 |
|---|---|
| 1、2 | 无；最小入口固定小输入，不代替 fio 正式模式 |
| 3 | `--trace ... --replay ... --full-trace --trace-timeout 300`；先用已核验的 367 回调输入，完整 pytest 单独选新输出和匹配超时 |
| 4 | `--brewfs-provenance ...`，且两臂使用同一只读热 lower 候选和 producer |
| 5 | 同块 3，加匹配候选的 `--brewfs-provenance ...`；full/Pack-off/all-off 使用同一候选 |
| 6 | `--tiny` 为 8 文件；Pack-off 及其 full 对照用同一候选并传 provenance |
| 7 | BrewFS fork 传其发布候选的 `--brewfs-provenance ...`；JuiceFS 使用同一父内容 |
| 8 | `--checkpoint ... --prepare-timeout 180 --ssh-config ... --target-host ... --target-repo ... --target-tools ... --target-check-timeout 900`；无需 `--opencode` |

先预览，不创建输出目录、不挂载、不调用模型：

```bash
python3 -m bench.experiment_smoke_matrix --block c2-trace \
  --common-args /private/common.json --out /srv/ax-runs/c2-trace-001 \
  --trace /srv/ax-input/small-recorded --replay /opt/ax/bin/full-fuse-replay \
  --full-trace --trace-timeout 300 --dry-run > /private/c2-preview.json
```

预览只检查参数，不证明二进制、镜像、网络或输入可用。核实际路径、版本和池锁后，去掉 `--dry-run` 才执行。rbm 仍使用已有的 `/home/dev/.ax-rbm-heavy.lock`；其他机器也要持有本池及迁移目标的资源锁。不要将不同块直接后台并发到同一 rbm。

运行生成 `execution-plan.json`、每 case 的日志与 `receipt.json`、`summary.json`。遇到 failed/blocked、缺回执或非零退出码立即停止本块，未开始项仍能从 plan 查到；不会继续操作可能残留的挂载。排查后用 `--only CASE` 和新输出目录补检查；不覆盖旧记录。

收小型结果和阶段时间：

```bash
python3 experiments/unified-suite/pilot-aliyun/scripts/summarize-smoke-check.py \
  --root /srv/ax-runs/c2-trace-001 --out /private/c2-summary.json
```

这份汇总也保留启动失败、缺回执和子进程非零退出。原始实验的采集、正式校准、冻结校验器继续按 [EXECUTION](EXECUTION.zh.md)执行。

本次[流程检查记录](../../../validation/parallel-blocks-20261113.json)：Linux 688 项测试运行完毕，26 项按条件跳过，无失败。未启动云机或性能实验，临时测试目录已清理。
