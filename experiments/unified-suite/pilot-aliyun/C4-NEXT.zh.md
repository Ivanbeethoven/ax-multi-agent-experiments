# C4：先跑100k派生基线

> 历史协议（截至 2026-11-11）：仅供旧冻结计划和结果追溯。新实验使用[当前方案](../current/README.zh.md)；本文旧路线、规模、默认配置与命令不作为新批次入口。

11 月 11 日 17:45，[B 组 JuiceFS](evidence/c4-jfs-01/README.zh.md)的 10 万文件派生已正式验收：六轮校准、正式计时、排空、父子工作区全量冷读、退出和归档均通过。校准的退出竞态只补收原档，没有重跑计时。[A 组 BrewFS flat](C4-NATIVE-FLAT.zh.md)正在导入原工作区。C4 派生为 1/5，跨机流转为 0/4。两组均复用原有机器，没有调用模型。

完整入口为[run-c4-batch.py](scripts/run-c4-batch.py)，包含一次基线准备、六次校准及一个正式派生，参数和收尾见[C4-OBSERVE.zh.md](C4-OBSERVE.zh.md)。下文的code14/draft-02是历史功能预演，用五文件检查了23个场景。

| 路线 | 当前能做什么 | 未完成条件 |
|---|---|---|
| JuiceFS `clone -p` | 十万文件派生已验收，[原始数据可下载](evidence/c4-jfs-01/oss/index.json) | 本轮已完成 |
| BrewFS `flat-v1`完整拷贝 | 同一入口，明确禁用 reflink；正在导入父工作区 | 实测容量、六轮校准和正式样本；这是完整拷贝对照，不能作为共享基线优势 |
| BrewFS `workspace-native-v2`派生 | 仅空revision派生与父子独立写入/冷读通过 | 非空100k发布→派生缺实验适配；dirty reopen后写/fsync仍EIO |
| `entity-native` | 尚不能执行100k | native lower的32768行上限未解决 |
| `packed` | 已有十万文件独立工作区读写和远端只读核验 | 原 C4 包含跨目录重命名，当前 Entity FUSE 缺此操作；另需父子隔离和完整生命周期证据 |
| JuiceFS handoff | 原共享会话冷读、空HOME恢复状态已有证明 | 真实resume尚无新tool完成；多池源准备、目标节点映射缺适配 |
| BrewFS handoff | 暂不执行 | workspace-native-v2 dirty reopen写入EIO |

另有[JuiceFS 无模型跨机检查草稿](evidence/c4-state-reopen-draft-01/)，复用 B 组源客户端和 A 组目标客户端。先等 A 组 C4 和 B 组 C2→C1 队列均关闭，再取得两组新的交接回执；此前 B 组转交 C2 的回执不能复用。该检查验证空 HOME 能否从同一工作区重开原 OpenCode 状态，不验证模型继续生成工具调用，也不把协调器拒绝旧授权写成 JuiceFS 自身的写入隔离。

现有五个阶段的状态库还没有初始化四个正式 `handoff-1` 单元（两种文件系统，各做原机接续和跨机接续）。诊断草稿里的四个单元只用于绑定原计划，不能作为正式样本。JuiceFS 仍需要实际恢复后完成原定工具操作的证据；BrewFS 工作区重挂后写入和 fsync 的 EIO 仍待解除。没有独立的第三台目标客户端。

## 已冻结的后续计划

协调者文件：`/srv/ax-pilot-control/parallel-control-01/next/fork-100k-01/plan.json`。

- 文件SHA256：`4b98ab083a06ae353806557f956a60fad8b3c29b8b43971c452cf3a7136b2a7e`。
- `plan_sha256`字段：`efc24682894f6863e55cf7646e352a2418a8c6ac629a4934af3a95a6ef7f992d`。
- JuiceFS：`c5e97774bcffa69a-r1-juicefs`，pool-b。
- flat对照：`c5e97774bcffa69a-r1-native`，pool-a。
- 其余三臂保留在计划中，新入口直接拒绝。未修改现有C1–C3的19项计划或state。

每池已有源：`/srv/ax-client/inputs/pilot-100k-01/fork-source`，100,000×4096B、100个目录。原树为同目录`receipts/c4-fixture-tree.json`，SHA256固定为`02aeda272edbab9512595f7b968d840a9cfdc0e8302ed036399bf4151f08bd13`。`smallfiles-source`使用另一套字节公式，不能替代。

