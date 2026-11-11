# 阿里云 C1–C4 小规模试跑

[下一批小规模服务器配置](../current/PILOT-HARDWARE.zh.md)：新建 RustFS 默认 4×100 GiB；原冻结批次不变。

> 历史协议，仅用于原冻结计划；新实验见[当前方案](../current/README.zh.md)。

复跑先读[按需服务器并行调度手册](REPEAT-SCHEDULING.zh.md)，再按本批实际计划和资源账本开机。

本轮在阿里云按量实例上试跑。C3和C4派生使用[10万文件](PILOT-100K.zh.md)，每文件4KiB。C1、C2完整trace和C4会话流转保持原负载。实验输入、原始数据和 SHA256 见[归档下载](OSS-ARCHIVE.zh.md)及[trace目录](../../../scenarios/k8s-containers/data-catalog.md)。

截至北京时间 2026-11-11 18:02，[系统实验验收 13/28](evidence/progress/parallel-20261111-1802-01/formal-close-audit.json)，剩 15 项；22 项消融另计。同一条件跨状态库只计一次。准备、校准和本机验证不计入正式完成数。复用一台跳板，两个私网组各三台；[18:03 新增资源费用估算](evidence/progress/parallel-20261111-1802-01/cost-total.json)为 ¥96.05（约 ¥9.24/小时），原跳板、网络与 OSS 另计。

| 实验 | 正式完成/计划 | 当前情况 |
|---|---:|---|
| C1 fio | 9/10 | 双方顺序读、顺序写、随机写、随机读及[BrewFS混合读写](evidence/c1-native-randrw-01/)已验收；余[JuiceFS 混合读写](C1-MIXED-AFTER-C2.zh.md)，同组在 C2 后接续 |
| C2 完整trace | 1/5 | [flat BrewFS](evidence/cloud-20261111/05357b5089cd679c-r1-native/)已验收，回放1,747,109次操作；B组 JuiceFS 核验 C4 交接后已自动接续，正在做原完整输入的回放和冷验资格检查；[Packed 和 entity-native 有文件操作阻断](C2-ENTITY-NEXT.zh.md) |
| C3 小文件（10万） | 2/4 | [JuiceFS](evidence/c3-jfs-01/)和[BrewFS flat](evidence/c3-flat-01/)均已验收并公开原始记录；Packed完整资格通过，观测开关差约5.98%，正式样本未启动 |
| C4 派生（10万） | 1/5 | [B组 JuiceFS](evidence/c4-jfs-01/)正式验收，完整冷读、排空、退出和归档通过。A 组 [BrewFS flat](C4-NATIVE-FLAT.zh.md) 已补齐运行包，正在准备原工作区，尚未开始计时 |
| C4 流转 | 0/4 | 本机JuiceFS原会话数据库冷重开通过；BrewFS工作区重挂后写入EIO。云上跨机和真实继续任务未完成，模型调用暂停 |

11:07查到[BrewFS分支推进到ca07159](evidence/preparation/latest-progress-15.json)，RustFS未变。[11:47独立重建完成](evidence/preparation/brewfs-ca07159-build/README.zh.md)，两份源码均干净、旧产物保留。[新版本10万文件Packed本机检查通过](evidence/preparation/c3-packed-ca07159-100k/README.zh.md)。已结束的云上随机写配对固定622736b；下一批重新核分支并绑定新版本。`configure-pilot.py --phase c1`或`--phase c2`可单独生成新阶段，旧状态库保留。

[正式C1报告](evidence/cloud-20261111/c1-progress-04.json)保留七项通过及一项排除的实际指标。两边随机写的正式包及六次校准包均已独立下载，核验全部嵌套节点包：[BrewFS](evidence/cloud-20261111/7084af367e0bcfad-r1-native/README.zh.md)、[JuiceFS](evidence/cloud-20261111/7084af367e0bcfad-r1-juicefs/README.zh.md)。该对同步随机写中，BrewFS约9.2 IOPS、JuiceFS约74.1 IOPS，保留BrewFS较慢的结果。每项仅1个Agent、1次pilot正式重复。

