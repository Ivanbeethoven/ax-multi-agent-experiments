# C1–C4 小规模试跑审查

范围是当前 `unified-c1-c4-v1`，不是旧 retest 编号。此次只检查计划、生成器与执行入口；`handoff --help` 和 `suite template --help` 可正常加载。尚未据此认定任何真实挂载、远端持久化或性能资格通过。

## 固定输入与完整系统清单

生成器：[suite_catalog.py](../../bench/suite_catalog.py#L36)。pilot只有一轮操作重复；不能据此给出稳定加速倍数或集群扩展结论。

| 组 | 输入和工作量 | arms | cells | 要保留的主要指标 |
|---|---|---|---:|---|
| C1 | read/write/randread/randwrite/randrw；每项1 job、64MiB；顺序1MiB、随机4KiB；写后fsync | native、juicefs | 10 | 原始读/写/同步延迟分布、IOPS、带宽、共同wall、排空、冷读回 |
| C2 | pytest-full-02完整包，N=1，不加模型等待；全部callback和真实写入内容 | native、native-workspace、entity-native、packed、juicefs | 5 | 回放wall、callback/实际读写字节、失败数、每层RPC/GET/PUT/字节、最终树 |
| C3 | 1,000,000×4KiB；1个新workspace；1024文件热集、8线程；cold/prewarm/hot/COW/upper预热/overwrite/rewarm/mixed | native、entity-native、packed、juicefs | 4 | 每阶段wall/ops/分布、命中和GET字节、首次COW、排空、隔离、远端读回 |
| C4 fork | 独立1M×4KiB fixture；1 child | 五条系统路径 | 5 | 创建、首个内容读取成功、修改/排空、父兄弟隔离、实际复制字节/存储增长 |
| C4 handoff | 1个真实OpenCode会话；相同原生checkpoint和任务后缀；源/目标不同主机 | native-workspace、juicefs，各stay/move | 4 | 完整停顿、首个新增完成工具、恢复后完成、会话/工具身份、旧writer释放 |

合计28 cells、9个配对条件。C2输入归档为407,606,955字节；全部大小和SHA256从[数据目录](../../scenarios/k8s-containers/data-catalog.md)读取。C3源与C4 fork源的生成算法/路径不同，不能直接复用一个fixture。每份1M源的逻辑内容为4,096,000,000字节，另计目录、元数据、对象封装、私有copy和日志。

## 消融清单及实际可执行条件

下表共22 cells。条件存在于生成器不代表已有对应运行时功能。

| 条件 | arms | cells | 启动测量前要有的证明 |
|---|---|---:|---|
| lower格式路径 | entity-native、packed-cache-off | 2 | 同源视图、cache=0；允许payload对象布局不同，结论限定整条格式路径 |
| Delta | delta-on、delta-eager | 2 | **同workspace引擎**的真实独立物化；完整copy成本与物理独立性 |
| Packed metadata | packed-on、packed-off | 2 | 同payload对象/帧、同引擎、cache=0；只改metadata表示 |
| payload cache | cache-on、cache-off | 2 | 同packed对象；512MiB/0生效及命中/驻留/eviction计数 |
| 客户端对象大小g | 256KiB、1MiB、4MiB | 3 | 实际B/q不变；相同fetch/cache/同步策略；小对象内联须单独核实 |
| 服务端布局B | off、64KiB、256KiB、1MiB、4MiB hint | 5 | g=4MiB；最终对象字节相同；hint真实接受；对象非内联及实际持久化B/q |
| 校验读取q | block-only、block-and-quantum | 2 | B固定1MiB而实际q不同；服务切换后重新准备独立布局 |
| D×P | D0P0、D0P1、D1P0、D1P1 | 4 | 同entity引擎、同payload、cache=0，真实eager物化和完整四格 |

**已发现的执行缺口**：实验仓库对 `materialization`、`same-engine-eager-materialization`、`identical-payload-layout`、`entity-eager-materialization` 只有目录声明和资格要求，没有自动完成这些条件的适配器（[catalog](../../bench/suite_catalog.py#L47)）。因此Delta2、纯metadata2、D×P4共8个cell，需先通过本批原生CLI与独立验证器的真实资格；缺失时明确记为未执行，不能用flat copy或native每文件对象替换后改称纯消融。其余14个cell也是“可进入资格检查”，不表示一定通过。

## 会影响论文结论的四点

1. **C4的恢复路线不包含packed。** 当前entity为fresh-only；C4原生重开用native-workspace/JuiceFS（[方法表](02-ablations.zh.md#L15)）。native-workspace迁移成功只能证明该路线，不能据此声称当前packed候选也能带原upper跨机继续。构建最新版本后若能力已改变，应以真实重开资格和新协议记录。
2. **文件访问trace不能提供原生会话checkpoint。** C4需要OpenCode二进制、原生export/import、相同会话与历史工具ID，以及resume后观察到的新completed工具；检查器会逐项核对（[handoff.py](../../bench/runtime/handoff.py#L61)）。`archive-io-20261026`这个输入名不能代替这些数据。C2回放通过也不能替代C4。
3. **fresh-only entity的写入验收需独立远端读回。** 重新创建一个namespace不能验证原upper。当前协议要求同namespace/manifest/upper的logical/object验证器；没有合格验证器时，C2/C3/C4相应写入结果不能通过持久化验收（[小文件协议](08-smallfiles.zh.md#L41)、[报告检查](../../bench/suite_report.py#L13)）。
4. **N=1不回答多Agent扩展与邻居影响。** handoff pilot没有邻居；`neighbor-throughput`资格应明确记录neighbor_count=0、结论不适用，不能填“邻居性能通过”。fork1也不能证明突发64路扩展。这里完成的是功能和计量接入。

## 最省资源的合法准备和执行

1. 在本地专用checkout同步BrewFS默认main与RustFS指定分支；记录latest核对时刻、源码SHA、dirty状态、features、工具链、二进制SHA、RustFS镜像revision/digest。构建、CLI加载和最小功能资格先完成，再开云机。一次构建后通过内网分发。
2. 本轮用**一个实际pool顺序比较**：配置 `placement={"mode":"paired-pool"}`，保留全部28系统cells；把全局及pool的metadata_backend同时改为redis，并填真实资源/observer ID。handoff move需要source+target两个2CPU/8GiB slot；模板的8 slots只是声明，不能照抄（[slots_for](../../bench/suite_plan.py#L43)、[默认模板](../../bench/suite_catalog.py#L126)）。
3. 不必把BrewFS和JuiceFS各建一套集群。单池复用相同硬件/服务，按冻结顺序、独立namespace/base/cache运行。不同arm的导入和一次完整验证都记账；同池准备不与测量并跑。
4. 若先不启动target以节省费用，必须把C1–C3和C4分成两个冻结campaign/inventory：前者observer不含不存在的target，后者包含真实target。不在已冻结plan中临时删observer。两批合起来仍覆盖原28 cells，分别报告状态和准备费用。
5. C3和fork准备包含每文件fsync、多个系统导入和完整读回；光准备就可能远超过前台测量。先记录实测准备速率，再估后续时长。不能承诺“28+22 cells在3–5小时完成”。
6. 观测开销要用至少三对真实on/off测量；比较共同wall与任务p99（[overhead](../../bench/runtime/observe.py#L253)）。4C/16GiB给采集留余量有帮助，不能保证≤5%。64MiB读条件尤其要保留启动/采样开销和原始分布。

复用[现有单步入口](05-execution.zh.md)：template → scale_plan → sizing/drafts → 真实资格/asset → claim/run-stage → closure/archive/finish → report。状态库只放一个协调节点本地盘，started不能自动重放。每个cell保留原始样本、边界mark、上传债务、真实退出、远端读回和费用。当前没有一条命令能替代这些原生准备与核验；新增自动化应调用既有阶段并输出真实回执。

## 可复用的真实执行入口

下列程序会执行工作量；`suite_support drafts`、`runtime.prepare`、`runtime.workloads` 只是生成受审读的配置。

| 用途 | 入口 | 本轮还要提供什么 |
|---|---|---|
| C1 初始化、测量、校验 | [fio_workload.py:107](../../bench/fio_workload.py#L107)，`--request ... --phase prepare\|measure\|verify --output NEW` | 本cell参数、受控挂载、实际fio二进制；保留json+原文 |
| C2 全量性能回放 | [prepare.py:150](../../bench/runtime/prepare.py#L150)生成trace worker，由[cohort.py:97](../../bench/runtime/cohort.py#L97)真正执行Rust回放 | 五条路线分别导入initial树；性能回放后单独验final树；不能把`valid=false`当严格校验通过 |
| C3 源与测量 | [smallfiles.rs:301](../../tools/fs-probe/src/bin/smallfiles.rs#L301)，`prepare`、`verify-base`、`run`、`verify-touched` | 源生成后按[导入表](04-inputs.zh.md#L22)分别导入；四路线尚无统一自动导入脚本 |
| C4 创建与首读 | [fork_burst.py:17](../../bench/fork_burst.py#L17)；[fs_step.py:15](../../bench/fs_step.py#L15)的fixture/first-read/mutate/check | 每个child的真实fork/clone/full-copy step；不可只计workspace记录创建 |
| C4 原生会话流转 | [handoff.py:46](../../bench/runtime/handoff.py#L46)，`--spec ... --sha256 ... --output NEW` | 源/目标挂载及5份真实单Agent cohort；仓库没有可直接运行的handoff spec或OpenCode cohort实例 |
| OpenCode原生命令参考 | [opencode_resume_probe.py:37](../../scripts/opencode_resume_probe.py#L37)、[已存结果](../../validation/opencode-resume-20261023/README.md) | 参考workspace内XDG、空HOME、export/import、`run --session`；改为两端Linux共享路径及本轮受控执行，不直接执行旧跨OS复制探针 |

已存OpenCode 1.19.4小任务的export/import路线成功，直接复制数据库路线失败；这只支持原生会话接法，不是BrewFS/JuiceFS上的跨机结果。原探针会复制整个workspace、带240秒超时和strace，不符合本轮“只转原生JSON、共享数据不复制”的测量路径（[probe:106](../../scripts/opencode_resume_probe.py#L106)）。

需要修正的旧入口：`examples/smallfiles.request.json`是4 workspaces、65536读等示例，pilot必须复制plan中的1 workspace、8192读等实际参数；旧`dsh-campaign-2026-11-08/scripts/c4-*.sh`硬编码TiKV、旧workspace-v1和旧私网地址，部分用pkill/延迟卸载，不可直接套到本轮；`results/c1-g-ablations-20261108/retest-fixed/run_retest_fixed.py:22`依赖仓库外`/mnt/slayerfs/run_c1_g_ablations`，不是独立可复现runner。本机旧`/home/hxy/src/brewfs`为80ddaf7，仅供历史参考；不能用其CLI缺失判定新main能力。最新干净源码/二进制才是本轮资格依据。

## 本轮准备状态

- 配置和执行入口审查完成；28系统/22消融数量由现有目录逐项核对。
- 真实服务、最新二进制、原生会话、持久化验证器及观测开销：仍需本轮资格。尚未为其给出通过结论。
- 最高优先级：先拿到各路线的真实CLI与重开/持久化能力清单，再冻结云上28-cell系统pilot。8个纯消融缺口单列，不为补齐数量伪造实现。

## 最新源码补充：开云机前的能力边界

**执行补充**：后续检查发现旧`paired-pool`的`claim`要求整个block资格齐全（`bench/suite_state.py:157`）。本轮因此使用现有`baseline-parallel`逐cell准入，并把两种product都映射到**同一pool-a**，保留同硬件顺序运行。这里不新增集群、不绕过资格，也不把部分通过的block当完整配对；当前具体设置以`pilot-aliyun/scope.json`为准。

此段检查WSL `/home/hxy/ax-unified-pilot-prep/src/brewfs`，实际HEAD为`622736b0a31caaa9007445886e626a2458bc316a`。下列行号均相对此BrewFS源码，属于静态证据；没有启动服务或声称运行通过。28是预定cells数，当前至少4个cell有明确阻断，其余24个仍需实际资格。

| 受影响cell（suite / case / arm） | 真实限制 | 开机前最小核验 |
|---|---|---|
| C2 / pytest-full-02-n1 / native-workspace | 没有把非空initial树发布为当前P1挂载/CLI可用的native immutable revision的接法 | 保存`workspace --help`/子命令help和源码类型/dispatch证据；不拿初始空fork_base验非空数据 |
| C4 / fork-1 / native-workspace | 同上；百万文件live upper不随`workspace fork`自动封存 | 同上；若未来补适配，必须先用两文件源完成发布→fork→首读/修改/重开，才导入百万源 |
| C3 / million-hot-1024 / entity-native | native lower最多32768行、32768 inode；manifest最多4MiB；1M文件+1024目录+root有1,001,025 inode | 固定源码SHA读取quota并与输入计数比较；可用下述纯本地API反例，不必云上先上传几万文件 |
| C4 / fork-1 / entity-native | 同一个native lower硬限，不能发布百万fixture | 同上；减少输入会成为另一测试，不能记作原cell通过 |

native限制位置：`src/workspace_overlay/entity/native_lower.rs:29–30,127,280–281,387–388`。纯本地API反例可以独立小程序依赖固定源码库，使用公开`MemoryEntityBackend::new(0)`及`LocalFsBackend::new(TMP)`：将JSON manifest的`rows`设32769个短key→32字节摘要、其余format/volume/chunk_size合法，按内容SHA命名本地对象，调用公开`NativeLower::open`。预期错误为`Corrupt("invalid native manifest")`；记录实际错误、输入bytes/hash，确认未访问Redis/S3。该JSON可控制在4MiB内，隔离行数限制；这不是模拟million性能。不要提高常量后仍声称测的是原样latest。

### native-workspace的公开API为何不能直接补成一个publisher命令

当前生产CLI在`src/config.rs:496–578`列出init-volume、init-packed-volume、init-native、create、fork、inspect等，没有seal/publish。`workspace init-native`（`src/cli.rs:2816–2848`）只在已有workspace-v1/schema1 catalog上建立P1 control header。Redis实现确实存在（`src/cli.rs:1220–1233`），但header不是非空baseline导入。

公开`native_base::lifecycle::{seal::begin_seal,publish::publish_candidate,publish::complete_publication,fork::fork_from_published}`分别在`seal.rs:166`、`publish.rs:207,567`、`fork.rs:60`。它们使用`native_base::wire::bnct::PublishedRevision`；当前workspace fork CLI接受`workspace_overlay::model::BaseRevision`。P1挂载的base仍由`WorkspaceBaseDataSource`读取catalog的`read_plan`（`src/native_base/runtime/workspace.rs:31–90`、`src/cli.rs:1368`），并非自动加载前者的data seal。唯一离线resolved-chain→flat lower接口`InstallTestBase/install_test_base`为`#[cfg(test)]`，注释明确生产lower来自packed publication（`src/workspace_overlay/catalog.rs:153–157,937–945`）。没有找到可直接构建的生产native publisher example。仅调用这些公开lifecycle函数还缺两套revision及catalog视图的连接，不能据此给出可运行假命令；`init-packed-volume`会改变lower格式。

可用的空卷/P1同workspace重开接法如下，所有变量须来自本批实际配置，返回JSON原样保存。它可用于独立验证handoff能力，不替代前述immutable native派生：

```bash
brewfs workspace --meta-backend redis --meta-url "$REDIS_URL" --workspace-namespace "$NS" init-volume --data-config "$DATA_CONFIG"
brewfs workspace --meta-backend redis --meta-url "$REDIS_URL" --workspace-namespace "$NS" init-native
brewfs mount --config "$P1_CONFIG" --metrics --metrics-addr 127.0.0.1:8910 "$MOUNT"
# P1_CONFIG携带返回的workspace、同一NS、meta.redis.url及完整native_base P1配置。
# 正常停止并核writer release后，以相同workspace/NS/data重新启动新client。
brewfs workspace --meta-backend redis --meta-url "$REDIS_URL" --workspace-namespace "$NS" inspect "$WORKSPACE"
```

### entity、packed导入与原namespace验收

entity-native首次挂载只在导入阶段加`--entity-native-source "$SOURCE"`，保存stdout `entity_experiment=`中的`lower_manifest`；以后用`--entity-native-manifest-key "$KEY"`创建fresh child。共同参数是`mount --config "$CONFIG" --volume-format workspace-entity-v1 --experimental-entity-mount --entity-volume-id "$VOLUME" --workspace "$CHILD_UUID" --workspace-namespace "$CATALOG_NS" --entity-read-cache-bytes 0 "$MOUNT"`。原生manifest绑定volume（`native_lower.rs:123–124`），复用lower时保留原`VOLUME`；不能每次换volume再引用旧key。原namespace重开仍被明确禁用（`src/config.rs:222–223`），`--entity-recover-uploads`只上传旧spool，不授予恢复挂载权限。

`packed_v3_snapshot_fixture`是单层参数CLI，**没有子命令**（`src/bin/packed_v3_snapshot_fixture.rs:21,909`）。本协议source目录接法：

```bash
packed_v3_snapshot_fixture --source-directory "$SOURCE" --source-consistency best-effort-detected \
  --source-hardlink-policy visible-links --output-dir "$TMP_PACK" --manifest-output "$MANIFEST_FILE" \
  --bucket "$BUCKET" --endpoint "$S3_ENDPOINT" --region us-east-1 --force-path-style true \
  --prefix "$ASSET_PREFIX" --metadata-codec raw --data-codec raw --inline-data off \
  --chunk-size 4194304 --block-size 4194304 --frame-policy size-only \
  --access-profile random-small-file --max-random-frame-raw-bytes 262144 --s3-rustfs-ec-block-size-hint auto
```

随后entity共同参数改用`--packed-manifest-key "$KEY" --entity-read-cache-bytes 536870912`。首次lower/native捕获在计时外单独记账；仅fresh child建立和mount就绪计入创建。普通SIGTERM/卸载后等待原PID真实退出，保存`entity_writeback=`和`entity_stopped=`；`orderly_close`在`src/cli/entity_mount.rs:244–270`等待drain并保留upper。不能用新namespace读base冒充旧upper验收。

原namespace只读probe已存在，可由root统一构建`cargo build ... --example entity_metadata_probe --features workspace-overlay`（并记录完整本批features）。实际调用：

```bash
entity_metadata_probe --backend redis --endpoint "$REDIS_URL" --volume "$VOLUME"
# 启动先输出{"ready":true}；向同一进程stdin逐行写：
{"op":"status","namespace":"原CHILD_UUID"}
{"op":"snapshot","namespace":"原CHILD_UUID"}
{"op":"objects","namespace":"原CHILD_UUID"}
```

API见`examples/entity_metadata_probe.rs:23–110`、公开`RedisEntityBackend::connect`、`EntityBackend::{read,read_checked,scan_prefix_page}`及`EntityKeys`。status返回descriptor/debt，snapshot返回原namespace行，objects扫**整个volume**对象descriptor；按namespace过滤，并对每个实际S3 GET结果核size/SHA256，不能只HEAD。probe的snapshot/objects都有100000行上限，超限应失败或另写分页输出适配，不能截断后通过。

最小logical verifier需固定原volume/namespace/manifest：停止writer、核Quiescent/lease释放、两次debt=0、稳定snapshot；用公开`codec/model`解码upper inode/dentry/chunk，按公开`resolver::{resolve_inode,resolve_dentry,resolve_chunk,merge_directory}`和同一个lower复原路径/字节，检查whiteout、rename、EOF、LowerRange/Data/Hole，再对独立expected树/expected-touched比对。native lower可直接公开`NativeLower::open`；packed可用公开`AuthenticatedV3Snapshot::{open,lookup_inode,lookup_dentry,readdir_page,read_inode_range}`+`V3IndexReader`做只读适配。不要用`PackedLower::open`声称纯只读，它会登记lower owner。现成million-r2 harness只验upper对象后用fresh namespace验base，尚不能直接充当原upper完整logical verifier。

### 观测和配置漂移

- 当前HEAD的`Cargo.toml:348`含`observability=[]`，`--metrics/--metrics-addr`和HTTP server都受该feature控制（`src/config.rs:69–79`、`src/cli.rs:125–130`）。本批构建必须包含它，否则实验适配器生成的`--metrics`会被拒绝。HTTP路径是`/metrics`。
- Redis配置字段是`meta: {backend: redis, redis: {url: ...}}`；native P1完整有效配置样例在`src/config.rs:3687–3762`。P1配置声明的预算不替代实际缓存和RSS验证。
- 当前HTTP指标含`brewfs_object_get_total`、`brewfs_object_get_bytes_total`、`brewfs_object_put_total`、`brewfs_object_put_bytes_total`及`brewfs_fuse_op_latency_seconds`；旧retest脚本读取`*_bytes_count/sum`不可沿用后填零（`src/observability/mod.rs:92–170`）。entity payload cache指标在同文件`:285–320`；entity债务用原namespace probe，不要求不存在的native FsStats gauge。
- entity S3Backend构造未传入mount的RustFS hint（`src/cli/entity_mount.rs:45–58`）；不能凭`auto`argv认定upper提示生效。packed producer现在有真实`--s3-rustfs-ec-block-size-hint`（fixture`:105–109`）。B/q仍需实际对象布局核验。
