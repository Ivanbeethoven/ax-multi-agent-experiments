# C2 完整 trace：第三次运行在 Packed 发布超时

2026-11-12，A 池运行 `c2-current-a-20261112t093043z`。容量、Redis、RustFS 和客户端准备均通过，worker 已启动。它核对了 [trace 输入哈希](worker/input-inventory.json)和[初始文件树](worker/initial-tree.json)，随后在发布 Packed lower 时超时，**未开始回放**。

- 输入只用了固定 `pytest-full-02` 的初态：614 个文件、100 个目录、35,526,497 B。[worker 结果](worker/result.json)记录 `publish-packed-lower` / `TimeoutExpired`；[producer 回执](worker/producer.exit.json)记录上限 300 秒、实测 300.801 秒、终止退出 `-15`。没有分段日志能定位发布内部耗时，不能据此判定瓶颈，也与 C3/C4 的十万文件规模无关。
- 本次 Redis `CONFIG` 与 [`INFO` 选定字段](redis-info-01.json)均通过，RustFS 镜像和四个动态开关也[已核对](rustfs.json)。首次客户端路径修复在这次已执行到启动 worker；它不解释第二次 Redis 预检的具体原因。
- [停止前回执](ready-for-stop.json)确认执行槽为空、无本轮挂载。[关闭回执](closed.json)保留失败；17:39:18 北京时间，[独立 API](independent-closure.json)确认七台 `Stopped / StopCharging`，未新增实例或云盘。[实例状态与哈希](instance-stop-checks.json)固定实际运行的脚本和公开的 worker 回执。

本次未产生回放操作计数、冷挂结果或性能样本。原始 trace、环境变量、凭据及内部请求日志未入库。若再做完整资格，先用同一份 614 文件初态分段观察 Packed 发布，区分本地扫描、编码、对象上传和清单写出；本轮不盲目重跑。
