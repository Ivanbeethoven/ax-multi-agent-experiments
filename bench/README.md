# 实验脚本

当前入口：[C1–C4 方案](../experiments/unified-suite/current/README.zh.md)。最小功能检查使用 [experiment_smoke.py](experiment_smoke.py)，参数见[五项检查](../experiments/unified-suite/pilot-aliyun/FIVE-CHECKS.zh.md)；C4 仅检查文件系统，不启动 Agent。

下表包含当前工具和旧冻结套件的共用模块。节点、参数和版本来自实际清单；旧 profile 不能直接当新方案运行。

| 职责 | 入口 |
|---|---|
| 统一计划/单步占用 | [suite.py](suite.py)、[suite_catalog.py](suite_catalog.py)、[suite_state.py](suite_state.py)，复用experiment_step与runtime |
| 冻结客户端与容量 | [suite_mount.py](suite_mount.py)生成cell绑定的显式缓存配置；suite_support sizing列工作量与各池预算 |
| 新工作量 | [fio_workload.py](fio_workload.py)、[smallfiles](../tools/fs-probe/src/bin/smallfiles.rs)、[fork_burst.py](fork_burst.py) |
| 证据与报告 | [suite_support.py](suite_support.py)、[suite_validate.py](suite_validate.py)、[archive_check.py](archive_check.py)、[suite_report.py](suite_report.py) |
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
| 踩坑与旁路复现 | [scripts/benchmark-pitfalls.zh.md](../scripts/benchmark-pitfalls.zh.md)：11 条实测坑；[scripts/README.md](../scripts/README.md) 里的 `bench_*` standalone harness 是旁路复现（不入 suite 调度） |

旧冻结协议使用 `suite template --legacy-catalog --profile pilot|main --study systems|ablations`；新方案的统一适配尚未齐备，见当前方案。旧 pilot-8/c1-8/main/expanded 仅用于历史复现。infra 负责环境，runtime 负责执行，tools 负责测量，结果放入 runs/<campaign> 或独立数据盘。
