# 实现任务：BrewFS 自动多档 EC block size 策略

你负责补齐 BrewFS 客户端的多档自动选择策略，完成测试后提 PR。先做实现和资格验证，不启动 C1–C4 正式实验，不新购云资源。

## 仓库和版本

- BrewFS：https://github.com/Ivanbeethoven/brewfs，从最新 main 创建 codex/ 分支。
- RustFS：https://github.com/Ivanbeethoven/rustfs，只用 feat/dynamic-block-size-rebased 最新代码。
- 实验仓库：https://github.com/Ivanbeethoven/ax-multi-agent-experiments。
- 先读各仓库 AGENTS.md。保留已有改动；提交 SHA 记录实际版本，不把旧 commit 当后续构建输入。同一轮对照使用同一构建。
- 可在本地 Linux 编译、跑测试。仅使用明确交付的已有 SSH 服务器做服务验证；没有服务器时完成代码、测试和可执行验证步骤，并列明尚未实测的项目。

## 已确认的问题

2026-11-01 检查 BrewFS src/cadapter/s3.rs 的 RustfsEcBlockSizeHint::for_object：
auto 对最终上传对象 ≤1 MiB 不发送 hint，>1 MiB 一律提示1 MiB。
显式64 KiB、256 KiB、1 MiB、4 MiB已经支持，但完整多档自动选择尚未实现。

先复核最新代码，阅读 doc/testing/rustfs-auto-object-hint.md。
早期auto大对象选4 MiB，后来特定整文件冷读比固定1 MiB慢约10%，因此改回1 MiB。这不能推导为所有负载都不需要多档，也不能直接恢复“越大越快”的规则。

RustFS需阅读：
crates/ecstore/src/object_api/ec_block_layout.rs、
实际inline判据、EC几何、bitrot帧和PUT/MPU路径。
BrewFS逻辑块、最终S3对象、RustFS EC块B、每shard读帧q是四个不同量。

## 实现要求

1. 在已有hint选择入口扩展，增加明确命名的多档策略（例如tiered），兼容现有auto和固定值。新策略支持按最终编码对象大小的确定性分段表；配置校验阈值顺序、覆盖范围和支持档位。先写简短策略表及选择依据，再实施。
2. 决策输入必须是压缩、格式头、补零完成后的对象字节数，不是用户文件大小、SSD预分配大小、一次FUSE请求或MPU part大小。至少用代表样本证明策略实际选出多个B；声明支持四档不等于四档已经用到。
3. 小对象保留RustFS实际inline路径。结合EC数据分片数、版本化与存储路径推导边界；不能把“≤1 MiB省略hint”写成“≤1 MiB都inline”。超出已验证几何时给出明确行为。
4. 区分整对象读取和稀疏Range读取的需求。第一版采用显式、可配置、确定性规则；不要未经测量加入在线学习或频繁重布局。默认策略表的依据写清；缺少性能证据的阈值标为候选。
5. 普通PUT、vectored、create-only、MPU、恢复上传和重试共用选择逻辑。同一对象重试保持相同选择，MPU按整个对象决定；历史对象按持久化布局读取，不在重挂、fork或读路径上隐式重写。
6. 复用已有CLI/YAML和Cargo feature能力；feature未编译却启用时明确报错。dynamic实验配置默认开启，策略值显式记录；保留off用于消融。既有auto语义兼容，实验仓库切换tiered必须明确更新配置和实验ID。
7. 核查#362后的read_fetch_policy。选择EC hint不能悄悄改变用来比较的GET/Range、预取和缓存策略；机制对照保持这些参数一致。产品默认组合另报告。
8. 覆盖flat-v1与workspace-native-v2实际上传路径；C2 Pack上传者若走独立入口，核查接入并记录支持范围。Pack只用于只读ML小文件，不混入一般读写基线。

## 验证与观测

先写能复现缺失行为的测试，再补实现：
- 每个策略阈值前后1 byte、空对象、小尾块、格式头边界、可压缩/不可压缩数据；
- 五类上传入口、MPU重组、失败重试、恢复上传、非法配置及feature关闭；
- 请求hint、服务器applied/ignored及原因、实际B/q、inline/external、完整GET/Range内容校验；
- 后端重启、客户端重挂后读回；旧对象兼容；
- fork与只读重挂不改变已有对象布局。

真实服务验证使用指定RustFS分支，四个开关全开：
RUSTFS_EC_BLOCK_SIZE_HINT_ENABLE
RUSTFS_EC_BLOCK_SIZE_HINT_FLEET_CONFIRMED
RUSTFS_EC_READ_QUANTUM_ENABLE
RUSTFS_EC_READ_QUANTUM_FLEET_CONFIRMED

记录每对象最终长度、策略/版本、选择的hint和理由；用有界标签汇总各档对象数/字节数、ignored计数，避免对象key成为高基数指标。保全请求、HEAD、每个shard持久化布局证据；开关为true不能代替生效证明。

有已交付合格服务器时，再做小规模机制性能对照：现有auto、新tiered和对应固定档位；全GET/4KiB Range分开，冷/热分开；相同数据、资源、读取策略与三个配对重复。报告吞吐、p50/p95/p99、应用/S3/物理读放大、PUT成本与空间。单盘多目录只能做功能资格，不能声称四盘性能。不能只保留有利条件或把缺测填0。

## 交付

- BrewFS PR：策略表、代码、兼容性、测试命令及结果，解释默认值依据。
- 独立的准备、上传、布局检查、读取、统计步骤；执行Agent逐步监督，不写自动包办整轮/删资源的控制器。
- 有真实服务数据就交付完整结果清单；无服务器则明确哪些检查未运行，不声称性能通过。
- 给实验仓库一份接入补丁：构建features、CLI/YAML、策略参数、生效验收、C1/C2/C4默认配置及C3固定档位对照。先提PR供review，不自动merge，不开正式实验。