## 执行入口与指标

新脚本：[run-c4-fork-baseline.py](scripts/run-c4-fork-baseline.py)。它在协调者持有**原`parallel-control-01/runner-locks`**中本池的pool/实例/盘锁，检查原state及显式列出的其它旧state中本池或资源相交的lease，再SSH调用本池client。另一池的独立任务可以继续。不另建state、锁目录或服务。

client还须实际通过：原slot空、8910/9567无人监听、新asset/输出/挂载路径、DBSIZE=0、至少10GiB和120万空闲inode、主机名与冻结inventory一致、程序及输入SHA一致。凭据继续从原私有env读取，不进入请求。

执行步骤固定：完整源检查→独立oracle→导入/逐文件与目录fsync/两次真实零债务/正常卸载→新缓存全量验父目录/卸载→新缓存创建一个child/首次内容读/逐文件fsync/零债务→六种固定修改/fsync/零债务/卸载→新缓存完整父子隔离检查/卸载。每个子命令保留argv、PID、stdout/stderr、实际退出和耗时，四次卸载均核原PID/启动身份退出及挂载消失。

| 输出指标 | 计时范围 |
|---|---|
| `create_command_seconds` | 真实clone/copy命令开始至退出；尚不等同持久化完成 |
| `first_read_seconds` | 首个固定文件open/read/close；读取4096B且核内容 |
| `creation_fsync_seconds` / `creation_drain_seconds` | 完整child与其父目录fsync；两次原生零上传债务，间隔≥1秒 |
| `mutate_fsync_seconds` / `mutation_and_drain_seconds` | overwrite/append/truncate/unlink/rename/create；文件、目录fsync及真实排空 |
| `full_cold_verification_seconds` | 新挂载完整检查父目录原树及child预生成期望树 |

三个原生`.prom`快照保留创建前、创建排空后、修改排空后的真实计数。完整Redis/S3/物理盘/CPU指标仍须接现有三角色observer，并做C4自身的三对on/off开销校准，才可正式admit。脚本结果明确`performance_accepted=false`、`formal_admission=false`，不会把功能预演记成正式cell完成。N=1没有兄弟并发或规模结论；新缓存也不代表服务端/主机缓存已清空。

## 请求与命令

独立脚本放协调者与client新的`preparation/tools-c4-fork-baseline-01/`；不覆盖code12/13。新计划和原inventory仅以小文件私网复制到client新目录，并机械核SHA；无需重编译。请求是`c4-fork-baseline-request-v1`、`reviewed=true`的JSON；每个文件引用均为`{"path":"绝对路径","sha256":"实际SHA256"}`，不得填猜测值。

| 协调者请求字段 | 内容 |
|---|---|
| `source_tool_sha256` | 新脚本的实际SHA；协调者/client必须相同 |
| `control` | `/srv/ax-pilot-control/parallel-control-01` |
| `canonical_plan` | 原control下`suite/plan.json`，不是新fork plan |
| `fork_plan` | 上述`next/fork-100k-01/plan.json`及固定文件SHA |
| `inventory` | 原control下`provenance/inventory.json`及实际SHA |
| `build_manifest` | 原control下`provenance/build-manifest.json`及实际SHA，须匹配fork plan |
| `ssh_config` | 已核主机key的现有SSH配置及SHA |
| `latest_check` | 本配对批实际分支检查：passed、两组件matches、对应本计划build manifest SHA；不热换正在运行的版本 |
| `pool` / `product` / `cell_id` | 上述明确cell；两种baseline分别提交 |
| `other_lease_controls` | 仍可能持有同批资源的其它原control绝对路径列表；没有则`[]` |
| `out` | 协调者全新证据目录 |
| `worker` | 下列client字段 |

`worker`文件引用：`tool`、`plan`、`inventory`、`node_tool`、`filesystem_binary`、`source_tree`、`fixture`、`input_closure`。后3项分别是原`receipts/c4-fixture-tree.json`、`receipts/c4-fixture.json`、真实`close-cloud-inputs.py --sources-only --files 100000`输出。`plan/inventory`内容SHA必须与协调者一致。`node_tool`指现有冻结代码的`pilot-node.py`；JuiceFS为`assets/bin/juicefs`，BrewFS为已核的`build-ca07159-01/bin/brewfs`，若路径不同以实际分发清单为准。

