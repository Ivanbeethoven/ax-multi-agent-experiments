# C3 云端脚本复验：四阶段完成

复用 A 池三台节点和跳板机，仍用十万文件基线中的同样 64 个文件。四阶段共 128 次内容校验、512 KiB，全部通过；正常卸载，四台实例均已停回 StopCharging。不是十万文件全量核验，也不计入正式性能单元。

| 阶段 | 文件校验次数 | 耗时 |
|---|---:|---:|
| 首次串行 A | 32 | 93.96 s |
| 首次并行 B | 32 | 18.99 s |
| 串行复读 B | 32 | 18.89 s |
| 并行复读 A | 32 | 9.27 s |

本轮修复三个运行问题：

- 固定 DB 249 已被使用。增加显式自动选择空数据库的入口，实际选到 DB 240；不清空已有库。
- 云端旧恢复脚本还要求已退役的 On1 临时盘。现在传输并核对当前恢复脚本，objects 使用已有的跳过旧临时盘开关，只恢复四块 RustFS 数据盘。
- 前轮首次访问约 115 秒，120 秒总窗口不足以完成复读。保持输入、操作、槽位及预算不变，把这项诊断的上限设为 240 秒；本轮四阶段合计约 141.10 秒，正式验收阈值未改。

[原始四阶段结果](passed/receipt/worker-cohort-result.json) · [逐次操作记录](passed/receipt/worker-cohort-events.ndjson) · [实际输入及数据库](passed/receipt/worker-intent.json) · [卸载](passed/receipt/worker-mount-exit.json) · [跳板关停](window/final.json)。三台 A 节点的关停回执在 passed/after-stop-*.json。两次运行失败分别保留在 db-occupied/、retired-disk/，均已取回日志并停机。
