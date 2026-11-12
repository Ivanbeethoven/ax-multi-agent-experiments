# A 池 C2：完整方案的全量 trace 资格检查

[执行草案](request.draft.json)尚未批准，也未联云执行。目标是验证**原版 BrewFS + 从 pytest 初态生成的 Packed wire005 lower + 私有可写 upper**，能否通过完整回放和同卷冷读回。通过也只说明该路径正确，后续还需正式性能对照。旧 native-v2、普通空 lower 的本机结果和 C3 的 100k lower 都不能代用。

## 启动条件

1. **优先 A，等 C3 真正结束。** A 客户端的 `/srv/ax-client/trace-exact-02/cases/pytest-full-02` 已有逐文件 [SHA 核对](../parallel-c2-native-01/exact-source-probe.json)，共 67,577 份；B 的[镜像也已核对](../b-c2-input-copy-01/verify-publish-stdout)，但 B 还要完成 C1、对象盘余量较紧，本轮不切 B。无需从本机上传，也不在两池之间复制。下载归档及 SHA 在[清单](../../../../../../scenarios/k8s-containers/archives.json)，只有原输入缺失才另开恢复步骤。Packed producer 仅使用 `case/initial`：614 个文件、100 个目录、35,526,497 字节；不要把整个 trace、blob 或最终树打入 lower。
2. **开机前验版本并留锁。** 先在本机用已存 build manifest 和 [verify-latest.py](../../../scripts/verify-latest.py) 查两条分支；若任何分支推进或查不到，先在独立构建位置重新准备并验 SHA。草案仅记录已分发的 `ca07159f` / `f4ed2025`，不宣称它们届时仍是最新。协调者等待 C3 的退出、回执和空槽位：复用 [pilot_pools.py](../../../../../../bench/runtime/pilot_pools.py) 的 `resource_guard`、`active_conflicts`。使用 `/srv/ax-pilot-control/parallel-control-01/suite/plan.json` 中 A 池资源集，从所有现存 `/srv/ax-pilot-control/*/state.sqlite` 解析并去重真实 `runner-locks`，先拿锁、再重扫控制库和冲突。只读查历史 lease，不新领旧 cell，不清理别的 lease。从开机前持锁到回执保存和 A 三台确认为 `Stopped/StopCharging`；B 独立资源不受影响。没有全程持锁的协调者监督器，就不启动。
3. **新开机只恢复原 A 服务。** 仅启动草案所列 client、metadata、objects。C2 从仓库分发并校验[恢复脚本](../../../scripts/a-pool-up.py)，只对 objects 传 `--without-legacy-observer-scratch`。仍挂回四块原 RustFS 数据盘和原数据目录，跳过旧 On1 观测盘；client 和 metadata 按原路径恢复。脚本写到各节点本轮私有目录，旧脚本和历史 On1 恢复默认行为不变。核当前 hostname、设备 serial、boot、三台服务 PID 和实际 RustFS 镜像/revision、四项动态块开关；Redis 记录实际版本、PID、`CONFIG GET appendonly appendfsync maxmemory maxmemory-policy` 和 AOF/内存，沿用原 Redis 服务。确认原 A 对象端仍为单节点四块盘，只用于本次资格。另选并实查空 Redis DB（242 仅候选），禁止 FLUSH。核四个对象盘和 client/metadata/协调者数据盘的字节及 inode；至少保留 20% 并另留本轮预计新增量、AOF、临时和取证空间。任何盘或 Redis 按当前取样过不了容量审查就停。回放进程进空的 `ax-slot-1`（2 CPU、8 GiB、swap 0），监控进程留在槽位外。
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


## 准备审阅与执行

先审[冻结输入和容量要求](request.draft.json)、[协调者锁与准入](run-c2-current-a.py)和[客户端执行器](qualify-c2-current-a.py)。等待 A 上一轮退出、回执留存、槽位清空，并与当前跳板机负责人交接。历史容量、Redis DB 状态、进程和挂载记录都不能作为本轮证据。两文件热读对照使用过另一个候选二进制；本次完整可写 trace 固定使用草案里的 stock 二进制。

