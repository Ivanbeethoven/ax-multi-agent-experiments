# B 组剩余 C1：每轮独立检查、独立启动

范围是原冻结的 JuiceFS 随机混合读写，cell `8e3d340ff23c3ece-r1-juicefs`。原 `mxb01` 失败留档；它的 Off1/On1 不进入新 overhead 校准。已完成的历史 On1 可作为容量下界参考。执行目录固定为新的 `/srv/ax-pilot-control/b-c1-after-reboot-01`，使用原冻结 plan 的相同字节、独立初始化的状态库和当前 boot 的新资格；物理锁仍在 `parallel-control-01`。**此入口仅在本地验证，实际准备尚未执行；完成下述重新资格、空间预算和真实边界后才可逐轮派发。没有部署或运行过云负载。**

1. 按[共同清理规则](STORAGE-CLEANUP.zh.md)，确认原轮退出、已有副本核验完成，再记录本轮边界。入口只接受 `pilot-prestep-cleanup-v1` 的 `not-needed`、`actions: []`，不执行删除；凡需实际清理，须先接入能核真实路径、归档、dry-run 和 native 身份的 B 回收器，目前没有。A 专用工具、手写 `passed/groups` 和 dry-run 均不算 B 实际清理证明。
2. 用原 `draft-one-c1.py` 生成一次完整新批次，选不同于 `mxb01` 的新后缀 `<suffix>`；`--run-directory` 设为 `<control>/one-cell-runs/<cell-id>-<suffix>`。保留原 workload、metrics、asset、节点和四个工具。
3. 逐轮准备独立请求。顺序固定为 **Off1 → On1 → On2 → Off2 → Off3 → On3 → 正式**。每条命令只做本轮；正式轮仍调用原 5% overhead check、admit、run。每轮准备、CRC 冷读、排空和正式统计使用原脚本。

每个 `c1-storage-step-v1` 请求填 `reviewed: true`、`step`（1–7）、`original_request`（新批次 SHA 引用）、`frozen_request`（旧 `mxb01` 原请求 SHA 引用）、`base_recheck`（下述当前 boot 的六份原生 CRC/挂载引用）、`prefix`（从 step 1 起的实际 `result.json` SHA 引用列表）、`prefix_archives`（同序的真实归档引用）、`plan`、`inventory`、`disk_mapping`（下述原部署回执）、`snapshot_tool`（原 `capacity-check.py`）、`cleanup`、`capacity_qualification`、`lease_controls`（实际所有 state.sqlite 的父目录，包含新目录和 canonical）和一个尚不存在的 `preflight_out`。所有引用都是绝对 `path` 和 `sha256`。新批次省略 `runtime_control`，直接用新 controller 的 fleet/probes；容量材料的 `runtime` 为 null。清理回执的 `original_request`、`step`、`prefix` 和 `prefix_archives` 必须完全相同，并晚于上一轮关闭。

每个 `prefix_archives` 项按 role 提供 `coordinator`、`client`，On 轮另有 `metadata`、`objects`。均为真实 `bench.archive_check` 格式的 tar，kind 为 `calibration-<attempt>-<role>`。协调者包以原 trial 为根，角色包沿用原 `retain-calibrations.py` 的节点成员布局；它的六轮总入口不能直接用于逐轮归档。入口重核整个 tar 的成员 SHA，并把协调者 finished/cohort、客户端退出/CRC/排空和 On 轮采样 history/原文绑回原始 pins。此前缀归档适配并未自动打包或上传 OSS；实际删除仍须遵守共同清理规则的 OSS 完整回读要求。

```bash
python3 run-c1-storage-step.py --request "$step_request" --sha256 "$step_request_sha" --validate-only
# 真实证据齐备且已安排无共享链路冲突的窗口后，明确派发这一步：
python3 run-c1-storage-step.py --request "$step_request" --sha256 "$step_request_sha"
```

`--validate-only` 只核对协调者文件与 SQLite；不连接工作节点。执行时持原物理锁，先检查无本池或共享资源租约、空 Agent 槽、原挂载/工作/观察进程已关闭和实际采集器接续。再查原四个对象盘及 client/metadata 盘实际 mount、盘序、序列号、设备号和 boot；盘目录误落系统盘会拒绝。原 `capacity-check.py snapshot` 在锁内读取清理后的字节、inode、Redis 和容器身份。逐盘及协调者输出盘按新增量扣除后须剩 20%；Redis 内存同样留 20%，AOF 增长及重写空间计入元数据盘。原快照没有固定 schema。