`worker.runtime_pins`必须给出`bench/fs_step.py`、`experiments/unified-suite/pilot-aliyun/scripts/fork-oracle.py`和`pilot_io.py`三个相对路径对应的实际SHA。B的code12中`fs_step.py`仍依赖umask，SHA为`d7597b…`；新入口强制使用已测的PR15版本`d58b7b7198450e1728c10b84cb978c0b793e257ecafca21ee5ac2829cb4a9c79`。oracle实际未变化（`06a226…`）。必须冻结新runtime到新目录，保留code12和原输入。

B的具体draft和只读证明已存协调者`/srv/ax-pilot-control/parallel-control-01/next/c4-fork-jfs-100k-01/`：当前用`request.draft-02.json`（SHA256`e8a622b505733426bceedd7b99695c9ddc84f35ee275190eb2742d11203c35c6`）、`deployment-02.json`、`code14-readonly.json`，原draft及`runtime-difference.json`、`source-readonly.json`保留。`reviewed=false`；client未部署、worker未启动。新asset/out/attempt统一为`c4-fork-jfs-100k-01`；DB200只读检查时为空，执行仍须复核。

已在协调者核code14的manifest SHA`2235c7240f48328c9d254ee85870a1d83014e4407903208b482a1d577f9625cd`及6个实际文件，入口SHA`33f8c7441f8fc8d736100ff0594a894feb86dc700731d39a6b213facd9a75862`和新fs_step均匹配。B空闲后才将该冻结包安装到新`/srv/ax-client/code-14`，计划/inventory复制到独立tools目录，再存新reviewed请求。协调者可直接用已存在的`/srv/ax-pilot-control/code-14`，不覆盖任何code12文件。

`worker`其它字段：`source`为上述fork-source；`asset`为`/srv/ax-client/a/`下全新目录；`out`为`/srv/ax-client/preparation/`下全新目录；`database`为现场检查为空的20–255独占DB；`attempt`为3–32字符小写字母/数字/连字符。不要复用C3 asset、旧attempt或已用DB。

协调者在本池阶段收尾后运行：

```bash
python3 "$NEW_TOOLS/run-c4-fork-baseline.py" run \
  --request "$REQUEST" --sha256 "$REQUEST_SHA256" \
  --runtime-repo "$FROZEN_COORDINATOR_REPO"
```

锁/lease/slot忙时直接拒绝，不能停另一任务抢位。SSH断开时只读复查原worker的PID/boot/start-tick、slot与挂载；无法确认退出就保留协调者进程及锁等待人工核验，不自动重跑或强卸载。原生步骤失败保留现场和普通卸载记录。

## handoff复用两台client

后续可用A的client作为B的迁移目标，反向也可；无需新ECS。但handoff必须锁双方client的真实实例/盘以及源池Redis/objects，因此不能与这些资源上的C1–C3并行。不同control的flock目录不互通，仍须统一到上述canonical锁根。

当前还不能直接加一个target别名就跑：`setup-controller.py`把target根写死为`/srv/ax-target`，实际另一client的数据盘是`/srv/ax-client`；`ssh_inventory`也拒绝把同一VM伪造成两个独立environment。需要真实执行节点复用及observer去重映射、相同2CPU/8GiB slot和冻结binary、空的相同workspace绝对路径。目标必须连接**源**Redis/桶/workspace，会话DB/XDG随原workspace走，不能改接目标池后端或覆盖其私有env。

另需给`prepare-handoff-source.py`补多池inventory及明确binary参数，并处理真实resume失败。现有JuiceFS证据只到状态可读；原180秒模型无响应记录不能算迁移完成。`state_ready_seconds`与包含模型等待的`first_completed_tool_ack_seconds`须分报；普通不同ECS只能证明换执行机，不能声称换物理宿主机。

三角色观测、开销校准及formal接线见[C4-OBSERVE.zh.md](C4-OBSERVE.zh.md)。

离线验证：`python3 -m unittest tests.test_c4_fork_baseline -v`，9项通过，只生成5个4KiB临时文件；本轮未运行云端C4。
