# 执行适配：配置字段与原生命令

操作顺序见[SSH-RUN](SSH-RUN.zh.md)。本页保留mount/worker/cohort/handoff的字段和原生命令细节。运行环境来自已交付SSH容器、VM或主机；按实际清单固定配额与放置。

## 1. 文件和执行单位

| 文件 | 一次职责 |
|---|---|
| [prepare.py](../../../bench/runtime/prepare.py) | 生成待检查的原生挂载命令、挂载 spec 或单个 Agent 容器创建命令；不执行 |
| [node.py](../../../bench/runtime/node.py) | 一个前台 FUSE 进程的 start/status/stop；一个 worker 的屏障和冻结任务队列 |
| [transport.py](../../../bench/runtime/transport.py) | 固定 SSH 配置/host-key、通过 stdin 传协议；校验远端 Python 和全部 runtime 模块 SHA |
| [cohort.py](../../../bench/runtime/cohort.py) | 全部 worker ready 后释放一个 cohort；保留各任务及失败，不自动重试 |
| [handoff.py](../../../bench/runtime/handoff.py) | 一次 clean stay/move，原生会话检查、源端卸载/lease 门、目标挂载及继续工具调用 |
| [observe.py](../../../bench/runtime/observe.py) | 原始采样、阶段标记、断档/摘要检查、配对采集开销核验；详见[观测协议](OBSERVABILITY.zh.md) |

所有引用采用 `{"path":"/absolute/path","sha256":"实际64位摘要"}`。路径由执行方填写，不能照抄另一台机器。输出路径须新建且位于被测挂载以外。`reviewed=false` 的生成文件不能执行；AI 检查实际配置后改为 true，**先固定 command，再更新 mount.command 的 SHA，最后固定 mount/worker/cohort SHA**。不要用修改布尔值代替资格证据。

## 2. 每 Agent 的运行边界

每个 Agent、FUSE 客户端和回放器共用 **2 CPU / 8 GiB / swap=0**。SSH 清单 v3 按实际 slots 分配，cohort 核对每个槽位与真实 cgroup 身份。直接进入已有容器时使用它的现有隔离；经宿主进入 Docker 时使用已分发的 digest 镜像。需要创建测试容器时，`prepare container` 生成 docker create，检查后再分步 create/start；输入只读，缓存和输出独占，权限限于 FUSE 挂载所需范围。

container 输入字段：`name=ax-...`、`campaign_id`、`agent_id`、`docker` 绝对路径、`image=repo@sha256:...`、`cwd`、`binds=[{source,target,readonly}]`，可选受保护的 `environment_file` 引用。密钥不写入 argv/公开结果。

独立 VM 可以同样用一个容器承载配额，仍标记为独占 VM 条件；若直接运行，管理员先建立独占 cgroup v2 子目录 `/sys/fs/cgroup/ax-本批/...`，启用 CPU/memory 控制，配置 `cpu.max=200000 100000`、`memory.max=8589934592`、`memory.swap.max=0`。peer 填 `cgroup` 路径，远端 bootstrap 将自身加入该组，挂载和任务继承同一配额。不能用两套各2CPU/8GiB的独立组冒充总预算。实际祖先限额也会检查。

## 3. 原生挂载

本批实际版本的完整 BrewFS JSON 配置（BrewFS YAML parser 可读取 JSON）由执行方准备：C1/C4 Redis；C2 Native 是 flat-v1＋TiKV，Pack 是 packed-metadata-v3。包括真实 S3、metadata、workspace/namespace、native_base（native-v2）、layout、fuse 和 cache。`cache.writeback_mode=upload_before_commit`，显式 TTL/keep_cache；其他缓存、codec、持久性预算沿用科学计划，不由适配器偷偷补默认值。native-v2 异步仍阻塞。

挂载默认追加 `--s3-rustfs-ec-block-size-hint auto`，二进制须编译 `rustfs-ec-block-size-hint` feature。配置可显式写 `data.s3.rustfs_ec_block_size_hint=auto`；配置与命令策略冲突时拒绝。固定档位用 `--dynamic-block 65536|262144|1048576|4194304`，显式客户端消融用 `--dynamic-block off` 并清空配置 hint。C1/C2/C4 主组使用 auto，C3直接发送固定对象 hint。lower/Pack 导入的上传者另核策略，只读挂载不会重写已有对象。服务端四个开关、auto局限和布局验收见 [RustFS](../../../infra/rustfs/README.md)。