磁盘映射使用[原 B 部署回执](evidence/preparation/pool-b-services-actual-01/result.json)，SHA-256 为 `05637a3d93dab1be99e6f15eb4d3f00767c78d0a5481f3ea659e98ffdab4a9ce`。将原字节放在协调者的绝对路径，在每个审阅请求中用 `disk_mapping` 引用；缺失或改变即拒绝。库存仅确定同一组原盘；实际挂载顺序取部署回执，从 `/srv/ax-objects-0` 到 `-3` 分别对应原盘 ID 的 `340/341/342/343` 后缀。两次容量采样均核相同映射。

容量资格材料改为 `c1-mixed-fio-capacity-qualification-v2`，绑定同一已固定 plan、workload、metrics、asset、runtime、node/observer 和四个执行工具。`modes` 可一次明确覆盖 `off/on/formal`；不再要求先完成三套额外六阶段试跑。它需要：

- `history` 引用[独立取回的 On1 150 样本记录](evidence/preparation/b-object-readonly-inspection-01/coordinator/readonly-disks.stdout.json)，SHA 固定为 `8ce88f3bf399796b57fffa0442a66b51b7955936ad178c650ec9fec62e7e037e`；`history_finished` 仍须引用原协调者的完整 On1 finished，并核同 workload、CRC、正常退出、排空和 raw audit。只读取回文件中的 lifecycle 摘要不能替代这些原文件。
- `history_is_lower_bound: true`。程序从首样到最低可用量分别算四盘 bytes/inodes 的已观察下降。盘 0 约 18.655 GB，其余各约 17.814 GB；历史对象节点的旧 boot 仅说明历史来源，不能用于当前身份。首样之前的准备、采样间隙、后续后台增长和归档均未被该下降覆盖，不能当成峰值上界。
- `current_snapshot` 为当前服务运行时新取的原生 snapshot，15 分钟内有效，每个节点 boot 必须等于当前 runtime probe。它只给当前身份与预算核算输入；执行时仍在锁内核 mount/盘序并重新 snapshot，与该当前身份逐项对上。
- `rows` 覆盖 client、metadata、四个对象盘的 bytes/inodes，Redis 内存，以及协调者 bytes/inodes。每行七分项为 `data_peak`、`preparation_margin`、`missed_peak_margin`、`observer_peak`、`archive_peak`、`background_margin`、`aof_peak`。各对象盘 data 至少覆盖历史下界，准备余量必须为正；所有行的漏采和后台余量必须为正；On/formal 的三角色原文、协调者归档以及元数据盘 AOF 重写均须留量。
- 每个分项都有 `basis`：`sources` 为真实原始 JSON 的 SHA 引用，`rationale` 说明字段、单位、覆盖范围与外推理由，`terms` 用 `source` 下标、`pointer`（逐层字段/数组下标）、正整数 `numerator/denominator` 明确换算。程序重算各项向上取整之和，预算不得更小。无适用占用的分项须空 terms、`not_applicable: true` 并保留来源和理由；不能把空材料写成通过。

审核须分别解释首样前完整准备、采样盲区、全程观测时长与原文增长、归档/压缩/解包同时占用、后台对象活动，以及 Redis AOF 增长和重写暂存的依据。字段存在、算术通过和一句 rationale 不能自动证明科学上界；没有可审查的安全预算就继续阻断。资格内 snapshot 先于本次清理边界，锁内 snapshot 晚于该边界，两次身份一致。每轮都重新审定预算和实际余量，不跨轮抵扣尚未释放的空间。

此轮没有生成或签发这份材料。历史样本与 C2 导入估算均只能作为指定的输入，不能冒充完整安全资格。容量门槛失败不派发。派发后失败保留原意图和结果、不重放；只有实际进程及挂载已关闭才放物理锁。

## 重启后的原生命令链

`pilot_context.select/validate` 的同 boot 限制保留。这里使用一个新的 controller，没有旧 runtime binding；原 `calibrate-c1.py`、`check-calibration.py`、`admit-c1.py`、`run-c1.py` 本来就支持新的 `--control`。旧状态库、旧回执和旧校准保留原义。新 plan 与旧 plan 文件 SHA 完全相同，因此旧原生 asset 内的 plan pin 仍有效；新正式轮的 tag 由新 control 路径产生独立 namespace。

先安排无共享测量/归档链路冲突的准备窗口，核盘 ID、serial 与原读写挂载点，恢复原 RustFS/Redis 的相同配置，核当前容器/PID。不能重跑格式化或 `object-paths` 初始化已有目录。Client 重启后若槽 cgroup 确实不存在，可用原 `configure-node.py slot --spec "$CLIENT_SPEC" --out "$NEW_SLOT_OUT"` 重建原 2 CPU/8 GiB/swap 0 槽。三台 B 节点分别用原 `freeze-maintenance.py --spec "$NODE_SPEC" --out "$ROLE_ROOT/preparation/b-c1-after-reboot-maintenance-01"` 记录当前 boot；spec 必须是原部署真实文件。

