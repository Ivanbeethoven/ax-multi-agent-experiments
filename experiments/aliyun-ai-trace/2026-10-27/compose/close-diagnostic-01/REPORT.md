本组只做 baseline→C4 一次 direct randwrite 诊断，使用原 compose 配置与新后端／目录；不是性能验收。两边正常卸载、挂载进程自然 exit 0，compose/backend 清理均通过。

| 指标 | baseline | C4 |
|---|---:|---:|
| fio active 写入秒数 | 20.001000000 | 20.009000000 |
| 写入字节 | 5930745856 | 6127878144 |
| 命令单调时钟秒数（含 strace） | 21.935383304 | 21.472541979 |
| 最终文件 close 秒数（strace -T） | 1.114247000 | 0.767870000 |
| 命令之外的 shell 秒数（含计时标记） | 0.111626622 | 0.115843823 |
| 原 shell 整数秒 | 22 | 21 |
| 命令 realtime 秒数 | 21.480333696 | 20.894432192 |
| realtime 与 monotonic 耗时之差 | -0.455049608 | -0.578109787 |

真实文件 close 确实阻塞，C4 本次反而更短；未观察到显式 fsync/fdatasync。post-write drain 两边均为 0 秒，pending/dirty/buffer-dirty 均为 0。这只把一部分收尾成本定位到 close，尚不能拆分 close 内部的 FUSE 工作，也不能把没有复现当作修复或验收通过。

时钟域存在变化：命令前后的 realtime-minus-monotonic 偏移各自发生约 0.455 秒／0.578 秒变化，原因未定位。因此不把 strace 的 epoch 时间、fio job_start 与 monotonic_ns 混合相减，不构造精确的最后一次写入时间轴。该诊断没有追踪 write/io_uring。

[匹配版本 strace 6.13 上游实现](https://github.com/strace/strace/blob/v6.13/src/syscall.c#L634-L672) 对 -T 的入口／出口耗时使用 CLOCK_MONOTONIC；打印小数位并不证明硬件精度，且未审计镜像发行版补丁。

本组去掉了原来前置 seqwrite，固定 20 秒又产生了不同写入量。若继续，只应针对“相同前置负载、相同写入量下是否仍有稳定差异”设计下一组，不据本组扩大性能结论。原始结果、strace、时钟标记、自然退出证据与文件 SHA 清单位于 retrieved/；summary.json 保留阶段值及逐条 syscall 行号。
