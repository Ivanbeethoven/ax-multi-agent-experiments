# A 池 C2：完整方案的全量 trace 资格检查

[执行草案](request.draft.json)尚未批准，也未联云执行。目标是验证**原版 BrewFS + 从 pytest 初态生成的 Packed wire005 lower + 私有可写 upper**，能否通过完整回放和同卷冷读回。通过也只说明该路径正确，后续还需正式性能对照。旧 native-v2、普通空 lower 的本机结果和 C3 的 100k lower 都不能代用。

## 启动条件

1. **优先 A，等 C3 真正结束。** A 客户端的 `/srv/ax-client/trace-exact-02/cases/pytest-full-02` 已有逐文件 [SHA 核对](../parallel-c2-native-01/exact-source-probe.json)，共 67,577 份；B 的[镜像也已核对](../b-c2-input-copy-01/verify-publish-stdout)，但 B 还要完成 C1、对象盘余量较紧，本轮不切 B。无需从本机上传，也不在两池之间复制。下载归档及 SHA 在[清单](../../../../../../scenarios/k8s-containers/archives.json)，只有原输入缺失才另开恢复步骤。Packed producer 仅使用 `case/initial`：614 个文件、100 个目录、35,526,497 字节；不要把整个 trace、blob 或最终树打入 lower。
2. **开机前验版本并留锁。** 先在本机用已存 build manifest 和 [verify-latest.py](../../../scripts/verify-latest.py) 查两条分支；若任何分支推进或查不到，先在独立构建位置重新准备并验 SHA。草案仅记录已分发的 `ca07159f` / `f4ed2025`，不宣称它们届时仍是最新。协调者等待 C3 的退出、回执和空槽位：复用 [pilot_pools.py](../../../../../../bench/runtime/pilot_pools.py) 的 `resource_guard`、`active_conflicts`。使用 `/srv/ax-pilot-control/parallel-control-01/suite/plan.json` 中 A 池资源集，从所有现存 `/srv/ax-pilot-control/*/state.sqlite` 解析并去重真实 `runner-locks`，先拿锁、再重扫控制库和冲突。只读查历史 lease，不新领旧 cell，不清理别的 lease。从开机前持锁到回执保存和 A 三台确认为 `Stopped/StopCharging`；B 独立资源不受影响。没有全程持锁的协调者监督器，就不启动。
3. **新开机只恢复原 A 服务。** 仅启动草案所列 client、metadata、objects，在原盘上复用 [C3 恢复路径](../../../scripts/run-c3-workspace-base.py) 的 `/srv/ax-receipts/a-pool-up/a-pool-up.py`，但另用 C2 的新标签和输出；不要调用 C3 启动器。核当前 hostname、设备 serial、boot、三台服务 PID 和实际 RustFS 镜像/revision、四项动态块开关；Redis 记录实际版本、PID、`CONFIG GET appendonly appendfsync maxmemory maxmemory-policy` 和 AOF/内存，沿用原 Redis 服务。确认原 A 对象端仍为单节点四块盘，只用于本次资格。另选并实查空 Redis DB（242 仅候选），禁止 FLUSH。核四个对象盘和 client/metadata/协调者数据盘的字节及 inode；至少保留 20% 并另留本轮预计新增量、AOF、临时和取证空间。任何盘或 Redis 按当前取样过不了容量审查就停。回放进程进空的 `ax-slot-1`（2 CPU、8 GiB、swap 0），监控进程留在槽位外。
4. **只读重验再写。** 在客户端 `REPO` 为实际验过 SHA 的代码根，`OUT` 为本轮私有新目录。使用现有 `trace_checks.py` 核每份输入，另核完整初态、硬链接和草案二进制 SHA。所有输入从原盘读，不能放到测量挂载内。

```bash
CASE=/srv/ax-client/trace-exact-02/cases/pytest-full-02
MAN=/srv/ax-client/trace-exact-02/manifest-replay-inputs-03.json
MAN_SHA=71a0661b82a433e9d14da29287a2bc1860460015d4887031f90a03ff5451a021
python3 -B "$REPO/bench/trace_checks.py" inputs --case "$CASE" \
  --manifest "$MAN" --sha256 "$MAN_SHA" --output "$OUT/input-inventory.json"
python3 -B "$REPO/bench/trace_checks.py" tree --root "$CASE/initial" \
  --expected "$CASE/initial-tree.json" --output "$OUT/initial-tree.json"
```

## 单轮执行

