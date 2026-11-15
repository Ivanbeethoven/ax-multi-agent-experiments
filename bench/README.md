# 实验工具

当前执行入口为 [HANDOFF](../docs/HANDOFF-C1-C3.md)，参数见 [experiments](../experiments/README.md)。

| 工具 | 用途 |
|---|---|
| `cluster/plan.py` | 导出每个 C 的条件、系统、种子和重复顺序 |
| `cluster/mounts.py` | 每挂载配置与同机资源汇总 |
| `cluster/audit_mount.py` | 核对实际进程、配置、cgroup 与容量指标 |
| `runtime/observe.py` | 宿主、进程与服务指标读取 |
| `fio_workload.py` | fio 工作量生成与结果读取 |
| `s3_requests.py` | S3 对象写入与 Range 访问 |
| `ml_corpus_inventory.py`、`ml_import.py` | 只读文件目录与导入 |
| `trace_think_plan.py` | 回放等待计划；当前适配要求见 SETUP |

`bench/cluster` 的检查使用 `python -m unittest discover -s tests/cluster -v`。其它模块包含历史套件支持，调用前按当前 C 的配置核实参数和实际执行路径。
