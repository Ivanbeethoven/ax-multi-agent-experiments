# 写放大实验原始材料

主报告：[jfs-write-amplification.md](../../jfs-write-amplification.md)。所有数值按 case 的 before/after 原始 Prometheus 文本做差，权威复算结果为 `summary.json`。

- `preflight.json`：原进程、版本、挂载帮助、原始指标与脱敏卷格式。
- `setup.json`、`setup-commands.jsonl`：独立挂载参数、64 KiB 卷创建命令（凭据已脱敏）。原挂载保留。
- 各 case 目录：`before.json`、`immediate.json`、`after.json`、`after-validation.json`、settle 轮询记录、`result.json`。JSON 内同时保存四个客户端的完整原始 metrics 文本和解析字段。
- F/D/E 读探针还保存首轮写前、写后、读后快照，以及每轮阶段差值。`F_write_400` 的 `read_phase` 是相同位置的无读采样段，可能包含异步写完成，不能将其命名当成执行过读。
- `summary.json`、`tables.md`：重新从原始文本解析的统一摘要。早期快照/运行结果的辅助 `selected` 字段没有正确选中 FUSE 字节及 staging 字节的实际指标名称；其零占位不作为证据。最终复算明确读取 `juicefs_fuse_written_size_bytes_*`、`juicefs_fuse_read_size_bytes_*`、`juicefs_staging_block_bytes`，并检查实际 staging gauges 排空。PUT、GET、compaction 的指标选择在整个实验中一致。
- `smoke-recheck.json`：只读重算旧 initial/stable/cleandiag 的计数器，附原快照哈希；不包含任何主负载执行。
- `*-mount.log`：三个实验客户端完整 debug 日志，包含对象请求和 compaction。
- `source/`、`source-commit.txt`、`source-final-manifest.json`：对应实际 binary commit 的源码副本与哈希。`source-manifest.json` 保留云端首次下载部分失败的记录；缺少文件随后由本地官方 GitHub 下载补齐，最终以 final manifest 为准。
- `finish.json`、`final-before-unmount.json`：排空后的最终指标、卷 block 配置、三个实验挂载点卸载结果、原挂载存在性。
- `verify_amp.ps1`、`local-verification.json`：独立用 .NET decimal 解析 raw 文本，核对 16 个 case、表格、快照与源码哈希、配置和卸载状态；792 项断言通过。既有 129 个归档文件哈希匹配旧 manifest，`report.md` 原内容按字节保持一致，仅追加链接。
- `amp-manifest.json`：交付文件大小与 SHA256 清单（不包含清单自身）。

云端文件在 `/data/amp/`，数据目录访问路径为 `/data/amp/{default,writeback,small}/amp-20261025-<case>/`。它们在收尾已卸载；同卷数据仍能经原卷看到，64 KiB 卷数据保留在独立 bucket/db2。未删除数据与缓存。

本轮实际执行顺序（始终串行）：

```text
python3 /data/amp/preflight.py
python3 /data/amp/setup_amp.py
python3 /data/amp/run_amp.py A_final B_full C_sparse_final A_each C_sparse_each C_sparse_distinct D_final D_each E_final E_each
python3 /data/amp/probe_read_trigger.py F_read_trigger F_read_100 D_read_100 E_read_100 F_span_64K
python3 /data/amp/probe_read_trigger.py F_write_400
python3 /data/amp/analyze_amp.py
python3 /data/amp/finish_amp.py
```

首次 preflight 脚本经 `/data/amp-preflight.py` 启动，随后移入 `/data/amp/preflight.py`。采样脚本只使用 Python 标准库。新运行的目录必须为空：脚本使用 `exist_ok=False` 和 `O_EXCL`，防止混入旧文件。setup 还拒绝向非空 db2 格式化。重做实验前应在脚本中选择新的 `/data/amp/` 子目录、metrics 端口、独立空 metadata db 和 bucket；不要覆盖本轮路径。

离线复算不要求挂载、网络或正在运行的客户端：在有 Python 3.9+ 的机器，将 `run_amp.py` 与 `analyze_amp.py` 放在同一目录，执行：

```text
python3 analyze_amp.py --out <本目录绝对路径> --smoke <既有results目录绝对路径>
```

分析脚本会更新本目录的派生 `summary.json`、`tables.md`、`smoke-recheck.json`，不改原始快照或旧 results。`summary.json` 中保留 raw 快照 SHA256，并验证校验成功、对象错误为零、其他客户端无相关流量、staging 排空。