下列变量从原文件取值，不填猜测路径：

| 变量 | 真实来源 |
|---|---|
| `FROZEN_REQUEST` | 原 `mxb01` reviewed request，包含原四工具和 node/observer SHA |
| `SCRIPTS` | 本次部署且 SHA 已核的协调者脚本目录；四执行工具仍使用 `FROZEN_REQUEST.tools` 的原 path/SHA |
| `CODE_DIRECTORY` | 所选冻结运行代码在六节点上的实际共同 `code-NN` 目录；setup 会逐文件核 runtime bytes，不能假设旧路径已更新 |
| `ASSET` | 原资格 `juicefs/binding.json` 的 `asset`；不创建新卷或改 asset.json |
| `ROUTE_PROOF` | 原资格 `filesystem.json.native_checks` 对应原 transfers 记录的客户端 `source` |
| `LAYOUT_PROOF` / `RUSTFS_SOURCE_PROOF` | 原 transfers 中 `rustfs-layout.json` / `rustfs-source.json` 对应对象节点 `source` |
| `COMPOSE` | 原 `juicefs/metrics.json.observer_settings.rustfs_compose.path` |
| `COLLECTOR_PROOF` | 当前明确选择的原采集器或新匹配采集器真实 `started.json`；Off1 关闭它，On1 新建自己的采集器 |
| `SERVICES` | 原 cross-pool proof 的 services 输入，逐项核当前 Redis/RustFS 的 source/compose/image_proof；若新建容器，必须引用该真实部署回执 |
| `API_INVENTORY` | 当前实际 ECS DescribeInstances/DescribeDisks 整理的 `instances` / `disks` 数组，沿用 [cross-pool 输入格式](PARALLEL-PREFLIGHT.zh.md) |
| `RELEASE` / `MOUNT_SPEC` | 原 JuiceFS client-binary.json 的官方 release pin，以及同一 qualified route 下原 mount spec/command 的真实远端路径 |
| `CONTAINER` | 当前 `docker compose -f "$COMPOSE" ps -q rustfs` 的实际完整 container ID |

原引用经过中间资格目录复制时，沿其 pins 追溯到最初 transfers；不能把本机副本路径当成远端 source。所有节点操作先持 canonical 的本池及实际共享资源锁；setup/cross-pool 会只读探测两池，应在已安排窗口完成。

```bash
OLD=/srv/ax-pilot-control/parallel-control-01
NEW=/srv/ax-pilot-control/b-c1-after-reboot-01
CELL=8e3d340ff23c3ece-r1-juicefs
python3 "$SCRIPTS/setup-controller.py" --inventory "$OLD/provenance/inventory.json" \
  --code-directory "$CODE_DIRECTORY" --out "$NEW"
# 仅保留不可变实验输入；不复制 state/probes/allocation 或旧 boot 回执。
python3 - "$OLD" "$NEW" <<'PY'
import hashlib, pathlib, sys
old, new = map(pathlib.Path, sys.argv[1:])
for name in ('suite/plan.json', 'provenance/inventory.json',
             'provenance/build-manifest.json', 'provenance/input-manifest.json'):
    data = (old/name).read_bytes()
    target = new/name
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open('xb') as stream:
        stream.write(data)
    assert hashlib.sha256(target.read_bytes()).digest() == hashlib.sha256(data).digest()
(new/'runner-locks').symlink_to(old/'runner-locks', target_is_directory=True)
PY
python3 -m bench.suite init --plan "$NEW/suite/plan.json" --db "$NEW/state.sqlite"

python3 "$SCRIPTS/qualify-parallel-hardware.py" --control "$NEW" \
  --inventory "$NEW/provenance/inventory.json" --api-inventory "$API_INVENTORY" \
  --services "$SERVICES" --out "$NEW/cross-pool-01"
python3 "$SCRIPTS/prepare-c1-controller.py" --control "$NEW" --pool pool-b --product juicefs \
  --asset "$ASSET" --maintenance-directory preparation/b-c1-after-reboot-maintenance-01 \
  --route-proof "$ROUTE_PROOF" --rustfs-layout-proof "$LAYOUT_PROOF" \
  --rustfs-source-proof "$RUSTFS_SOURCE_PROOF" --collector-proof "$COLLECTOR_PROOF" \
  --rustfs-compose "$COMPOSE" --out "$NEW/qualification-base"
python3 "$SCRIPTS/attest-client-binary.py" --qualification "$NEW/qualification-base/juicefs" \
  --ssh-config "$NEW/ssh.conf" --client-alias pool-b-client --release "$RELEASE" \
  --mount-spec "$MOUNT_SPEC" --admitter "$ADMITTER" --build-manifest "$NEW/provenance/build-manifest.json"
python3 "$SCRIPTS/derive-c1-qualification.py" --source "$NEW/qualification-base" \
  --plan "$NEW/suite/plan.json" --case fio-randrw --out "$NEW/qualification-mixed"
```

