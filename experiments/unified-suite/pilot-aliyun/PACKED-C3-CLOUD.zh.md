# Packed C3下一批入口

> 历史协议，仅用于原冻结计划；新实验见[当前方案](../current/README.zh.md)。

节点动作：`scripts/packed-c3-node.py`。不编译新工具，不修改当前冻结批次。现有`prepare-packed.py`先发布完整100k输入并检查base，之后每轮只新建私有entity upper，共用同一packed manifest。

先跑云上资格检查，结果不能算正式性能样本：

```bash
python3 "$TOOLS/packed-c3-node.py" qualify \
  --base-asset "$BASE" --asset "$PRIVATE" --plan "$PLAN" --cell "$CELL" \
  --manifest /srv/ax-client/assets/provenance/build-manifest.json \
  --probe /srv/ax-client/assets/bin/entity_metadata_probe --probe-sha256 "$PROBE_SHA" \
  --smallfiles /srv/ax-client/assets/bin/smallfiles --smallfiles-sha256 "$SMALLFILES_SHA" \
  --node-tool "$NODE_TOOL" --node-tool-sha256 "$NODE_SHA" --mount-state "$MOUNT_STATE" \
  --reader "$READER" --reader-sha256 "$READER_SHA" \
  --verifier /srv/ax-client/preparation/pool-a-next-01/smallfiles-verify --verifier-sha256 "$VERIFIER_SHA" \
  --oracle "$ORACLE" --oracle-sha256 "$ORACLE_SHA" \
  --source /srv/ax-client/inputs/pilot-100k-01/smallfiles-source \
  --source-proof /srv/ax-client/inputs/pilot-100k-01/receipts/c3-verify/result.json \
  --private-object-env /srv/ax-client/private/rustfs.env --out "$OUT"
```

实际参数：`BASE`为本次`prepare-packed.py`生成的asset目录；`PRIVATE`、`MOUNT_STATE`、`OUT`均必须新建；`PLAN/CELL`必须属于同一冻结计划的packed C3；probe与smallfiles的SHA必须匹配该计划的build manifest。reader必须有与本次BrewFS构建一致的原链接记录；reader、完整verifier、oracle、node tool均传实际路径和SHA。私密env保持0600且在证据目录之外。启动前该池必须空闲，槽位为2 CPU、8 GiB、swap 0。

`qualify`依次执行fresh、原监督挂载、原smallfiles八阶段、原CSV全量检查、两次native status、正常停、原namespace只读冷验及父目录隔离检查。失败不重跑；保留原记录。

| 正式stage | 接线 |
|---|---|
| create-w01 | `packed-c3-node.py fresh`：保留manifest/bucket/端点，新volume、namespace、catalog，不复制base |
| mount-w01 | 原`pilot-node.py draft-mount/start/worker`；worker传`--root-at-mount --workspace 1`，只挂一次 |
| measure-w01 | 原`workloads.worker`、`cohort.run`、`suite_validate.work`；固定八阶段和全部8个worker CSV |
| drain-w01 | 新`debt`：已构建probe单进程，两次同namespace status相隔≥1秒；保存stdin/stdout、PID/boot/start_ticks、manifest/SHA；扁平债务值取原`debt.bytes/objects` |
| unmount-w01 | 原监督器正常stop；保留原PID退出、mount absent、anchor |
| verify-w01 | 新`cold-verify`：传原stop文件及SHA、原expected-touched.tsv；只读reader验证原child和原parent；不重挂entity |

三角色采集继续用原observer；client builder必须传`--entity`。五秒采样、三对on/off、5%门禁和四个w01阶段mark保持原协议。entity债务来自native probe，不能填flat writeback gauge。producer发布时间、private create、mount、八阶段时间、drain、正常停、冷验分别记录。

`stage-spec --request ACTION.json --out STEP.json`可为fresh/debt/cold-verify生成原`experiment-step-v1`草稿。ACTION为`{"schema":"packed-c3-node-action-v1","action":"debt","arguments":{"asset":"实际路径","plan":"实际路径","cell":"原cell ID","manifest":"实际路径","probe":"实际路径","probe_sha256":"实际SHA","out":"新输出目录"}}`。再用原suite review/run-stage及同一`state.sqlite`和lease执行，不另建状态库。

协调者入口：`scripts/packed-c3-controller.py`。只使用原`parallel-control-01/state.sqlite`；新文件不改变已冻结flat执行器。云上资格通过以后依次执行：

```bash
python3 packed-c3-controller.py collect --context context.json \
  --cloud-route "$QUALIFICATION/result.json" --base-preparation "$PRODUCER/result.json" \
  --shared-qualification "$SHARED_QUALIFICATION"
python3 packed-c3-capacity.py --request capacity-request.json --out "$CAPACITY"
python3 packed-c3-controller.py pairs --request pairs-request.json --out "$PAIRS"
python3 packed-c3-controller.py admit --context context.json \
  --overhead "$PAIRS/overhead/result.json" --capacity-budget "$CAPACITY/result.json" \
  --latest "$LATEST_BRANCH_RECEIPT" --cross-pool-qualification "$CROSS_POOL_PROOF"
python3 packed-c3-controller.py run --context "$CELL_OUT/context.json"
```