[BrewFS随机读补跑](evidence/c1-native-randread-02/)使用新目录，沿用相同产品二进制、输入和六轮原校准，核对现用采集器镜像及配置后只执行正式样本。九个阶段均通过。fio记录读带宽59.81 MiB/s、15,312 IOPS、读P99 71.168 µs。新样本与先前JuiceFS随机读的批次不同，暂不据此报加速比。

12:11的[新增资源估算](evidence/cloud-20261111/cost-progress-15.json)为¥43.59，当前¥4.671/小时；未新增目标机或扩盘，原跳板、网络和OSS另计。[扩盘备选](OBJECT-CAPACITY.zh.md)未执行。归档工具首次云上执行的路径问题[已修复并保留失败记录](evidence/preparation/calibration-retention-fix/README.zh.md)；测量继续使用原code-07。

[最新版native路线复查](evidence/preparation/native-ca07159-guards/README.zh.md)：flat在空数据库中通过三次挂载及重挂后修改；workspace仍在重挂后写入时返回EIO。另发现本机检查脚本默认复用旧flat数据库的问题，已增加空库检查和失败后正常卸载，并在真实Redis/FUSE上验证。原失败和后续检查分开保留。

JuiceFS顺序写先后保留两次校准失败：[采集接口随正常卸载关闭](evidence/preparation/c1-juicefs-write-calibration-failure/diagnosis.json)、[Linux复用mount ID使新增的退出检查误判](evidence/preparation/c1-juicefs-write-q2-calibration-failure/diagnosis.json)。前者负载和冷CRC通过，后者尚未执行负载。两次均已完整归档、独立下载和核验。采集器现以原进程身份及正常退出回执判断卸载，运行中断连、HTTP错误、解析错误和计数回退仍判失败。[54项本机检查通过](evidence/preparation/runtime-refresh-local.json)。修订后的新六次校准和正式单元已全部通过，旧结果不覆盖、不复用。

[code-07在四个角色部署并逐文件核验](evidence/preparation/runner-code-07-deployment.json)。只更新执行与采集代码，核心二进制未变；原状态库、旧代码和失败现场保留。

C4新增前置检查发现：[首批622736b的BrewFS工作区正常重挂后，读回通过，但修改文件并fsync返回EIO](evidence/preparation/c4-native-reopen-blocker/README.zh.md)。两个新工作区均复现，挂载和进程已收尾；此项为本机真实FUSE验证，不是云上性能结果。BrewFS该路线暂不准入C4恢复性能比较，保留失败证据。

BrewFS flat的[独立本机检查](evidence/preparation/c4-native-flat-reopen/result.json)通过：首次写入、正常卸载、第二次空缓存重挂并修改/新建/同步、第三次空缓存读回一致。[三个原挂载进程与挂载均已关闭](evidence/preparation/c4-native-flat-reopen/operator-closure.json)。这条路线可继续准备派生实验；它不解除workspace路线的EIO，也不代表百万文件派生或跨机恢复已通过。

JuiceFS 的两次本机检查均通过完整工作区冷读回及原 OpenCode 数据库历史核对；[实际 resume 的模型请求超时](evidence/preparation/session-shared-juicefs-01/README.zh.md)，[关闭思考模式的独立尝试仍超时](evidence/preparation/session-shared-juicefs-02/README.zh.md)。两次已停止并核清原进程和挂载，不能记作恢复成功。没有启动目标机。C1–C3和trace回放不调用模型；C4另报存储/会话状态就绪时间，把后续模型等待单列。


