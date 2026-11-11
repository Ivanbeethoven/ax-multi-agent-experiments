# 云端会话恢复：限制请求次数和运行时间

这两个独立入口复用已发布的原生 worker 和 capped gate，不改变活动的 `session-relay.py`、`session-worker.py`、FUSE、handoff 协调器或原 slot 的预算。它们的离线测试不构成云端 resume 通过证据。

## 独立 relay

部署 `scripts/session-relay-bounded.py` 及同目录、SHA 固定的 `session-relay.py` 和 `qualify-session-shared-capped.py`。创建新的 owner-only private config，使用独立端口和独立 relay key；固定 `max_requests: 4`、`thinking_mode: "disabled"`。保持 provider master key 只在 relay 私有配置中，不把配置内容放入命令行、日志或归档。不要替换或重启旧活动 relay。

```sh
python3 scripts/session-relay-bounded.py \
  --private-config /private/c4-bounded-relay.json \
  --out /private/evidence/c4-bounded-relay-01 --wall-seconds 900
```

首个转发完整 body 的 SHA 固定为 `3a792e6a0068fc3e73332289cb8be92e16315ae8cb4a2c496ed7d2d630631537`，无覆盖参数。首 body 不匹配、重复、并发、工具 schema 变化、不完整 SSE 或超出四个不同请求均锁闭，保留失败 metadata，不能学习新 SHA 或自动重试。每次请求要求 `max_tokens=512`、stream 和 disabled thinking；urllib 单次 timeout 仍为 180 秒。relay 总生命周期最多 900 秒。

`state.json` 提供计数与锁闭原因，`calls.jsonl` 仅保留原 handler metadata 和 SSE 完成计数。原生任务成功后，等 `in_flight=false` 且 `reserved=recorded`，对本次 `ready.json` 的确切 relay PID 正常发送 SIGTERM，检查 `result.json.passed=true`。中途停机或超时保留失败；服务 ready 不代表 C4 通过。

## 远端 worker 与 handoff

`draft-session-phase.py` 的 `session_worker` pin 指向新 `session-worker-bounded.py`；同目录原 `session-worker.py` 的内容必须符合包装器固定 SHA。在传给生成器的 `phase` 中显式加入整数 `total_timeout_seconds`，例如 resume 固定 `205`，export 固定 `60`。允许范围为 1–900 秒，另有最多 5 秒子 cgroup 清理及 5 秒进程回收窗口。phase JSON 本身仍由原生成器固定 SHA。

在测量前把共享工作区原生 provider model 声明准备为 `limit: {"context": 0, "output": 512}`，并指向本次独立 relay URL/key；将这些准备变更纳入新的 checkpoint/config pins。包装器不会在运行中改配置或修改 checkpoint。原完整请求 SHA 不吻合时保留失败，另行检查输入。

仍由原 cohort transport 将包装器放入 `/sys/fs/cgroup/ax-slot-1`。包装器核验有效 2 CPU、8 GiB、零 swap，在此 slot 下创建唯一 `native-phase-*` 子 cgroup，仅把原生 worker 及其后代放入其中。不启用额外 controllers、不改限额、不移动或终止 FUSE。stdout 直接继承，原生工具 JSON 即时到达 coordinator，保留 first completed tool ACK。正常退出、超时和 SIGTERM/SIGINT 都收尾子树，写 `bounded.json`；旧 `started.json` 保持原义，其无限 timeout 字段由独立 bounded 收据说明实际上限。异常清理失败保留 cgroup 和失败收据供检查。

使用现有真实协调入口，不使用零模型 state-reopen runner：

```sh
python3 -m bench.runtime.handoff \
  --spec /reviewed/handoff.json --sha256 "$HANDOFF_SHA256" \
  --output /evidence/handoff-bounded-01
```

spec 的 `restore_method` 使用 `shared-workspace-db`，原流程保持 quiesce → source-stop → lease_check → target-start → restore → resume → export_after。handoff 成功后，外层仍须核验 cold/零债务、正常 stop、slot 和子 cgroup 无活动进程并归档；handoff 返回时 target mount 原本就保留，包装器不代替这些收尾。

离线测试（Linux/WSL；不监听端口、不调用模型、不变更真实 cgroup）：

```sh
python3 -m unittest discover -s tests -p 'test_bounded_cloud_session.py'
```
