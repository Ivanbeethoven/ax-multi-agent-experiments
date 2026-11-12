# C3 Packed：同一次启动内重采容量

**目前只验证了取样接口，未上云。** WSL 测试读真实 `/proc`、cgroup 和磁盘余量，以模拟 Redis INFO 走通 `capture-c3-packed-capacity-before.py → packed-c3-capacity.py::before_sample`，并验证旧 boot、PID 不符及文件改动会被拒绝。下面是一次完整重采的操作草稿，先审阅资源预算。

冻结容量工具把 before 绑定到 **本次 producer 开始前、三台同 boot、Redis/RustFS 同 PID**，并把预算绑定到 **本次完整 parent 与资格 child 的原始回执**。旧基线只读冷验、空目录试写或小批量 seed 不能满足这条路径。完整重采要另外发布一次 10 万文件基线并完成一次功能资格。新基线和 child 会改变指标与负载合同的 SHA，旧三对采样及披露也不能直接拿来准入。若目的是保留旧基线和旧三对结果，停在只读取证；须另行审阅新的预算口径。

1. **先判空间。** A 池无其他作业或租约，使用原物理锁，清理完成后获取[重启证据](C3-PACKED-REBOOT-EVIDENCE.zh.md)及本次 `capacity-check.py snapshot --control "$NEW" --pool pool-a --objects-container "$CONTAINER" --out "$FRESH"`（不传旧 before）。逐盘以设备 serial 对齐历史增量，只作花钱前估算。重采本身一份，验收还需留七份：开跑前每盘可用字节及 inode 至少应覆盖 **20% 当前总量 + 八倍历史窗口增量 + 额外已知临时峰值**；Redis 当前 `used_memory + 8 × 历史增量 ≤ 0.8 × maxmemory`，另核 AOF 和协调者空间。旧窗口里最紧的对象盘 0 每份增量为 1,147,904,000 B；若新总量仍为 60,070,604,800 B，仅前两项就需 21,197,352,960 B。这个数只是估算，余量不足就不启动 producer；不要在 before/after 之间做 GC、清旧数据或重启服务。

2. **采 before。** 从当前已锁定的三台主机各取一次。以下命令在相应节点执行；`TOOLS` 为当前已核 SHA 的脚本目录，`ROLE` 为 `client`、`metadata` 或 `objects`，`HOST` 来自新控制的 fleet。每个角色用本机独立且尚未使用的 `NODE_WINDOW`，`METRIC_SHA` 来自原冻结的指标合同，`COLLECTOR` 为本次已核实在运行的采集容器。client 的 `BASE_MOUNT` 是即将用于 producer 的新监督目录；objects 必须使用当前实际 Compose 路径和 SHA。

```bash
set -euo pipefail
args=()
case "$ROLE" in
  client) args=(--mount-directory "$BASE_MOUNT") ;;
  metadata) ;;
  objects) args=(--collector-name "$COLLECTOR" --rustfs-compose "$COMPOSE"
                --rustfs-compose-sha256 "$COMPOSE_SHA") ;;
  *) exit 2 ;;
esac
python3 "$TOOLS/make-observer.py" --role "$ROLE" --product brewfs --pool pool-a \
  --hostname "$HOST" --entity --metric-contract-sha256 "$METRIC_SHA" \
  --out "$NODE_WINDOW/spec" "${args[@]}"
SPEC="$NODE_WINDOW/spec/observer.json"
SPEC_SHA=$(sha256sum "$SPEC" | cut -d' ' -f1)
python3 "$TOOLS/capture-c3-packed-capacity-before.py" --role "$ROLE" --spec "$SPEC" \
  --sha256 "$SPEC_SHA" --out "$NODE_WINDOW/before"
```

   在协调者分别新建 `$WINDOW/before/{client,metadata,objects}/`，通过新控制的固定 SSH 配置拷回每台的整个 `$NODE_WINDOW/before/sample-000000/`，以及 `spec/` 和工具输出的 sample SHA；校对 SHA 并保留 stdout/stderr。**不要只拷 `sample.json`**；冻结读取器会校验同目录下每个原始文件，Redis 还直接读取其中的 `redis.txt`。确认三个 `utc_ns` 都早于本次 producer 的 `started.json.at`，以相同时间源校时。

