# 阿里云 C1–C4 小规模试跑

最新核对：北京时间2026-11-11 05:54，[两个远端分支仍与构建一致](evidence/preparation/latest-progress-04.json)。[05:00执行快照](evidence/cloud-20261111/progress-0500.json)记录当前仍在运行的JuiceFS完整trace验证；C1–C4全轮尚未完成。


05:55–05:56另核了[运行中的RustFS程序](evidence/preparation/cloud-runtime-progress-04.json)与[已部署BrewFS程序](evidence/preparation/cloud-brewfs-progress-04.json)，SHA256均与本批构建一致。执行脚本[code-02已在四个角色部署并逐文件核验](evidence/preparation/runner-code-02-deployment.json)；旧任务仍使用原脚本，不热替换。
本轮已于2026-11-11启动3台私网节点，并复用原跳板和一块独立协调盘；迁移目标机尚未创建。四盘RustFS、Redis、BrewFS和JuiceFS已完成接入及小文件写入/正常卸载/重挂读回检查，C1输入也已完成完整CRC与冷重挂校验。C1两项随机读已关闭：JuiceFS全部阶段通过；BrewFS测量及数据检查通过，归档阶段失败后补收证据，仍记失败，不输出该配对的加速比。设置见[scope.json](scope.json)，路线限制见[审查记录](../PILOT-REVIEW.zh.md)。系统计划有28个单元；未通过资格的路线保留实际失败。C3仍为百万文件，C4恢复仍使用真实OpenCode原生会话。

本轮构建使用BrewFS `main` 的 `622736b0a31caaa9007445886e626a2458bc316a`、RustFS `feat/dynamic-block-size-rebased` 的 `f4ed202508f2153afb7956bae6f7799b60eccd00`。二者均从指定分支构建，准备期间再次核对远端最新提交；运行镜像内RustFS二进制SHA256与本机产物相同。`scripts/verify-latest.py`在新批次计时前再核一次，分支推进则先重建；开始计时的配对批次固定版本。

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
| `scripts/session-relay.py` | C4私网模型转发，主密钥仅在工作区外的私有配置，工作区使用独立转发凭据；不记录请求/响应正文，不自动重试；[云上健康检查通过](evidence/preparation/c4-relay-health.json)，尚无模型调用；本地5项验证通过 |
| `scripts/qualify-opencode-session.py`、`package-session-runtime.py` | 两轮真实会话验证export/import/resume，打包已安装依赖，密钥留在工作区外 |
| `scripts/link-brewfs-tool.py`、`verify-entity-remote.rs` | 复用本轮BrewFS库构建只读核验器；检查原namespace的远端字节、目录、硬链接、ownership及核验前后状态 |
| `scripts/local-services.py` | 本机功能验证服务；四个目录共用一块盘，不能作为云上四独立盘的性能证据 |
| `scripts/qualify-rustfs-local.py`、`metric-collector.py` | 核实际B/q、完整与Range读、已知请求数和原生计数；[采集部署](OBSERVABILITY.zh.md) |
| `scripts/qualify-entity-local.py` | 实际挂载、hardlink/COW/空洞/重命名修改、正常卸载，再以只读Redis账号核原namespace |
| `scripts/package-deployment.py` | 打包本轮二进制、运行依赖、镜像和执行代码，逐成员核SHA256，供私网分发 |
| `scripts/package-code.py`、`install-code.py` | 单独冻结执行代码并安装到新的 `code-NN`，核所有成员；旧版本和正在运行的进程保持原路径 |
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
| `scripts/prepare-smallfiles.py`、`prepare-packed.py` | 分别准备完整百万文件的native/JuiceFS与packed base；当前新增入口尚待云上执行验证 |
| `scripts/probe-small-object.py` | 在独立新桶写24个4KiB对象，核四份XL2、inline数据、实际分配字节和inode；为百万文件准备容量依据 |
| `scripts/smallfiles-oracle.py` | 从不可变源和file/version/workspace状态独立计算预期树，供原namespace只读验证；不读取被测输出作为预期 |
| `scripts/observe-command.py` | 为单次准备/诊断命令采集三角色原始指标并逐成员核归档；不充当正式性能验收 |
| `scripts/draft-one-c1.py`、`run-one-c1.py` | 先生成可查看的请求，再执行明确指定的一个flat C1/C2单元：六次预先列出的观测开关试验、准入和原有阶段执行；任一步失败便停止 |
| `scripts/retain-cell.py` | 仅打包已关闭单元，核封存文件集合及全部成员摘要，供独立下载；不启动负载 |
| `scripts/record-cost.py` | 按真实创建时间与报价保存新增资源的费用估算，列清未包含的项目 |
| `scripts/verify-latest.py` | 将本轮构建提交与指定远端分支最新提交逐一比较，保留带时间的结果 |

