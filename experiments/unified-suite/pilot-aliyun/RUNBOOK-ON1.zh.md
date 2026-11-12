# A 池 C4 On1 正式重跑执行手册
适用：A 池 `pool-a` / cell `c5e97774bcffa69a-r1-native` 的 step 2（On1，观察者开）。目标：5 小时窗口内走完前置链 → 测量 → 冷验 → 三角色归档 → 收尾停机。
`P` = `experiments/unified-suite/pilot-aliyun`。除标注 `ssh` 的块外，命令都在协调者上以 `root` 执行。

失败即停手，保留现场和回执；旧测量仍是失败记录。重启后，旧准入回执不能用于新窗口。新批次的 objects 采集器直接写 NDJSON，三个角色各出一个原格式 tar 包；冻结的归档校验器和原采集模块保持原样。

**状态：暂不能直接启动。** 下列旧路径仅用于核对前置链。必须先有经过审阅的新 attempt 和相符的物理容量证明，且第 5 节以新请求通过。已有 On1 即使折叠或拆包成功，也不会补成一次通过的试验。

## 0. 窗口前 10 分钟检查

```bash
export CTL=/srv/ax-pilot-control/fork-control-01
export CODE=/srv/ax-pilot-control/code-c4-ndjson-01
export PSTEP=$CTL/preparation/c4-on1-prestep-04
export OUT=$CTL/continuation-on1-04
export SCRATCH=/srv/ax-c4-observer-scratch
export TAG=c4-a-flat-on1
export CELL=c5e97774bcffa69a-r1-native
export SSHCONF=$CTL/runtime-a-03/ssh.conf
export OBJ=i-bp1h9kaoya0predubdkn META=i-bp1gy3cfuj9jik0y4oc3 CLI=i-bp1809mw7rlujtj9f705
```

| # | 检查 | 命令 | 判定 |
| --- | --- | --- | --- |
| 0.1 | 实例状态 | `aliyun ecs DescribeInstances --InstanceIds "[\"$OBJ\",\"$META\",\"$CLI\"]"` | 三台 `Running`；`Stopped` 先按第 1 步启动 |
| 0.2 | 数据盘 | `findmnt -no SOURCE,TARGET,FSTYPE,OPTIONS /srv/ax-objects-0 /srv/ax-objects-1 /srv/ax-objects-2 /srv/ax-objects-3 /srv/ax-metadata /srv/ax-client /srv/ax-c4-observer-scratch` | 全部 `ext4 rw,noatime`；采样盘 UUID 必须 `91d693ad-0c15-4e6e-b843-73e581aec594` |
| 0.3 | RustFS 容器 | `ssh -F $SSHCONF pool-a-objects "docker inspect -f '{{.State.Running}}' ax-pilot-20261111-objects-01-rustfs-1"` | `true` |
| 0.4 | Redis 生效值 | `for k in appendonly databases dir bind; do redis-cli -h 10.20.2.162 CONFIG GET $k; done` | `yes` / `256` / `/srv/ax-metadata/redis` / `127.0.0.1 10.20.2.162`；只看进程不算数 |
| 0.5 | 采样盘余量 | `df -B1 $SCRATCH; df -i $SCRATCH` | 可用 ≥ 55,000,000,000 B、inode ≥ 20,000,000；低于此值停手 |
| 0.6 | 协调者时钟 | `timedatectl show -p NTPSynchronized --value; date -u +%s` | `yes`；与三台 `ssh ... date -u +%s` 差 ≤ 2 秒 |

任何一项不满足：停手，把输出写进 `$PSTEP/blocked-precheck.json`，不要用“上次是好的”代替。

### 0.7 开跑前核对的采集和验收路径

旧 On1 完成了测量和冷验（2,954 样本，15,385 秒），但 132,939 个原文文件使 objects 清单超过 16 MiB。新窗口使用 `producer: ndjson`：仅 objects 原位写 `samples.ndjson`，client 和 metadata 继续写目录。四个阶段标记仍通过原 socket 发送。归档入口、协调者解包后的审计、六次校准报告和正式验收都必须使用同一份固定 SHA 的格式模块。旧拆包工具只用于保全证据。

### 0.8 审阅 pin 与校准

