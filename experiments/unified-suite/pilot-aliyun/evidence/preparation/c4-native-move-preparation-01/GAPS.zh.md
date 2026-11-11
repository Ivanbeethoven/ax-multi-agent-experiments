# C4 原生跨机恢复：仍然缺什么

2026-11-12。本目录是**离线准备记录**，不是跨机恢复回执。本轮只新增了本地 draft / 外层 driver 骨架 / 16 个离线测试；未接触云端、未 ssh、未启动或停止任何实例与服务、未调用模型、未 commit。下列任何一条未关闭，都不能把 `draft-c4-native-handoff.py` 的输出当作可执行请求，也不能把 driver 的 `result.json` 当作跨机完成。

## 已完成（本轮，仅本地）

- `scripts/draft-c4-native-handoff.py`：从原 verified checkpoint + release pins 生成 `c4-native-handoff-request-v1`；任一 pin 不可核、checkpoint session/export SHA 不符、relay body SHA 缺失、目标 client 身份未给出、A 后端 Redis DB/bucket 未给出即整体拒绝，输出 `reviewed=false, execution_enabled=false`。
- `scripts/run-c4-native-handoff.py`：外层骨架，持两池 `resource_guard`（两客户端 + A 后端角色）、审计所有 control 的 `cell_leases`、按 quiesce→export→source-stop→lease_check→target-start→restore→resume→export_after 编排；默认执行器只拒绝并打印完整命令，只有 `--execute` 才真跑；失败保留首个证据、`automatic_retry=false`、安全关闭失败写 `lock-held.json`。
- `tests/test_c4_native_handoff.py`：16 项离线测试全绿（`python3 -m unittest tests.test_c4_native_handoff -v`，WSL Ubuntu-24.04）。仅证明进程内契约，不证明云端。

## 仍然缺的（逐条含前置条件）

### 1. 真实两端 spec 与逐阶段动作 pin
缺：`experiment-handoff-v1` spec 里 `source`/`target` 的真实 `node_id`、`peer`、`mount_directory`、`anchor`，五份 cohort（quiesce/lease_check/restore/resume/export_after）的实际 pin；以及外层 `phases.quiesce.argv`/`phases.restore.argv` 的**已审阅**远端/本地 argv。
前置：A 池 C4 On1 关闭并交接、B 客户端由 root 明确启动后，重新冻结两端 runtime repo 路径与 binary；用 `draft-session-phase.py` 重新生成每阶段 phase/step/worker（`total_timeout_seconds` resume=205、其余=60）。缺省值不能替代；driver 只接受 `python3 -m bench.runtime.handoff --spec … --sha256 … --output …`。

### 2. 独立 relay 接入
缺：新端口（≠18443）的独立 bounded relay 私有配置、`max_requests=4`、`output_tokens=512`、`thinking=disabled`、`wall_seconds`、确切 PID 收据与 idle 后正常 SIGTERM 的 `result.json`。
前置：owner-only 私有配置（master key 只在 relay 侧，relay key 只进 worker/shared config）；`ready.json` 与 `state.json` 落在归档外；旧 18443 活动服务不动。

### 3. 外层 cold / closure
缺：target 端完整 full-tree cold read（必须在任何原生 export 重新写 DB 之前）、两次原生零债务采样、正常 stop、`/sys/fs/cgroup/ax-slot-1` 与子 cgroup 空、relay 正常退出、完整归档与校验。
前置：driver 在 handoff 成功后仍保持两池锁；cold/零债务工具与 observer gate 的 pin；归档排除 private config 与 token 正文。当前 driver 明确输出 `closure_complete=false`，不代做这些。

### 4. 正式准入（独立 native route plan）
缺：从冻结 `cases('pilot','systems')` 的 handoff case 派生的独立 native route plan；canonical plan 只有 C1/C2/C3，`draft.json` 已记 `handoff_cells_in_canonical_plan` 为空。还缺 route inventory、allocation/hardware/input/filesystem qualification、workload/metric contract 与 observer gate。
前置：两池物理资源与 inventory 匹配、`cell_leases` 清空、`purpose=experiment`；不得用 `adapter-test` 或零模型 state-reopen 结果冒名。