05:55–05:56另核了[运行中的RustFS程序](evidence/preparation/cloud-runtime-progress-04.json)与[已部署BrewFS程序](evidence/preparation/cloud-brewfs-progress-04.json)，SHA256均与本批构建一致。执行脚本[code-02已在四个角色部署并逐文件核验](evidence/preparation/runner-code-02-deployment.json)；旧任务仍使用原脚本，不热替换。
[code-03](evidence/preparation/runner-code-03-deployment.json)加入会话迁移准备工具；[code-04](evidence/preparation/runner-code-04-deployment.json)加入容量检查、可选完整物化工具及采集收尾修复。四个角色均逐成员核验，BrewFS/RustFS二进制未变。
[code-05](evidence/preparation/runner-code-05-deployment.json)部署C3完整读回及单工作区执行流程，并固定导入器、只读核验器的实际二进制摘要。
本轮已于2026-11-11启动3台私网节点，并复用原跳板和一块独立协调盘；迁移目标机尚未创建。四盘RustFS、Redis、BrewFS和JuiceFS已完成接入及小文件写入/正常卸载/重挂读回检查，C1输入也已完成完整CRC与冷重挂校验。C1两项随机读已关闭：JuiceFS全部阶段通过；BrewFS测量及数据检查通过，归档阶段失败后补收证据，仍记失败，不输出该配对的加速比。设置见[scope.json](scope.json)，路线限制见[审查记录](../PILOT-REVIEW.zh.md)。系统计划有28个单元；未通过资格的路线保留实际失败。C3和C4派生现按10万文件另建阶段，C4恢复仍使用真实OpenCode原生会话。

首批构建使用BrewFS `main` 的 `622736b0a31caaa9007445886e626a2458bc316a`、RustFS `feat/dynamic-block-size-rebased` 的 `f4ed202508f2153afb7956bae6f7799b60eccd00`。二者均从指定分支构建，准备期间再次核对远端最新提交；运行镜像内RustFS二进制SHA256与本机产物相同。`scripts/verify-latest.py`在新批次计时前再核一次，分支推进则先重建；开始计时的配对批次固定版本。

用户确认`rbm:/srv/build/brewfs`保持`622736b`原生干净状态。实验脚本的端口就绪等待和卸载监控修复已从`fix/observer-mount-drain-race`合入（`9114cdd`），[34项本机检查通过](evidence/preparation/runtime-rbm-fix-local.json)。没有修改BrewFS源码；云上活动任务仍使用code-07，新修复尚未部署。

正式C1前于北京时间2026-11-11 04:05再次核远端，两个提交仍为最新：[分支检查](evidence/preparation/latest-before-formal-c1.json)、[运行容器与二进制检查](evidence/preparation/cloud-runtime-before-formal-c1.json)。`source-versions.json`是更早的准备记录，里面的“构建中”字段保留原义。

C2前于04:31再次核对，[两个分支均未变化](evidence/preparation/latest-before-c2.json)。BrewFS完整trace的原严格回放在第1,056,101次操作停止：同一个`TCGETS`探测，记录为ENOTTY=25，BrewFS为EOPNOTSUPP=95，原挂载已正常退出。后续单独标记的字节校验仅容许这一个非修改探测的拒绝码差异；不改原trace，不覆盖失败结果，不放宽读写数据检查。JuiceFS首次导入漏了目录权限，尚未回放便停止；修正`sync --dirs --perms --links`后在新资产重新验证。

先在Linux本地准备最新源码和产物；`BUILD`是本机独立构建盘的绝对路径：

```bash
bash experiments/unified-suite/pilot-aliyun/scripts/build-local.sh sources "$BUILD"
bash experiments/unified-suite/pilot-aliyun/scripts/build-local.sh brewfs "$BUILD"
bash experiments/unified-suite/pilot-aliyun/scripts/build-local.sh rustfs "$BUILD"
bash experiments/unified-suite/pilot-aliyun/scripts/build-local.sh tools "$BUILD"
bash experiments/unified-suite/pilot-aliyun/scripts/build-local.sh manifest "$BUILD"
```

