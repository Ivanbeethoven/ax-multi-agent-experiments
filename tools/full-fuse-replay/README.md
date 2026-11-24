# Agent 文件系统 trace 回放

`full-fuse-replay` 在 Linux 上执行 `full_fuse_record.py` 记录的文件操作。每条 trace 使用原有的 `events.jsonl` 和 `blobs/`，目标工作区须提前恢复到这条 trace 的初始状态。只使用可信输入和没有其他写者的专用工作区。

从实验仓库根目录构建：

```text
cargo build --release --locked --manifest-path tools/full-fuse-replay/Cargo.toml
```

## 多条 trace 并发回放

将不同 Agent 的 trace 写进同一个 `batch.json`。文件路径可使用绝对路径，也可相对于清单所在目录。

```json
{
  "schema": "full-fuse-parallel-v1",
  "streams": [
    {
      "id": "pytest",
      "events": "inputs/pytest/capture/events.jsonl",
      "blobs": "inputs/pytest/capture/blobs",
      "root": "workspaces/pytest"
    },
    {
      "id": "sklearn",
      "events": "inputs/sklearn/capture/events.jsonl",
      "blobs": "inputs/sklearn/capture/blobs",
      "root": "workspaces/sklearn"
    }
  ]
}
```

```text
full-fuse-replay --parallel batch.json batch-result.json --performance
```

- 支持 2～64 条 trace。一条 trace 对应一个线程，线程就绪后统一开始。各条 trace 内仍按原序执行，文件句柄和读写统计各自独立。无需复制或拼接原始 JSONL。
- 每条 trace 必须使用独立的可写工作区。可以共享不可变父数据；若复用同一输入，也必须为每个线程准备独立工作区。把输入、清单和新报告放在工作区之外。
- 可为每条 trace 加 `think_plan`，复用[已有的等待计划](THINK-TIME.zh.md)。未指定时连续执行，不插入等待。
- 报告记录本机总耗时、最大同时活跃线程数和总操作数、读写字节；每个线程保留单独的起止时间、操作延迟、重复读取比例、等待时间和错误。活跃线程也可能在等待，数量不能当作同时发出的 FUSE 请求数。
- 任一 trace 失败时命令返回非零并保留报告，其他已开始的线程继续执行。汇总操作数和字节量只包含已成功结束的线程；正式结果还应单独验证各工作区最终内容。
- 这用于模拟多 Agent 同时工作。单个 Agent 内原有的进程调度、请求重叠和文件锁没有在采集时保留。

## 单条 trace 回放与通用参数

```text
full-fuse-replay EVENTS.jsonl BLOBS_DIR DEDICATED_ROOT NEW_OUTPUT.json
```

单条回放可用 `--simulate-llm THINK_PLAN.json`；并发回放通过清单中每条 trace 的 `think_plan` 设置。以下选项适用于两种入口：

| 选项 | 用途 |
| --- | --- |
| 默认 | 检查操作顺序、返回值、读写长度和错误码。最终文件内容须另行逐字节比较。 |
| `--performance` | 用于已检查的输入。减少路径和返回值检查，仍执行每条操作并检查读写长度。报告的 `completed` 表示执行结束。 |
| `--portable-terminal-probe` | 允许 `ioctl(TCGETS)` 预期返回 `ENOTTY`、实际返回 `EOPNOTSUPP`，单独记录差异。与 `--performance` 互斥。 |
| `--cache-parent-fds` | 每条 trace 最多复用 256 个父目录句柄。性能对比的两侧应使用相同设置。 |

计时包含 JSON 解析、写入载荷读取以及实际文件操作。报告的读写字节是应用层统计；物理磁盘、对象存储和 FUSE 请求数由对应服务的计数器记录。
