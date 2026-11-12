# C4 优化版构建：窗口到期

现有 rbm 的 `ax-brewfs-c4-native-fork-gwrxndj8-release-03.service` 只运行一次。2026-11-12 08:20:57 UTC 启动，08:50:45 UTC 到达 1,788 秒运行上限后被 SIGTERM 停止，另外预留 12 秒停止窗口。原 [构建回执](build-receipt.json) 固定 InvocationID `61b9c3ecc6d5414fbbba3ab312bd3e4e`，终态 `Result=timeout`、`ExecMainCode=2`、`ExecMainStatus=15`、`MainPID=0`。峰值内存 25,353,900,032 B（约 23.6 GiB），低于 38 GiB 限制；本次由时间上限终止，不是 OOM。

[源码版本](source-versions.json)固定 BrewFS `ca07159`、Packed fork 补丁 SHA `4464b9aa…` 及 RustFS `f4ed2025`。Cargo 用常规 `--release`（`opt-level=3`、debug 0、incremental 0），离线、锁版本、3 个 Cargo job，systemd 限 5 CPU、无 swap。复用了独立复制的依赖缓存；构建日志进入 BrewFS、tikv-client 和 asyncfuse，运行中从本次 cgroup 直接读到 BrewFS rustc 的 `opt-level=3`。私有原构建日志 SHA-256：`19d848253baf4db1eb2e486ce85e54628363d304202b6939b97f3927e6b40919`。

原 [shell EXIT 记录](build.exit)在同一时刻写了 `exit_code=0`，与 systemd 记录不一致，按原样保留。采集器以匹配的 systemd 终态、可执行文件和 SHA 为准；本次没有 `artifact.sha256` 或可验证的优化版产物。没有重试，也未启动云机或运行性能实验。[已有 opt=0 功能资格](../c4-local-packed-fork-candidate-01/root-nlink-build-receipt.json)单独保留，不替代优化版。