脚本把自身保存为按SHA256命名的副本，使用独立源码目录；每次构建保留独立日志。BrewFS使用main，RustFS使用`feat/dynamic-block-size-rebased`；最终清单再次核对分支最新提交。二进制还需完成依赖、RustFS镜像来源和真实挂载资格后才用于云上试跑。

云上复用跳板和现有网络，其余节点仅私网；同池顺序比较BrewFS与JuiceFS。分为C1–C3（19个单元）、fork（5个）、handoff（4个）三批冻结inventory；目标机只在handoff时启动。没有硬性费用上限；记录计费起止、实际资源和账单，及时收尾闲置资源。

调度使用现有`baseline-parallel`模式，但两种系统都映射到同一个`pool-a`，实际不会并行占用该池。原因是旧`paired-pool`会等待整组方法全部通过资格，缺失路线会挡住同组所有测试。未通过资格的单元保留原因，不伪造通过记录；完整配对和性能结论仍按原检查器判断。

| 脚本 | 用途 |
|---|---|
| `scripts/build-local.sh`、`attest-assets.py` | 同步指定分支，构建一次，核源码、工具链、实际命令、产物和日志摘要 |
| `scripts/build-rustfs-image.py` | 从本轮二进制制作镜像，核镜像内二进制、OCI提交号和registry digest，导出内网分发包 |
| `scripts/prepare-dependencies.py` | 核JuiceFS官方发布摘要，固定OpenCode二进制 |
| `scripts/prepare-inputs.sh`、`qualify-trace.py` | 准备百万文件和完整trace，严格核回放次数、字节、目录树、硬链接与数据库副本 |
| `scripts/session-worker.py` | 单个会话阶段使用空HOME、工作区内XDG；原生export/import及历史一致性[已实测](evidence/preparation/opencode-phase-wrapper.json)，resume和跨机还待云上执行 |
| `bench/runtime/handoff.py` 的 `shared-workspace-db` | 目标直接打开原工作区内相同的数据库/XDG路径，原生export核历史后继续会话；不传输或导入额外会话文件。新增流程检查通过，实际跨机尚待执行 |
| `scripts/qualify-native-local.py --mutate-reopened`、`qualify-writer-fencing.py` | 检查重挂后写入、活跃写者互斥及过期后接管；实际失败和未到达的步骤单独记录，不用只读重挂代替恢复成功 |
| `scripts/prepare-session-input.py` | 将固定检查点放入已挂载的工作区，核完整输入、内部链接、配置替换与fsync；只调用原生export。[本机真实OpenCode导出通过](evidence/preparation/session-input-local.json)，没有模型调用；挂载控制在该项本机检查中为模拟，云上验证另做 |
| `scripts/prepare-handoff-source.py` | 准备单个共享文件系统上的会话，正常重挂后从原数据库导出；可另选一次实际resume和完整冷读回。当前仅完成语法和命令入口检查，尚未在云上运行；不创建目标机 |
| `scripts/draft-handoff-target.py` | 在目标端生成同一后端、原工作区和相同二进制的挂载配置，不格式化卷、不复制工作区。配置检查通过；实际目标挂载尚待执行 |
| `scripts/qualify-session-shared-local.py` | 在私有挂载命名空间内，用真实JuiceFS检查原数据库重开和可选resume；保护原路径，记录实际代码、空HOME、上游请求元数据及正常退出。当前两次原数据库冷读回通过，模型环节超时 |
| `scripts/probe-session-provider.py` | 默认只查模型列表；显式选择才发一次最多16输出token的小请求。已完成一次9 token检查，旧名称请求返回`deepseek-flash`；不提供文件系统恢复结论 |
| `scripts/package-session-checkpoint.py`、`check-session-checkpoint.py` | 打包、恢复同一份关闭的会话输入，逐文件核内容、模式和内部链接；拒绝活动转发密钥、损坏包及不匹配的核验工具。[894项完整恢复与反例检查通过](evidence/preparation/session-checkpoint-package-local.json)，不调用模型 |
| `scripts/fork-oracle.py` | 从C4原输入独立计算child修改后的完整预期树及固定首读摘要。[实际百万文件清单已生成](evidence/preparation/c4-fork-oracle-million-local.json)，三项本机交叉检查通过；云上派生和隔离验证尚未执行 |
| `scripts/qualify-native-fork-local.py` | 从初始空revision派生两个child，分别写入并检查父子隔离、六次挂载的正常退出和冷读回。[本机通过](evidence/preparation/c4-native-empty-fork/README.zh.md)，不调用模型；非空base发布及百万文件派生仍待验证 |
| `scripts/session-relay.py` | C4私网模型转发，主密钥仅在工作区外的私有配置，工作区使用独立转发凭据；不记录请求/响应正文，转发器不自动重试。可显式选择`--thinking-mode disabled`并记录策略，默认原样转发；OpenCode自身可能重试。云上仍为原版本，只完成[健康检查](evidence/preparation/c4-relay-health.json) |
| `scripts/qualify-opencode-session.py`、`package-session-runtime.py` | 两轮真实会话验证export/import/resume，打包已安装依赖，密钥留在工作区外 |
| `scripts/link-brewfs-tool.py`、`verify-entity-remote.rs` | 复用本轮BrewFS库构建只读核验器；检查原namespace的远端字节、目录、硬链接、ownership及核验前后状态 |
| `scripts/local-services.py` | 本机功能验证服务；四个目录共用一块盘，不能作为云上四独立盘的性能证据 |
| `scripts/qualify-rustfs-local.py`、`metric-collector.py` | 核实际B/q、完整与Range读、已知请求数和原生计数；[采集部署](OBSERVABILITY.zh.md) |
| `scripts/qualify-entity-local.py` | 实际挂载、hardlink/COW/空洞/重命名修改、正常卸载，再以只读Redis账号核原namespace |
| `scripts/package-deployment.py` | 打包本轮二进制、运行依赖、镜像和执行代码，逐成员核SHA256，供私网分发 |
| `scripts/package-code.py`、`install-code.py` | 单独冻结执行代码并安装到新的 `code-NN`，核所有成员；旧版本和正在运行的进程保持原路径 |
| `scripts/setup-controller.py --code-directory code-NN`、`bench/runtime/pilot_context.py` | 为新执行代码重新探测；只允许代码目录不同，主机、磁盘、启动身份和Agent配额必须一致。状态库仍留在原协调目录，旧文件不覆盖 |
| `scripts/configure-pilot.py` | 按三阶段生成计划；实际inventory、build、input摘要齐全后才可冻结执行 |
| `scripts/cloud.py` | 盘点、询价、逐台购机、独立协调盘、释放与后查；创建前核最新分支，不自动重购 |
| `scripts/provision-node.py` | 核实际ECS身份与磁盘序列号，只格式化本轮新建的空数据盘；分开安装依赖 |
| `scripts/install-deployment.py`、`install-code.py`、`download-assets.py` | 核外层及逐文件SHA256，安装到新目录，私网分发；保留前一版 |
| `scripts/configure-node.py`、`deploy-registry.py`、`deploy-objects.py` | 部署私网入口、Redis、四盘RustFS；核真实镜像来源与容器身份 |
| `scripts/prepare-cloud-inputs.py`、`prepare-cloud-trace.py`、`close-cloud-inputs.py` | 云上生成并验证百万文件、保留trace原权限并严格回放；失败单独保留 |
| `scripts/freeze-maintenance.py`、`setup-controller.py` | 暂停新节点定时维护，核实际主机、运行代码、cgroup、盘空间与SSH身份 |
| `scripts/pilot-node.py`、`make-observer.py`、`prepare-c1-controller.py`、`calibrate-c1.py` | 一次执行一个准备/挂载/负载步骤，或一次明确指定的观测开销试验；不重试正式负载 |
| `scripts/check-calibration.py` | 检查明确列出的三对结果、原始观测审计、冷读回和退出；不自动挑选试验 |
| `scripts/derive-c1-qualification.py` | 复用已校验的两种CRC base，另建精确负载合同；其他fio模式仍各自校准 |
| `scripts/admit-c1.py`、`run-c1.py` | 对一项匹配资格的flat C1/C2单元准入；每阶段先写SQLite，再执行一次，核三角色原始归档后关闭；新增C2分支待云上验证 |
| `scripts/prepare-c2.py` | 独立导入完整初始树，创建私有child，回放并核字节/链接/数据库，再从原后端空缓存重挂核最终树和父目录 |
| `scripts/trace-manifest.py`、`prepare-c2-controller.py` | 固定完整回放所需文件；只收集已通过的实际路线，生成精确的C2输入、指标和观测开销合同 |
| `tools/fs-probe/src/bin/smallfiles-copy.rs`、`scripts/check-smallfiles-copy.py` | 可选8线程完整物化，逐文件核4KiB内容并fsync；[128文件交叉校验、独立inode与坏输入拒绝通过](evidence/preparation/smallfiles-copy-functional.json)。实际百万导入和冷读回尚待云上运行 |
| `scripts/prepare-smallfiles.py`、`prepare-packed.py` | 分别准备完整百万文件的native/JuiceFS与packed base；当前新增入口尚待云上执行验证 |
| `tools/fs-probe/src/bin/smallfiles-verify.rs`、`scripts/check-smallfiles-verifier.py` | 8线程只读核验全部文件、目录名、模式、owner和硬链接数；根据原始file/version/workspace状态计算修改后的内容；[原负载交叉检查及损坏/权限/链接/状态反例通过](evidence/preparation/smallfiles-verifier-functional.json)，尚无云上百万读回结果 |
| `scripts/prepare-c3-controller.py` | 收集实际百万文件导入、冷读回、正常退出和容量证据，生成匹配的C3合同；没有容量余量或完整原始验证便拒绝准入 |
| `scripts/probe-small-object.py` | 在独立新桶写24个4KiB对象，核四份XL2、inline数据、实际分配字节和inode；为百万文件准备容量依据 |
| `scripts/inspect-bucket-layout.py` | 在写者退出后抽查实际任务桶的持久化B/q、inline和分片大小，核四盘格式校验和与来源版本；不把前缀样本当作全桶分布 |
| `scripts/capacity-check.py` | 导入前后取四盘空间/inode及Redis内存/AOF快照，按剩余完整副本数和20%余量核容量；换盘、重启或后台回收导致分配量下降时拒绝沿用预测 |
| `scripts/plan-object-growth.py` | 只读核四块原数据盘、生成逐盘扩容备选请求并询价；七项身份/配置检查与真实ECS查询通过，不发送扩盘请求，不更改正在测量的容量 |
| `scripts/smallfiles-oracle.py` | 从不可变源和file/version/workspace状态独立计算预期树，供原namespace只读验证；不读取被测输出作为预期 |
| `scripts/observe-command.py` | 为单次准备/诊断命令采集三角色原始指标并逐成员核归档；不充当正式性能验收 |
| `scripts/draft-one-c1.py`、`run-one-c1.py` | 先生成可查看的请求，再执行明确指定的一个flat C1/C2单元：六次预先列出的观测开关试验、准入和原有阶段执行；任一步失败便停止 |
| `scripts/launch-one.py` | 启动明确指定的一项；先核前项关闭、归档全部成员、无上传债务、空槽位和采集器衔接，不选择下一项、不重试 |
| `scripts/draft-session-phase.py` | 固定单个export/import/resume或退出检查的命令、输入摘要和结果条件；resume只转发实际工具完成事件 |
| `scripts/retain-cell.py` | 仅打包已关闭单元，核封存文件集合及全部成员摘要，供独立下载；不启动负载 |
| `scripts/retain-calibrations.py` | 在完整单元结束后，另存六次校准的负载结果、三角色原始采样、冷读回和退出记录。[使用方法](CALIBRATION-RETENTION.zh.md)；本机六项检查通过，云上完整归档尚待执行 |
| `scripts/record-cost.py` | 按真实创建时间与报价估算费用。扩容按有凭据的新单价分段；只有核实实例及所有附属盘已释放，才停止计费估算。请求成功本身不算释放 |
| `scripts/release-evidence.py` | 只读查询，核清原实例及系统盘、数据盘；真正全部消失后才生成费用结束事件。[资源收尾步骤](RELEASE.zh.md)。已核本轮3台私网实例和全部10块盘，均仍存在；未执行释放 |
| `scripts/verify-latest.py` | 将本轮构建提交与指定远端分支最新提交逐一比较，保留带时间的结果 |

