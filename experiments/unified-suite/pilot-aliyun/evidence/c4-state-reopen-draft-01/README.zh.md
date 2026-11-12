# C4：无模型跨机打开 OpenCode 原会话

先做状态验证：B 组 client 正常关闭，A 组 client 从原 B 组 Redis/RustFS 重挂同一工作区，用空 HOME 和工作区内的四个 XDG 目录打开同一个 OpenCode 会话。目标不接收 checkpoint 包或会话导出文件。OpenCode 只执行 `export`，进程在只有 loopback 的网络命名空间里，没有模型调用。

源端同步文件、检查两次上传债务均为零，确认原 OpenCode 和 FUSE 进程退出、挂载消失。协调者切换执行授权，并用原授权调用同一个门禁，必须在发出 SSH 前被拒绝。目标端先核对原工作区全树摘要，再核对原生会话 ID、历史与导出 SHA256。这是有序关闭后的接续检查；它不能证明 JuiceFS 能拒绝持有旧文件描述符的崩溃客户端，也不能证明模型已经继续工作。未填写正式 C4 样本。

复用 B/A 的现有 client、B 的 Redis/RustFS，以及原物理锁目录。协调机已恢复检查点并逐文件验证（894 项）；草稿没有联系任何 worker。数据库 210 只是候选，使用前要求实际为空。两组仍有工作时不执行本检查。

## C1 收尾后跑一次 A→B

1. 等 C1 正常关挂载、取回原始证据并释放 A/B 资源。取得两池本轮实际交接回执，核对各控制目录的 `cell_leases`。本次只用 A 客户端、A Redis、A RustFS 和 B 客户端；B 的 Redis、RustFS 保持关机。A 对象端用已核 SHA 的 [恢复脚本](../../scripts/a-pool-up.py)执行 `objects --without-legacy-observer-scratch --out "$A_OBJECTS_UP"`，只恢复四块原 RustFS 数据盘。A/B 客户端分别用对应的 [A](../../scripts/a-pool-up.py) / [B](../../scripts/b-pool-up.py) 脚本恢复原 `/srv/ax-client`。
2. 在两客户端核原实例、数据盘、空的 `/srv/ax-pilot-session` 和相同 JuiceFS、原 OpenCode 的二进制 SHA。重启后恢复磁盘不会重建执行槽；仅在槽不存在时用 [`configure-node.py slot`](../../scripts/configure-node.py) 和该客户端原 `hardware-inputs-01/data/pool-a-client-spec.json` 或 `pool-b-client-spec.json` 恢复，使用新输出目录。已有槽只读核对 2 CPU、8 GiB、无 swap 且无进程。两端都须能通过私网访问 A Redis/RustFS；源端另核私有对象凭证、服务身份、当前容量和候选 Redis DB 确实为空。DB 210 被占用就另选空 DB，禁止清空旧 DB。
3. 在协调机生成 A→B 路线，复用原 894 项检查点。下面 `SCRIPTS` 指向本轮已核 SHA 的脚本目录；`ROUTE`、`CURRENT_BOOTS` 为新的绝对路径。`RUNTIME_REPO` 使用已部署的兼容运行目录（如 `/srv/ax-pilot-control/code-15`）。执行器的 `pilot-node.py`、`prepare-session-input.py`、`package-session-checkpoint.py`、`draft-handoff-target.py` 四份 SHA 必须与该协调机运行目录一致。缺的代码或二进制只从协调机私网分发到新目录。

```bash
CTRL=/srv/ax-pilot-control/parallel-control-01
INPUT=/srv/ax-pilot-control/preparation/c4-state-reopen-draft-01
sha() { sha256sum "$1" | cut -d' ' -f1; }
python3 "$SCRIPTS/draft-c4-state-reopen.py" --source pool-a --candidate-database "$EMPTY_DB" \
  --runtime-repo "$RUNTIME_REPO" --control "$CTRL" --archive "$INPUT/checkpoint.tar.gz" \
  --checkpoint-receipt "$INPUT/receipt.json" \
  --opencode-release /srv/ax-pilot-control/assets/provenance/opencode-runtime.json --out "$ROUTE"
DRAFT="$ROUTE/draft.json"
```

4. 两池交接后执行只读探针，再用 [`verify-latest.py`](../../scripts/verify-latest.py) 对原 build manifest 生成新回执（600 秒内有效）。重启后的客户端不得覆盖原 `probes/*/probe.json`：

```bash
RUNNER="$SCRIPTS/run-c4-state-reopen.py"
python3 "$RUNNER" probe-current-boots --draft "$DRAFT" --draft-sha256 "$(sha "$DRAFT")" \
  --runtime-repo "$RUNTIME_REPO" --out "$CURRENT_BOOTS"
python3 "$SCRIPTS/verify-latest.py" --manifest "$CTRL/provenance/build-manifest.json" --out "$LATEST"
```

5. 人工审阅后另存 `pilot-c4-state-reopen-request-v1` 请求，设置 `reviewed: true` 和唯一 `attempt`。执行器在物理资源锁内重新读两客户端，比对实例、数据盘、boot 和空槽；探针超过 900 秒、资源改变或槽被占用时停止。旧请求未带 `current_boots` 时仍严格匹配原 boot 证据。

请求字段：

| 字段 | 来源 |
| --- | --- |
| `draft` | 本轮的 `$ROUTE/draft.json`；旧 B→A 草稿不可用于 A→B |
| `handoffs.pool-a`、`handoffs.pool-b` | 各组实际释放回执，包含 `pool` 与 `safe_for_handoff=true` |
| `latest` | 本次两条分支最新检查回执，10 分钟内 |
| `worker` | `c4-state-reopen-node.py` 的实际文件引用 |
| `verified_checkpoint`、`source_tree` | `$INPUT/verified-checkpoint/checkpoint.json`、`source-tree.json` |
| `peers.source`、`peers.target` | 各含实际 `repo`、`opencode`、`juicefs` 引用 |
| `current_boots` | 两端本轮只读探针回执，重启后必填 |

```bash
python3 "$RUNNER" --request "$REQUEST" --sha256 "$(sha "$REQUEST")" \
  --runtime-repo "$RUNTIME_REPO" --out "$FRESH_OUT"
```

执行时安排值守：当前 SSH 阶段和原生 export 没有硬超时。卡住时检查两端进程、挂载和槽，按原 supervisor 正常停止并核验；不要直接杀协调器释放锁，也不要自动重试。

停机前，从两客户端取回 `/srv/ax-client/preparation/$ATTEMPT/evidence/` 中的 `result.json`、`native-export/{started,exit,network,export}.json`、`debt/{0,1}.{json,prom}`，以及各自 `/srv/ax-client/m/$ATTEMPT-{source,target}/stopped.json`。按节点结果中的 SHA 核对，随协调机结果归档；会话原文只进私有归档。确认两端退出、无挂载、槽为空后，再按本轮资源归属执行 `StopCharging`。

保留三个时间：目标挂载到就绪、就绪到验证原会话、源关闭到目标完整结束。第三项含配置传送、目标同步、排空和卸载；没有正式观测校准，不作为接续性能结果。
