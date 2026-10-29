# 实验分步执行手册

参数见 [实验方案](../../paper-design/FINAL-PLAN.zh.md)。以下命令在 Linux 测试机的实验仓库根目录执行；变量由 AI 从本批 `run-spec.json` 读取。每次只执行一个步骤，读取输出后再决定下一步。

## 1. 执行分工

四组负责人、拓扑和初始 prompt 见 [CLUSTERS](CLUSTERS.zh.md)。C1=G/A，C2=P，C3=D，C4=F/M。先读对应 [阿里云资源准备](../alicloud/PROVIDER.zh.md) 或 [自备服务器准备](../servers/README.zh.md)；以下测量步骤共用。

| 执行者 | 负责内容 |
|---|---|
| 单步脚本 | 完成一个操作、写原始输出/检查结果、退出；不启动下一 baseline，不自动处理失败 |
| AI | 核版本和配置、检查机器/进程/挂载、决定是否继续、协调两机就绪和换位、解释异常、保全结果 |

所有结果使用新目录。每步记录实际命令、开始/结束时间、退出码、输出路径和 AI 检查结论。测量命令不负责销毁实例。

## 2. 共用准备：逐步执行

| 步骤 | 操作入口 | 输出 | AI 检查后才能继续 |
|---|---|---|---|
| 00 机器 | [experiment_inventory.py](../../harness/experiment_inventory.py)；[host_preflight.py](../../harness/host_preflight.py) | 填妥 inventory 与每机检查收据 | 节点归属、配对规格、盘/OS 隔离、空闲状态；3 台模式先取得本组独占交接 |
| 01 计划 | [final_plan.py](../../harness/final_plan.py) | 任务表 | 池、baseline、区组、周期、机器分配和参数正确 |
| 02 版本 | [freeze_backend.py](../alicloud/deploy/freeze_backend.py)；需要新产物时才调用 [build-client.sh](../../experiments/retest-2026-10-29/scripts/build-client.sh) | backend-pin、二进制 SHA、构建记录 | 分支 SHA、OCI revision、运行镜像与二进制一致 |
| 03 配置 | [generate_rustfs.py](../alicloud/deploy/generate_rustfs.py) | node.json、compose.json | 四个真实盘、限额、目录、凭据文件、D 开关正确 |
| 04 检查 | [rustfs_node.py](../alicloud/deploy/rustfs_node.py)，不带 `--start` | 节点检查结果 | 数据盘不是系统盘，各 volume 独立，来源与主机身份吻合 |
| 05 启动 | 同脚本加 `--start` | 本机后端状态 | 只启动本节点后端；核实际镜像、EC、资源限额和健康状态 |
| 06 数据 | trace 检查或 ML 清单检查，见下文 | 输入摘要和清单 | 目标命名空间专用；数据完整；容量足够 |
| 07 挂载 | BrewFS/JuiceFS 本版本 CLI；参数参考 [mount_command](../../harness/aliyun_suite.py#L432) | mount argv、环境、PID、挂载点 | baseline、metadata DB、bucket、缓存目录与配额正确；FUSE 实际参数留证 |

示例分别执行，不串成自动流水线：

```bash
python3 harness/final_plan.py --cluster C1 --output "$RUN_DIR/plan.json"
```

```bash
python3 infra/alicloud/deploy/freeze_backend.py \
  --rustfs-source "$RUSTFS_SOURCE" --brewfs-source "$BREWFS_SOURCE" \
  --brewfs-binary "$BREWFS" --image "$IMAGE" --output "$RUN_DIR/backend-pin.json"
```

```bash
python3 infra/alicloud/deploy/generate_rustfs.py "$DEPLOY_SPEC" "$DEPLOY_DIR"
```

```bash
python3 infra/alicloud/deploy/rustfs_node.py "$NODE_DIR"
```

```bash
python3 infra/alicloud/deploy/rustfs_node.py "$NODE_DIR" --start
```

正式 deploy spec 增加下面的 `experiment` 字段（其余 image/mode/nodes 字段沿用生成器）；项目名使用本批/节点/attempt 的唯一实际名称：

```json
{"experiment":{"project_name":"ax-campaign-c3-n06-attempt1","stage":"D","layout_profile":"enabled","cpus":3,"memory_bytes":17179869184}}
```

生成器会写四个功能开关、3 CPU/16 GiB 与禁止自动重启；`layout_profile` 为 disabled/block-only/enabled，G/A/P/F/M 只接受 disabled。兼容旧 spec 的模式只供历史使用，不能省略该字段运行新正式组。容器启动后再核实际 env、CPU/memory/swap 与 OCI revision；配置生成并不证明特性生效。

客户端＋元数据另外放本组专用父 cgroup，总计 4 CPU/8 GiB、禁 swap。AI 在启动任一服务前建立并核该父组，核对所有子 PID 都在其中；不能给客户端、Redis/TiKV 各自重复分配整个预算。初始化 DB/卷、immutable lower、客户端 mount 各自独立调用。前台 mount 用独立、无总时限的 systemd 单元托管，记录 PID/cgroup，后续 AI 单独查就绪；不要让计时包装器等待长期 mount 退出。

机器检查分别执行：

```bash
python3 harness/experiment_inventory.py check --inventory "$INVENTORY" --output "$RUN_DIR/inventory-check.json"
```

```bash
python3 harness/host_preflight.py --inventory "$INVENTORY" --node "$NODE_ID" --output "$RUN_DIR/host-check.json"
```

### 单条命令与证据

复制 [step spec 模板](STEP-SPEC.example.json)，填绝对 cwd、argv 数组、可执行文件 SHA、输入/脚本/配置路径及 SHA、cell ID、步骤名。凭据放 0600 文件或受保护环境，不写 argv；`inherit_environment` 仅列确实需要的变量名（AWS、HOME/XDG、明确的 BrewFS 控制等），不保存变量值。默认不继承隐藏的缓存/布局环境变量。

AI 核实际命令后设置 `reviewed=true`，重新计算 spec SHA，再执行一次：

```bash
python3 harness/experiment_step.py run --spec "$STEP_SPEC" --spec-sha256 "$STEP_SHA256" --output "$STEP_OUT"
```

目录内有 spec、started、PID、stdout/stderr 和 finished 收据。非零退出保留失败，缺 finished/有 interrupted 代表进程状态未明，先查真实 PID/后代再决定；不自动重试。包装器墙钟包含进程启动/退出，不能替代工具内部阶段计时；所有阶段和条件使用相同包装方式。每次只包装一个命令，不包装旧整套运行器，也不把多个 shell 命令拼进 argv。

## 3. G1 / A1：一次真实 trace

### 3.1 核输入

正式输入保留 [七份 case 与来源](../../paper-design/TRACE-WORKLOADS.zh.md)。逐一下载完整包，下载器检查大小与整包 SHA；不能以 OSS HEAD 成功代替：

```bash
python3 scenarios/k8s-containers/scripts/download_archives.py \
  --out "$ARCHIVES" --cases "$CASE_ID"
```

```bash
python3 scenarios/k8s-containers/scripts/check_archives.py "$ARCHIVES" --cases "$CASE_ID"
```

解压到新输入目录，核对应 root_dir 和完整文件清单后准备本次初态。events/blobs 放在被测挂载之外，输入盘/预读策略在各臂一致；Django 事件流约 21.36 GiB，另核输入 I/O 与解析占比。首次资格验证先用 pytest-full-02，正式七份逐 case 执行。

[trace_checks.py inputs](../../harness/trace_checks.py) 只读输入并检查清单，要求包含 final-tree：

```bash
python3 harness/trace_checks.py inputs --case "$TRACE_CASE" \
  --manifest "$TRACE_MANIFEST" --sha256 "$TRACE_MANIFEST_SHA" \
  --output "$RUN_DIR/input-check.json"
```

AI 检查可信输入来源与摘要；将相同初始树准备到本次专用挂载。Overlay 的初始内容在已验证不可变 native lower，回放写入 upper；flat/JuiceFS 导入相同内容。初始树检查命令如下，检查结束后再建立规定缓存初态：

```bash
python3 harness/trace_checks.py tree --root "$REPLAY_ROOT" \
  --expected "$TRACE_CASE/initial-tree.json" --output "$RUN_DIR/initial-check.json"
```

### 3.2 执行一个回放

两机就绪后，AI 分别启动本周期的 [Rust 回放器](../../tools/full-fuse-replay/README.md)，同时保存资源、Redis/S3/FUSE 计数和单调时钟；不设总回放时限：

```bash
"$REPLAYER" "$TRACE_CASE/capture/events.jsonl" "$TRACE_CASE/capture/blobs" \
  "$REPLAY_ROOT" "$RUN_DIR/replay.json" --performance
```

### 3.3 检查并停下

```bash
python3 harness/trace_checks.py replay --result "$RUN_DIR/replay.json" \
  --metrics "$TRACE_CASE/metrics.json" --output "$RUN_DIR/replay-check.json"
```

```bash
python3 harness/trace_checks.py tree --root "$REPLAY_ROOT" \
  --expected "$TRACE_CASE/final-tree.json" --output "$RUN_DIR/final-check.json"
```

AI 核进程已经结束，单独卸载、重新挂载，再执行 tree 检查写入新的 `remount-check.json`。检查通过只覆盖回放计数和所列文件树属性；保留 errno 差异。两个 baseline 本周期都收集完毕后，AI 才安排换位。

## 4. G2 / A2：暖读逐步执行

[分步工具](../../harness/warm_same_files.py) 不再从 CLI 自动预热并测三遍。四个文件必须预先写满、内容核验、大小各为 512 MiB；`FILE0..3` 是完整文件路径。

1. **生成覆盖任务**，只写 job 和元数据，不启动 fio：

```bash
python3 harness/warm_same_files.py prepare --phase coverage \
  --files "$FILE0" "$FILE1" "$FILE2" "$FILE3" --output "$STEP_DIR"
```

2. AI 检查 `step.json` 与 `job.fio`：readonly、文件身份、direct=1、iodepth=32、文件数量/大小；再执行单个 fio：

```bash
fio --readonly --output-format=json --output="$STEP_DIR/fio.json" "$STEP_DIR/job.fio"
```

3. **检查这一步**，脚本写入 `checked.json` 后结束：

```bash
python3 harness/warm_same_files.py check --step "$STEP_DIR"
```

4. 完整覆盖通过后，用新 `STEP_DIR`、`--phase warm --seconds 30` 生成下一遍；仍按“生成→AI 检查→fio→结果检查”执行。AI 比较最近两遍吞吐、GET/缓存变化，最多 6 遍。
5. AI 确认暖态后，用 `--phase measure --seconds 30` 单次生成和测量，分别完成三个新目录。每遍结束检查退出码、缓存状态、实际读取量；异常时保留原始输出，由 AI 决定下一步。

三个短测属于一次测量；不自动重试，不把低而平的吞吐当作完整预热。

## 5. P：只读 ML 小文件

| 步骤 | 具体入口 | AI 检查 |
|---|---|---|
| 01 语料 | [prepare-ml-corpus.py](../../experiments/retest-2026-10-29/scripts/prepare-ml-corpus.py)，已有语料直接复用 | 1,048,576 条独立记录，冻结任务和 readset |
| 02 清单 | [ml_corpus_inventory.py](../../harness/ml_corpus_inventory.py) | 数量、路径、大小、manifest SHA；内容另用 verify |
| 03 发布/导入 | Pack 单次 fixture；Native 用 [ml_import.py](../../harness/ml_import.py) 写入已挂载空目录 | 两臂源内容一致；发布/导入分别计时；对象/inline 分布留证 |
| 04 挂载/检查 | BrewFS 单次 mount；[ml-corpus-scan](../../tools/ml-corpus-scan/src/main.rs) verify | 实际 backend、解码预算、内容、总资源；检查后重建冷态 |
| 05 单次访问 | 同 scanner 的 stat 或 read | 一次只执行一个读者数和一种模式；读集一致、无漏文件/短读 |
| 06 收集 | 保存原始结果、请求/资源计数和 SHA | 内容检查与测量分开；训练计算/LLM 等待不并入文件访问墙钟 |

```bash
python3 harness/ml_corpus_inventory.py --root "$SOURCE" --manifest "$MANIFEST" \
  --output "$RUN_DIR/inventory"
```

```bash
"$SCANNER" "$MOUNT" "$MANIFEST" verify 16 1048576 "$RUN_DIR/content.json"
```

```bash
"$SCANNER" "$MOUNT" "$READSET_MANIFEST" read 16 1048576 "$RUN_DIR/read.json"
```

`stat`、1 读者使用单独调用与新结果文件。当前 scanner 按 manifest 顺序访问；正式打乱 readset 必须包含同一批完整记录并单独核 SHA。scanner 尚无访问延迟直方图，P 的延迟测量需补齐。现有 [P1](../../harness/ml_readonly_matrix.py)、[P2](../../harness/ml_readonly_end_to_end.py) 会串起发布、挂载和多轮扫描，只作为已完成资格验证的参考，不作为本轮分步执行入口。

Pack 发布只调用一次已构建 fixture；桶已由 AI 单独创建并记录归属，源树静止，参数及清单先核对并写入 step spec：

```bash
"$FIXTURE" --source-directory "$SOURCE" --source-consistency best-effort-detected \
  --source-hardlink-policy reject-external --bucket "$BUCKET" --endpoint "$ENDPOINT" \
  --region us-east-1 --force-path-style true --prefix "$OWNED_PREFIX" \
  --manifest-output "$MANIFEST_KEY_FILE" --output-dir "$PRODUCER_OUT" \
  --wire-version 5 --metadata-codec raw --data-codec raw --inline-data off \
  --frame-policy size-only --access-profile sequential-small-file
```

Native 的普通 flat-v1/TiKV 挂载准备好后，再导入一次：

```bash
python3 harness/ml_import.py --source "$SOURCE" --manifest "$MANIFEST" \
  --manifest-sha256 "$MANIFEST_SHA256" --target "$EMPTY_MOUNT_DIR" \
  --files 1048576 --bytes "$CORPUS_BYTES" --output "$RUN_DIR/native-import.json"
```

导入内容失败保留部分目录，不能直接重投到同一目录。AI 另做 drain、卸载/重挂、scanner verify，然后重新建立规定缓存初态。两臂构建/导入成本单列，不计入 readonly 访问时间。

## 6. D：动态块

按“启动一个 profile→上传固定对象→核布局→建立冷/暖态→执行固定请求→收集”逐项操作。AI 每次检查 PUT/HEAD 的 effective B、四 shard 的实际 B/q、inline/external 和重启读回；通过后才进入计时。

[布局参数](../../experiments/retest-2026-10-29/layout-matrix.json)、[布局解析](../../experiments/retest-2026-10-29/scripts/inspect-layout.py)、[已有资格探针](../../experiments/retest-2026-10-29/scripts/dynamic-probe.py)。布局解析 CLI 接受已有探针结果目录；纯解析函数可复用到新对象检查。旧探针会管理本地容器，不用它启动正式性能批次。

[s3_requests.py](../../harness/s3_requests.py) 的三个入口分别执行。`OBJECTS`、`REQUESTS` 根据容量和预试冻结；requests 至少覆盖每对象一次。两臂使用同一个请求计划及 SHA，仅 bucket/prefix/hint 随独立初态改变。

```bash
python3 harness/s3_requests.py plan --objects "$OBJECTS" --requests "$REQUESTS" --object-bytes 8388731 --output "$REQUEST_PLAN"
```

```bash
python3 harness/s3_requests.py upload --plan "$REQUEST_PLAN" --plan-sha256 "$REQUEST_SHA256" \
  --endpoint "$ENDPOINT" --bucket "$BUCKET" --prefix "$OWNED_PREFIX" --hint 65536 --output "$RUN_DIR/upload"
```

默认臂省略 `--hint`；其余改为冻结 B。PUT 使用 create-only 条件，服务器不支持就阻断，不能退回覆盖写。上传完成后 AI 单独核 HEAD、四 shard 的实际 B/q/外置布局，建立冷态或暖态，然后运行**一次**请求清单：

```bash
python3 harness/s3_requests.py read --plan "$REQUEST_PLAN" --plan-sha256 "$REQUEST_SHA256" \
  --endpoint "$ENDPOINT" --bucket "$BUCKET" --prefix "$OWNED_PREFIX" --mode range --output "$RUN_DIR/range"
```

完整 GET 改 `--mode full` 并使用独立结果目录。每请求计时包含 SDK/签名、HTTP 和收完 body，数据生成/内容核验在该计时之外；原始逐请求行、实际应用字节、退出/失败保留。单并发服务速率不等于系统饱和吞吐。连接/单请求读有 10/120 s 超时，SDK 无重试；这不是 trace 回放总时限。HTTP header/后端/设备字节仍由部署监控独立记录，不能拿应用字节冒充 I/O 放大证据。

## 7. F：fork / clone

AI 先准备并核验静止源树，再对一个 baseline 执行一次创建：

| baseline | 创建操作 |
|---|---|
| BrewFS | 本版本 `workspace fork REVISION --count N`；具体连接参数参考 [fork 初始化](../../harness/aliyun_suite.py#L319) |
| JuiceFS | 已核对 `clone --help` 后执行本版本 `clone -p SRC DST`；8 个副本按预先冻结的顺序逐次创建 |
| 全量复制 | 在 BrewFS Native flat-v1 挂载内执行 `cp -a --reflink=never --sparse=never SRC DST`，记录实际读写量；不是本地 ext4/XFS 参考 |

创建之后，AI 分别安排挂载、首次读、首次写+fsync、卸载/重挂、隔离检查；各阶段分别计时。8 副本主组均按串行创建和串行首用，报告总 makespan 与逐副本值；并行创建另列。

[现有 fork-base](../../harness/aliyun_suite.py#L573) 仅供语义实现参考。用 `experiment_step.py run` 包装当前**一条**原生创建命令；八个子副本逐条记录，保留统一协调端总区间，不合并成三个 baseline 的自动循环。

8 副本单元内不插入 AI 思考/审批等待；AI 先审阅本单元的固定调用顺序，再执行并验收。若只能人工逐条调用，先报告每次 API 墙钟与其和，包含人工间隙的总区间标为流程时间，不能冒充创建 makespan。

[fs_step.py](../../harness/fs_step.py) 提供独立入口，证据输出必须在被测树外：

```bash
python3 harness/fs_step.py fixture --root "$NEW_SOURCE" --files 100000 --file-bytes 4096 --output "$RUN_DIR/fixture.json"
```

```bash
python3 harness/fs_step.py snapshot --root "$SOURCE" --output "$RUN_DIR/source-tree.json"
```

```bash
python3 harness/fs_step.py first-read --root "$CHILD" --file d000000/f00000000 --sha256 "$FIRST_FILE_SHA256" --output "$RUN_DIR/first-read.json"
```

```bash
python3 harness/fs_step.py durable-write --root "$CHILD" --file first-write --payload "$FROZEN_PAYLOAD" --output "$RUN_DIR/first-write.json"
```

资格验证使用 1,024 文件。先在独立本地参考副本执行 `mutate`、`snapshot` 形成预期变更清单；再在被测子副本执行 `mutate`，卸载/重挂后 `check --expected ... --expected-sha256 ...`。源与未修改兄弟分别对原清单 check。mutate 包含修改、追加、截断、删除、重命名、新建和 fsync；不把对被测错误输出自身 snapshot 当正确答案。首次读/写后再做全量内容验证，避免预先遍历把首次访问变成暖读。

## 8. M：Agent 流转

执行 [MIGRATION.zh.md](MIGRATION.zh.md) 的独立步骤；`session_check.py checkpoint/restored/resumed` 检查历史，`fs_step.py snapshot/check` 检查文件。原生 export/import/mount/resume 各自单条命令留证，不直接调用旧整套 resume probe。

## 9. 收集与清理

AI 核对每次测量的配置、输入、版本、退出码、原始结果、计数和检查文件，记录 SHA；下载后再核 SHA。先确认实际进程结束和 FUSE 卸载，再按本批精确资源清单单独清理。连接中断或状态不明时保留现场，先查进程，不自动重投。

对检查过的**显式文件清单**保全，避免递归打包凭据、私钥或模型配置。seal 只是文件哈希，不是内容脱敏器或实验完整性门；AI 必须先确认原始日志不含凭据、必需结果均已列入。

```bash
python3 harness/experiment_step.py seal --root "$STEP_OUT" --files spec.json started.json process.json stdout.log stderr.log finished.json --output "$RETENTION_MANIFEST"
```

另加入工具产生的完整原始结果、输入/pin/资源/内容与验收证据；不能只保存这六个包装器文件。下载后用同一 manifest（管理端预留其 SHA）核验：

```bash
python3 harness/experiment_step.py verify --root "$LOCAL_COPY" --manifest "$RETENTION_MANIFEST" --manifest-sha256 "$RETENTION_SHA256" --output "$LOCAL_VERIFY"
```

云资源按阿里云指南释放；provided 仅撤销本轮创建的服务/挂载/namespace，不删除服务器或格式化盘。保全收据不自动触发任何清理。

结果汇总沿用 [summarize_suite.py](../../harness/summarize_suite.py) 的已支持格式；新分步格式需单独转换并保留来源关系，不能直接当成旧套件结果。只合并相同条件的完整配对区组，缺失值保留为空。