新云节点和跳板启动都要求本地产物、镜像、依赖、功能资格完成。现成脚本不等于已通过路线资格，失败记录保留。

目录句柄复用选项已完成[本机174.7万次完整回放](evidence/preparation/replay-parent-fd-local.json)，读写字节、最终树、硬链接和SQLite一致；[计数](evidence/preparation/replay-parent-fd-local-counts.json)记录最多255个缓存句柄。云上[BrewFS同选项完整验证](evidence/preparation/c2-native-parent-fd/result.json)通过：1,747,109次操作、读281,774,624B、写194,706,661B，最终树、硬链接、SQLite及冷重挂读回均通过。1,048次终端属性探测的拒绝码不同，单独记录；中间读字节检查保持开启。回放88.236秒只是资格验证时间，尚无配对性能结论。采集归档路径错误已修复，原始三角色数据[补收并核验](evidence/preparation/c2-native-parent-fd/observer-closure-recovery.json)，没有重跑负载。JuiceFS相同选项的[完整验证](evidence/preparation/c2-juicefs-parent-fd/result.json)也通过，回放1,257.738秒、探测码差异为0；[三角色原始监控包](evidence/preparation/c2-juicefs-parent-fd/observer-closure.json)已独立下载并核全部成员。两边正式C2前仍各需匹配的三对观测开销校准。