在 reviewed request draft 的 `archive_overlay.observer_format` 下写入 `{"producer":"ndjson","audit_module":{"path":"<CODE>/bench/runtime/observe_ndjson.py","sha256":"<sha256sum>"}}`。包装器在协调者验证文件，在 objects 端通过原 SSH/runtime 校验后传入同一份源码。不要把新模块放进旧 worker 的 `bench/runtime`。

直接写流的 CPU 和写盘开销需要用新一轮观察开/关校准验证；不能沿用旧 writer 的观测开销。仅做离线保全时可用 legacy 折叠；它的空间预算要同时包含原目录和新流，不能拿直接写流的预算代替。

### 0.9 文件大小与覆盖范围

每行包含原采样字段及全部原文，包括 `sample.json`、进程字段和采集器 CPU。流关闭时记录 SHA 和字节数，独立审计逐行重验原文哈希、完整性、采样间隔和四个阶段标记。上一轮 objects 原目录约 22 GB；当前容量预算继续以实测原文及其编码开销为基础，不能按 180 MB 估算。


## 1. 启动实例并恢复 A 池现场

数据盘重启后**不会自动挂载**，必须显式恢复。

```bash
for i in $OBJ $META $CLI; do aliyun ecs StartInstance --InstanceId "$i"; done
aliyun ecs DescribeInstances --InstanceIds "[\"$OBJ\",\"$META\",\"$CLI\"]"
```

判定：三台 `Status=Running`；`a-pool-up.py` 会自己核 hostname（`ax-pilot-20261111-objects` 等），错机会直接拒绝。失败：停手，保留 `DescribeInstances` 输出，不要重复 `StartInstance`。

上传并运行 `a-pool-up.py`（每个角色一次，`--out` 必须全新）：

```bash
for r in objects metadata client; do
  scp -F $SSHCONF P/scripts/a-pool-up.py pool-a-$r:/root/a-pool-up.py
  ssh -F $SSHCONF pool-a-$r "python3 /root/a-pool-up.py $r --out /srv/ax-receipts/a-pool-up-$(date -u +%Y%m%dT%H%M%SZ)-$r"
done
```

判定：三份 `result.json` 均 `"passed": true`、`"automatic_retry": false`；objects 回执含四块 `6kvd/6kve/6kvf/6kvg`、采样盘 `bp16jaiu2fcmbij8mg7i`、四个 bind 和容器启动；metadata 回执 `effective` 四项正确。失败：停手，保留 `/srv/ax-receipts/a-pool-up-*`；**永远不对原盘 mkfs**。

复核（不要只看脚本回执）：

```bash
ssh -F $SSHCONF pool-a-objects 'findmnt -R /srv/ax-objects-0; findmnt -R /srv/ax-c4-observer-scratch; docker inspect -f "{{.State.Running}}" ax-pilot-20261111-objects-01-rustfs-1'
ssh -F $SSHCONF pool-a-metadata 'redis-cli -h 10.20.2.162 CONFIG GET appendonly databases dir bind'
ssh -F $SSHCONF pool-a-client 'cat /sys/fs/cgroup/ax-slot-1/cgroup.procs; findmnt -no TARGET /srv/ax-client'
```

判定同 0.2–0.4，且 client slot 为空。失败：停手。

## 2. 使用已审阅的新 attempt

保留旧树、归档和回执。用新请求明确的新 tag 和输出路径；前置链发现冲突即停。不要移动旧目录来让同名 attempt 通过。

## 3. 部署协调者覆盖层并核对 pin

```bash
P=experiments/unified-suite/pilot-aliyun
test ! -e "$CODE" && cp -a /srv/ax-pilot-control/code-c4-directory-hold-01 "$CODE"
for f in c4-on1-preflight.py resume-c4-after-directory-hold.py calibrate-c4-closed-observer.py \
         archive-c4-closed-observer.py run-c4-formal-closed-observer.py; do
  install -m 0644 "$P/scripts/$f" "$CODE/$P/scripts/$f" || exit 1
  cmp "$P/scripts/$f" "$CODE/$P/scripts/$f" || exit 1
done
install -m 0644 bench/runtime/observe_ndjson.py "$CODE/bench/runtime/observe_ndjson.py"
cmp bench/runtime/observe_ndjson.py "$CODE/bench/runtime/observe_ndjson.py"
sha256sum "$CODE"/bench/runtime/observe_ndjson.py
```

