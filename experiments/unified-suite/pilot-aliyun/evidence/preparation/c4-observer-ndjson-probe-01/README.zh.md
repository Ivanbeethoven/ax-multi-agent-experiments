# 单流观察者本机端到端验证（2026-11-12 09:59，零云成本）

在协调者本机用最小 spec（`role=host-only`、间隔 0.5 秒、无 target/cgroup/process）真跑了一遍 `bench/runtime/observe_ndjson.py`：`start()` → 采样 → `mark` → `stop` → `audit()`。没有启动任何实例。

## 结果

| 项 | 值 |
| --- | --- |
| 启动 | `passed: true`，已完成 2 个采样 |
| 停止 | `passed: true`，共 **10 个采样** |
| 审计 | `passed: true`，`samples: 10`，`stream_sha256: 4686aac2…` |
| 目录内文件 | **5 个普通文件** + 1 个 socket：`samples.ndjson`、`finished.json`、`ready.json`、`mark-0001.json`、`collector.log` |
| 流大小 | **153,970 B / 10 行 = 15.4 KB 每行**（host-only；带 target 的真实 spec 约 50 KB/行） |
| `finished.json` | 3,352 B |

对照旧格式：10 个采样在旧布局下是 10 个目录、每目录约 45 个文件，即约 450 个成员；单流是 1 个成员。

## 真跑抓到的两个 bug（单元测试没覆盖）

单元测试从不调用 `serve()`，所以这两个只有在真跑里才暴露：

1. `serve()` 取 `last['counters']`，而 `snapshot_ndjson` 返回的摘要里没有该字段 → 第二个采样就 `KeyError: 'counters'`。
2. 修完继续跑，`current['target_states']` 又缺 → `KeyError: 'target_states'`。

修法：摘要补上 `counters` 与 `target_states` 供 `serve` 使用；同时把写进 `history` 的字段收窄为 `index`/`monotonic_*`/`passed`/`errors`/`line_sha256`/`line_bytes`，避免把每个采样的 `target_states` 全塞进 `finished.json`。

## 意义

- 归档成员数从 13 万降到**个位数**，16 MiB 清单问题在机制上消失。
- 审计判据没有放宽：`audit()` 仍然核对每行哈希、原文长度与哈希、时钟单调、最大间隔、marker 绑定与必需 marker。
- 这只证明模块本身；**还没有证明 overlay 在节点部署树里被采用**，那需要一次真实的短窗口。

## 产物

`finished.json`、`ready.json`、`samples-first-line.ndjson`（首行原文，含 host 采集内容）在本目录。完整流留在协调者 `/srv/ax-pilot-control/preparation/c4-ndjson-probe-01/run-01/collector/`。
