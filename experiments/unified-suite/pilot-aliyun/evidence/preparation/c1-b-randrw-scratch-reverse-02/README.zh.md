# C1：两种运行顺序均完成冷验

两轮各用 B 池同三台实例、同 BrewFS Flat、JuiceFS 1.4.2、Redis、RustFS 和 fio，且每轮另挂四块同规格 100 GiB PL1 临时盘。原盘未扩容。反序轮输入与[正序轮](../c1-b-randrw-scratch-02/)逐字节一致。每臂都用同一份 64 MiB 输入做 4 KiB 随机混合读写，种子 9173，开启 `--fsync=1`。

| 运行顺序 | BrewFS fio | JuiceFS fio | JuiceFS / BrewFS |
| --- | ---: | ---: | ---: |
| JuiceFS → BrewFS | 36.160 s | 792.007 s | 21.90× |
| BrewFS → JuiceFS | 63.425 s | 741.537 s | 11.69× |

四臂各读取 50,479,104 B（12,324 次）、写入 16,629,760 B（4,060 次）。各臂先确认两次零上传债务，再冷重挂核验全部 67,108,864 B CRC，最后正常卸载。两轮的最终文件哈希允许不同，每臂分别核对自己的写入结果。

[首次反序](../c1-b-randrw-scratch-reverse-01/)在第一份非零样本处停止。此轮只改计时后的排空等待：BrewFS 第一份样本仍有 65,536 B 脏数据和 1 个活跃 slice，后两份样本相隔 1.053 秒且均为零；排空共 2.109 秒。JuiceFS 的头两份样本都为零，排空 1.056 秒。排空和冷验均未计入表中的 fio 时间。

在这项 64 MiB 同步混合读写诊断里，两种运行顺序下 BrewFS 的 fio 用时均较短。每种顺序仅一轮，物理盘不同，也未校准观测开销；不能用这两轮拆出运行顺序的影响、归因到某个设计，或外推到真实 Agent 负载。对象盘时钟未同步，物理读写仅保留整臂统计。正式 C1 仍是 9/10。

[控制器](released.json)在 08:39:27 UTC 确认四块临时盘已释放。[独立 API](independent-closure.json)在 08:39:59 UTC 再次确认四盘均不存在、七台实例均为 `Stopped/StopCharging`。

[双轮原始回放审计](cross-order-comparison.json) · [本轮审计](audit.json) · [客户端原始包](client-evidence.tar.gz) · [对象盘采样](objects-evidence.tar.gz) · [复核脚本](verify-c1-cross-order.py) · [SHA-256 清单](SHA256SUMS)
