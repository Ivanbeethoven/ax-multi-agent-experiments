# JuiceFS C4 原生跨客户端恢复：续作入口

2026-11-12，本轮仅本地源码审查和协调者小元数据读取。未修改云端、未部署、未启动服务、未唤醒 B、未调用模型、未提交。此目录是准备记录，不是 handoff 成功回执。可运行外层 driver 尚未实现；`draft.json` 明确禁止执行，缺失门槛不能填假值。

## 已核实的路线

使用 A 客户端作为 source，A Redis/RustFS 作为唯一共享后端，B 已有客户端作为 target。B 的 metadata/objects 无需启动。原 canonical plan 只有 C1/C2/C3，没有可直接使用的 C4 handoff cell。`draft-c4-state-reopen.py` 的 B→A 只是 CLI 默认值，`--source pool-a` 已支持 A→B，协议不强制 B→A；但该工具输出是零模型诊断，不能当真实 resume 请求。

真实协调入口保持 `python3 -m bench.runtime.handoff --spec ... --sha256 ... --output ...`，`restore_method=shared-workspace-db`。原流程依次 quiesce 原生 export、source-stop、lease_check、target-start、restore 原生 export、resume、export_after。目标仍打开 `/srv/ax-pilot-session/workspace`，project 为其 `repo`，XDG 为原 `source/{data,state,cache,config}`。目标只接收 checkpoint/asset/mount/command 元数据；不能复制 workspace/native DB，也不 format 新目标后端。

## 已有实现及本轮验证

复用 `scripts/session-relay-bounded.py`、`session-worker-bounded.py`、`draft-session-phase.py`、`draft-handoff-target.py`；不要修改旧活动 relay/worker，不调用 capped qualifier 的本地 `Hooks.worker`。2026-11-12 在 WSL Ubuntu-24.04 运行 `python3 -m unittest discover -s tests -p test_bounded_cloud_session.py`，12 tests 全通过（2.593 秒）。这是离线验证，不是云端恢复证据。

relay 固定第一个完整上游 body SHA `3a792e6a0068fc3e73332289cb8be92e16315ae8cb4a2c496ed7d2d630631537`，512 output、disabled thinking、最多四个不同顺序请求；不匹配保留失败，不更新 SHA、不重试。finish 已锁内永久闭合 reserve，server_error 安全判失败。原 worker 原生 JSON stdout 直接保留，first completed tool ACK 由 handoff 观察。

worker 需要显式 timeout（建议 resume 205 秒，export/verify-stopped 60 秒），在原 `/sys/fs/cgroup/ax-slot-1` 下创建独立子 cgroup，继承云端 2 CPU / 8 GiB / swap 0；正常、超时、信号都收尾后代。不要套本地 1 CPU 沙箱，不改 FUSE cgroup。

## 必须补齐的最小实现

1. 从冻结 `cases('pilot','systems')` 的 handoff case 派生独立 native route plan，保留原 resume/writer/neighbor 门槛；不能把当前新方案的 C4 clone/fork 混入，也不能将零模型 state-reopen 的结果改名。原 canonical 两池物理资源和 inventory 必须匹配。
2. 外层 owner 在准备至归档持原 canonical 两池 guard（含两客户端及 A 后端）；检查所有实际 control `state.sqlite` 的 **`cell_leases`**。A scratch/On1 准备正由 root 与 c4_parent_close 协调，不能抢占。B 仅由 root 明确启动客户端；新鲜 B boot/instance/hostname、原数据盘挂载、空 slot、有效预算和精确 runtime/binary pins 必须在启动后核实，旧 boot 回执不可代替。
3. 独立 source 安装薄入口：复用 SHA 固定 `prepare-session-input.py` 的 `tree`/`sync_tree`，只初次复制完整 checkpoint 到 A canonical workspace，允许唯独 provider config 改为独立 bounded relay URL/key 和 model limit `{context:0, output:512}`。旧 installer `run` 硬编码 `http://10.20.0.10:18443/v1`，且调用 `worker.run(phase_dict,out)`；新 wrapper 的 `run` 接受 pinned request ref。因此不能简单替换它的 worker pin，不能改旧文件破坏 qualifier 依赖 SHA。新入口通过 CLI 调 bounded worker 做 export，精确核对原 canonical export SHA；无模型。
4. source 资产只在 A 未占用 Redis DB（20..255，现场 DBSIZE=0）和新 bucket 初始化；候选 DB 210 只是旧草稿候选，未证明当前空闲。目标用 `draft-handoff-target.py` 从 source asset/mount/command 派生，保留 anchor、后端、binary SHA、canonical paths，独立 cache/mount supervisor。
5. 各阶段使用 `draft-session-phase.py` + bounded worker pin + 固定 phase timeout。生成真实 cohort 所需 route inventory、allocation/hardware/input/filesystem qualification、workload/metric contracts 和适用 observer gate。不能用 `purpose=adapter-test` 绕过生产门槛，不能冒用零模型 qualification 作 resume 通过。
6. 独立 relay 使用新私有配置/端口/key，不替换旧 18443 活动服务；master key 只在 relay private config。模型调用只在所有准备 pins 审查后开始。worker/shared config 只含 relay key；归档排除任何 private config、token 正文。独立 relay 启动与停止必须纳入确切 PID/identity。
7. 调原 handoff；不重写协调流程。成功后 target 仍挂载，外层必须完整 full-tree/cold read、两次原生零债务采样、正常 stop、原进程/slot/子 cgroup 空、relay idle 正常 SIGTERM 并验 result、完整归档/校验。cold 检验要在任何原生 export 重新写 DB 前核全树。失败保留首次证据，不能自动 retry；锁保持至实际安全关闭，不把 SSH 断线当 stop。

## 本轮核实的协调者输入

详见 `draft.json`。安装好的原 checkpoint 位于 `/srv/ax-pilot-control/preparation/c4-state-reopen-draft-01/verified-checkpoint`；它是 coordinator 输入，不表示 A 上已有可恢复共享 mount。checkpoint.session_id=`ses_e39418e40ffer6Ar6szDXn1kx2`，原 export SHA=`b3bdd63f6969387f11394cf78fd050352522c60a61a37c69ee835e4d30bd1bd9`，OpenCode SHA=`025e625d7ee316f9ce246056997551869b72e4bffa778dd3ad8248ba0e014233`。

原 coordinator `/srv/ax-pilot-control/parallel-control-01/fleet.json` 的 client repo 是 `/srv/ax-client/code-11`，后续实际部署可能不同，不能据此填最终 runtime closure。一次读取误用了 `parallel-control-01/assets/provenance/opencode-runtime.json`（不存在），因此本轮未重新取得 release/binary 位置，不把该路径当有效 pin。可从旧 `c4-state-reopen-draft-01/recipe/draft.json` 的精确 release pin 继续读小元数据，避免扫盘。

## 给下一轮的执行约束

工作树 `C:/Users/HXY/.cache/ax-pilot-20261111/publish-fsync-1911-01`，先读 AGENTS。当前只授权准备本地 runnable request/driver 和离线 tests、协调者小元数据只读；云端部署/新服务/模型/B 启动均还未授权。SSH `ssh -F C:/Users/HXY/.cache/ax-pilot-20261111/bootstrap-ssh.conf ax-pilot-jump`。不要动其他 agent 的 observer scratch 修改；不要 commit。先完成上列薄 source 入口和持锁外层 driver/tests，再给 root 可审查的精确请求与 launch 顺序。原零模型 `run-c4-state-reopen.py` 只供诊断阅读，不调用它代替真实 handoff。
