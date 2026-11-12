# Trace 工具与测量 harness

当前实验使用已有 trace 回放；不再运行真实 Agent 或 OpenCode resume。采集和会话工具保留用于解释已有数据及其依赖，不属于当前测试流程。[当前脚本入口](../experiments/unified-suite/pilot-aliyun/scripts/README.zh.md)。

本目录分两层，职责不同、互不替代：

- **跨批次 trace 工具 / 维护脚本** —— 采集、回放、归档、仓库检查（下表）。
- **实验测量 harness（standalone）** —— 2026-11 实验轮次固化下来的诊断/复现脚本，用于快速
  拿到可信数字。它们**不是** `bench/` suite 的替代：正式测量走
  [bench/README](../bench/README.md) 与 [统一 C1–C4 协议](../experiments/unified-suite/README.md)；
  这些 standalone 脚本对应「先单独验证一步、或复现某个坑」的快速路径，输入输出与 suite 语义一致
  （每 cell 一个固定工作、保留 raw 证据、磁盘护栏、串行）。

所有坑的完整记录在 [benchmark-pitfalls.zh.md](benchmark-pitfalls.zh.md)，每条都标了对应的统一协议文档。

## 跨批次 trace 工具与维护脚本

| 工作 | 入口 |
|---|---|
| 真实 OpenCode / SWE-bench 采集 | [run_opencode_full_trace.py](run_opencode_full_trace.py)、[run_swebench_opencode.py](run_swebench_opencode.py)；说明见 [full-fuse-trace](full-fuse-trace.zh.md) 和 [swebench-local](swebench-local.zh.md) |
| FUSE 记录、内容与序列核查 | [full_fuse_record.py](full_fuse_record.py)、[full_trace_common.py](full_trace_common.py)、[analyze_full_fuse_trace.py](analyze_full_fuse_trace.py) |
| 归档、上传与目录汇总 | [bundle_full_trace.py](bundle_full_trace.py)、[archive_local_traces.py](archive_local_traces.py)、[upload_trace_archives.py](upload_trace_archives.py)、[summarize_local_full_traces.py](summarize_local_full_traces.py) |
| 完整 trace 回放 | [replay_full_trace_bundle.py](replay_full_trace_bundle.py)，核心是 [Rust 回放器](../tools/full-fuse-replay/README.md) |
| 历史 strace 格式 | [fs-trace.zh.md](fs-trace.zh.md)；`fs_trace_capture.py` 和 `replay_fs_trace_bundle.py` 仍被内容核查/安全解包调用，保留 |
| 历史 OpenCode 会话恢复 | [opencode_resume_probe.py](opencode_resume_probe.py)；[旧 M 手册](../experiments/retest-2026-10-29/runbook/MIGRATION.zh.md)仅供查档，当前 C4 不执行会话恢复 |
| 仓库结构与归档 | [check_repository.py](check_repository.py)、[check_repository_archive.py](check_repository_archive.py) |

## 实验测量 harness（standalone）

| 脚本 | 对应 C 阶段 | 对应统一协议文档 | 关键产出 |
|---|---|---|---|
| [bench_fio_matrix.py](bench_fio_matrix.py) | C1 普通 fio | [06-fio.zh.md](../experiments/unified-suite/06-fio.zh.md) | bw / iops / p50·p95·p99，create-new 与 coldread 语义正确 |
| [object_store_ctl.sh](object_store_ctl.sh) | C1/C3 冷读前置 | [03-environment.zh.md](../experiments/unified-suite/03-environment.zh.md) | 安全重启对象存储（`pkill -x`、health 轮询） |
| [bench_workspace_fork.py](bench_workspace_fork.py) | C4 fork / 流转 | [09-fork-handoff.zh.md](../experiments/unified-suite/09-fork-handoff.zh.md) | fork `--count N` 耗时 + fork→可用端到端 |
| [check_trace_xattr.py](check_trace_xattr.py) | C2 AI trace 前置 | [07-trace.zh.md](../experiments/unified-suite/07-trace.zh.md) | trace 是否含 xattr 事件（回放前预检） |
| [bench_redis_meta.py](bench_redis_meta.py) | C3 元数据后端对照 | [08-smallfiles.zh.md](../experiments/unified-suite/08-smallfiles.zh.md) | 写入吞吐 + 元数据内存 |
| [benchmark-pitfalls.zh.md](benchmark-pitfalls.zh.md) | 全部 | [01-method.zh.md](../experiments/unified-suite/01-method.zh.md) | 11 条坑 + 实测数字 |

**约定**：

- 这些 standalone 脚本只做「固定工作、保留 raw 证据、串行、磁盘护栏」，与 `bench/` suite 的 cell 语义
  一致，但不参与 `suite.py` 的 plan/queue/lease 调度；跑完把结果目录并进 `runs/<campaign>`。
- 命名统一 `bench_*` 前缀，避免与 `bench/` 内同名工作负载混淆时再看本表：这里的是**旁路复现**，
  `bench/fio_workload.py`、`bench/fork_burst.py` 才是 suite 内正式工作负载。
- 每个脚本头部 docstring 都写清「为什么存在 / 对应坑 / 用法 / 输出」。

本目录不承担云资源调度。原 `check_plan.py` 只转发已废弃的网格校验器，原 `smoke_check.py` 与磁盘换挂脚本属于旧 baseline，已随其实现和测试归档。