`ADMITTER` 来自 `FROZEN_REQUEST.tools.admit.path`。以上 prepare 仍保留原完整基线的历史创建/CRC记录；必须另外在 B client 做当前 boot 的完整冷读。下列是客户端原工具命令，`NODE_TOOL` 及 SHA 来自 frozen request，每条命令单独记录真实退出码；不循环重试。`CHECK` 和 `MOUNT_RUN` 分别为尚不存在的 `/srv/ax-client/q/c1-rb-base-01`、`/srv/ax-client/m/c1-rb-base-01`：

```bash
python3 "$NODE_TOOL" draft-mount --asset "$ASSET" --cell "$CELL" --purpose experiment --out "$CHECK/mount"
python3 "$NODE_TOOL" start --asset "$ASSET" --join-slot --spec "$CHECK/mount/mount.reviewed.json" --out "$MOUNT_RUN"
python3 "$NODE_TOOL" fio-base-check --asset "$ASSET" --join-slot --block-bytes 4096 --out "$CHECK/verify"
python3 "$NODE_TOOL" stop --asset "$ASSET" --mount-run "$MOUNT_RUN" --out "$CHECK/mount-exit.json"
```

核 SHA 后取回六份实际 JSON 到新 controller：`mount_spec`=`$CHECK/mount/mount.reviewed.json`、`mount_ready`=`$MOUNT_RUN/ready.json`、`mount_exit`=`$CHECK/mount-exit.json`、`fio_result`=`$CHECK/verify/result.json`、`fio_raw`=`$CHECK/verify/fio.json`、`fio_exit`=`$CHECK/verify/exit.json`，组成请求中的 `base_recheck` 字典。入口核当前 client boot、原 asset 的 mount/anchor/plan、完整 64 MiB/4 KiB CRC、原生 fio options、实际 filename 和正常卸载；不会把这次只读资格算作性能样本。

回到协调者，用新 collector 的实际 name/out（必须与 `COLLECTOR_PROOF` 一致），以及原 request 的 node/observer/four-tools 原值调用 `draft-one-c1.py`。可从 frozen request 读出参数后通过 Python argv 传入，避免手工抄错：

```bash
python3 - "$FROZEN_REQUEST" "$SCRIPTS/draft-one-c1.py" "$NEW" "$COLLECTOR_NAME" "$COLLECTOR_OUT" <<'PY'
import json, pathlib, subprocess, sys
source, draft, directory, collector, collector_out = sys.argv[1:]
s = json.loads(pathlib.Path(source).read_bytes())
new = pathlib.Path(directory)
argv = [sys.executable, draft, '--control', str(new), '--cell', s['cell_id'],
        '--qualification', str(new/'qualification-mixed'), '--collector-name', collector,
        '--collector-out', collector_out, '--attempt-suffix', 'rb01',
        '--run-directory', str(new/'one-cell-runs'/(s['cell_id']+'-rb01')),
        '--latest-check', s['latest_check']['path'],
        '--cross-pool-qualification', str(new/'cross-pool-01/result.json'),
        '--node-tool', s['node_tool']['path'], '--node-tool-sha256', s['node_tool']['sha256'],
        '--observer-tool-name', s['observer_tool']['name'], '--observer-tool-sha256', s['observer_tool']['sha256'],
        '--out', str(new/'drafts/mixed-rb01.json')]
for option, key in [('calibrator','calibrate'),('checker','check'),('admitter','admit'),('runner','run')]:
    argv += ['--'+option, s['tools'][key]['path']]
subprocess.run(argv, check=True)
PY
python3 "$SCRIPTS/capacity-check.py" snapshot --control "$NEW" --pool pool-b \
  --objects-container "$CONTAINER" --out "$NEW/capacity-current-01.json"
```

这里保留原冻结批次的 latest-check 与相同 build，不宣称重新检查了最新分支。入口逐字节核 plan/provenance，逐字段核新旧 workload/metrics 语义及原 asset SHA，检查新库只有 `initialized` 事件且所有 cells pending、无旧资格/资产/阶段/租约，核新硬件维护 boot、当前 cross-pool 服务 container/PID 和容量 snapshot。之后才能用本页上半部分的实际容量材料、清理边界与 step 1 请求执行 `--validate-only`，再单独派发 Off1。此链不会恢复旧失败轮，也不会自动执行后续轮。
