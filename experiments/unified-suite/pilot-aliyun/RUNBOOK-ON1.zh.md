# A 池 C4 On1 正式重跑执行手册
适用：A 池 `pool-a` / cell `c5e97774bcffa69a-r1-native` 的 step 2（On1，观察者开）。目标：5 小时窗口内走完前置链 → 测量 → 冷验 → 三角色归档 → 收尾停机。
`P` = `experiments/unified-suite/pilot-aliyun`。除标注 `ssh` 的块外，命令都在协调者上以 `root` 执行。

三条硬规则：**失败即停手**，保留现场和回执，按第 10 节记失败，不自动重试、不重放已有 attempt、不改冻结代码；**旧证据全部作废**，重启后 `boot_id` 变化，旧 `capacity.json` / `placement.json` / `observer-scratch.json` 一个都不能续用；**归档只改包装**，`bench/archive_check.py` 一字不动，objects 观察原文走拆包，client/metadata 走原单包。

## 0. 窗口前 10 分钟检查

```bash
export CTL=/srv/ax-pilot-control/fork-control-01
export CODE=/srv/ax-pilot-control/code-c4-split-01
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

### 0.7 归档入口 G0（硬前提）
上一轮 4.5 小时测量+冷验已完成（2,954 样本 / 15,385 秒 / `passed`，三角色标记齐全），但没有 trial 级 `finished.json`，按失败记录、不重试；失败点是 objects 在 `bench/archive_check.py:37` 被 16 MiB 内部清单拒绝。只把 `pack-c4-observer-split.py` 放进树里**不等于**归档入口会调用它。开计时前必须证明两件事：

```bash
grep -n "pack-c4-observer-split\|observer-index.ndjson" $CODE/experiments/unified-suite/pilot-aliyun/scripts/archive-c4-closed-observer.py
grep -n "objects-observer.tar.gz\|observer.tar.gz" $CODE/experiments/unified-suite/pilot-aliyun/scripts/run-c4.py
```

两条都必须命中。若第一条没有，objects 仍会打 132,939 个成员的单一 tar，4.5 小时后以同一个 `ValueError: unbounded internal manifest` 失败；若第二条没有，拆包后协调者的 `audit(raw/objects/o/$TAG, ...)` 会因目录不存在失败。**任一不满足：不启动 unit，本窗口只做前置链并按失败记录**。

### 0.8 验收侧同样不认识拆包（2026-11-12 补充核实）

除了归档入口，**验收器**也要改：`check-c4-calibration.py:61` 才是执行 `raw/<role>` 解包后 `audit(raw/<role>/o/<tag>, 30, marks)` 的地方（`retain-calibrations.py:115` 同形），它只认 `archives/<role>.tar.gz` 这一种形状。

因此"观察原文单独 tar"不是加个工具就能落地的事，它同时改动**验收判据**。三条可行路线，任选其一都必须作为**新一批**记录：

1. 新批次的 `check-c4-calibration.py` 同时接受 `archives/<role>.tar.gz` 与 `archives/<role>-observer.tar.gz`，先解小包再解观察包，audit 不变。改动最小，但要重新声明验收规则并给新 checker 固定 SHA。
2. 让观察者直接写单个 NDJSON 流而不是 132,939 个文件。成员数从 13 万降到个位数，原单包格式与冻结验收器都不用动；代价是观察输出格式变了，属于新的观测合同。
3. 缩短观察窗口或放粗采样间隔，使成员数回到 16 MiB 清单以内。不改任何代码，但改变 C4 On1 的观测强度，需要重新论证"最短可测窗口"。

在选定之前，On1 的 5 小时窗口**不具备开跑条件**；本仓库只保证拆包工具本身在真实 13.3 万成员上可用。


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

## 2. 清空旧 tag 命名空间（否则前置链必拒）

`c4-on1-preflight.py` 会检查 `o/$TAG`、`q/$TAG-observer`、`archives/$TAG-objects.tar.gz` 是否已存在；上一轮 22.47 GB 观察原文还在采样盘上，同名会直接拒绝本轮。

```bash
mkdir -p $SCRATCH/attempts-archive-bound-01
mv $SCRATCH/o/$TAG $SCRATCH/attempts-archive-bound-01/o-$TAG
mv $SCRATCH/q/$TAG-observer $SCRATCH/attempts-archive-bound-01/q-$TAG-observer 2>/dev/null || true
mv $SCRATCH/archives/$TAG-objects.tar.gz $SCRATCH/attempts-archive-bound-01/ 2>/dev/null || true
test ! -e $SCRATCH/o/$TAG && test ! -e $SCRATCH/q/$TAG-observer && echo TAG_CLEAR
```

判定：输出 `TAG_CLEAR`；`findmnt -no TARGET $SCRATCH` 仍是那块盘（同盘 `mv`，数据未拷贝）；旧包 `sha256sum $SCRATCH/archives-split-01/$TAG-objects-observer.tar.gz` 必须等于 `c4-observer-split-01/README.zh.md` 的 `504e8450…`。失败：停手；**不要删除旧树**，也不要把它改名后当成本轮证据。

## 3. 部署拆包代码树并核对 pin

```bash
test ! -e $CODE
cp -a /srv/ax-pilot-control/code-c4-directory-hold-01 $CODE
cp P/scripts/pack-c4-observer-split.py $CODE/experiments/unified-suite/pilot-aliyun/scripts/
scp -F $SSHCONF P/scripts/pack-c4-observer-split.py \
  pool-a-objects:/srv/ax-objects-0/code-19/experiments/unified-suite/pilot-aliyun/scripts/
