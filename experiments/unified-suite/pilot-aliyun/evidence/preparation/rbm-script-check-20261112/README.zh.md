# rbm 最小脚本检查

2026-11-12，按用户要求在 `ssh rbm` 串行验证脚本，不开云机、不调用模型。26 个现有入口最终 **21 通过、5 阻塞**；加上修复后重跑和默认输入检查，共 33 次尝试。首次失败保留在 [report.json](report.json) 的 `history`，`latest` 只合并对应入口的后续回执。

| 子项 | 最小输入 | 结果 |
|---|---|---|
| C1 读写、动态块配置 | 1 MiB；16 个随机位置；同步写、冷重挂 | 9 个入口通过 |
| C2 回放 | 实际采集的完整小 trace，367 回调，读 82,192 B、写 107,307 B | basic/full/JuiceFS/dynamic-off 均通过 |
| C2 热 lower 对照 | 两文件共 77 B，两遍读取；无性能计时 | 查询上层、跳过上层均通过 |
| C3 小文件 | 8 × 4 KiB，1 worker，8 次读、4 次写、8 次混合操作 | BrewFS、JuiceFS 均通过 |
| C4 原生派生 | 两文件共 77 B；子读父、覆盖、新建、父不变、子冷重挂 | BrewFS fork、JuiceFS clone 均通过 |
| C4 原会话重挂 | 原检查点 894 个目录项；空 HOME、工作区内 XDG；两次原生会话导出 | 两产品均通过；不是跨机或模型续跑 |

另外复验了默认的 140 回调合成输入，修正打包权限后通过。正式 `design.json` 没改：pilot 仍是 1 Agent、C3/C4 各 10,000 文件；本次只通过 `--tiny` 和小 trace 参数缩小输入。

## 卡在哪里

| 位置 | 实测 | 处理与结论 |
|---|---|---|
| C2 第 20 个回调 | 四条路径都在目录权限校验失败，尚未完成回放 | 启动器 `umask=077` 被回放器继承，创建模式再次被过滤。仅回放子进程设 `umask=0`，保留严格模式检查；四条路径重跑全部通过。合成回放入口也使用相同处理。 |
| 每项启动校验 | C2 四次重跑的二进制 SHA 校验为 16.5–17.8 s；单项全程约 23–26 s | 统一二进制与 producer 合计约 5 GB，重复计算 SHA 占了大部分时间；不是 trace 本身的耗时。本轮保留校验，没有缓存或删除验证步骤。 |
| C2 小输入发布、回放 | Packed 发布 1.62/1.87 s；四条回放路径约 0.9–1.4 s | 最小流程可以完成。旧云端 614 文件、35.5 MB 发布超时并未在相同环境重现，不能据此宣布解决。 |
| BrewFS 原会话重挂 | Packed 发布 40.83 s；初态校验 22.87 s；运行阶段 30.39 s；冷重挂校验 32.34 s；全程 153.53 s | 耗时落在发布和全树操作。尚未将发布内部耗时拆成上传、读回校验等。运行与冷验还包含导出、同步和树校验。 |
| OpenCode 退出 | BrewFS 首次导出主进程约 3.26 s 退出，后台进程再用 6.64 s；冷重挂后等待 7.03 s | 现有运行器正确等待原进程组自然结束，两次导出摘要相同。原来的固定 5 s 窗口没有足够余量。没有强杀后记为通过。 |
| JuiceFS 原会话导入 | 首次 `cp -a` 超过 60 s；独立重跑导入 53.52 s，完整流程 97.16 s | 新增独立 `--prepare-timeout`，本次该入口用 180 s；其余校验不变。53.52 s 是第二次实测，不是首次实际完成时间。 |
| Windows 打包默认输入 | 目录变成 0777、文件变成 0666，与清单的 0755/0644 不符 | 改用 `git -c tar.umask=0022 archive` 打包，Linux `tar --same-permissions` 解包。原失败保留；140 回调恢复通过。 |

阶段记录存在嵌套，例如回放命令包含在 `workload:run` 中，不能把所有阶段时间直接相加。这里的耗时只定位脚本等待，不比较两产品性能。

## 仍然不能跑的五个入口

- `C2-replay-packed-off`、`C2-replay-all-off`、`C3-smallfiles-packed-off`：同引擎开关尚未接入执行器，入口立即返回 blocked。
- `C4-handoff-brewfs-full-move`、`C4-handoff-juicefs-move`：最小执行器没有跨主机适配，不能用单机重挂代替。

同质/混合并发、错峰、多节点 barrier、四节点 RustFS 和可写 Overlay 消融也没有被本次单机小样验证。C1 layout 入口只验证参数、读写和重挂，不验实际落盘 B/q。C3 小样不代替一万文件资格。

## 复用

- [矩阵入口](../../../../../../bench/experiment_smoke_matrix.py)：`--only CASE` 只重跑指定项，`--case-overrides JSON` 为 C2/C4 绑定各自候选；默认仍遍历全部入口。
- [rbm 启动脚本](../../../scripts/run-rbm-script-check.sh)：使用新的 `/home/dev/ax-script-check-*` 目录，目录内 `repo` 为完整实验仓库；拒绝覆盖已有 `results`。脚本固定本次产物，是本轮复现入口，新版本须重新核来源。
- [阶段汇总](../../../scripts/summarize-smoke-check.py)：`--root /path/to/results --out /new/report.json`。原始阶段 JSONL 保存在每项 `run/stages.jsonl`。
- [实际首轮命令](run.sh)、[C2 修复后重跑](retry.sh)、[会话和默认输入复验](recheck-session.sh)。默认输入的最终成功结果使用 Git 导出的新目录，见 `report.json` 中 `results-default-retry`。

脚本包必须包含根目录 `scripts/full_trace_common.py`，不能依赖服务器 `/opt/ax-experiments` 中碰巧存在的副本。本轮补齐该依赖，并核对实际脚本 SHA。

## 版本、验证与清理

BrewFS main 为 `ca07159f`，RustFS 指定分支为 `f4ed2025`，开跑前均与远端分支一致。统一二进制复用已有默认 features 加 workspace-overlay、observability、rustfs-ec-block-size-hint 的 release；C2 只读开关、C4 fork 分别使用已核 SHA 的候选补丁产物，不能当作同一正式性能构建。

测试进程限 2 CPU/8 GiB；每个私有 RustFS 限 2 CPU/2 GiB、单节点四目录，Redis 为临时实例。不是正式分布式拓扑或持久性配置。

26 项针对性回归检查通过，见 [final-tests.txt](final-tests.txt)。[cleanup.json](cleanup.json)核实本轮 28 个临时容器均不存在、所有本轮挂载已卸载、4 个运行单元 MainPID 均为 0；原有两个容器仍在运行。没有启动、停止或删除阿里云实例，也没有修改 rbm 上其他任务的源码或服务。

[机器与产物](inventory.json) · [实际执行脚本 SHA](scripts.json) · [文件校验](SHA256SUMS)。本地只保存结果包，未下载工作区、trace 原档或会话原文。
