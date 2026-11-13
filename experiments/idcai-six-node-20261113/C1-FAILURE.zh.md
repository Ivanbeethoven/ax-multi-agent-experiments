# C1 混合读写：冷重挂读取失败

运行 `ax-four-aa0966f7c0c2`，BrewFS `8a215a65`、RustFS `f4ed2025`。九项 I/O 中八项通过。BrewFS `randrw` 的计时阶段完成，但冷重挂后的读取失败，这一项不计为通过，也不用于性能收益比较。

## 已确认的现象

- 文件和 I/O 总量均为 64 MiB，4 KiB 随机操作，读写比 75:25，每次写入执行 fsync。
- 准备和计时完成；卸载前，两次采样中的上传债务均为零。
- 冷挂载后，fio 读到偏移 `16,777,216` 时返回 `Input/output error`，请求长度 4,096 B，退出码为 1。不是已报告的校验和不匹配。
- 原始 `fio.json` 在 JSON 前带两行错误文字。原运行器因此另报 `JSONDecodeError`，掩盖了前面的 I/O 错误。
- 同时段 BrewFS 记录三次后台合并的 S3 PUT `InternalError`。失败读取对应第 4 个逻辑块，日志中的合并涉及第 6、3、0 块。现有证据不能证明二者有直接因果关系。

## 脚本怎么改

`run-performance-c1.py` 在 fio 失败时另外保存退出码、原始输出哈希、错误文字和失败阶段。原始文件与旧失败回执不改写，冷重挂校验也不降低标准。

原始证据在跳板机：

```text
/srv/ax-idcai-20261113/campaign-100k-001/ax-four-aa0966f7c0c2/c1-randrw-brewfs-qualification/
```

重点文件为 `trial.json`、`verify/exit.json`、`verify/fio.json`、`verify-mount.log`。仓库的小型汇总和归档索引随该运行一起保存。

## 日志缺口与后续记录

当时 RustFS 把应用日志发往 OTLP，但 collector 只有 metrics 管道；因此没有留住对应请求的后端错误日志。现有 journal 不能补回这些记录。collector 还报告过两项进程指标的重复标签，不能把“有 Prometheus 样本”理解成所有指标都有效。

后续启用 `-ServiceLogs` 的运行将 RustFS 的 warn/error 写到 stdout，由 systemd journal 留存；关闭没有接收端的 OTLP 日志出口，保留原 OTLP 指标出口。每轮结束时先停止本轮服务，再运行 `collect-service-journal.py`：脱敏、保存三个服务的日志、记录哈希，成功后才删临时凭据。日志收集失败则停止归档，不删凭据，不假报成功。日志与实际部署脚本进入原始归档，仓库只放小型索引。

三种 B/q 检查保持原日志配置一致。从 C4/C3 开始启用新配置；同一比较中的各臂一致。新日志不能补写到旧 C1/C2 结果中。后续复现混合读写失败须另立运行编号，保留本次失败。