`collect`只收原回执和实际二进制SHA，输出`qualification/packed/{binding,input,filesystem,metrics,workload}.json`、原base/private asset和硬件证明。`shared-qualification`中需有当前runtime的`hardware.json`和`native/metrics.json`。所有新输出目录必须不存在；失败保留原现场，不重跑。

`context.json`必要字段如下；`pin`表示真实绝对路径和SHA组成的`{"path":"...","sha256":"..."}`：

```text
control, runtime_control(pin), cell_id, qualification, out, attempt, owner
purpose: experiment | observer-calibration-on | observer-calibration-off
base_runner(pin): 原冻结run-c1.py，复用SSH/marks/close_cell
node_tool(pin), entity_tool(pin): client实际pilot-node.py、packed-c3-node.py
observer_tools: {client: pin, metadata: pin, objects: pin}
packed_tools: {binary: pin, probe: pin, reader: pin, verifier: pin, oracle: pin}
private_object_env: 原0600文件，保持在证据目录外
collector_name, collector_out: 当前真实collector
new_collector_name: 需要从off切回on时使用的新名字
```

`attempt`必须每轮不同。保留runtime12 fleet/peer时，显式把client observer/node/entity工具指向实际新代码目录；其余角色可继续用原冻结工具。不得拿旧fleet.repo猜新helper路径。

`pairs-request.json`为`{"schema":"packed-c3-pairs-v1","reviewed":true,"contexts":[六个context的pin],"checker":校验脚本的pin}`。六轮固定`off,on,on,off,off,on`，各有不同out/attempt，其余绑定相同。切回off时，`collector_name/out`指向前一轮实际启用的collector。每轮仍执行完整八阶段，保留8份CSV、两次native零债务和正常退出；开启采集的三轮保留三个角色的原始数据。校准不领取正式格，不写正式样本。

已有通过完整冷读的100k资格结果时，可以只在校准中缩短计时后的校验。六个context使用相同的`calibration_verification: live-touched-v1`，绑定`calibration_node`（client上`packed-c3-calibration-node.py`的pin）和`full_cold_qualification`（协调者`qualification/packed/filesystem.json`的pin）；`checker`使用`check-packed-c3-live-calibration.py`。每轮在`measure-end`后用原`smallfiles verify-touched`逐一读回256个修改文件（共1 MiB），核对大小、权限、属主和链接数。退出后再用native status核对子空间已静止、父空间的标识及lower digest未变。采集开销门禁对makespan和任务P99分别计算三对`on/off−1`的中位数，二者绝对值都须不超过5%；回执显式记录`full_namespace_cold: false`。正式context不带这三个校准字段，保留自己的完整子空间和父空间冷读。

`draft-packed-c3-live.py --source ORIGINAL_PAIRS.json --formal-context context.json --checker "$TOOLS/check-packed-c3-live-calibration.py" --calibration-node "$CLIENT_TOOL" --calibration-node-sha256 "$CLIENT_TOOL_SHA" --out "$DRAFT"` 可生成上述六轮绑定。它检查已有完整资格与client脚本，输出`reviewed:false`的草稿，不启动任务。复核后另存批准的request再交给`pairs`；先确认六个输出目录均未使用。

`capacity-request.json`字段：

```text
schema: packed-c3-capacity-request-v1; reviewed: true
control, runtime_control(pin), cell_id, hardware(pin)
before_samples: {client: 原sample.json的pin, metadata: pin, objects: pin}
node_specs: {client: 原云盘spec的pin, metadata: pin, objects: pin}
base_started, base_preparation, qualification: client三个原JSON绝对路径
objects_container: 当前原RustFS容器名或ID
extra_full_copies: 7; reserve_fraction: 0.2
```

容量脚本只在该池空闲时运行。核验原before全部raw文件、采样早于producer、相同boot和原服务PID；after读取实际statvfs、lsblk serial、Redis INFO和原正常停止证明。RustFS容器可经bind别名挂盘，四个源路径必须与各盘`rustfs-data`目录的设备号及inode逐一相同。before只有可用量，不补总量。预算包含观察窗口内归档/部署开销、完整parent和资格upper，并保守预留七个同量副本；不声称精确producer增量或克隆节省。当前A的原before来自C2最后三角色sample，原spec在`hardware-inputs-01/data/pool-a-*-spec.json`。

正式`run`由原state逐stage写intent、调用原cohort、原archive_check/suite_validate、封存后由原finish释放资源。只检查真实单mount；reader/probe/observer/worker原进程、两份原生零债务和三角色raw均需正常收尾。workload-specific entity系列在原measure-end sample检查，缺项拒绝。小fixture通过只代表接口检查；正式完成须以云上原样本和报告验收为准。

正式批次退出后，`audit-packed-c3-close.py --context "$CELL_OUT/context.json" --pipeline "$PIPELINE_OUT/result.json" --unit "$UNIT" --out "$HANDOFF"` 检查当前A池是否可以交给下一项：原state已关闭、无lease、原进程和挂载已消失、槽位空闲，并记录实际collector的名称和目录。它只检查交接边界，不另启动任务或停止现有服务；原档验收另用`suite_report.sample`。
