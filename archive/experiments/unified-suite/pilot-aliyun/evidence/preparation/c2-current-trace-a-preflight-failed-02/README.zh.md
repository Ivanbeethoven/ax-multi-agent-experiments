# C2 完整 trace：第二次云端预检

2026-11-12，A 池，运行 `c2-current-a-20261112t091353z`。七处[容量检查](capacity.json)通过，Redis 的持久化和 `noeviction` 配置检查通过；控制脚本随后在 Redis `INFO` 的组合检查失败。未进入客户端准备，未启动回放 worker，没有冷挂校验或性能样本。

[失败回执](controller-failure.json)记录了 `preflight` 第 290 行的 `ValueError`。该检查要求 Redis 返回全部选定字段、AOF 开启、没有进行 AOF 重写、`maxmemory` 为正且内存使用低于上限的 80%。控制脚本只在整项通过后保存字段，因此本次没有保存这些值，不能判断哪项触发失败。RustFS 独立校验位于该检查之后，本次未执行。

本次运行的控制脚本已包含[首次客户端 `Path` 报错](../c2-current-trace-a-preflight-failed-01/README.zh.md)的修复；由于提前失败，未在真实客户端验证该修复。[停止前回执](ready-for-stop.json)记录 `worker_launched=false`，没有自动重试。[关闭回执](closed.json)固定了 A 池停机原始回执的 SHA-256。A 池三台和跳板均已由 API 确认停机不收费；[独立复核](independent-closure.json)及[实例状态](instance-stop-checks.json)记录了七台的最终状态。

未将环境变量文件、原始 Redis INFO 或 trace 内容入库。旧冻结套件正式验收仍为 14/28。
