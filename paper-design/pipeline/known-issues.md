# 执行问题

当前问题、修复与资格边界见 [审阅](../REVIEW-20261028.md) 和 [运行说明](../../infra/alicloud/RUN.zh.md)。
测量口径见 [测量口径](MEASUREMENT-PROTOCOL.zh.md)。

旧版关于“应用读取字节应接近零”“超过磁盘上限就是 cache-hit”“低于 2×收益作废”的规则已撤销。

## KI-1 冷口径会系统性冤枉 overlay（尤其 packed）
同一二进制、同一机器，仅改口径（下层是否预热），overlay+native 的损耗率从 **9.0%** 变到 **56.8%**；
packed 在冷口径下只剩 flat 的 21.1%。冷口径把「冷取下层 + 冷建 chunk 索引/read plan」的一次性成本
摊到每次读上，**因此 cold 数字只能作参考，不能用于结论或验收**。见 [测量口径](MEASUREMENT-PROTOCOL.zh.md) §1。

## KI-2 `clat` / `p99` 不能用来反推阶段占比
`fio` 的 `clat` 含队列排队时间（`numjobs × iodepth` 个在途请求），
实测 native 与 flat 的 `clat` 相差 2.3 倍，但两者的**进程内单次读耗时**只有 1.19 ms 量级。
阶段占比必须用进程内六段计时，定义见 [测量口径](MEASUREMENT-PROTOCOL.zh.md) §3。

## KI-3 平均占比与尾部分位占比可能相反
native 实测：平均 Redis 60.4% / 数据 36.1%，但**最慢 1% 样本是 Redis 33.2% / 数据 65.4%**。
报告必须同时给两组，不能只给平均。

## KI-4 「同一份缓存」不等于「同一路径生效」
#378 的 chunk read plan 缓存只接在 native 的 `read_plan` 上；
packed binding 走 `prepare_unified_read`（`src/chunk/read_plan.rs`），**绕过该缓存**。
因此同类优化的收益必须**按路径分别验收**，不能用一条路径的结果代表全部。

`SELFTEST-OUTPUT.txt` 是旧执行器的历史输出；仅作追溯，不能代表当前验证结果。历史 CSV 保持原样，其中 C3 W2 的九行字节字段现在被拒绝。