- **预算：从首个 A 开机请求算起，最多 45 分钟。** 前 40 分钟含开机、检查、生成 lower、严格回放和冷读回；最后至少 5 分钟专用于正常卸载、回执和停机。协调者固定同一截止时刻，每一步只拿剩余时间。若进入最后 5 分钟，向本轮前台进程组发 TERM、短等待后仅清本轮剩余子进程，取部分进度和失败回执，正常停本轮挂载并停机。若停机未确认，保留锁和告警，查实际云状态；不自称停机。窗口不是完工预测。
- **新建 Packed lower。** 从 A 私有 env 为进程设置 S3 凭据，禁止输出凭据及私有 env；用 boto3 向原 A RustFS 创建全新短桶名。参照 [prepare-packed.py](../../../scripts/prepare-packed.py) 的建桶和 producer 命令，使用草案的 producer、`--source-directory "$CASE/initial"`、`--source-hardlink-policy reject-external`、raw/raw、inline off、chunk/block 4 MiB、size-only、random-small-file、262144 B frame cap、hint auto。TMPDIR 和输出均在 A 客户端数据盘。保存实际 manifest key、对象 SHA 和 wire005 证明；不能用 S3 ETag 代替 SHA。再以新 namespace/空 DB 调用 `brewfs workspace --meta-backend redis --meta-url "$REDIS_URL" --workspace-namespace "$NS" init-packed-volume --manifest-key "$KEY" --data-config "$DATA_CONFIG"`，保存实际 workspace UUID、lower 绑定和独立 upper。不要调用 `init-native`，不使用旧资产。
- **挂载、回放。** 按[当前设置](../../../../current/CONFIGURATION.zh.md)生成 `workspace-v1` 配置，含实际 lower 绑定、chunk/block 4 MiB、keep_cache=false、私有缓存、upload_before_commit。writer 必须是实际 FUSE 挂载；回放根直接用该挂载根，不再将初态复制到 upper。先让[校验器](../../../scripts/qualify-trace.py)在 `--prepared-root` 下比较 714 项和硬链接；不一致时保留差异并停止，不屏蔽缺项，不另找替代卷。随后原始回放核每次读的字节。必须实际完成 1,747,109 次回调、281,774,624 字节读、194,706,661 字节写，并通过最终内容、权限、硬链接和复制后 SQLite 快检；终端探针仅沿用既定兼容规则。由限时监督器调用：

```bash
QUAL="$REPO/experiments/unified-suite/pilot-aliyun/scripts/qualify-trace.py"
REPLAY=/srv/ax-client/assets/bin/full-fuse-replay-parent-fd-01
timeout --signal=TERM --kill-after=10s "$remaining" \
  python3 -B "$QUAL" --case "$CASE" --binary "$REPLAY" --root "$WRITER" \
  --out "$OUT/strict" --prepared-root --portable-terminal-probe --cache-parent-fds
```

- **排空、冷读、归档、停机。** 保存实际 FUSE 状态、写回队列及卸载回执；缺指标不得填零。writer 正常卸载后启动另一 PID、另一个 FUSE 挂载点和新的私有缓存目录，原 Redis DB/namespace、workspace UUID、lower 和 upper 保持不变。以同一严格回执 `"$OUT/strict/replay.json"` 调同一校验器：`--root "$COLD" --out "$OUT/cold" --verify-only --replay-result ... --portable-terminal-probe --cache-parent-fds`。核最终树、链接与 SQLite，确认两次挂载均退出、无残留进程。保存允许清单内的版本、容量、命令、stdout/stderr、strict/cold、排空与卸载证据，逐 SHA 取回；不得收私有 env、trace blob 或 SQLite 原文。保留新资产身份，实际服务清理只移除本轮私有临时目录，失败证据和其他资产不动。确认 A 三台都已 `StopCharging` 后释放锁，不自动重试。

目前只确认了历史输入、构建和已有恢复路径。当前 A 开机后的空间、空 DB、实跑 guard 与完整 Packed 初态/可写回放尚未验证。普通 `workspace-v1` 的本机限时[记录](../c2-local-stock-full-pytest-01/README.zh.md)只证明部分进度，不能推出云端耗时；当前独立草案不进入旧 `prepare-c2.py` 或正式测量状态库。


## 操作入口

本目录有 [PowerShell 开停机入口](run-c2-current-a.ps1)、[协调者锁持有器](run-c2-current-a.py)和 [A 客户端 worker](qualify-c2-current-a.py)。锁定前不启动 ECS；以锁获取时间计 40 分钟工作截止、45 分钟费用窗口，比从开机请求计略早。协调者监控退出并收回回执，PowerShell 独立负责停机。无法证明三台已经 StopCharging 时，协调者继续持锁。

在 C3 关闭并取回证据后，操作人准备：

- `latest.json`：执行 `verify-latest.py`，本机现有 build manifest 为 WSL 的 `/home/hxy/ax-unified-pilot-prep-next-01/receipts/build-manifest.json`。记录须在 15 分钟内，两个分支均与本轮产物一致。
- `instances-before.json`：从阿里云 `ecs DescribeInstances` 原样保存草案三台（`cn-hangzhou`）；三个实例均须处于 `Stopped/StopCharging`，5 分钟内有效。
- 操作人另存 `operator.reviewed.json`，schema 为 `c2-current-a-operator-review-v1`，包含 `reviewed`、`reviewer`、`scope`（精确为 `A-only stock workspace-v1 Packed C2 qualification, 45 minute cap`）、`tag`（`c2-current-a-YYYYMMDDtHHMMSSz`）、整数 `database`，以及四个 SHA256：`controller_sha256`、`worker_sha256`、`latest_sha256`、`before_sha256`。执行器从不生成审阅记录。

在 Windows PowerShell 中，从本目录运行：

```powershell
./run-c2-current-a.ps1 -SshConfig $aSshConfig -ReceiptRoot $newReceiptParent -Latest $latestReceipt -InstancesBefore $beforeReceipt -OperatorReview $reviewFile
```

收到 `coordinator/closed.json` 后才判断是否完成。若本地控制终端意外中止，直接核实并按原计划停这三台；保留原回执。不要先停协调者的锁进程。离线检查：`python3 -B check-offline.py [本机初态清单路径]`。这一步不会连云。
