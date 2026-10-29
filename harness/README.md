# 当前实验脚本入口

操作命令和 AI 检查点只维护在 [统一分步手册](../infra/experiment/RUN.zh.md)，不在这里再复制参数。

| 工具 | 单次职责 |
|---|---|
| [final_plan.py](final_plan.py) | 为一个组生成任务表；不调度 |
| [experiment_inventory.py](experiment_inventory.py) / [host_preflight.py](host_preflight.py) | 拓扑模板/静态检查；Linux 主机只读检查 |
| [experiment_step.py](experiment_step.py) | 固定摘要后执行一条命令；独立保全/复核明确的文件清单 |
| [trace_checks.py](trace_checks.py) / [trace_oss_audit.py](trace_oss_audit.py) | trace 本地输入/结果/内容检查；OSS 目录与 sidecar 核查 |
| [warm_same_files.py](warm_same_files.py) | 生成或检查一遍 fio |
| [ml_corpus_inventory.py](ml_corpus_inventory.py) / [ml_import.py](ml_import.py) | 只读语料清单；向已挂载空目录导入一次 |
| [s3_requests.py](s3_requests.py) | 冻结请求表、上传或执行一次 GET/Range |
| [fs_step.py](fs_step.py) / [session_check.py](session_check.py) | fork/clone 文件状态与首用；Agent 原生会话历史核对 |
| [experiment_io.py](experiment_io.py) | 共用 JSON/SHA/路径函数，无部署副作用 |

`aliyun_suite.py`、`run_trace_matrix.py`、`run_cell.py`、`ml_readonly_matrix.py`、`ml_readonly_end_to_end.py` 仍被历史实验、资格验证和测试引用，保留可复现行为；不作为新一轮自动推进入口。ML 导入实现已集中在 `ml_import.py`，JSON/SHA 基础函数集中在 `experiment_io.py`，旧调用复用这些函数。

新的测量单步不负责同时创建资源、挂载、跑多 baseline、重试并清理。原生发布/mount/fork/clone CLI 由负责 Agent 按冻结版本核实后逐条执行；不能以包装器退出成功替代实验验收。