JuiceFS原工具、完整路径解析的[严格验证](evidence/preparation/c2-juicefs-original-strict/result.json)已于06:27完成：1,747,109次操作，回放6,425.173秒，完整字节和冷重挂校验通过；[95成员原始包已独立核验](evidence/preparation/c2-juicefs-original-strict/archive-check.json)。原验证未中途换工具，其时间不能与BrewFS新工具的验证时间直接相除。性能回放的冷读回使用 `qualify-trace.py --verify-only --performance-readback`，明确记录只校验最终数据，不把 `valid=false` 的性能报告改称严格通过。

C3的[独立初始清单](evidence/preparation/c3-independent-base-oracle.json)已覆盖100万文件、4,096,000,000B；[生成器交叉检查](evidence/preparation/c3-oracle-generator-check.json)核对了实际Rust修改后的全部64个文件。完整预期树和输入清单留在数据盘，Git只保存回执。05:26的[新增资源估算](evidence/preparation/cost-progress-02.json)约¥12.06，当前新增资源¥4.671/小时；原跳板、网络和OSS另计。

C3的flat native/JuiceFS流程已接入现有单元执行器：独立child、8个固定负载阶段、原始8条CSV、原工作区完整冷读回、完整父目录检查、`-w01`阶段及观测标记。各自仍需三对观测开销校准；本适配明确只接受1个Agent、1个工作区，不代替扩展规模实验。[本机27项相关检查通过](evidence/preparation/c3-harness-local.json)，云上完整C3尚未执行。正式负载仍使用本批原始`smallfiles`二进制；新工具只做完整导入和只读核验。
[新核验器已在本机完整读取100万文件、4,096,000,000B](evidence/preparation/smallfiles-verifier-million-local.json)，并核全部名称、模式和链接数；用户命名空间只映射测试源的owner，没有改源文件ownership。这仍不是云上读回或性能结果。

