# C3：Packed 输出预算复现

沿用 A 池现有 100,000×4 KiB 基线、2 CPU / 8 GiB 槽位和 32 路核验，BrewFS `ca07159f`，RustFS `f4ed2025`。只打开已有的 `brewfs::packed_v3_budget` 日志，读回窗口 75 秒。

[BrewFS 日志](worker-mount.log)记录 23 次 `mount output budget temporarily exhausted`，输出预算为 33,554,432 B；例如已用 32,948,224 B，再请求 1,310,720 B。相同窗口[核验器](worker-verify.log)有 23 个线程收到 `ENOMEM`，与[上次没有 cgroup OOM](../c3-current-workspace-enomem-01/)的证据一致。没有成功的全量核验。

后续诊断通过同一二进制的 `BREWFS_PACKED_V3_OUTPUT_BUDGET_BYTES` 只调整这项挂载内上限，保持机器、输入和并发不变。该验证属于配置问题排查，不给正式 C3 计时补数。

原挂载[正常退出](worker-mount-exit.json)，三台 A 实例已[确认停机不收费](cloud-stopped.json)。没有创建或扩容资源。
