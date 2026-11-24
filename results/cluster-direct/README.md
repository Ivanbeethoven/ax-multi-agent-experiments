# 直连网络实验结果

[run-002 阶段结果](progress.md)逐项列出已通过的正式实验、性能指标和原始目录。表格由 `bench/cluster/runtime/report_progress.py` 读取协调机上的状态与结果生成。

[Packed 三项正式读取的 96 份读者结果](run-002-packed-readers.csv)来自六台主机的读者结果文件及标准错误记录。每项用 32 个读者读取 10 万个文件两遍。第 07 项（Dynamic 开、整包缓存开）14/32 通过；第 10 项（开、关）23/32 通过；第 11 项（关、开）17/32 通过。其余读者均返回 I/O 错误，三项均未计入有效性能结果。

[64 个 Agent 的逐任务耗时](run-002-dynamic-agent-times.csv)按同一任务配对，列出回放、文件系统操作、模拟等待、上传排空和跨机验证时间，单位为秒。最后一列为关闭减去开启 Dynamic 的回放耗时，负值表示关闭后更快。

在相同的主机分配和随机等待计划下，开启 Dynamic（修改版 RustFS，9000）用时 4,914.838 秒，关闭 Dynamic（原版 RustFS，19000）用时 3,549.335 秒，两组各有 64/64 个 Agent 通过跨机冷重挂验证。关闭后整组耗时缩短 27.8%；逐任务比较，44 个更快，20 个更慢，配对差值的中位数为 −456.992 秒。两轮均执行 92,940,160 次操作，实际读取 17,901,710,240 字节、写入 5,227,516,608 字节。两组使用不同的 RustFS 实现，这次比较反映两套正式配置的差异。

[64 个 Agent 的挂载指标](run-002-dynamic-mount-times.json)来自四台服务器的 128 份挂载结束记录，由 [replay_mount_times.py](../../bench/cluster/runtime/replay_mount_times.py) 提取。关闭 Dynamic 后，40 个 Agent 的 FUSE flush 累计耗时减少，配对差值的中位数为 −415.79 秒；47 个 Agent 的 S3 PUT 累计耗时减少。两轮实际上传均为 5.228 GB，相差 12,968 字节。按请求汇总，开启时的平均 PUT 耗时为 421 毫秒，关闭时为 173 毫秒。最慢的 sklearn 任务 a007 在两轮均位于 node-store-1，其 flush 从 1,748.22 秒降至 520.39 秒。各 Agent 和并发 PUT 的累计时间不可相加作为整组回放耗时。

[修复版本与复测安排](retest.md)记录候选改动、合并条件和下一轮的对照方法。

按第一个未使用的 `run-NNN/` 保存 C1～C4，服务器对应 `/srv/ax-cluster/runs/direct/run-NNN/`。范围见 [experiment-suite.json](../../configs/experiment-suite.json)，执行方法见 [交接文档](../../docs/RUNNING.md)。

每轮保留负载计划、源码版本、网络和实际服务端点、配置、命令、原始指标及各 C 结果。`network/` 保存直连和隧道诊断输出。结果目录包含 C1/C2/C3/C4、逐主机采样与汇总表；公开材料去除凭据。
