# C1：反序轮在 BrewFS 排空处停止

复用 B 池同三台实例、同版本和设置，挂四块新的 100 GiB PL1 临时盘。原盘未扩容。计划是先 BrewFS Flat、后 JuiceFS 1.4.2；[复核](reverse-verified.json)确认输入逐字节与[正序轮](../c1-b-randrw-scratch-02/)一致，客户端二进制、fio、参数和应用读写量也相同。

| 轮次 | BrewFS fio 计时 | BrewFS 冷验 | JuiceFS |
| --- | ---: | --- | --- |
| 正序：JuiceFS → BrewFS | 36.160 s | 64 MiB 完整 CRC | 792.007 s，64 MiB 完整 CRC |
| 反序：BrewFS → JuiceFS | 42.634 s | 未开始 | 未启动 |

反序轮 BrewFS 的 fio 退出 0，应用读取 50,479,104 B（12,324 次），写入 16,629,760 B（4,060 次）。计时结束后的第一份原生样本记录 65,536 B 活跃脏数据、1 个活跃 slice、4,096 B 在途上传；其余四项排空计数为 0。原脚本要求第一份样本就全为 0，在计时结束约 37 ms 后失败，因此没有第二份样本、测量挂载正常退出回执或冷验。清理时测量服务已退出。一次失败样本不能判断排空实际需要多久；本轮不构成反序配对，正式 C1 仍是 9/10。

[本地审计](audit.json)使用当时的 `b9a7dee` 版审计脚本。原始 fio、阶段事件、原生指标和服务退出都在[客户端归档](client-evidence.tar.gz)。对象节点记录 27 次四盘采样，最少仍可用 99.955 GB，无容量中止。

[停机](stopped.json)与[释放](released.json)回执确认三台 B 实例均停机不收费，四块临时盘于 08:19:05 UTC 已释放。[独立 API 复查](independent-closure.json)于 08:21:02 UTC 确认七台实例停机不收费、四块临时盘均不存在。

[运行顺序与版本](plan.json) · [原始服务轮询](brewfs-unit-status.ndjson) · [对象节点记录](objects-evidence.tar.gz) · [SHA-256 清单](SHA256SUMS)