### 5. 双池 guard 与 handoff 授权
缺：A 池 On1（`ax-pilot-c4-on1-01`）结束后的显式 root 交接回执（`pool-a`、`pool-b` 各一份 `safe_for_handoff`），以及 B 的新鲜 boot/instance/hostname/数据盘挂载/空 slot/runtime+binary pin。
前置：B 只由 root 启动客户端；旧 boot 回执不能代替；driver 只在两份 handoff 证明齐备且所有 control `state.sqlite` 无冲突 lease 时进入阶段。

### 6. A 后端 DB 与新 bucket 证据
缺：候选 Redis DB（20..255）当刻 `DBSIZE=0` 的探针、目标 bucket 尚不存在、RustFS 端点/凭据归属。
前置：DB 210 只是旧草稿候选，未证明空闲；必须新探针。draft 只要求显式给出身份并与 inventory 的 metadata/objects 私网地址对齐，不做假布尔声明。

### 7. source-only checkpoint 安装薄入口
缺：复用 `prepare-session-input.py` `tree`/`sync_tree` 的 source 端薄入口（只初次复制完整 checkpoint 到 A canonical workspace，provider config 仅允许改为独立 relay URL/key 与 `{context:0, output:512}`），并以无模型方式复核原 export SHA。
前置：旧 `installer run` 硬编码 `http://10.20.0.10:18443/v1` 且调用 `worker.run(phase_dict,out)`，不能直接换 worker pin，也不能改旧文件破坏 qualifier SHA。

### 8. 目标端 mount spec 与二进制 pin
缺：B 端 `draft-handoff-target.py` 派生结果（原 anchor、backend、binary SHA、canonical paths，独立 cache/mount supervisor），以及 B 的 OpenCode/binary 与新 mount spec 的 pin。
前置：target 不 format、不建 bucket、不复制 workspace/native DB；两份客户端 binary SHA 一致。

### 9. 审阅后的 run request
缺：把 `reviewed-draft.json`（`reviewed=true, execution_enabled=true`）与 `c4-native-handoff-run-v1`（plan pin、两份 handoff pin、peers、8 个 phase 的 argv、`close_probe`）一起冻结。
前置：`close_probe` 必须是只读探针；driver 在失败且探针未确认安全时写 `lock-held.json`，禁止复用两池。

## 按阻塞概率排序

1. **两端 spec 与 per-phase argv 仍未冻结**（第 1、8、9 条）：draft/driver 已要求这些 pin，但没有它们只能拒绝。
2. **外层 cold/零债务/正常 stop 与归档仍未实现**（第 3 条）：即使 handoff 通过，也不能算 C4 跨机完成。
3. **B 的 root 授权与新鲜身份 + 独立 relay 生命周期**（第 2、5 条）：任一缺失都会让真实运行停在启动前，且不能靠重试补齐。

## 设计取舍与已知风险

- 外层只执行两个外部动作：source 端 bounded export 预检（`quiesce`）与 pinned `python3 -m bench.runtime.handoff` 入口（`restore`）。`source-stop`/`lease_check`/`target-start`/`resume`/`export_after` 由 handoff 执行，外层按它 `result.json.phases` 的 monotonic `start_ns` 与 `session_validation` 逐阶段核验。重复执行会二次 stop mount、二次 resume 并突破 relay 的 4 请求上限，因此不允许。
- 预检 export 与 handoff 内部 export 若字节不同，外层先拒；handoff 自身的 `checkpoint-transfer-check` 也会独立拒绝。两者都不自动重跑；若确认是序列化差异，只能在测量外重新准备 checkpoint 并重新出 draft。
- `--execute` 下失败且 `close_probe` 未确认安全时，driver 写 `lock-held.json` 并要求人工复核；进程退出后 OS flock 必然释放，该回执是"禁止复用两池"的唯一依据，不能当成 stop 证据。