sha256sum $CODE/experiments/unified-suite/pilot-aliyun/scripts/pack-c4-observer-split.py P/scripts/pack-c4-observer-split.py
```

判定：两处 SHA 相同；`python3 -B $CODE/.../pack-c4-observer-split.py --help` 能打印用法。失败：停手；不要覆盖 `code-c4-directory-hold-01` 或 `code-19` 的任何原文件。若 G0 的 (a)(b) 还没接上，只做到这里就停，按第 10 节记 `blocked`，不要进入第 4 步。

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

判定：stdout 是 JSON，`"passed": true`、`"step": 2`、`"archive_mode": "closed-observer-direct"`、`"validation_only": true`、`"worker_dispatched": false`。失败：停手；读 stderr 的 `ValueError`，**不要**去掉 `--validate-only` 硬跑。

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

判定：测量期间 `active/running`；观察者 `finished.json` 出现在 `$SCRATCH/o/$TAG/finished.json`，`passed:true`，`seconds` 在 13,372–18,000 之间（上一轮 15,385.08 秒 / 2,954 样本可参照）；四个标记 `measure-start`、`measure-end`、`drain-end`、`verify-end` 齐全；最后 `$OUT/trial/finished.json` 存在且 `passed:true`。失败：`ActiveState=failed` 或 5 小时后仍无 `$OUT/trial/finished.json`，按第 10 节记失败；**保留 unit.stdout/stderr 和采样盘上的观察原文**，不要重跑。

## 8. 归档（三份包与拆包落点）

本轮要三份 role 包，objects 必须拆包；client/metadata 默认走原单包，但上一轮没有留下它们的可用归档回执，本轮必须各自重新产出并校验，**先数成员数**：若 `find <role>/o/$TAG -type f | wc -l` 超过 100,000，单包清单也可能越 16 MiB，必须同样拆包（`pack-c4-observer-split.py` 的 `--role` 接受三个角色）。

| 角色 | 方式 | 落点 | 校验回执 |
| --- | --- | --- | --- |
| client | 原单包（默认） | `/srv/ax-client/archives/$TAG-client.tar.gz` | `$OUT/trial/archives/client-check.json` |
| metadata | 原单包（默认） | `/srv/ax-metadata/archives/$TAG-metadata.tar.gz` | `$OUT/trial/archives/metadata-check.json` |
| objects | 拆包：闭合小包 | `$SCRATCH/archives/$TAG-objects.tar.gz`（实测 552 B / 1 成员） | `$OUT/trial/archives/objects-check.json` |
| objects | 拆包：观察原文 | `$SCRATCH/archives/$TAG-objects-observer.tar.gz`（实测 379,794,692 B / 132,939 成员） | `$SCRATCH/archives/$TAG-objects-split.json` |

objects 拆包命令（输出必须在证据根之外；`/var/tmp` 根盘需 ≥ 5 GB）。unit 的 objects 归档阶段应执行等价命令；**若 unit 已成功，不要再跑**（脚本拒绝覆盖已有输出）。只有 unit 在归档前失败、需要保全证据时，才用新目录 `/var/tmp/c4-split-failed-02` 另跑一次，且不得据此把本轮改判为通过。若归档入口还要求 `q/$TAG-observer`，再加 `--include q/$TAG-observer`（成员数从 1 变 2，仍远小于 16 MiB）。

```bash
df -B1 /var/tmp; df -i /var/tmp
python3 /srv/ax-objects-0/code-19/experiments/unified-suite/pilot-aliyun/scripts/pack-c4-observer-split.py \
  --root $SCRATCH --tag $TAG --role objects --cell-id $CELL \
  --include q/$TAG-closure --out-dir /var/tmp/c4-split-02 --code /srv/ax-objects-0/code-19