JuiceFS 配置字段：`metadata_url`（不含密码的 Redis URL）、`cache_dir`、`cache_mib`、`buffer_mib`、`writeback=false`、`attr_cache_seconds`、`entry_cache_seconds`。凭据使用冻结版本原生支持的受保护环境。两臂缓存口径必须配对，不能将产品默认误称为相同配置。

```bash
python3 -m bench.runtime.prepare mount --filesystem brewfs --cluster C1 \
  --config "$CONFIG" --config-sha256 "$CONFIG_SHA" \
  --binary "$BREWFS" --binary-sha256 "$BREWFS_SHA" \
  --mount-point "$MOUNT" --cwd "$WORK_ROOT" --output "$NEW_MOUNT_SPEC_DIR" \
  --metrics-address 127.0.0.1:19567 --fstypes "$OBSERVED_FUSE_TYPE" \
  --inherit-environment AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY
```

实际 fstype 从该冻结版本的 Linux 资格挂载取得；不把旧版本名称当事实。每 Agent 独立 loopback metrics 端口（host network 下不重复）；BrewFS 必须编译 `observability` feature。换 JuiceFS 时只替换 filesystem、二进制与配置。配置和工作目录映射在容器内必须一致。

```bash
python3 -m bench.runtime.node start --spec "$MOUNT_SPEC" --sha256 "$MOUNT_SHA" --output "$MOUNT_STATE"
python3 -m bench.runtime.node status --output "$MOUNT_STATE"
```

start 要求空挂载点、新缓存目录；记录真实 kernel mount ID、原进程和预算。准备完成后，AI 单独核内容/权限/xattr/重挂/非空 lower，再准备任务。stop 只向原始前台子进程发正常 SIGINT，等待退出和物理卸载；活跃 worker 会阻止 stop。没有 force、lazy unmount、超时强杀、自动恢复或删除。启动失败保留现场，不能换 run ID 盲重投。

## 4. 一个并发批次

worker spec `experiment-worker-v1`：`reviewed`、`worker_id`、`purpose`、`kind=trace|ml|s3|control`、`budget`、`requires_mount`、`mount={spec:引用,directory:状态目录}`、`jobs`。每 job 有全局唯一 `id`、`step` 引用、`result={path,equals:{...}}`。step 使用现有 [experiment-step-v1](../../../bench/experiment_step.py) 的原生 argv、binary SHA、inputs、cwd、受控环境；任务队列提前冻结，运行中不分题/不重排。

| 场景 | worker 运行内容 | 必检结果 |
|---|---|---|
| C1 | Rust full-fuse-replay 的完整任务；队列256预先均分 | completed、mode、valid、ops、实际读写字节、handles=0；performance 的 valid=false 是预期，不能改成正确性通过 |
| C2 | ml-corpus-scan，固定真实 ML 清单/读序；每 Agent 的内部 workers 数另冻结 | 文件数、实际字节、errors=0、access_latency_ns_log2；verify 与计时 read/stat 分开 |
| C3 | s3_requests read，一 requester 一 shard，不要求挂载 | 请求数、字节、shard、全局并发；两个 requester 的份额之和只能等于1/16/64。并发1只用一个 requester |
| C4 F | 原生 fork/clone/cp，冻结ID/批次/原请求；每调用一 job | 全部完成/实际返回身份、后续首用/隔离检查；202不等于完成。不自动重试fork或清理失败项 |

peer字段：`transport=ssh`、`alias`、`repo`、`python`、`pins`、`ssh={executable,config,known_hosts}`；container/cgroup二选一。pins由fleet probe核验并写入peer.json，覆盖Python及全部runtime依赖。SSH使用独立Host配置，目标及跳板均设置StrictHostKeyChecking yes、UserKnownHostsFile为固定文件、GlobalKnownHostsFile none、ForwardAgent no、PermitLocalCommand no；不允许Include/Match/ProxyCommand/LocalCommand。local仅供adapter-test。

cohort spec `experiment-cohort-v1`：`agents`、`provider`、`inventory`、`purpose`、`workers=[{id,node_id,slot_id,peer,spec,output}]`（slot_id用于SSH清单v3）、`metric_contract`、`workload_contract`、`observers`、`input_qualification`、`filesystem_qualification`、`observer_overhead`。资格引用实际passed记录。workload_contract是已审阅的experiment-workload-contract-v1，含binary_pins、input_pins、任务映射和缓存/资源配置，on/off两臂共用同一摘要。每observer填id,node_id,peer,directory,spec,required_live_targets；涉及集群的全部已分配主机均覆盖。C3的filesystem_qualification记录对象服务/布局资格，不虚构FUSE检查。

