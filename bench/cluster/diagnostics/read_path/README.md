# 冷读诊断结果

这三个脚本读取已有实验文件，不启动服务或重新生成负载。

```text
python compare-cold.py <结果目录>
python summarize-read-path.py <独立采集器结果目录>
python summarize-profile.py <CPU 采样结果目录>
```

- `compare-cold.py` 输出冷读吞吐、P99、GET 次数、下载字节及内容检查结果，支持 BrewFS 和 JuiceFS。
- `summarize-read-path.py` 生成 `monitoring-summary.json`，包含 RustFS 读取阶段、客户端与服务端 CPU 消耗、CPU 限流时间及服务端磁盘读取字节。
- `summarize-profile.py` 生成 `cpu-profile-summary.json`，包含 `perf` 函数采样比例和已记录的内存占用。

输入保留原有的 `fio-*/1-candidate/<操作>/` 结构。每个条件使用独立缓存目录和新挂载。RustFS 每次重新启动时，采集器也使用新进程，随后按冷读前后累计指标求差。

吞吐按应用读取字节除以运行 fio 的总时间计算；`fio_seconds` 单独保存 fio 报告的执行时间。CPU 采样和逐请求计时用于诊断，性能对比单独运行。各阶段可以同时执行，耗时不能直接相加。客户端冷缓存与服务端磁盘冷缓存分别记录。
