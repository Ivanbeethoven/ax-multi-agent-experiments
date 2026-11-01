# 实验脚本

Agent操作入口：[SSH-RUN](../experiments/retest-2026-10-29/runbook/SSH-RUN.zh.md)。从已有SSH环境开始，节点数量与配置来自实际清单。

| 职责 | 入口 |
|---|---|
| 方法矩阵 | [scale_plan.py](scale_plan.py)：C1–C4、配对区组、baseline/writeback；provider=ssh分离机器预算 |
| SSH节点与运行 | [runtime](runtime/README.md)：接入、挂载、worker、cohort、流转和观测 |
| 清单 | [ssh_inventory.py](ssh_inventory.py)：现有SSH环境；[experiment_inventory.py](experiment_inventory.py)：兼容旧角色清单 |
| 单条命令与保全 | [experiment_step.py](experiment_step.py)：execute / seal / verify |
| Trace | [trace_checks.py](trace_checks.py)：输入、工作量、内容；[trace_oss_audit.py](trace_oss_audit.py)：OSS对账 |
| 只读ML | [ml_corpus_inventory.py](ml_corpus_inventory.py)、[ml_import.py](ml_import.py)；测量使用tools/ml-corpus-scan |
| 对象请求 | [s3_requests.py](s3_requests.py)：冻结请求、创建对象、分片有界并发GET/Range |
| 派生与会话 | [fs_step.py](fs_step.py)、[session_check.py](session_check.py) |
| 可选等待 | [trace_think_plan.py](trace_think_plan.py)：固定总等待、0–15秒工具间隔，默认关闭 |
| 辅助与复现 | [host_preflight.py](host_preflight.py)、[final_plan.py](final_plan.py)、[warm_same_files.py](warm_same_files.py)、[reference](reference/README.md) |

C1当前profile为pilot-8/c1-8；main/expanded保留C2–C4设计与大规模扩展。实际运行按交接条件选择一个cohort。infra负责环境，runtime负责执行，tools负责测量，结果集中到runs/<campaign>。
