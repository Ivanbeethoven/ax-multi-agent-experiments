本目录保留一次 baseline→C4 的 direct randwrite 收尾诊断，**不是性能验收**。没有原实验的前置 seqwrite，固定 20 秒产生了不同写入字节数；不能把本次未复现回退解释为已经修复。

[简短结果](REPORT.md) · [结构化阶段值与逐行 syscall 证据](summary.json) · [公开文件 SHA 清单](retrieved/manifest.json)

两组最终文件 close 分别为 1.114247／0.767870 秒；命令内 realtime 与 monotonic 耗时之差约为 −0.455／−0.578 秒。不同时间域不能混合相减，未采集最后一次 write/io_uring 时间，也未拆分 close 内部 FUSE 工作。

保留原始 fio、时钟标记、所有相关 strace、drain／自然退出／清理记录。未复制 backend 配置、私有环境、重复日志或不相关采样。[完整原始归档的哈希索引](retrieved/source-manifest.json) 仅作溯源，其中被排除的路径不代表公开目录内存在该文件。