mv /var/tmp/c4-split-02/$TAG-objects.tar.gz /var/tmp/c4-split-02/$TAG-objects-observer.tar.gz \
   /var/tmp/c4-split-02/$TAG-objects-split.json $SCRATCH/archives/
```

判定：包内 `observer-index.ndjson` 是 NDJSON（一行一个成员，首行 header），`members` 与 `tar -tzf $SCRATCH/archives/$TAG-objects-observer.tar.gz | grep -vc '^observer-index.ndjson$'` 相等；小包经冻结 `archive_check.verify` 通过；`sha256sum` 与 split 回执一致。失败：停手；**不要提高 16 MiB 上限、不要改 `bench/archive_check.py`**。

16 MiB 边界（原文）：`bench/archive_check.py:37`
`if member.size>16*1024**2:raise ValueError('unbounded internal manifest')`。
上一轮 objects 清单 20,430,785 B / 132,944 成员，单包必然越界；拆包把 132,939 个原文成员移出单清单，索引按 129 B/成员流式读，约 16.4 MiB 也不会被整体读进内存。实测 132,939 成员 / 6 分 52 秒 / 峰值内存 269 MB / exit 0，两个包都重新读取校验通过；上一轮拆包输出放 `/var/tmp/c4-split-01` 可行。**这是唯一改动，冻结校验器原样保留。**

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
scp -F $SSHCONF pool-a-objects:$SCRATCH/archives/$TAG-objects-split.json P/evidence/calibration/a-c4-flat-on1-rerun-01/
```

目录里至少要有：`unit.json`（invocation）、`cleanup.json`、`capacity-native.json`、`observer-scratch.json`、`placement.json`、`capacity.json`、`request.json`、三份 `*-check.json`、`objects-split.json`、收尾探针输出、`README.zh.md`（实际时间、包大小、SHA256）。缺少任何一件，本轮不能记通过。

## 10. 本次窗口不算通过的情况

以下任一发生，**必须**写 `failed.json`（`{"passed":false,"error":"...","automatic_retry":false}`）并保留现场，不得改判、不得重试：

1. **测量未完成**：观察者 `$SCRATCH/o/$TAG/finished.json` 不存在或 `passed:false`；窗口 < 13,372 秒；四个标记缺任何一个。
2. **冷验失败**：`verify/result.json` 的 `passed`、`parent_unchanged`、`full_cold_child` 任一不为 `true`；或 `verify-exit.json` / `exit.json` 非 0。
3. **归档失败**：三角色任一 `*-check.json` 不存在或 `passed:false`；objects 仍报 `unbounded internal manifest`；拆包 `observer`/`evidence` 的 `sha256` 或 `members` 对不上；小包未过冻结 `archive_check.verify`。
4. **收尾不干净**：任何角色仍有挂载、juicefs/opencode/brewfs 进程、client slot 非空、metadata 仍有 JuiceFS 会话；或三台没有确认 `Stopped/StopCharging`。
5. **准入失效**：`cleanup.finished_at` 晚于任一采样、`capacity.at` 超过 900 秒、`placement` 的 `boot_id` 与 `capacity-native` 不一致；或 `--validate-only` 未通过。
6. **invocation 缺失/复用**：没有 `$PSTEP/unit.json`，或 invocation 等于 `048f667dbca245a885100a1432d97d54`，或不是 32 位十六进制。
7. **重放/覆盖**：复用了已存在的 `$OUT`、`$PSTEP`、`--scratch-remote-dir`，或对失败 attempt 重跑。

## 11. 时间与成本

- 测量窗口按 15,000–18,000 秒留 5 小时；A 池三台约 ¥4.67/小时 × 5 ≈ **¥23.4**；采样盘 ¥0.105/小时 × 5 ≈ **¥0.53**；合计约 **¥23.9**（不含跳板、网络、OSS 和账单取整）。
- 时间线：T+0 启动/挂盘；T+10 前置链；T+15 启动 unit；T+4.5h 测量+冷验；T+5h 归档；T+5.5h 收尾停机。超过 15 分钟窗口未启动 unit，回到第 4 步用新目录重做前置链。
