# Trace 采集、回放与仓库检查

这里只放跨批次的 trace 工具和维护脚本。实验测量单步在 [bench](../bench/README.md)，本轮操作顺序在 [runbook](../experiments/retest-2026-10-29/runbook/RUN.zh.md)。

| 工作 | 入口 |
|---|---|
| 真实 OpenCode / SWE-bench 采集 | [run_opencode_full_trace.py](run_opencode_full_trace.py)、[run_swebench_opencode.py](run_swebench_opencode.py)；说明见 [full-fuse-trace](full-fuse-trace.zh.md) 和 [swebench-local](swebench-local.zh.md) |
| FUSE 记录、内容与序列核查 | [full_fuse_record.py](full_fuse_record.py)、[full_trace_common.py](full_trace_common.py)、[analyze_full_fuse_trace.py](analyze_full_fuse_trace.py) |
| 归档、上传与目录汇总 | [bundle_full_trace.py](bundle_full_trace.py)、[archive_local_traces.py](archive_local_traces.py)、[upload_trace_archives.py](upload_trace_archives.py)、[summarize_local_full_traces.py](summarize_local_full_traces.py) |
| 完整 trace 回放 | [replay_full_trace_bundle.py](replay_full_trace_bundle.py)，核心是 [Rust 回放器](../tools/full-fuse-replay/README.md) |
| 历史 strace 格式 | [fs-trace.zh.md](fs-trace.zh.md)；`fs_trace_capture.py` 和 `replay_fs_trace_bundle.py` 仍被内容核查/安全解包调用，保留 |
| OpenCode 会话恢复参考 | [opencode_resume_probe.py](opencode_resume_probe.py)；正式跨节点流转按 [M 手册](../experiments/retest-2026-10-29/runbook/MIGRATION.zh.md) 做资格 |
| 仓库结构与归档 | [check_repository.py](check_repository.py)、[check_repository_archive.py](check_repository_archive.py) |

本目录不承担云资源调度。原 `check_plan.py` 只转发已废弃的网格校验器，原 `smoke_check.py` 与磁盘换挂脚本属于旧 baseline，已随其实现和测试归档。