从本目录在 Windows PowerShell 运行以下**准备命令**。两个根目录应已存在且互不相同。`242` 只是待检查的 DB 候选。

```powershell
$packet = ./prepare-review.ps1 -PacketRoot $localReviewRoot -Database 242 | ConvertFrom-Json
```

[准备脚本](prepare-review.ps1)用[归档构建清单](../brewfs-ca07159-build/build-manifest.json)查最新分支，并向 ECS 只读查询三个精确 A 实例。只写本地目录，生成 `latest.json`、`instances-before.json` 和 `operator.review.template.json`；模板明确为 `reviewed=false`。这条命令不会 SSH、启动实例或生成批准。若分支推进、实例不是 `Stopped/StopCharging`，或证据过期，停止准备并保留原因。可用 `-LatestReceipt` 与 `-InstancesBeforeReceipt` 做离线复查，两份输入仍须在时效内。

| 证据 | 何时重取，谁校验 |
| --- | --- |
| BrewFS/RustFS 分支和产物 | 开跑前重取，最多 **900 秒**；`latest_ok` 检查分支、提交和查询成功。 |
| A 三台停机状态 | 签审前重取，最多 **300 秒**；`exact_stopped` 检查实例 ID、可用区、状态，上传保留原时间戳。拿到锁后，开机前再查一次，锁回执最多 **45 秒**。 |
| 跳板机控制盘 | 在此次开机后验证 UUID `ffe25afe-9d22-4e20-8a5b-fc19baa6687d`、设备、路径和原控制库。PowerShell 在任何远程写入前用[原盘检查](../../../scripts/restore-existing-jump-control.py)拒绝错误挂载。 |
| A 数据盘、服务、槽位和空 DB | 拿锁并启动 A 后按同一开机状态重取，紧接着才运行 worker。容量门槛为至少 20% + 2 GiB 可用、20% + 10,000 个可用 inode；还要检查 Redis AOF/内存、镜像和空 DB。历史回执不进入这条准入链。 |

当前[跳板机窗口脚本](../../../scripts/run-existing-jump-window.ps1)只支持 C1/C3。若跳板机已停，交接后由当前窗口负责人单独启动既有 `ax-c1-jump`、核实身份，运行[原盘检查](../../../scripts/restore-existing-jump-control.py)的 `--mount` 选项；该选项只允许挂回指定原盘，绝不格式化。已有窗口占用时不要启动、恢复或停它。C2 入口只拥有 A 三台的开停机，不负责跳板机。

```powershell
Get-Content ../../../scripts/restore-existing-jump-control.py -Raw -Encoding utf8 |
  ssh -F $aSshConfig -oBatchMode=yes -oConnectTimeout=8 ax-pilot-jump 'python3 - --mount'
```

操作人核对精确文件和四项 SHA 后，单独保存实际签审文件 `operator.reviewed.json`，填写真实 `reviewer` 并改为 `reviewed=true`。若超过 `$packet.expires_utc`，重取证据并重新签审，不沿用旧批准。实际执行入口：

```powershell
./run-c2-current-a.ps1 -SshConfig $aSshConfig -ReceiptRoot $newReceiptParent `
  -Latest (Join-Path $packet.packet latest.json) `
  -InstancesBefore (Join-Path $packet.packet instances-before.json) -OperatorReview $reviewFile
```

启动后由协调者持有锁；锁获取起 40 分钟结束工作、预留 5 分钟关停。最迟在锁后 43 分 30 秒请求停机；实际停机以 API 回执为准。收到 `coordinator/closed.json` 和三台 `StopCharging` 回执后才判断是否完成。若本地控制终端中止，先核实并按原计划停这三台，保留回执，不先杀锁进程。独立资格不提交正式 C2 cell，也不替代冻结套件自己的 900 秒准入。离线检查：`python3 -B check-offline.py [本机初态清单路径]`。