部署前确认源 checkout 是审阅过的版本；六份部署文件与源一致。原 `code-c4-directory-hold-01`、各 worker 的 `code-19` 保持原样。编写新 draft 时把本节打印的 SHA 放进第 0.8 节的字段。旧批次的 path/SHA 引用不得替换。

## 4. 生成 900 秒前置链（一条命令）

先确认 reviewed draft 和旧回执都在（路径不对就停手，不要拿旧 `capacity.json` 顶替）：

```bash
for f in $CTL/preparation/c4-on1-continuation-03/draft/capacity-draft.json \
         $CTL/preparation/c4-on1-continuation-03/draft/request-draft.json \
         /srv/ax-pilot-control/preparation/a-old-c1-observer-reclaim-01/apply-01/result.json \
         $CTL/preparation/c4-on1-scratch-01/ownership.json \
         $CTL/preparation/c4-on1-scratch-01/remediation.json; do test -f "$f" || { echo MISSING:$f; exit 1; }; done
python3 -c "import json;d=json.load(open('$CTL/preparation/c4-on1-continuation-03/draft/capacity-draft.json'));assert d['reviewed'] is True and d['passed'] is True and not d.get('draft_status') and d['step']==2 and len(d['prefix'])==1" || { echo DRAFT_BLOCKED; exit 1; }
test ! -e $PSTEP && test ! -e $OUT && echo FRESH_OK
```

执行（`--records`、`--out`、`--scratch-remote-dir` 每轮全新）：

```bash
python3 -B $CODE/experiments/unified-suite/pilot-aliyun/scripts/c4-on1-preflight.py --control $CTL --runtime $CTL/runtime-a-03 --code $CODE \
  --draft $CTL/preparation/c4-on1-continuation-03/draft/capacity-draft.json --request-draft $CTL/preparation/c4-on1-continuation-03/draft/request-draft.json \
  --reclaim /srv/ax-pilot-control/preparation/a-old-c1-observer-reclaim-01/apply-01/result.json \
  --remediation $CTL/preparation/c4-on1-scratch-01/remediation.json --ownership $CTL/preparation/c4-on1-scratch-01/ownership.json \
  --ownership-sha256 ec98e27e844fbb70c956929cecb49dc57b4b0fd0c4d64d5fa933522baf78a106 \
  --node-ownership /srv/ax-receipts/c4-on1-scratch/ownership.json --scratch-remote-dir /srv/ax-receipts/c4-on1-scratch/prepare-04 \
  --records $PSTEP --out $OUT --cell $CELL --tag $TAG
```

判定：stdout 是 JSON，含 `cleanup`、`snapshot`、`scratch`、`placement`、`capacity`、`request` 六个 `path/sha256`，且 `rows_moved: 2`。失败：停手，保留 `$PSTEP` 已写出的文件；下一轮换 `-05` 新目录，**不要删了重跑同一目录**。

核对顺序与时效（脚本已核一次，这里独立复核）：

```bash
python3 - "$PSTEP" <<'PY'
import json,sys;from datetime import datetime,timezone
r=sys.argv[1];L=lambda n:json.load(open(f'{r}/{n}'))
c=L('cleanup.json');cap=L('capacity.json');pl=L('placement.json');sn=L('capacity-native.json')
t=lambda s:datetime.fromisoformat(s)
assert t(c['finished_at'])<=t(cap['at']) and (datetime.now(timezone.utc)-t(cap['at'])).total_seconds()<=900
assert all(pl['workers'][k]['boot_id']==sn['nodes'][k]['boot_id'] for k in ('client','metadata','objects'))
print('ORDER_OK',cap['at'])
PY
```

判定：输出 `ORDER_OK`；`cleanup.finished_at` ≤ 其余每个 `at`；`placement.workers.*.boot_id` 与 `capacity-native.nodes.*.boot_id` 一致。失败：停手，整条链重做（新目录），不能只补一件。

## 5. 试运行校验（不派发 worker）

```bash
REQ=$PSTEP/request.json
REQ_SHA=$(sha256sum $REQ | cut -d' ' -f1)
python3 -B $CODE/experiments/unified-suite/pilot-aliyun/scripts/resume-c4-after-directory-hold.py --request $REQ --sha256 $REQ_SHA --validate-only
```

