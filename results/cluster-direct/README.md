# 直连网络实验结果

[run-002 阶段结果](progress.md)逐项列出已通过的正式实验、性能指标和原始目录。表格由 `bench/cluster/runtime/report_progress.py` 读取协调机上的状态与结果生成。

按第一个未使用的 `run-NNN/` 保存 C1～C4，服务器对应 `/srv/ax-cluster/runs/direct/run-NNN/`。范围见 [campaign-direct.json](../../configs/campaign-direct.json)，执行方法见 [交接文档](../../docs/RUNNING.md)。

每轮保留负载计划、源码版本、网络和实际服务端点、配置、命令、原始指标及各 C 结果。`network/` 保存直连和隧道诊断输出。结果目录包含 C1/C2/C3/C4、逐主机采样与汇总表；公开材料去除凭据。
