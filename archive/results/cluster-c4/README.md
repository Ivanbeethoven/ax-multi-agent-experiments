# C4 运行结果

每轮写入第一个未使用的 `run-NNN/`，首轮为 `run-001/`。服务器对应目录为 `/srv/ax-cluster/runs/c4/run-001/`，A366 汇总六机记录。

保存 `plan.json`、`inventory.json`、`versions.json`、工作区与对象桶清单、逐挂载设置、阶段事件、CLI 原始输出、宿主/Redis/RustFS 指标、内容检查和三次重复的汇总。每次合并的记录放在 `C4/<condition>/<system>/r<repeat>/`。

操作 UUID 文件保存在服务器 `private/operations/`，其中可能包含 Redis 凭据。公开结果只保留操作 UUID、工作区 ID 与去除凭据的元数据地址。完整执行说明见 [C4 交接](../../docs/HANDOFF-C4.md)。
