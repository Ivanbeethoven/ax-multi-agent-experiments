# 最小脚本检查

当前按 [8 个实验块](../current/BLOCKS.zh.md)选择。已有 26 个入口的最小检查已完成：首轮 21 项通过，[后续五项](evidence/preparation/rbm-five-checks-20261112/README.zh.md)补齐。当前不运行 Agent 或 OpenCode，不调用模型。

这里只验证输入、配置、读写、重挂和清理；不会改变正式计时资格或历史结果。使用 Linux、Python ≥3.11、root 权限、Docker、/dev/fuse、redis-server、boto3，以及本批的工具与二进制。只用已有服务器；本页不创建云机。

| 项目 | 默认最小输入 | 可选项 |
|---|---|---|
| C1 | 1 MiB 顺序写读、16 次随机操作、fsync 和冷重挂 | 配置/layout 检查，不作正式 fio 成绩；已跳过的正式 JuiceFS randrw 不补跑 |
| C2 replay | 合成 140 回调 | 已录制的 367 回调或完整 trace：传 --full-trace，并核完整初终态及字节；不同输入分开记 |
| C2 runtime | 两文件只读热 lower | 同一通过核验的 read-control 候选；不是可写 Overlay 对照 |
| C3 | 128×4 KiB | --tiny 改为 8 文件；此入口没有一万文件参数 |
| C4 fork | 两文件、77 B | BrewFS 发布候选与原生 fork；JuiceFS 原生 clone |
| C4 handoff | 既有 checkpoint 文件树 | stay 或另一宿主 move；文件核验和目标持久写，无 Agent 进程 |

先在仓库根目录查看组名并预览：

```bash
python3 -m bench.experiment_smoke_matrix --list-blocks
python3 -m bench.experiment_smoke_matrix --block c1-io \
  --common-args /private/common.json --out /srv/ax-runs/c1-io-001 --dry-run
```

[common.json 的字段、每块额外参数、候选覆盖和汇总命令](../current/BLOCKS.zh.md#现有脚本怎样按块用)集中维护。预览不创建目录、不检查运行资格、不启动服务。检查真实路径、SHA 和输入后，取得本池资源锁再去掉 --dry-run。rbm 的锁仍为 `/home/dev/.ax-rbm-heavy.lock`；跨机另锁目标。相同物理资源不能因换了块名便并行。

所有输出路径须为新目录。运行器写 execution-plan.json，再逐 case 写日志、receipt.json 和 summary.json。非零退出、failed/blocked 或缺回执都会停止本块；先核退出与挂载清理，再用 --only CASE 和新的 --out 补做。其他独立资源池可以继续。

C2 需要 --trace 和 --replay。--full-trace 使用全部回调和内容校验；portable terminal probe 的有限 errno 差异沿用原回执，不能宣称所有 errno 完全一致。--trace-timeout 只用于该输入的资格步骤，不自动截短负载。生成新的无模型小输入仍用 `capture-smoke-trace.py`，它需要 fusepy。

Pack-off/all-off 传匹配候选的 --brewfs-provenance；C2 只读候选和 C4 发布候选也各有自己的回执格式。旧诊断候选不冒充同一优化产物，更不能按臂混用构建做性能消融。

C4 的文件系统参数见 [FIVE-CHECKS](FIVE-CHECKS.zh.md)。--checkpoint 的同级 workspace/ 是输入；不再传 --opencode，不导出原生会话。目标需要同版本脚本和同 SHA 的文件系统二进制；源退出后才重开同一卷。两台机器都不能占用其他任务的挂载点。

旧机器路径、旧候选命令和最早的 16/26、21/26 进度见[历史说明](https://github.com/Ivanbeethoven/ax-multi-agent-experiments/blob/9222c6318b8f9db0c3617219c8f18e12653aeba8/experiments/unified-suite/pilot-aliyun/RBM-MINIMAL.zh.md)，不作为当前启动命令。