新云节点和跳板启动都要求本地产物、镜像、依赖、功能资格完成。现成脚本不等于已通过路线资格，失败记录保留。

目录句柄复用选项已完成[本机174.7万次完整回放](evidence/preparation/replay-parent-fd-local.json)，读写字节、最终树、硬链接和SQLite一致；[计数](evidence/preparation/replay-parent-fd-local-counts.json)记录最多255个缓存句柄。当前云上JuiceFS原验证仍使用旧工具，未中途替换。后续两种文件系统使用该选项前，须分别通过同选项的云上完整验证。性能回放的冷读回使用 `qualify-trace.py --verify-only --performance-readback`，明确记录只校验最终数据，不把 `valid=false` 的性能报告改称严格通过。

C3的[独立初始清单](evidence/preparation/c3-independent-base-oracle.json)已覆盖100万文件、4,096,000,000B；[生成器交叉检查](evidence/preparation/c3-oracle-generator-check.json)核对了实际Rust修改后的全部64个文件。完整预期树和输入清单留在数据盘，Git只保存回执。05:26的[新增资源估算](evidence/preparation/cost-progress-02.json)约¥12.06，当前新增资源¥4.671/小时；原跳板、网络和OSS另计。

C3有一项容量前置条件：[本机24对象探针](evidence/preparation/small-object-footprint-local.json)中，4KiB数据在每个RustFS槽位占4KiB文件分配和4KiB目录分配；三个hint设置均如此。若每文件对应一个对象，百万文件的base、六个校准child和一个正式child合计每盘65,536,000,000B，已超过60GiB，尚未计共享目录、元数据和旧数据。该探针四目录共盘，只用于布局检查；C3前须在实际四盘复核，并用首份完整base的空间、inode和Redis增量重新核算。容量不足时先扩容并重建C3的实际inventory/资格记录，或在核归档及回收完成后释放已结束的副本；不靠删除目录便假定物理空间已回收。

已关闭C1随机读的[检查报告](evidence/cloud-20261111/c1-randread-report.json)保留两项各自状态。两份完整原始包已另存本地并核全部成员，未把二进制、密钥或完整数据包提交Git。C2探测差异的[原始失败](evidence/cloud-20261111/c2-native-strict-failure.json)和[正常退出](evidence/cloud-20261111/c2-native-strict-closure.json)也已保留。新增回放选项的[本机完整验证](evidence/preparation/replay-portable-terminal-local.json)只证明工具与输入接通，尚不代表云上路线通过。

C1随机读的三对观测开销结果：[BrewFS](evidence/preparation/cloud-native-observer-overhead.json)、[JuiceFS](evidence/preparation/cloud-juicefs-observer-overhead.json)。BrewFS共同wall变化为+15.18%、−0.72%、+0.02%，JuiceFS为+0.08%、−2.55%、+1.06%；按预定配对中位数及任务p99规则均通过5%门槛。保留单组波动，不能把该结果推广到其他负载或并发规模。早期三次观测启动失败均未执行负载，现场和正常关闭记录保留；原因是I/O统计接口未保持开启，以及观测源ID重名。

已完成的本机检查见[evidence/preparation](evidence/preparation)：pytest完整trace为1,747,109次操作，实际读281,774,624B、写194,706,661B，严格回放及最终状态一致；OpenCode 1.2.26在两个空HOME、独立数据库之间导入原会话并继续完成新工具。上述检查均不是云性能结果。版本快照见[source-versions.json](evidence/preparation/source-versions.json)，部署前还会重新核对。

历史输入恢复使用[restore_historical_oss.ps1](../../drive-backup-2026-10-29/restore_historical_oss.ps1)。该脚本逐项校验原始SHA256，再上传到原路径并比较本地/服务端CRC64与大小；不删除源数据。

06:02的[新增资源估算](evidence/preparation/cost-progress-03.json)为¥14.84，新增资源仍为¥4.671/小时；该数不含原跳板、网络和OSS，不是最终账单。
