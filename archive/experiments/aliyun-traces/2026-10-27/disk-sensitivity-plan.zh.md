# 云盘参数对照

独立新批次，三台机器并发；不混入已交付的 100 GiB 复测图。用户确认后，三台实例已于 2026-10-27 06:23–06:27 UTC 创建并完成环境准备；先校准各自云盘，再执行下面四种方法。目前没有本批次完整回放结果。

统一使用杭州 k 区 `ecs.g8i.2xlarge`（8 vCPU、32 GiB）、Ubuntu 24.04、40 GiB PL0 系统盘、XFS 数据盘。该实例持续云盘上限为 60,000 IOPS、4 Gbit/s，足以覆盖下表；本轮旧 e 实例只有 0.8 Gbit/s，不能仅扩大其磁盘就认定吞吐瓶颈已消除。[实例规格](https://help.aliyun.com/zh/ecs/user-guide/general-purpose-instance-families)、[共享型实例](https://help.aliyun.com/zh/ecs/user-guide/shared-instance-families)

| 数据盘 | 单盘理论 IOPS | 单盘理论吞吐 | 本次查询的整机硬件估价 |
|---|---:|---:|---:|
| 100 GiB PL0 | 3,000 | 125 MB/s | 0.5656 元/小时 |
| 500 GiB PL0 | 7,800 | 180 MB/s | 0.9856 元/小时 |
| 500 GiB PL1 | 26,800 | 350 MB/s | 1.5106 元/小时 |

前两组比较容量，后两组比较性能等级。理论值按阿里云公式计算：PL0 为 `min(1800+12×GiB,10000)` IOPS、`min(100+0.25×GiB,180)` MB/s；PL1 为 `min(1800+50×GiB,50000)` IOPS、`min(120+0.5×GiB,350)` MB/s。实际还受实例总上限约束，不能用理论值代替实测。[云盘规格及公式](https://help.aliyun.com/zh/ecs/user-guide/essds)

表中采用各节点创建前重新取得的组合报价，包含抢占式计算、系统盘及数据盘，三台合计约 3.0618 元/小时；不含公网流量、OSS，现货价格可能变化，最终按账单为准。采用一小时保护期，不设置实例自动释放时间或 trace 总时限。

1. 用原生 fio 在独立、真实写入的 16 GiB 文件上校准：4 KiB 随机读/写（QD32）、1 MiB 顺序读/写、4 KiB 同步写，各测量 60 秒。保留吞吐、IOPS、总延迟及分位数；同步写另记 fdatasync 成本。校准不计入 trace 耗时。
2. 每台按相同顺序运行 native、native-overlay、native-overlay-pack、JuiceFS，各一次完整 `pytest-full-02`。复用同一 C4 BrewFS、C4 fixture 和原 Rust 回放器；保留全部操作。每方法使用新目录、缓存及独立后端状态，统一缓存预算、Redis AOF always、压缩、FUSE 参数和日志落盘位置，不在回放中调整云盘。
3. 保留低频 diskstats、CPU/steal、读写字节、队列、await 与云盘/实例限速证据。记录实际 CPU 型号和设备映射；busy% 不能单独证明饱和。准备、打包上传、挂载、回放、卸载分别计时。[云盘分析](https://help.aliyun.com/zh/ecs/user-guide/cloud-disk-analysis/)
4. 每方法完整归档并验证 SHA；最后核对实例、两块盘、安全组和临时桶的精确清单并释放。三台全部释放后再删除本批次专用交换机，保留既有 VPC、交换机和 trace 存储。

本批次是单次探索性对照。实例型号相同仍须记录实际 CPU 和宿主差异；无法排除这些差异时，仅报告配置组合结果，不把全部变化归因于云盘。最终论文结论需要后续重复验证。