3. **跑一次完整写入窗口。** 只在获准重采后，在 A client 上运行。沿用原冻结 `PLAN_CLIENT`、`INVENTORY_CLIENT` 的字节和原 100k 的 `SOURCE/SOURCE_PROOF`；从旧实际资格的命令/上下文取已核 SHA 的原工具，不凭路径猜版本。选该批次未用的 Redis DB 号 `BASE_DB`（20–255；脚本还会查空库），新建 `BASE`、`PARENT_OUT`、`BASE_MOUNT`、`PRIVATE`、`CHILD_OUT`、`CHILD_MOUNT` 对应的独立路径，调用前这些路径本身均不存在。

```bash
set -euo pipefail
test -z "$(cat /sys/fs/cgroup/ax-slot-1/cgroup.procs)"
python3 "$TOOLS/prepare-packed.py" --asset "$BASE" --plan "$PLAN_CLIENT" \
  --inventory "$INVENTORY_CLIENT" --source "$SOURCE" --source-proof "$SOURCE_PROOF" \
  --node-tool "$NODE_TOOL" --node-tool-sha256 "$NODE_SHA" --database "$BASE_DB" \
  --mount-state "$BASE_MOUNT" --out "$PARENT_OUT"
python3 "$TOOLS/packed-c3-node.py" qualify --base-asset "$BASE" --asset "$PRIVATE" \
  --plan "$PLAN_CLIENT" --cell 16f4ac389d2a2da6-r1-packed --manifest "$BUILD_MANIFEST" \
  --probe "$PROBE" --probe-sha256 "$PROBE_SHA" --smallfiles "$SMALLFILES" --smallfiles-sha256 "$SMALLFILES_SHA" \
  --node-tool "$NODE_TOOL" --node-tool-sha256 "$NODE_SHA" --mount-state "$CHILD_MOUNT" \
  --reader "$READER" --reader-sha256 "$READER_SHA" --verifier "$VERIFIER" --verifier-sha256 "$VERIFIER_SHA" \
  --oracle "$ORACLE" --oracle-sha256 "$ORACLE_SHA" --source "$SOURCE" --source-proof "$SOURCE_PROOF" \
  --private-object-env /srv/ax-client/private/rustfs.env --out "$CHILD_OUT"
```

   任一命令失败即留存现场并停下；不要自动重试或删回执。正常结果应包括 parent 的 100,000 文件完整核对，child 的八阶段、两次零债务、正常卸载，以及 parent/child 各 409,600,000 B 冷读。执行期间持续保留原始输出，凭据不纳入证据目录。

4. **在同一 boot 立即采 after。** 确认两次正常停及槽位空闲。容量工具会自行取得 canonical 锁；若前面用独立守护程序持锁，在安排好无其他任务插队的情况下释放后直接调用。按 [原请求字段](PACKED-C3-CLOUD.zh.md) 生成草稿，`control=$NEW`，新控制直接使用新 probes（省略 `runtime_control`）；`hardware`、`node_specs` 绑定实际本次硬件和原盘身份；`before_samples` 指向刚取回的三份 sample，`base_started/base_preparation/qualification` 指向 **client** 的 `$PARENT_OUT/started.json`、`$PARENT_OUT/result.json`、`$CHILD_OUT/result.json`。对象容器用实际完整 ID，`extra_full_copies=7`、`reserve_fraction=0.2`。人工复核后，执行：

```bash
python3 "$TOOLS/packed-c3-capacity.py" --request "$REVIEWED_CAPACITY_REQUEST" --out "$WINDOW/capacity"
```

   保留 `raw/*/{intent.json,stdout,stderr,exit.json}`、`before.json`、`after.json`、`result.json`。退出码 0 且所有行通过只代表容量；退出码 2 代表预算不通过。掉盘、PID 或 boot 变化、可用量上升导致拒绝时，不能改原读数补过。正式实验还需要同一新 parent/child 的功能收证、匹配合同的校准与新控制的跨池资格。