判定：stdout 是 JSON，`"passed": true`、`"step": 2`、`"archive_mode": "closed-observer-direct"`、`"observer_producer": "ndjson"`、`"validation_only": true`、`"worker_dispatched": false`，且 `observer_format_sha256` 与审阅值相同。失败：停手；**不要**去掉 `--validate-only` 硬跑。

## 6. 启动 unit 并记录 invocation

unit 名固定 `ax-pilot-c4-on1-01`；invocation 必须当场记录，不能复用上一轮的 `048f667dbca245a885100a1432d97d54`。

```bash
systemd-run --unit=ax-pilot-c4-on1-01 --collect --property=Restart=no --property=Type=exec --property=WorkingDirectory=$CODE \
  --property=StandardOutput=append:$PSTEP/unit.stdout --property=StandardError=append:$PSTEP/unit.stderr \
  /usr/bin/python3 -B $CODE/experiments/unified-suite/pilot-aliyun/scripts/resume-c4-after-directory-hold.py --request $REQ --sha256 $REQ_SHA
INV=$(systemctl show ax-pilot-c4-on1-01.service -p InvocationID --value)
echo "$INV" | grep -Eq '^[0-9a-f]{32}$' || { echo BAD_INVOCATION; exit 1; }
[ "$INV" != 048f667dbca245a885100a1432d97d54 ] || { echo REUSED_OLD_INVOCATION; exit 1; }
printf '{"unit":"ax-pilot-c4-on1-01","invocation":"%s","request_sha256":"%s","at":"%s","automatic_retry":false}\n' "$INV" "$REQ_SHA" "$(date -u +%FT%TZ)" > $PSTEP/unit.json
```

判定：`systemd-run` 打印 `Running as unit: ax-pilot-c4-on1-01.service`；`$PSTEP/unit.json` 的 invocation 是 32 位小写十六进制且不是旧值；`systemctl show ... -p ActiveState` 为 `active`。失败：unit 名已存在时先看旧 invocation，**停手**，不要 `reset-failed` 后重开同名 unit。

## 7. 监控测量、冷验和归档
每 30 分钟一次（不要用 `sleep 5h` 代替）：

```bash
systemctl show ax-pilot-c4-on1-01.service -p ActiveState,SubState,MainPID,ExecMainStatus,InvocationID
journalctl -u ax-pilot-c4-on1-01.service -n 30 --no-pager
ssh -F $SSHCONF pool-a-objects 'df -B1 /srv/ax-c4-observer-scratch; pgrep -af "bench.runtime.observe" | head'
```

判定：测量期间 `active/running`；objects 的 `$SCRATCH/o/$TAG/samples.ndjson` 持续增长。关闭后 `finished.json` 有 `passed:true`、匹配的格式 SHA 和四个标记 `measure-start`、`measure-end`、`drain-end`、`verify-end`。试验的最终 `finished.json` 以 `$OUT/result.json` 的 `finished.path` 为准。失败：保留 unit.stdout/stderr 和采样盘上的观察原文，按第 10 节记录。

## 8. 归档和验收

三个角色各出一包：`/srv/ax-client/archives/$TAG-client.tar.gz`、`/srv/ax-metadata/archives/$TAG-metadata.tar.gz`、`$SCRATCH/archives/$TAG-objects.tar.gz`。objects 包含 `o/$TAG/samples.ndjson`、采集回执和关闭凭证；协调者解包后逐行审计。保留三份 `archives/*-check.json`，并核对每份 archive SHA。内嵌清单仍受冻结的 16 MiB 上限；任何包不通过，本次记失败，不切换到拆包。

正式测量的独立复核使用同一份已固定 SHA 的正式请求：`run-c4-formal-closed-observer.py --request <path> --sha256 <sha> --verify-sample`。此命令只复核现有样本。

## 9. 收尾：三角色检查与 StopCharging
先证明没有残留，再停机；不要先停实例再补检查。