C3有一项容量前置条件：[云上四独立盘24对象探针](evidence/preparation/small-object-footprint-cloud.json)确认，4KiB数据在每盘占8KiB分配空间和2个inode；三个hint设置均如此。若每文件对应一个对象，百万文件的base、六个校准child和一个正式child合计每盘65,536,000,000B，已超过60GiB，尚未计共享目录、元数据和旧数据。C3前仍须用首份完整base的空间、inode和Redis增量重新核算。容量不足时先扩容并重建C3的实际inventory/资格记录，或在核归档及回收完成后释放已结束的副本；不靠删除目录便假定物理空间已回收。

C1实际应用桶的[BrewFS样本](evidence/preparation/c1-native-application-layout.json)与[JuiceFS样本](evidence/preparation/c1-juicefs-application-layout.json)各32个对象，四盘均通过格式校验，样本B=1MiB、q=256KiB、external；两边采样布局相同，不能据此宣称B选择带来性能差异。这是混合C1资产桶的确定性前缀样本，不是完整分布，也不是请求hint的直接记录。

已关闭C1随机读的[检查报告](evidence/cloud-20261111/c1-randread-report.json)保留两项各自状态。两份完整原始包已另存本地并核全部成员，未把二进制、密钥或完整数据包提交Git。C2探测差异的[原始失败](evidence/cloud-20261111/c2-native-strict-failure.json)和[正常退出](evidence/cloud-20261111/c2-native-strict-closure.json)也已保留。新增回放选项的[本机完整验证](evidence/preparation/replay-portable-terminal-local.json)只证明工具与输入接通，尚不代表云上路线通过。

