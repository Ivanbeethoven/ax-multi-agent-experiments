# 负载采集与数据工具

| 工作 | 入口 |
|---|---|
| OpenCode 与 SWE-bench trace 采集 | `run_opencode_full_trace.py`、`run_swebench_opencode.py` |
| FUSE 记录与内容读取 | `full_fuse_record.py`、`full_trace_common.py` |
| trace 分析 | `analyze_full_fuse_trace.py`、`check_trace_xattr.py` |
| 归档与上传 | `bundle_full_trace.py`、`archive_local_traces.py`、`upload_trace_archives.py` |
| 完整 trace 回放 | `replay_full_trace_bundle.py`、`tools/full-fuse-replay` |

采集方法见 [full-fuse-trace.zh.md](full-fuse-trace.zh.md)。正式实验使用现有输入目录和 Rust 回放工具，运行入口见 [RUNNING](../docs/RUNNING.md)。