```bash
ssh -F $SSHCONF pool-a-client 'findmnt -t fuse.juicefs,fuse.brewfs; pgrep -af "juicefs|opencode|brewfs"; cat /sys/fs/cgroup/ax-slot-1/cgroup.procs'
ssh -F $SSHCONF pool-a-metadata 'pgrep -af "juicefs|opencode|brewfs"; redis-cli -h 10.20.2.162 CLIENT LIST | grep -Ei "juicefs|go-redis" || echo NO_JFS_SESSION'
ssh -F $SSHCONF pool-a-objects 'pgrep -af "bench.runtime.observe|juicefs|opencode"; docker inspect -f "{{.State.Running}}" ax-pilot-20261111-objects-01-rustfs-1'
# 上面三项都干净后，才执行下面两行
for i in $OBJ $META $CLI; do aliyun ecs StopInstance --InstanceId "$i" --StoppedMode StopCharging; done
aliyun ecs DescribeInstances --InstanceIds "[\"$OBJ\",\"$META\",\"$CLI\"]"
```

判定：三台都没有 juicefs/opencode/brewfs 进程；client cgroup `cgroup.procs` 为空；metadata 没有 JuiceFS 会话；objects 没有 `bench.runtime.observe`；RustFS 容器仍 `true`（保留服务，直到确认归档回执拉回）。任一不满足：停手，先查 PID/挂载来源，**不要** `kill -9` 未知进程。停机后三台必须都是 `Stopped` 且 `StoppedMode=StopCharging`；采样盘 `d-bp16jaiu2fcmbij8mg7i` 仍是 `In_use`，不要 detach/delete。失败：保留 API 回执，下一窗口再核，不要反复发 `StopInstance`。

回执归位到 `P/evidence/calibration/`（只拉 JSON/日志，不拉 22 GB 原文和 380 MB tar）：

```bash
mkdir -p P/evidence/calibration/a-c4-flat-on1-rerun-01
cp -a $PSTEP/*.json $PSTEP/unit.stdout $PSTEP/unit.stderr P/evidence/calibration/a-c4-flat-on1-rerun-01/
```

还需保存 objects 的 `ready.json`、`finished.json`、协调者的三份 `*-check.json` 和 `$OUT/result.json`、收尾探针输出，以及实际耗时、包大小和 SHA。先取回执再停机。

## 10. 本次窗口不算通过的情况

以下任一发生，**必须**写 `failed.json`（`{"passed":false,"error":"...","automatic_retry":false}`）并保留现场，不得改判、不得重试：

1. **测量未完成**：观察者 `$SCRATCH/o/$TAG/finished.json` 不存在或 `passed:false`；窗口 < 13,372 秒；四个标记缺任何一个。
2. **冷验失败**：`verify/result.json` 的 `passed`、`parent_unchanged`、`full_cold_child` 任一不为 `true`；或 `verify-exit.json` / `exit.json` 非 0。
3. **归档失败**：三角色任一 `*-check.json` 不存在或 `passed:false`；objects 缺少固定 SHA 的采集回执、流审计失败，或任一包未过冻结的归档检查。
4. **收尾不干净**：任何角色仍有挂载、juicefs/opencode/brewfs 进程、client slot 非空、metadata 仍有 JuiceFS 会话；或三台没有确认 `Stopped/StopCharging`。
5. **准入失效**：`cleanup.finished_at` 晚于任一采样、`capacity.at` 超过 900 秒、`placement` 的 `boot_id` 与 `capacity-native` 不一致；或 `--validate-only` 未通过。
6. **invocation 缺失/复用**：没有 `$PSTEP/unit.json`，或 invocation 等于 `048f667dbca245a885100a1432d97d54`，或不是 32 位十六进制。
7. **重放/覆盖**：复用了已存在的 `$OUT`、`$PSTEP`、`--scratch-remote-dir`，或对失败 attempt 重跑。

## 11. 时间与成本

- 测量窗口按 15,000–18,000 秒留 5 小时；A 池三台约 ¥4.67/小时 × 5 ≈ **¥23.4**；采样盘 ¥0.105/小时 × 5 ≈ **¥0.53**；合计约 **¥23.9**（不含跳板、网络、OSS 和账单取整）。
- 时间线：T+0 启动/挂盘；T+10 前置链；T+15 启动 unit；T+4.5h 测量+冷验；T+5h 归档；T+5.5h 收尾停机。超过 15 分钟窗口未启动 unit，回到第 4 步用新目录重做前置链。