另需 `allocation_qualification` 与 `hardware_qualification`，均引用passed记录且绑定inventory_sha256；前者记录真实授权范围/资产身份，后者汇集实际CPU/内存/盘/网络与负载头部余量。静态inventory通过不能代替这两项。每job填写workload_id，同类任务分别统计经验分位数和样本数；输入摘要检查在屏障前，任务输入全程只读冻结。大trace的预检时间单独预算，ready_timeout_seconds可按实际输入校验设置，不给回放设总时限。

```bash
python3 -m bench.runtime.cohort --spec "$COHORT" --sha256 "$COHORT_SHA" --output "$COHORT_OUTPUT"
```

先校验全部 worker，再测一次 live telemetry 并记录 release intent，随后放行；传输丢失后状态为未知，绝不再放行。结果给出各任务结果、release spread、协调器 makespan、任务/s、started/finished ACK时间。**ACK重叠不等于内核I/O同时进行**；需要CPU/I/O时间序列佐证。任务总回放不设时限。`performance_accepted=false` 保持到排空、独立读回、观测 audit 和本地保全完成。

原生结果不匹配或退出非零时，仍保存实际JSON、摘要、日志和失败原因，不只保存布尔值。统计只把通过的任务计为完成，并同时列失败/未启动数；完整cohort失败不能用部分吞吐进入排名。

## 5. 一次 C4 交接

先在计时外冻结同一个已完成工具边界的 native export。`handoff` 只接受 clean 会话迁移，不是 live process/FD 迁移；邻居由另一个持续 cohort 驱动，不随迁移退出。

handoff spec `experiment-handoff-v1`：`mode=stay|move`、`filesystem`、`session_id`、`source/target={node_id,physical_host_id,peer,mount_directory,anchor}`（target另含 mount_spec）、以及 `quiesce/lease_check/restore/resume/export_after` 五份单 Agent cohort 引用。quiesce 的末 job 是原生 export，restore 的 jobs 是原生 import 后再次 export；resume 使用冻结的 `opencode run --session ... --format json` 命令并设置 `observe_opencode_tools=true`，最后再导出。具体 CLI 以固定版本 help 和资格测试为准。

只复制原生 session JSON 到目标本地私有路径，**不复制共享数据树**。`stage_export` 引用 `experiment-stage-json-v1`，含受控 root 和唯一 allowed_paths；`import_path` 固定。`export_transfer_sha256` 是 `json.dumps(export,ensure_ascii=False)+'\n'` 的 UTF-8 摘要，提前写入 import step 的 inputs。实际 checkpoint 不一致就终止，不能在计时中改计划。

```bash
python3 -m bench.runtime.handoff --spec "$HANDOFF" --sha256 "$HANDOFF_SHA" --output "$HANDOFF_OUTPUT"
```

顺序：quiesce/export → 原进程正常卸载/退出 → lease_check → stage export → 目标挂载同 anchor → import/export历史前缀核对 → resume新工具 → export复核。BrewFS `workspace inspect` 必须给出原 workspace ID、lease_id/holder_generation均null、pending_writeback_bytes=0。JuiceFS无同构的workspace独占lease，需源进程退出与本任务独占写者证明，不能伪造 lease 字段。原生导出本身不能证明客户端已退出。

所有阶段时钟取同一协调器；首工具只计入最后导出里新增的 completed callID，旧历史重发不会算恢复。任务评分、外部副作用去重和文件内容另验。stay同样停写、重挂、恢复；move须换执行节点，只有真实物理宿主证据不同才声称跨物理机。

## 6. 本地验证与实际资格

```bash
python3 -m unittest discover -s tests -p test_runtime_adapters.py -v
```

包含64个真实本地 worker、输入失败不释放、错误结果不重试、原进程正常退出/活跃worker阻止卸载、分片并发上限、lease拒绝阻止目标启动、真实 `/proc`/HTTP采集与缺项/计数回退/断档/文件篡改检查。mountinfo与产品API的部分失败测试使用受控替身，**不代表真实BrewFS/JuiceFS两节点资格、阿里云64 Agent性能或实际采集开销已经通过**。这些必须在本批实际机器上取得证据后开始正式C1。