C1随机读的三对观测开销结果：[BrewFS](evidence/preparation/cloud-native-observer-overhead.json)、[JuiceFS](evidence/preparation/cloud-juicefs-observer-overhead.json)。BrewFS共同wall变化为+15.18%、−0.72%、+0.02%，JuiceFS为+0.08%、−2.55%、+1.06%；按预定配对中位数及任务p99规则均通过5%门槛。保留单组波动，不能把该结果推广到其他负载或并发规模。早期三次观测启动失败均未执行负载，现场和正常关闭记录保留；原因是I/O统计接口未保持开启，以及观测源ID重名。

已完成的本机检查见[evidence/preparation](evidence/preparation)：pytest完整trace为1,747,109次操作，实际读281,774,624B、写194,706,661B，严格回放及最终状态一致；OpenCode 1.2.26在两个空HOME、独立数据库之间导入原会话并继续完成新工具。上述检查均不是云性能结果。版本快照见[source-versions.json](evidence/preparation/source-versions.json)，部署前还会重新核对。

历史输入恢复使用[restore_historical_oss.ps1](../../drive-backup-2026-10-29/restore_historical_oss.ps1)。该脚本逐项校验原始SHA256，再上传到原路径并比较本地/服务端CRC64与大小；不删除源数据。

06:02的[新增资源估算](evidence/preparation/cost-progress-03.json)为¥14.84，新增资源仍为¥4.671/小时；该数不含原跳板、网络和OSS，不是最终账单。
06:40的[新增资源估算](evidence/preparation/cost-progress-04.json)为¥17.83，小时费用未变；尚未新增迁移目标机或扩盘。
07:25的[新增资源估算](evidence/preparation/cost-progress-05.json)为¥21.28，小时费用未变；原跳板、网络和OSS另计。
08:00的[新增资源估算](evidence/preparation/cost-progress-06.json)为¥24.00，新增资源仍为¥4.671/小时；未新建迁移目标机或扩盘。
