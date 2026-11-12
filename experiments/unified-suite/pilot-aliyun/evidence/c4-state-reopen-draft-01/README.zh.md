# C4：无模型跨机打开 OpenCode 原会话

先做状态验证：B 组 client 正常关闭，A 组 client 从原 B 组 Redis/RustFS 重挂同一工作区，用空 HOME 和工作区内的四个 XDG 目录打开同一个 OpenCode 会话。目标不接收 checkpoint 包或会话导出文件。OpenCode 只执行 `export`，进程在只有 loopback 的网络命名空间里，没有模型调用。

源端同步文件、检查两次上传债务均为零，确认原 OpenCode 和 FUSE 进程退出、挂载消失。协调者切换执行授权，并用原授权调用同一个门禁，必须在发出 SSH 前被拒绝。目标端先核对原工作区全树摘要，再核对原生会话 ID、历史与导出 SHA256。这是有序关闭后的接续检查；它不能证明 JuiceFS 能拒绝持有旧文件描述符的崩溃客户端，也不能证明模型已经继续工作。未填写正式 C4 样本。

复用 B/A 的现有 client、B 的 Redis/RustFS，以及原物理锁目录。协调机已恢复检查点并逐文件验证（894 项）；草稿没有联系任何 worker。数据库 210 只是候选，使用前要求实际为空。两组仍有工作时不执行本检查。

执行入口：

1. 两组都交接后，核对空槽与原 ECS 主机身份。为两端固定相同的 JuiceFS SHA 和原 OpenCode SHA；复用已部署的代码和应用资产，缺失时只从协调机私网分发。源端必须有原代码的 `pilot-node.py`、`prepare-session-input.py`、`package-session-checkpoint.py`；目标端使用相同版本的 `draft-handoff-target.py`。两边的 `/srv/ax-pilot-session` 必须为空或不存在，不能遮住已有数据。
2. 跑一次 `verify-latest.py`，生成新回执。写新的请求 `schema=pilot-c4-state-reopen-request-v1`、`reviewed=true`、唯一 `attempt`；各文件引用均为绝对路径和 SHA256。
3. 用 `run-c4-state-reopen.py --request "$REQUEST" --sha256 "$SHA" --runtime-repo /srv/ax-pilot-control/code-15 --out "$FRESH_OUT"` 启动。归档各端原始导出、网络隔离、两次债务采样和正常退出回执；目标不再传工作区内容。

若使用 A 作源、B 作目标，先以 `draft-c4-state-reopen.py --source pool-a` 另生成 route。此时只启动 A client/Redis/RustFS 和 B client，B 的存储服务不参与。重启过的客户端不得覆盖原 `probes/*/probe.json`。两池交接后，从协调者执行只读探针：

```bash
python3 "$RUNNER" probe-current-boots --draft "$DRAFT" \
  --draft-sha256 "$(sha256sum "$DRAFT" | cut -d' ' -f1)" \
  --runtime-repo "$RUNTIME_REPO" --out "$CURRENT_BOOTS"
```

`RUNNER` 是含此命令的 `run-c4-state-reopen.py`。审阅探针结果，把 `CURRENT_BOOTS` 的绝对路径和 SHA256 作为 `current_boots` 加入已审阅运行请求。执行器在同一把物理资源锁内重读两个客户端，逐项比对实例 ID、原数据盘、boot ID 和空的 2 CPU / 8 GiB 槽；记录超过 900 秒、boot/盘变动或任一槽被占用都停止。旧请求未带 `current_boots` 时仍严格匹配原 boot 证据。

请求字段：

| 字段 | 来源 |
| --- | --- |
| `draft` | 协调机的 `preparation/c4-state-reopen-draft-01/recipe/draft.json` |
| `handoffs.pool-a`、`handoffs.pool-b` | 各组实际释放回执，包含 `pool` 与 `safe_for_handoff=true` |
| `latest` | 本次两条分支最新检查回执，10 分钟内 |
| `worker` | `c4-state-reopen-node.py` 的实际文件引用 |
| `verified_checkpoint`、`source_tree` | 同批协调机 `verified-checkpoint/checkpoint.json`、`source-tree.json` |
| `peers.source`、`peers.target` | 各含实际 `repo`、`opencode`、`juicefs` 引用 |
| `current_boots` | 可选；重启后的两端只读探针回执，随请求一并审阅 |

保留三个时间：目标挂载到就绪、就绪到验证原会话、源关闭到目标完整结束。第三项含配置传送、目标同步、排空和卸载；没有正式观测校准，不作为接续性能结果。
