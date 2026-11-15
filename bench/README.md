# 实验运行代码

- `cluster/suite.py`：展开四组条件和结果目录。
- `cluster/runtime/run_suite.py`：执行、保存状态、继续未完成条件。
- `cluster/runtime/suite_groups.py`、`suite_volumes.py`：fork、Packed、trace 与数据准备。
- `cluster/runtime/run_c1.py`：fio 输入、执行和跨机器读回。
- `cluster/runtime/run_c2.py`、`run_c3.py`：复用的 trace 与批量读取函数。
- `cluster/mounts.py`、`audit_mount.py`、`runtime/mount_driver.py`：挂载配置与实际进程检查。
- `cluster/runtime/host_batch_runner.py`、`host_collector.py`：服务器内部并发与采集。
- `cluster/runtime/cluster_lib.py`：共享 SSH 连接、文件分发与阶段指标。
- `cluster/runtime/setup_suite.py`、`status_suite.py`：环境准备与单次进度查询。

运行命令见 [RUNNING](../docs/RUNNING.md)，历史套件保存在 `archive/legacy-code/cluster-detailed/`。
